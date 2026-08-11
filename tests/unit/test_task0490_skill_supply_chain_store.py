from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
import sqlite3

import pytest

from host_brain.persistence.supply_chain_repository import (
    BundleKind,
    SKILL_SUPPLY_CHAIN_MIGRATION_SHA256,
    SQLiteSkillSupplyChainStore,
    SkillSupplyChainStoreError,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT / "host_brain/audit/migrations/0060_skill_supply_chain.sql"
)


class FrozenClock:
    def __init__(self, value: int = 1_950_000_000_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


def _sha(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _migrated_db(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "skill-supply-chain.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(MIGRATION.read_text(encoding="utf-8"))
        conn.executescript(
            """
            CREATE TABLE schema_migration_assets (
                asset_key TEXT PRIMARY KEY,
                migration_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                disposition TEXT NOT NULL,
                applied_ts_ms INTEGER NOT NULL,
                UNIQUE(migration_id, name)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO schema_migration_assets(
                asset_key, migration_id, name, checksum,
                disposition, applied_ts_ms
            ) VALUES (
                '0060#skill_supply_chain', 60, 'skill_supply_chain',
                ?, 'APPLIED', ?
            )
            """,
            (SKILL_SUPPLY_CHAIN_MIGRATION_SHA256, 1_950_000_000_000),
        )
    return db_path


def _intake(store: SQLiteSkillSupplyChainStore) -> dict:
    return store.intake_bundle(
        bundle_id="skill://safe-skill/v1",
        bundle_kind=BundleKind.SKILL,
        bundle_name="safe-skill",
        bundle_version="v1",
        publisher_id="publisher://trusted/test",
        bundle_digest=_sha("bundle-safe-skill-v1"),
        quarantine_audit_ref="audit://quarantine/safe-skill/v1",
        idempotency_key="intake-safe-skill-v1",
    )


def _stage(store: SQLiteSkillSupplyChainStore) -> dict:
    return store.stage_bundle(
        bundle_id="skill://safe-skill/v1",
        expected_status_version=0,
        signature_key_id="signing-key://release/test-1",
        signature_algorithm="hmac-sha256",
        signature_verification_audit_ref=(
            "audit://signature/safe-skill/v1"
        ),
        sbom_digest=_sha("sbom-safe-skill-v1"),
        sandbox_profile_id="sandbox-profile://python/isolated-v1",
        sandbox_profile_digest=_sha("sandbox-profile-isolated-v1"),
        audit_ref="audit://stage/safe-skill/v1",
        idempotency_key="stage-safe-skill-v1",
    )


def test_schema_is_checksum_bound_and_private_material_free(
    tmp_path: Path,
) -> None:
    source = (
        ROOT / "host_brain/persistence/supply_chain_repository.py"
    ).read_text(encoding="utf-8").upper()
    assert "CREATE TABLE" not in source
    assert hashlib.sha256(MIGRATION.read_bytes()).hexdigest() == (
        SKILL_SUPPLY_CHAIN_MIGRATION_SHA256
    )

    db_path = _migrated_db(tmp_path)
    SQLiteSkillSupplyChainStore(db_path).assert_schema_ready()
    with sqlite3.connect(str(db_path)) as conn:
        columns = {
            str(row[1]).lower()
            for table in (
                "skill_supply_chain_bundle",
                "skill_supply_chain_operation",
            )
            for row in conn.execute(f"PRAGMA table_info({table})")
        }
    assert not any(
        forbidden in column
        for column in columns
        for forbidden in (
            "raw_bundle",
            "bundle_body",
            "signature_value",
            "private_key",
            "secret",
            "credential",
        )
    )
    assert "raw_bundle" not in str(
        inspect.signature(SQLiteSkillSupplyChainStore.intake_bundle)
    )


def test_intake_and_stage_are_strictly_idempotent(
    tmp_path: Path,
) -> None:
    store = SQLiteSkillSupplyChainStore(_migrated_db(tmp_path))
    first = _intake(store)
    duplicate = _intake(store)
    assert first["bundle"]["state"] == "QUARANTINED"
    assert duplicate["acceptance_status"] == "DUPLICATE_SAME"

    staged = _stage(store)
    staged_replay = _stage(store)
    assert staged["bundle"]["state"] == "STAGED"
    assert staged["bundle"]["signature_verified"] is True
    assert staged_replay["acceptance_status"] == "DUPLICATE_SAME"

    with pytest.raises(
        SkillSupplyChainStoreError,
        match="ALGORITHM_UNTRUSTED",
    ):
        store.stage_bundle(
            bundle_id="skill://safe-skill/v1",
            expected_status_version=1,
            signature_key_id="signing-key://release/test-1",
            signature_algorithm="MD5_WITH_RSA",
            signature_verification_audit_ref="audit://signature/bad",
            sbom_digest=_sha("sbom-bad"),
            sandbox_profile_id="sandbox-profile://isolated/v1",
            sandbox_profile_digest=_sha("sandbox-bad"),
            audit_ref="audit://stage/bad",
            idempotency_key="stage-bad",
        )


def test_supply_store_cannot_activate_or_reject_without_v2_authority(
    tmp_path: Path,
) -> None:
    store = SQLiteSkillSupplyChainStore(_migrated_db(tmp_path))
    _intake(store)
    _stage(store)
    assert not hasattr(store, "activate_bundle")
    assert not hasattr(store, "reject_bundle")
    assert store.get_active_bundle(
        bundle_kind="SKILL",
        bundle_name="safe-skill",
    ) is None


def test_busy_database_returns_stable_timeout_reason(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    store = SQLiteSkillSupplyChainStore(db_path, timeout_ms=10)
    blocker = sqlite3.connect(str(db_path), isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(SkillSupplyChainStoreError, match="STORE_BUSY"):
            _intake(store)
    finally:
        blocker.rollback()
        blocker.close()
