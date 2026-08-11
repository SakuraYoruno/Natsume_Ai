from __future__ import annotations

import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from host_brain.skills import supply_chain_quarantine as quarantine_module
from host_brain.skills.supply_chain import (
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    SIGNATURE_FILENAME,
    SkillSupplyChainVerifier,
    SupplyChainActivationMode,
    canonical_json_bytes,
    compute_inventory_digest,
)
from host_brain.skills.supply_chain_quarantine import SkillBundleQuarantine


KEY_ID = "quarantine-test-key"
SECRET = b"quarantine-dedicated-signing-secret-more-than-32-bytes"
PUBLISHER_ID = "publisher://trusted/quarantine-example"


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _make_bundle(root: Path) -> dict[str, object]:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("# quarantined skill\n", encoding="utf-8")
    (root / "provider.py").write_text(
        "raise RuntimeError('quarantine must not execute me')\n",
        encoding="utf-8",
    )
    (root / "nested").mkdir()
    (root / "nested" / "config.json").write_text('{"enabled":true}', encoding="utf-8")
    (root / "sbom.json").write_text(
        '{"bomFormat":"CycloneDX","components":[],"specVersion":"1.5"}',
        encoding="utf-8",
    )
    return _seal(root)


def _seal(root: Path) -> dict[str, object]:
    files: list[dict[str, object]] = []
    for path in root.rglob("*"):
        if path.is_file() and path.name not in {MANIFEST_FILENAME, SIGNATURE_FILENAME}:
            raw = path.read_bytes()
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": len(raw),
                    "digest": _digest(raw),
                }
            )
    files.sort(key=lambda item: str(item["path"]).encode("utf-8"))
    sbom_digest = next(item["digest"] for item in files if item["path"] == "sbom.json")
    manifest: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "name": "quarantine-example",
        "version": "1.0.0",
        "bundle_kind": "SKILL",
        "publisher_id": PUBLISHER_ID,
        "bundle_digest": compute_inventory_digest(files),
        "files": files,
        "sbom": {"format": "cyclonedx-json", "path": "sbom.json", "digest": sbom_digest},
        "sandbox": {
            "profile": "python-restricted-v1",
            "secrets": [],
            "network": [],
            "process": "deny",
            "workspace": "none",
        },
        "signing": {"algorithm": "hmac-sha256", "key_id": KEY_ID},
    }
    _write_manifest(root, manifest)
    return manifest


def _write_manifest(root: Path, manifest: dict[str, object], *, padded: bool = False) -> None:
    if padded:
        (root / MANIFEST_FILENAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    else:
        (root / MANIFEST_FILENAME).write_bytes(canonical_json_bytes(manifest))
    signature = hmac.new(SECRET, canonical_json_bytes(manifest), hashlib.sha256).hexdigest()
    (root / SIGNATURE_FILENAME).write_text(f"hmac-sha256:{signature}", encoding="ascii")


def _verifier(mode: str | SupplyChainActivationMode = "QUARANTINE") -> SkillSupplyChainVerifier:
    return SkillSupplyChainVerifier(
        mode=mode,
        trusted_keys={
            KEY_ID: {
                "secret": SECRET,
                "publisher_id": PUBLISHER_ID,
                "allowed_bundle_kinds": ["SKILL"],
                "allowed_bundle_names": ["quarantine-example"],
            }
        },
    )


def _service(source_parent: Path, *, mode: str = "QUARANTINE", **limits: int) -> SkillBundleQuarantine:
    return SkillBundleQuarantine(
        verifier=_verifier(mode),
        quarantine_root=source_parent / "quarantine",
        **limits,
    )


class _ExplodingPath:
    def __fspath__(self) -> str:
        raise AssertionError("BUILTINS_ONLY must not inspect the source")


def test_builtins_only_rejects_without_reading_source_or_creating_root(tmp_path: Path) -> None:
    quarantine_root = tmp_path / "quarantine"
    service = SkillBundleQuarantine(
        verifier=SkillSupplyChainVerifier(mode="BUILTINS_ONLY"),
        quarantine_root=quarantine_root,
    )
    decision = service.quarantine_bundle(_ExplodingPath())  # type: ignore[arg-type]
    assert decision.quarantined is False
    assert decision.reason_code == "SUPPLY_CHAIN_EXTERNAL_BUNDLES_DISABLED"
    assert quarantine_root.exists() is False


def test_verifier_exception_is_a_stable_rejection_without_quarantine_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)

    def explode(_: object) -> object:
        raise RecursionError("untrusted manifest depth")

    monkeypatch.setattr(service._verifier, "verify_bundle", explode)
    decision = service.quarantine_bundle(tmp_path / "source")

    assert decision.reason_code == "SUPPLY_CHAIN_VERIFIER_FAILED"
    assert decision.quarantined is False
    assert not (tmp_path / "quarantine").exists()


