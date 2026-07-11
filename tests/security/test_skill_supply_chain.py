from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

import pytest

from host_brain.skills.supply_chain import (
    ACTIVATION_MODE_ENV,
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    SIGNATURE_FILENAME,
    SIGNING_KEYS_ENV,
    BundleDisposition,
    SkillSupplyChainVerifier,
    SupplyChainActivationMode,
    SupplyChainConfigurationError,
    canonical_json_bytes,
    compute_inventory_digest,
    load_trusted_signing_keys,
    resolve_activation_mode,
)


KEY_ID = "release-2026-07"
SECRET = b"skill-supply-chain-test-secret-32-bytes-minimum"
PUBLISHER_ID = "publisher://trusted/external-echo"


def _trust(
    *,
    secret: bytes | str = SECRET,
    publisher_id: str = PUBLISHER_ID,
    kinds: tuple[str, ...] = ("SKILL",),
    names: tuple[str, ...] = ("external-echo",),
) -> dict[str, object]:
    return {
        "secret": secret,
        "publisher_id": publisher_id,
        "allowed_bundle_kinds": list(kinds),
        "allowed_bundle_names": list(names),
    }


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _write_payload(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("# external\n", encoding="utf-8")
    (root / "provider.py").write_text(
        "raise RuntimeError('bundle code must never execute during verification')\n",
        encoding="utf-8",
    )
    (root / "metadata").mkdir()
    (root / "metadata" / "config.json").write_text(
        json.dumps({"capability": "echo"}, separators=(",", ":")),
        encoding="utf-8",
    )
    (root / "sbom.cdx.json").write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.5",
                "components": [{"type": "library", "name": "example", "version": "1.0.0"}],
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def _inventory(root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name in {MANIFEST_FILENAME, SIGNATURE_FILENAME}:
            continue
        raw = path.read_bytes()
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": len(raw),
                "digest": _digest(raw),
            }
        )
    return sorted(entries, key=lambda item: str(item["path"]).encode("utf-8"))


def _resign(root: Path, manifest: dict[str, object], *, secret: bytes = SECRET) -> None:
    (root / MANIFEST_FILENAME).write_bytes(canonical_json_bytes(manifest))
    signature = hmac.new(secret, canonical_json_bytes(manifest), hashlib.sha256).hexdigest()
    (root / SIGNATURE_FILENAME).write_text(f"hmac-sha256:{signature}", encoding="ascii")


def _seal(
    root: Path,
    *,
    sandbox: dict[str, object] | None = None,
    secret: bytes = SECRET,
) -> dict[str, object]:
    files = _inventory(root)
    sbom_entry = next(item for item in files if item["path"] == "sbom.cdx.json")
    manifest: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "name": "external-echo",
        "version": "1.0.0",
        "bundle_kind": "SKILL",
        "publisher_id": PUBLISHER_ID,
        "bundle_digest": compute_inventory_digest(files),
        "files": files,
        "sbom": {
            "format": "cyclonedx-json",
            "path": "sbom.cdx.json",
            "digest": sbom_entry["digest"],
        },
        "sandbox": sandbox
        or {
            "profile": "python-restricted-v1",
            "secrets": [],
            "network": ["https://api.example.com"],
            "process": "deny",
            "workspace": "read_only",
        },
        "signing": {"algorithm": "hmac-sha256", "key_id": KEY_ID},
    }
    _resign(root, manifest, secret=secret)
    return manifest


