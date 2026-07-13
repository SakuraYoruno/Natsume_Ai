from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.skills import custom_provider_installer
from host_brain.skills.hub import hub_installer, hub_loader
from host_brain.skills.registry import SkillRegistry
from host_brain.skills.supply_chain import (
    MANIFEST_SCHEMA_VERSION,
    SkillSupplyChainVerifier,
    SupplyChainActivationMode,
    canonical_json_bytes,
    compute_inventory_digest,
)
from host_brain.skills.supply_chain_quarantine import SkillBundleQuarantine
from host_brain.skills.supply_chain_service import SkillSupplyChainService
from host_brain.persistence.supply_chain_repository import SQLiteSkillSupplyChainStore


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"
KEY_ID = "task0490.integration.signing.v1"
SECRET = "task0490-integration-dedicated-secret-32-bytes"
PUBLISHER_ID = "publisher://task0490/integration"


def _trust() -> dict[str, object]:
    return {
        "secret": SECRET,
        "publisher_id": PUBLISHER_ID,
        "allowed_bundle_kinds": ["PROVIDER"],
        "allowed_bundle_names": ["task0490-safe-bundle"],
    }


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _write_signed_bundle(bundle: Path, *, marker: Path) -> Path:
    bundle.mkdir(parents=True)
    skill_md = (
        "---\n"
        "name: task0490-safe-bundle\n"
        "version: 1.0.0\n"
        "description: isolated supply-chain integration\n"
        "clearance: LOW\n"
        "risk_level: 1\n"
        "scope: host\n"
        "tool: task0490_test_tool\n"
        "---\n"
    ).encode("utf-8")
    # If either the verifier, quarantine or Hub boundary imports this file,
    # the marker exposes the security regression without external effects.
    provider = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
        "class UnsafeProvider: pass\n"
        "SKILL_PROVIDER_CLASS = UnsafeProvider\n"
    ).encode("utf-8")
    sbom = canonical_json_bytes(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "components": [],
        }
    )
    payloads = {
        "SKILL.md": skill_md,
        "provider.py": provider,
        "sbom.json": sbom,
    }
    for relative, raw in payloads.items():
        (bundle / relative).write_bytes(raw)
    files = [
        {"path": relative, "size": len(raw), "digest": _digest(raw)}
        for relative, raw in sorted(payloads.items())
    ]
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "name": "task0490-safe-bundle",
        "version": "1.0.0",
        "bundle_kind": "PROVIDER",
        "publisher_id": PUBLISHER_ID,
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
        "signing": {"algorithm": "hmac-sha256", "key_id": KEY_ID},
    }
    manifest_bytes = canonical_json_bytes(manifest)
    (bundle / "bundle-manifest.json").write_bytes(manifest_bytes)
    signature = hmac.new(
        SECRET.encode("utf-8"),
        manifest_bytes,
        hashlib.sha256,
    ).hexdigest()
    (bundle / "bundle-manifest.sig").write_text(
        f"hmac-sha256:{signature}",
        encoding="ascii",
    )
    return bundle