def test_valid_bundle_is_copied_reverified_and_atomically_content_addressed(tmp_path: Path) -> None:
    source = tmp_path / "source"
    manifest = _make_bundle(source)
    service = _service(tmp_path)

    decision = service.quarantine_bundle(source)

    expected_name = "sha256-" + str(manifest["bundle_digest"]).split(":", 1)[1]
    target = Path(decision.quarantine_path or "")
    assert decision.quarantined is True
    assert decision.idempotent is False
    assert decision.activation_eligible is False
    assert decision.reason_code == "SUPPLY_CHAIN_QUARANTINED"
    assert target.name == expected_name
    assert target.parent == (tmp_path / "quarantine").resolve()
    assert (target / "provider.py").read_bytes() == (source / "provider.py").read_bytes()
    assert not list(target.parent.glob(".*.tmp"))
    assert _verifier().verify_bundle(target).verified is True


def test_staged_mode_preserves_eligibility_but_still_only_quarantines(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_bundle(source)
    decision = _service(tmp_path, mode="STAGED").quarantine_bundle(source)
    assert decision.quarantined is True
    assert decision.activation_eligible is True
    assert Path(decision.quarantine_path or "").is_dir()


def test_quarantine_never_executes_bundle_code(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_bundle(source)
    marker = tmp_path / "bundle-code-ran"
    (source / "provider.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    _seal(source)
    assert _service(tmp_path).quarantine_bundle(source).quarantined is True
    assert marker.exists() is False


def test_same_digest_and_exact_manifest_is_idempotent_after_reverification(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_bundle(source)
    service = _service(tmp_path)
    first = service.quarantine_bundle(source)
    second = service.quarantine_bundle(source)

    assert first.quarantined is True and first.idempotent is False
    assert second.quarantined is True and second.idempotent is True
    assert second.reason_code == "SUPPLY_CHAIN_QUARANTINE_ALREADY_PRESENT"
    assert first.quarantine_path == second.quarantine_path


def test_existing_corrupt_target_is_never_overwritten_or_deleted(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_bundle(source)
    service = _service(tmp_path)
    first = service.quarantine_bundle(source)
    target = Path(first.quarantine_path or "")
    corrupt = b"existing-corruption"
    (target / "provider.py").write_bytes(corrupt)

    second = service.quarantine_bundle(source)
    assert second.quarantined is False
    assert second.reason_code == "SUPPLY_CHAIN_QUARANTINE_EXISTING_CONFLICT"
    assert (target / "provider.py").read_bytes() == corrupt
    assert not list(target.parent.glob(".*.tmp"))


def test_publish_race_never_replaces_noncooperating_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    manifest = _make_bundle(source)
    quarantine_root = tmp_path / "quarantine"
    target = quarantine_root / (
        "sha256-" + str(manifest["bundle_digest"]).split(":", 1)[1]
    )
    original_publish = quarantine_module._publish_directory_noreplace

    def race_publish(temp: Path, destination: Path) -> None:
        assert destination == target
        destination.mkdir()
        (destination / "racer-owned.txt").write_text("preserve", encoding="utf-8")
        original_publish(temp, destination)

    monkeypatch.setattr(
        quarantine_module,
        "_publish_directory_noreplace",
        race_publish,
    )

    decision = _service(tmp_path).quarantine_bundle(source)

    assert decision.reason_code == "SUPPLY_CHAIN_QUARANTINE_EXISTING_CONFLICT"
    assert decision.quarantined is False
    assert (target / "racer-owned.txt").read_text(encoding="utf-8") == "preserve"
    assert not list(quarantine_root.glob(".*.tmp"))


def test_same_payload_digest_with_different_manifest_is_existing_conflict(tmp_path: Path) -> None:
    source = tmp_path / "source"
    manifest = _make_bundle(source)
    service = _service(tmp_path)
    first = service.quarantine_bundle(source)
    _write_manifest(source, manifest, padded=True)

    second = service.quarantine_bundle(source)
    assert second.reason_code == "SUPPLY_CHAIN_QUARANTINE_EXISTING_CONFLICT"
    assert second.quarantine_path is None
    assert Path(first.quarantine_path or "").is_dir()


def test_copy_limit_failure_cleans_random_temp_and_creates_no_target(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_bundle(source)
    service = _service(tmp_path, max_total_bytes=1)
    decision = service.quarantine_bundle(source)

    root = tmp_path / "quarantine"
    assert decision.reason_code == "SUPPLY_CHAIN_QUARANTINE_TOTAL_SIZE_LIMIT_EXCEEDED"
    assert decision.quarantined is False
    assert not list(root.glob("sha256-*"))
    assert not list(root.glob(".*.tmp"))


def test_invalid_signature_is_rejected_before_quarantine_root_is_touched(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_bundle(source)
    (source / SIGNATURE_FILENAME).write_text("hmac-sha256:" + "0" * 64, encoding="ascii")
    root = tmp_path / "quarantine"
    decision = _service(tmp_path).quarantine_bundle(source)
    assert decision.reason_code == "SUPPLY_CHAIN_SIGNATURE_INVALID"
    assert root.exists() is False


def test_source_mutation_after_initial_verification_fails_reverify_and_cleans_temp(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_bundle(source)
    delegate = _verifier()

    class _TamperingVerifier:
        mode = delegate.mode
        configuration_valid = delegate.configuration_valid

        def __init__(self) -> None:
            self.calls = 0

        def verify_bundle(self, path):  # noqa: ANN001, ANN201
            self.calls += 1
            result = delegate.verify_bundle(path)
            if self.calls == 1:
                (source / "provider.py").write_text("# changed after verify", encoding="utf-8")
            return result

    root = tmp_path / "quarantine"
    service = SkillBundleQuarantine(verifier=_TamperingVerifier(), quarantine_root=root)  # type: ignore[arg-type]
    decision = service.quarantine_bundle(source)
    assert decision.reason_code == "SUPPLY_CHAIN_QUARANTINE_REVERIFY_MISMATCH"
    assert not list(root.glob("sha256-*"))
    assert not list(root.glob(".*.tmp"))


def test_source_and_quarantine_tree_may_not_overlap(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_bundle(source)
    nested_root = source / "quarantine"
    service = SkillBundleQuarantine(verifier=_verifier(), quarantine_root=nested_root)
    decision = service.quarantine_bundle(source)
    assert decision.reason_code == "SUPPLY_CHAIN_QUARANTINE_PATH_OVERLAP"
    assert not list(nested_root.glob(".*.tmp"))


def test_symlink_is_rejected_and_no_temp_survives(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_bundle(source)
    try:
        (source / "linked.py").symlink_to(source / "provider.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    decision = _service(tmp_path).quarantine_bundle(source)
    assert decision.reason_code == "SUPPLY_CHAIN_SYMLINK_FORBIDDEN"
    assert not (tmp_path / "quarantine").exists()


def test_concurrent_same_digest_has_one_fresh_result_and_one_idempotent_result(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _make_bundle(source)
    service = _service(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.quarantine_bundle(source), range(2)))
    assert all(result.quarantined for result in results)
    assert sorted(result.idempotent for result in results) == [False, True]
    assert len(list((tmp_path / "quarantine").glob("sha256-*"))) == 1
    assert not list((tmp_path / "quarantine").glob(".*.tmp"))


def test_quarantine_audit_has_no_source_path_secret_or_raw_content(tmp_path: Path) -> None:
    source = tmp_path / "sensitive-source-name"
    _make_bundle(source)
    decision = _service(tmp_path).quarantine_bundle(source)
    encoded = json.dumps(dict(decision.audit), sort_keys=True)
    assert SECRET.decode() not in encoded
    assert str(source) not in encoded
    assert "quarantine must not execute" not in encoded
    assert decision.audit["target_id"] == Path(decision.quarantine_path or "").name


def test_module_contains_no_dynamic_import_execution_or_active_overwrite() -> None:
    source = Path("host_brain/skills/supply_chain_quarantine.py").read_text(encoding="utf-8")
    assert "importlib" not in source
    assert "exec(" not in source
    assert "eval(" not in source
    assert "subprocess" not in source
    assert "os.replace" not in source
    assert "copytree" not in source
