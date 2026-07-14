from __future__ import annotations

import hashlib
import hmac
import json
import stat as stat_module
from pathlib import Path
from types import SimpleNamespace

from host_brain.skills.hub import hub_installer, hub_loader
from host_brain.skills.registry import SkillRegistry
from host_brain.skills.supply_chain import (
    MANIFEST_SCHEMA_VERSION,
    canonical_json_bytes,
    compute_inventory_digest,
)


_KEY_ID = "hub-boundary-test"
_SECRET = "hub-boundary-test-secret-32-bytes!!"
_PUBLISHER_ID = "publisher://trusted/hub-boundary"


def _trust(name: str = "demo-skill") -> dict[str, object]:
    return {
        "secret": _SECRET,
        "publisher_id": _PUBLISHER_ID,
        "allowed_bundle_kinds": ["SKILL"],
        "allowed_bundle_names": [name],
    }


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _write_signed_bundle(
    bundle_dir: Path,
    *,
    name: str = "demo-skill",
    python_extension: bool = False,
) -> Path:
    bundle_dir.mkdir(parents=True)
    skill_md = (
        "---\n"
        f"name: {name}\n"
        "version: 1.0.0\n"
        "description: staged demo\n"
        "clearance: LOW\n"
        "risk_level: 1\n"
        "scope: host\n"
        "tool: demo_tool\n"
        "---\n"
    ).encode("utf-8")
    sbom = canonical_json_bytes(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "components": [],
        }
    )
    payloads: dict[str, bytes] = {
        "SKILL.md": skill_md,
        "sbom.json": sbom,
    }
    if python_extension:
        payloads["executor.py"] = b"raise RuntimeError('must never execute')\n"
    for relative, raw in payloads.items():
        (bundle_dir / relative).write_bytes(raw)

    files = [
        {"path": relative, "size": len(raw), "digest": _digest(raw)}
        for relative, raw in sorted(payloads.items())
    ]
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "name": name,
        "version": "1.0.0",
        "bundle_kind": "SKILL",
        "publisher_id": _PUBLISHER_ID,
        "bundle_digest": compute_inventory_digest(files),
        "files": files,
        "sbom": {
            "format": "cyclonedx-json",
            "path": "sbom.json",
            "digest": _digest(sbom),
        },
        "sandbox": {
            "profile": "python-restricted-v1",
            "secrets": [],
            "network": [],
            "process": "deny",
            "workspace": "none",
        },
        "signing": {"algorithm": "hmac-sha256", "key_id": _KEY_ID},
    }
    manifest_bytes = canonical_json_bytes(manifest)
    (bundle_dir / "bundle-manifest.json").write_bytes(manifest_bytes)
    signature = hmac.new(_SECRET.encode("utf-8"), manifest_bytes, hashlib.sha256).hexdigest()
    (bundle_dir / "bundle-manifest.sig").write_text(
        f"hmac-sha256:{signature}",
        encoding="ascii",
    )
    return bundle_dir


def test_hub_verifier_exception_is_a_stable_rejection(tmp_path: Path) -> None:
    bundle = tmp_path / "exploding"
    bundle.mkdir()
    skill_md = bundle / "SKILL.md"
    skill_md.write_text("# never parsed\n", encoding="utf-8")

    class ExplodingVerifier:
        def verify_bundle(self, source: object) -> object:
            raise RecursionError("untrusted manifest depth")

    info = hub_loader._load_single_skill(
        skill_dir=bundle,
        skill_md=skill_md,
        registry=SkillRegistry(),
        executor_registry=None,
        verifier=ExplodingVerifier(),  # type: ignore[arg-type]
    )

    assert info.status == "REJECTED"
    assert info.reason_code == "SUPPLY_CHAIN_VERIFIER_FAILED"


def test_legacy_install_and_search_reject_before_network_or_subprocess(monkeypatch, tmp_path: Path) -> None:
    calls = {"cli": 0, "download": 0}
    monkeypatch.delenv("HOST_SUPPLY_CHAIN_ACTIVATION_MODE", raising=False)
    monkeypatch.setattr(
        hub_installer,
        "_run_clawhub_cli",
        lambda *args, **kwargs: calls.__setitem__("cli", calls["cli"] + 1),
    )
    monkeypatch.setattr(
        hub_installer,
        "_download_github_skill",
        lambda *args, **kwargs: calls.__setitem__("download", calls["download"] + 1),
    )

    install = hub_installer.install_skill(
        r"\\attacker.invalid\credential-leak",
        skills_dir=tmp_path / "must-not-exist",
        force=True,
    )
    search = hub_installer.search_remote_skills_with_status(
        "anything",
        registry_url="file:///C:/sensitive.json",
    )

    assert install.success is False
    assert install.error == "SUPPLY_CHAIN_EXTERNAL_BUNDLES_DISABLED"
    assert search.results == []
    assert search.error == "SUPPLY_CHAIN_EXTERNAL_BUNDLES_DISABLED"
    assert calls == {"cli": 0, "download": 0}
    assert not (tmp_path / "must-not-exist").exists()


def test_non_builtin_modes_require_signed_quarantine_intake(monkeypatch, tmp_path: Path) -> None:
    for mode in ("QUARANTINE", "STAGED"):
        monkeypatch.setenv("HOST_SUPPLY_CHAIN_ACTIVATION_MODE", mode)
        install = hub_installer.install_skill(
            "github:owner/repo/demo-skill",
            skills_dir=tmp_path / mode,
            force=True,
        )
        search = hub_installer.search_remote_skills_with_status("demo")
        assert install.error == "SUPPLY_CHAIN_SIGNED_BUNDLE_REQUIRED"
        assert search.error == "SUPPLY_CHAIN_SIGNED_BUNDLE_REQUIRED"
        assert not (tmp_path / mode).exists()