def test_task0490_signed_bundle_quarantine_store_and_zero_import_wave(
    tmp_path: Path,
    monkeypatch,
) -> None:
    marker = tmp_path / "provider-imported.txt"
    source = _write_signed_bundle(tmp_path / "source", marker=marker)
    db_path = tmp_path / "central.sqlite3"
    migration = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=MIGRATIONS,
    ).apply_migrations(request_id="task0490-integration", ts_ms=2_000_000_000_000)
    assert migration.success, migration.as_dict()
    assert migration.summary["current_version"] >= 60

    verifier = SkillSupplyChainVerifier(
        mode=SupplyChainActivationMode.STAGED,
        trusted_keys={KEY_ID: _trust()},
    )
    quarantine = SkillBundleQuarantine(
        verifier=verifier,
        quarantine_root=tmp_path / "quarantine",
    )
    store = SQLiteSkillSupplyChainStore(db_path)
    service = SkillSupplyChainService(
        verifier=verifier,
        quarantine=quarantine,
        store=store,
    )

    first = service.intake_signed_bundle(
        source,
        kind="PROVIDER",
        publisher=PUBLISHER_ID,
        idempotency="task0490.integration.intake.1",
    )
    duplicate = service.intake_signed_bundle(
        source,
        kind="PROVIDER",
        publisher=PUBLISHER_ID,
        idempotency="task0490.integration.intake.1",
    )

    assert first.reason_code == duplicate.reason_code == "SUPPLY_CHAIN_INTAKE_STAGED"
    assert first.stored is first.quarantined is first.staged is True
    assert first.activation_performed is first.import_performed is False
    assert duplicate.intake_acceptance_status == "DUPLICATE_SAME"
    assert duplicate.stage_acceptance_status == "DUPLICATE_SAME"
    assert first.bundle_id == duplicate.bundle_id
    assert not marker.exists()
    assert not any(name.startswith("_natsume_hub_skill_") for name in sys.modules)
    assert store.get_active_bundle(
        bundle_kind="PROVIDER",
        bundle_name="task0490-safe-bundle",
    ) is None
    persisted = store.get_bundle(bundle_id=str(first.bundle_id))
    assert persisted is not None and persisted["state"] == "STAGED"
    assert [
        event["operation"]
        for event in store.list_audit_operations(bundle_id=str(first.bundle_id))
    ] == ["INTAKE", "STAGE"]

    # A post-signature byte change is rejected before quarantine/store and can
    # neither overwrite the content-addressed copy nor create another record.
    (source / "provider.py").write_bytes(
        (source / "provider.py").read_bytes() + b"# tampered\n"
    )
    tampered = service.intake_signed_bundle(
        source,
        kind="PROVIDER",
        publisher=PUBLISHER_ID,
        idempotency="task0490.integration.intake.tampered",
    )
    assert tampered.reason_code == "SUPPLY_CHAIN_FILE_DIGEST_MISMATCH"
    assert tampered.stored is tampered.quarantined is False
    assert not marker.exists()
    assert len(store.list_bundles()) == 1

    # Existing HTTP/skill facades cannot bypass signed quarantine: their old
    # source-only contract is rejected before filesystem/network/subprocess.
    monkeypatch.setenv("HOST_SUPPLY_CHAIN_ACTIVATION_MODE", "STAGED")
    monkeypatch.setenv(
        "HOST_SUPPLY_CHAIN_HMAC_KEYS_JSON",
        json.dumps({KEY_ID: _trust()}),
    )
    legacy_install = hub_installer.install_skill(
        str(source),
        skills_dir=tmp_path / "legacy-live-dir",
        force=True,
    )
    assert legacy_install.success is False
    assert legacy_install.error == "SUPPLY_CHAIN_SIGNED_BUNDLE_REQUIRED"
    assert not (tmp_path / "legacy-live-dir").exists()
    try:
        custom_provider_installer.write_provider_source(
            "legacy",
            "class Legacy: pass\nSKILL_PROVIDER_CLASS = Legacy",
        )
    except ValueError as exc:
        assert str(exc) == "SKILL_SUPPLY_CHAIN_LEGACY_SOURCE_REJECTED"
    else:
        raise AssertionError("legacy custom provider source was accepted")

    # Scanning the physical quarantine may report the signed bundle, but the
    # Python extension is rejected and the active registry remains untouched.
    registry = SkillRegistry()
    staged_scan = hub_loader.load_hub_skills(
        registry,
        skills_dir=tmp_path / "quarantine",
    )
    assert staged_scan.registered_count == 0
    assert staged_scan.activation_eligible_count == 0
    assert any(
        item.reason_code == "SUPPLY_CHAIN_PYTHON_EXTENSION_FORBIDDEN"
        for item in staged_scan.skills
    )
    assert registry.list_skill_names() == []
    assert not marker.exists()