def _bundle(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    root = tmp_path / "bundle"
    _write_payload(root)
    return root, _seal(root)


def _verifier(mode: SupplyChainActivationMode | str) -> SkillSupplyChainVerifier:
    return SkillSupplyChainVerifier(mode=mode, trusted_keys={KEY_ID: _trust()})


def test_activation_mode_defaults_and_invalid_values_fail_to_builtins_only() -> None:
    missing = resolve_activation_mode(environ={})
    assert missing.mode is SupplyChainActivationMode.BUILTINS_ONLY
    assert missing.valid is True
    assert missing.explicitly_configured is False

    invalid = resolve_activation_mode(environ={ACTIVATION_MODE_ENV: "enabled"})
    assert invalid.mode is SupplyChainActivationMode.BUILTINS_ONLY
    assert invalid.valid is False
    assert invalid.explicitly_configured is True

    assert resolve_activation_mode(" quarantine ").mode is SupplyChainActivationMode.QUARANTINE
    assert resolve_activation_mode("STAGED").mode is SupplyChainActivationMode.STAGED


def test_builtins_only_rejects_external_bundle_before_reading_path(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    decision = SkillSupplyChainVerifier(
        mode=SupplyChainActivationMode.BUILTINS_ONLY,
        trusted_keys={},
    ).verify_bundle(missing)
    assert decision.allowed is False
    assert decision.verified is False
    assert decision.must_quarantine is False
    assert decision.activation_eligible is False
    assert decision.reason_code == "SUPPLY_CHAIN_EXTERNAL_BUNDLES_DISABLED"


def test_invalid_mode_is_terminal_even_though_effective_mode_is_builtins(tmp_path: Path) -> None:
    decision = SkillSupplyChainVerifier(mode="future-mode", trusted_keys={KEY_ID: _trust()}).verify_bundle(
        tmp_path / "missing"
    )
    assert decision.reason_code == "SUPPLY_CHAIN_ACTIVATION_MODE_INVALID"
    assert decision.allowed is False


def test_quarantine_verifies_but_never_grants_activation(tmp_path: Path) -> None:
    root, manifest = _bundle(tmp_path)
    decision = _verifier(SupplyChainActivationMode.QUARANTINE).verify_bundle(root)

    assert decision.disposition is BundleDisposition.QUARANTINE_VERIFIED
    assert decision.reason_code == "SUPPLY_CHAIN_QUARANTINE_VERIFIED"
    assert decision.verified is True
    assert decision.allowed is False
    assert decision.must_quarantine is True
    assert decision.activation_eligible is False
    assert decision.bundle_name == "external-echo"
    assert decision.bundle_digest == manifest["bundle_digest"]


def test_staged_returns_only_activation_eligibility_and_never_executes_provider(tmp_path: Path) -> None:
    root, _ = _bundle(tmp_path)
    marker = root / "verification-executed-code"
    (root / "provider.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    _seal(root)

    decision = _verifier(SupplyChainActivationMode.STAGED).verify_bundle(root)
    assert decision.disposition is BundleDisposition.STAGED_ELIGIBLE
    assert decision.verified is True
    assert decision.allowed is True
    assert decision.activation_eligible is True
    assert decision.must_quarantine is True
    assert decision.audit["bundle_kind"] == "SKILL"
    assert decision.audit["publisher_id"] == PUBLISHER_ID
    assert marker.exists() is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bundle_kind", "PROVIDER"),
        ("publisher_id", "publisher://spoofed/admin"),
        ("name", "different-name"),
    ],
)
def test_signer_trust_statement_binds_kind_publisher_and_name(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    root, manifest = _bundle(tmp_path)
    manifest[field] = value
    _resign(root, manifest)

    decision = _verifier("STAGED").verify_bundle(root)

    assert decision.reason_code == "SUPPLY_CHAIN_SIGNER_SCOPE_FORBIDDEN"
    assert decision.verified is False


def test_success_audit_contains_only_safe_metadata_not_secret_or_file_content(tmp_path: Path) -> None:
    root, manifest = _bundle(tmp_path)
    decision = _verifier(SupplyChainActivationMode.STAGED).verify_bundle(root)
    encoded = json.dumps(dict(decision.audit), sort_keys=True)

    assert SECRET.decode("utf-8") not in encoded
    assert "raise RuntimeError" not in encoded
    assert str(root) not in encoded
    assert decision.audit["signing_key_id"] == KEY_ID
    assert decision.audit["signature_algorithm"] == "hmac-sha256"
    assert decision.audit["sbom_digest"] == manifest["sbom"]["digest"]
    assert decision.audit["sandbox_profile_id"] == "python-restricted-v1"
    assert decision.audit["sandbox_profile_digest"] == _digest(
        canonical_json_bytes(manifest["sandbox"])
    )
    assert decision.audit["file_count"] == 4
    assert decision.audit["sandbox"] == {
        "profile": "python-restricted-v1",
        "secret_ref_count": 0,
        "network_origin_count": 1,
        "process": "deny",
        "workspace": "read_only",
    }


def test_rejection_audit_never_echoes_unverified_cross_contract_metadata(tmp_path: Path) -> None:
    root, manifest = _bundle(tmp_path)
    manifest["signing"] = {"algorithm": "attacker-algorithm", "key_id": "attacker-key"}
    manifest["sbom"] = {
        "format": "cyclonedx-json",
        "path": "sbom.cdx.json",
        "digest": "sha256:" + "f" * 64,
    }
    manifest["sandbox"] = {
        "profile": "attacker-profile",
        "secrets": [],
        "network": [],
        "process": "deny",
        "workspace": "none",
    }
    _resign(root, manifest)

    decision = _verifier("STAGED").verify_bundle(root)
    assert decision.allowed is False
    assert {
        "signature_algorithm",
        "signing_key_id",
        "sbom_digest",
        "sandbox_profile_id",
        "sandbox_profile_digest",
        "sandbox",
    }.isdisjoint(decision.audit)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("schema_version", "v2", "SUPPLY_CHAIN_MANIFEST_VERSION_UNSUPPORTED"),
        ("name", "../../evil", "SUPPLY_CHAIN_BUNDLE_NAME_INVALID"),
        ("version", "", "SUPPLY_CHAIN_BUNDLE_VERSION_INVALID"),
        ("bundle_digest", "sha256:bad", "SUPPLY_CHAIN_BUNDLE_DIGEST_INVALID"),
    ],
)
def test_manifest_identity_and_digest_are_required(
    tmp_path: Path,
    field: str,
    value: object,
    reason: str,
) -> None:
    root, manifest = _bundle(tmp_path)
    manifest[field] = value
    _resign(root, manifest)
    assert _verifier("STAGED").verify_bundle(root).reason_code == reason