def test_github_source_and_remove_reject_path_escape_without_mutation(tmp_path: Path) -> None:
    protected = tmp_path / "protected"
    protected.mkdir()
    marker = protected / "marker.txt"
    marker.write_text("keep", encoding="utf-8")

    assert hub_installer._parse_github_source("github:owner/repo/../repo-main/demo") is None
    assert hub_installer._parse_github_source("github:owner/repo/--force") is None
    assert hub_installer._parse_github_source(
        "https://github.com.evil.invalid/owner/repo/tree/main/demo-skill"
    ) is None
    assert hub_installer.remove_skill("..", skills_dir=protected).error == "SUPPLY_CHAIN_SKILL_NAME_INVALID"
    assert hub_installer.remove_skill(r"..\victim", skills_dir=protected).error == "SUPPLY_CHAIN_SKILL_NAME_INVALID"
    assert (
        hub_installer.remove_skill("demo-skill", skills_dir=protected).error
        == "SUPPLY_CHAIN_MANAGED_REMOVAL_REQUIRED"
    )
    assert marker.read_text(encoding="utf-8") == "keep"


def test_staged_signed_declarative_bundle_is_reported_but_never_registered(
    monkeypatch,
    tmp_path: Path,
) -> None:
    hub_dir = tmp_path / "skills"
    _write_signed_bundle(hub_dir / "demo-skill")
    monkeypatch.setenv("HOST_SUPPLY_CHAIN_ACTIVATION_MODE", "STAGED")
    monkeypatch.setenv(
        "HOST_SUPPLY_CHAIN_HMAC_KEYS_JSON",
        json.dumps({_KEY_ID: _trust()}),
    )
    registry = SkillRegistry()

    result = hub_loader.load_hub_skills(registry, skills_dir=hub_dir)

    assert result.registered_count == 0
    assert result.activation_eligible_count == 1
    assert result.skills[0].status == "STAGED"
    assert result.skills[0].activation_eligible is True
    assert registry.list_skill_names() == []


def test_staged_signed_python_extension_is_rejected_without_import(
    monkeypatch,
    tmp_path: Path,
) -> None:
    hub_dir = tmp_path / "skills"
    _write_signed_bundle(hub_dir / "demo-skill", python_extension=True)
    monkeypatch.setenv("HOST_SUPPLY_CHAIN_ACTIVATION_MODE", "STAGED")
    monkeypatch.setenv(
        "HOST_SUPPLY_CHAIN_HMAC_KEYS_JSON",
        json.dumps({_KEY_ID: _trust()}),
    )

    result = hub_loader.load_hub_skills(SkillRegistry(), skills_dir=hub_dir)

    assert result.registered_count == 0
    assert result.activation_eligible_count == 0
    assert result.skills[0].status == "REJECTED"
    assert result.skills[0].reason_code == "SUPPLY_CHAIN_PYTHON_EXTENSION_FORBIDDEN"


def test_quarantine_and_invalid_modes_return_before_scan(monkeypatch, tmp_path: Path) -> None:
    for raw, expected_reason in (
        ("QUARANTINE", "SUPPLY_CHAIN_QUARANTINE_NO_ACTIVE_SCAN"),
        ("invalid", "SUPPLY_CHAIN_ACTIVATION_MODE_INVALID"),
    ):
        monkeypatch.setenv("HOST_SUPPLY_CHAIN_ACTIVATION_MODE", raw)
        monkeypatch.setattr(
            hub_loader,
            "_scan_single_dir",
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not scan")),
        )
        result = hub_loader.load_hub_skills(SkillRegistry(), skills_dir=tmp_path / raw)
        assert result.total_dirs_scanned == 0
        assert result.registered_count == 0
        assert result.reason_code == expected_reason


def test_staged_scan_rejects_windows_reparse_dir_before_following_children(
    monkeypatch,
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "junction-skill"
    candidate.mkdir()
    original_lstat = Path.lstat
    original_is_dir = Path.is_dir
    reparse_flag = 0x400

    def _lstat(path: Path):
        if path == candidate:
            return SimpleNamespace(
                st_mode=stat_module.S_IFDIR,
                st_file_attributes=reparse_flag,
            )
        return original_lstat(path)

    def _is_dir(path: Path) -> bool:
        if path == candidate:
            raise AssertionError("reparse candidate must not be followed")
        return original_is_dir(path)

    monkeypatch.setattr(Path, "lstat", _lstat)
    monkeypatch.setattr(Path, "is_dir", _is_dir)
    monkeypatch.setattr(hub_loader.stat, "FILE_ATTRIBUTE_REPARSE_POINT", reparse_flag, raising=False)
    result = hub_loader.HubLoadResult(
        skills_dir=str(tmp_path),
        total_dirs_scanned=0,
        registered_count=0,
        skipped_count=0,
        error_count=0,
    )

    hub_loader._scan_single_dir(
        scan_dir=tmp_path,
        registry=SkillRegistry(),
        executor_registry=None,
        result=result,
        verifier=object(),  # type: ignore[arg-type]
    )

    assert result.total_dirs_scanned == 1
    assert result.error_count == 1
    assert result.skills[0].reason_code == "SUPPLY_CHAIN_SYMLINK_FORBIDDEN"