def test_unknown_manifest_fields_are_rejected_even_when_signed(tmp_path: Path) -> None:
    root, manifest = _bundle(tmp_path)
    manifest["load_module"] = "malicious.provider"
    _resign(root, manifest)
    assert _verifier("STAGED").verify_bundle(root).reason_code == "SUPPLY_CHAIN_MANIFEST_FIELDS_INVALID"


def test_signature_tamper_and_untrusted_key_fail_closed(tmp_path: Path) -> None:
    root, _ = _bundle(tmp_path)
    (root / SIGNATURE_FILENAME).write_text("hmac-sha256:" + "0" * 64, encoding="ascii")
    assert _verifier("STAGED").verify_bundle(root).reason_code == "SUPPLY_CHAIN_SIGNATURE_INVALID"

    root, manifest = _bundle(tmp_path / "second")
    manifest["signing"] = {"algorithm": "hmac-sha256", "key_id": "unknown-key"}
    _resign(root, manifest)
    assert _verifier("STAGED").verify_bundle(root).reason_code == "SUPPLY_CHAIN_SIGNING_KEY_UNTRUSTED"


@pytest.mark.parametrize(
    "signature",
    [
        "0" * 64,
        "sha256:" + "0" * 64,
        "hmac-sha256:" + "A" * 64,
        "hmac-sha256:" + "0" * 64 + "\n",
    ],
)
def test_signature_file_format_is_unambiguous(tmp_path: Path, signature: str) -> None:
    root, _ = _bundle(tmp_path)
    (root / SIGNATURE_FILENAME).write_text(signature, encoding="ascii")
    assert _verifier("STAGED").verify_bundle(root).reason_code == "SUPPLY_CHAIN_SIGNATURE_FORMAT_INVALID"


def test_any_payload_tamper_or_unlisted_file_breaks_full_inventory(tmp_path: Path) -> None:
    root, _ = _bundle(tmp_path)
    (root / "provider.py").write_text("# tampered", encoding="utf-8")
    assert _verifier("STAGED").verify_bundle(root).reason_code == "SUPPLY_CHAIN_FILE_DIGEST_MISMATCH"

    root, _ = _bundle(tmp_path / "second")
    (root / "unlisted.py").write_text("# extra", encoding="utf-8")
    assert _verifier("STAGED").verify_bundle(root).reason_code == "SUPPLY_CHAIN_FILE_INVENTORY_MISMATCH"


@pytest.mark.parametrize("bad_path", ["../escape.py", "/absolute.py", "C:/escape.py", "dir\\file.py"])
def test_manifest_file_paths_cannot_escape_or_use_ambiguous_separators(
    tmp_path: Path,
    bad_path: str,
) -> None:
    root, manifest = _bundle(tmp_path)
    files = list(manifest["files"])
    files[0] = {**files[0], "path": bad_path}
    manifest["files"] = files
    _resign(root, manifest)
    assert _verifier("STAGED").verify_bundle(root).activation_eligible is False


def test_duplicate_or_case_colliding_manifest_paths_fail_closed(tmp_path: Path) -> None:
    root, manifest = _bundle(tmp_path)
    files = list(manifest["files"])
    manifest["files"] = [files[0], {**files[0]}, *files[1:]]
    _resign(root, manifest)
    assert _verifier("STAGED").verify_bundle(root).reason_code == "SUPPLY_CHAIN_FILE_PATH_COLLISION"

    root, manifest = _bundle(tmp_path / "second")
    files = list(manifest["files"])
    manifest["files"] = [files[0], {**files[0], "path": str(files[0]["path"]).upper()}, *files[1:]]
    _resign(root, manifest)
    assert _verifier("STAGED").verify_bundle(root).reason_code == "SUPPLY_CHAIN_FILE_PATH_COLLISION"


def test_symlink_in_bundle_is_rejected(tmp_path: Path) -> None:
    root, _ = _bundle(tmp_path)
    link = root / "linked.py"
    try:
        link.symlink_to(root / "provider.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    assert _verifier("STAGED").verify_bundle(root).reason_code == "SUPPLY_CHAIN_SYMLINK_FORBIDDEN"


def test_duplicate_manifest_json_keys_and_nonfinite_numbers_fail_before_signature(tmp_path: Path) -> None:
    root, _ = _bundle(tmp_path)
    (root / MANIFEST_FILENAME).write_text('{"name":"one","name":"two"}', encoding="utf-8")
    decision = _verifier("STAGED").verify_bundle(root)
    assert decision.reason_code == "SUPPLY_CHAIN_JSON_INVALID"

    (root / MANIFEST_FILENAME).write_text('{"size":NaN}', encoding="utf-8")
    decision = _verifier("STAGED").verify_bundle(root)
    assert decision.reason_code == "SUPPLY_CHAIN_JSON_INVALID"


@pytest.mark.parametrize("payload", ['{"key":1,"KEY":2}', '{"value":Infinity}'])
def test_duplicate_or_nonfinite_json_anywhere_in_payload_fails_closed(
    tmp_path: Path,
    payload: str,
) -> None:
    root, _ = _bundle(tmp_path)
    (root / "metadata" / "config.json").write_text(payload, encoding="utf-8")
    _seal(root)
    assert _verifier("STAGED").verify_bundle(root).reason_code == "SUPPLY_CHAIN_JSON_INVALID"


def test_sbom_must_be_listed_digest_bound_and_structurally_valid(tmp_path: Path) -> None:
    root, manifest = _bundle(tmp_path)
    manifest["sbom"] = {"format": "cyclonedx-json", "path": "missing.json", "digest": "sha256:" + "0" * 64}
    _resign(root, manifest)
    assert _verifier("STAGED").verify_bundle(root).reason_code == "SUPPLY_CHAIN_SBOM_MISSING"

    root, manifest = _bundle(tmp_path / "second")
    manifest["sbom"] = {**manifest["sbom"], "digest": "sha256:" + "0" * 64}
    _resign(root, manifest)
    assert _verifier("STAGED").verify_bundle(root).reason_code == "SUPPLY_CHAIN_SBOM_DIGEST_MISMATCH"

    root, _ = _bundle(tmp_path / "third")
    (root / "sbom.cdx.json").write_text('{"bomFormat":"CycloneDX","components":{}}', encoding="utf-8")
    _seal(root)
    assert _verifier("STAGED").verify_bundle(root).reason_code == "SUPPLY_CHAIN_SBOM_INVALID"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("profile", "host", "SUPPLY_CHAIN_SANDBOX_PROFILE_UNAPPROVED"),
        ("secrets", ["all"], "SUPPLY_CHAIN_SECRET_SCOPE_INVALID"),
        ("secrets", ["All"], "SUPPLY_CHAIN_SECRET_SCOPE_INVALID"),
        ("secrets", ["HOST"], "SUPPLY_CHAIN_SECRET_SCOPE_INVALID"),
        ("secrets", ["INHERIT"], "SUPPLY_CHAIN_SECRET_SCOPE_INVALID"),
        ("secrets", "none", "SUPPLY_CHAIN_SECRET_SCOPE_INVALID"),
        ("network", ["http://api.example.com"], "SUPPLY_CHAIN_NETWORK_SCOPE_INVALID"),
        ("network", ["https://127.0.0.1"], "SUPPLY_CHAIN_NETWORK_SCOPE_INVALID"),
        ("network", ["https://*.example.com"], "SUPPLY_CHAIN_NETWORK_SCOPE_INVALID"),
        ("process", "inherit", "SUPPLY_CHAIN_PROCESS_SCOPE_INVALID"),
        ("workspace", "read_write", "SUPPLY_CHAIN_WORKSPACE_SCOPE_INVALID"),
    ],
)
def test_sandbox_scope_escapes_are_rejected(
    tmp_path: Path,
    field: str,
    value: object,
    reason: str,
) -> None:
    root = tmp_path / "bundle"
    _write_payload(root)
    sandbox: dict[str, object] = {
        "profile": "python-restricted-v1",
        "secrets": [],
        "network": [],
        "process": "deny",
        "workspace": "none",
    }
    sandbox[field] = value
    _seal(root, sandbox=sandbox)
    assert _verifier("STAGED").verify_bundle(root).reason_code == reason


def test_missing_sandbox_scope_field_fails_closed(tmp_path: Path) -> None:
    root, manifest = _bundle(tmp_path)
    del manifest["sandbox"]["workspace"]
    _resign(root, manifest)
    assert _verifier("STAGED").verify_bundle(root).reason_code == "SUPPLY_CHAIN_SANDBOX_INVALID"


def test_dedicated_key_loader_has_no_fallback_and_rejects_weak_ambiguous_config() -> None:
    unrelated = {"HOST_SECURE_INGRESS_HMAC_SECRETS_JSON": json.dumps({KEY_ID: _trust(secret=SECRET.decode())})}
    assert dict(load_trusted_signing_keys(environ=unrelated)) == {}

    loaded = load_trusted_signing_keys(
        environ={SIGNING_KEYS_ENV: json.dumps({KEY_ID: _trust(secret=SECRET.decode())})}
    )
    assert loaded[KEY_ID].secret == SECRET
    assert loaded[KEY_ID].publisher_id == PUBLISHER_ID

    invalid_values = [
        json.dumps({KEY_ID: _trust(secret="short")}),
        json.dumps(
            {
                KEY_ID: _trust(secret=SECRET.decode()),
                "another": _trust(secret=SECRET.decode()),
            }
        ),
        '{"same":"' + SECRET.decode() + '","same":"another-secret-that-is-long-enough-000"}',
        '{"key":NaN}',
    ]
    for raw in invalid_values:
        with pytest.raises(SupplyChainConfigurationError):
            load_trusted_signing_keys(raw)


def test_environment_factory_requires_dedicated_keys_for_nonbuiltin_modes(tmp_path: Path) -> None:
    root, _ = _bundle(tmp_path)
    verifier = SkillSupplyChainVerifier.from_environment(
        environ={ACTIVATION_MODE_ENV: "STAGED"}
    )
    assert verifier.verify_bundle(root).reason_code == "SUPPLY_CHAIN_SIGNING_KEYS_UNAVAILABLE"


def test_inventory_digest_is_order_independent_but_changes_with_metadata() -> None:
    files = [
        {"path": "b.py", "size": 1, "digest": "sha256:" + "b" * 64},
        {"path": "a.py", "size": 2, "digest": "sha256:" + "a" * 64},
    ]
    assert compute_inventory_digest(files) == compute_inventory_digest(list(reversed(files)))
    changed = [dict(item) for item in files]
    changed[0]["size"] = 3
    assert compute_inventory_digest(files) != compute_inventory_digest(changed)


def test_file_count_and_size_limits_fail_closed(tmp_path: Path) -> None:
    root, _ = _bundle(tmp_path)
    decision = SkillSupplyChainVerifier(
        mode="STAGED",
        trusted_keys={KEY_ID: _trust()},
        max_files=2,
    ).verify_bundle(root)
    assert decision.reason_code == "SUPPLY_CHAIN_FILE_LIMIT_EXCEEDED"

    decision = SkillSupplyChainVerifier(
        mode="STAGED",
        trusted_keys={KEY_ID: _trust()},
        max_total_bytes=1,
    ).verify_bundle(root)
    assert decision.reason_code == "SUPPLY_CHAIN_TOTAL_SIZE_LIMIT_EXCEEDED"


def test_empty_directory_fanout_is_bounded(tmp_path: Path) -> None:
    root, _ = _bundle(tmp_path)
    (root / "empty-a").mkdir()
    (root / "empty-b").mkdir()

    decision = SkillSupplyChainVerifier(
        mode="STAGED",
        trusted_keys={KEY_ID: _trust()},
        max_directories=1,
    ).verify_bundle(root)

    assert decision.reason_code == "SUPPLY_CHAIN_DIRECTORY_LIMIT_EXCEEDED"


def test_deep_manifest_json_is_a_stable_rejection(tmp_path: Path) -> None:
    root = tmp_path / "deep-manifest"
    root.mkdir()
    (root / MANIFEST_FILENAME).write_text("[" * 2000 + "0" + "]" * 2000, encoding="utf-8")
    (root / SIGNATURE_FILENAME).write_text("hmac-sha256:" + "0" * 64, encoding="ascii")

    decision = _verifier("STAGED").verify_bundle(root)

    assert decision.reason_code == "SUPPLY_CHAIN_JSON_DEPTH_EXCEEDED"
    assert decision.verified is False


def test_deep_payload_json_is_rejected_before_eligibility(tmp_path: Path) -> None:
    root = tmp_path / "deep-payload"
    _write_payload(root)
    (root / "metadata" / "deep.json").write_text(
        "[" * 80 + "0" + "]" * 80,
        encoding="utf-8",
    )
    _seal(root)

    decision = _verifier("STAGED").verify_bundle(root)

    assert decision.reason_code == "SUPPLY_CHAIN_JSON_DEPTH_EXCEEDED"
    assert decision.activation_eligible is False


def test_verifier_source_has_no_dynamic_import_or_execution_primitive() -> None:
    source = Path("host_brain/skills/supply_chain.py").read_text(encoding="utf-8")
    assert "importlib" not in source
    assert "exec(" not in source
    assert "eval(" not in source
    assert "sys.path" not in source
    assert "subprocess" not in source
    assert "provider.py" not in source
