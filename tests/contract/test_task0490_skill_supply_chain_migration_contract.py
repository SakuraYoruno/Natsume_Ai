from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import sqlite3

from host_brain.audit.migration_guard import STATUS_READY, SQLiteMigrationVersionGuard
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.skills.supply_chain_store import (
    SKILL_SUPPLY_CHAIN_MIGRATION_ASSET_KEY,
    SKILL_SUPPLY_CHAIN_MIGRATION_SHA256,
)


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "host_brain/audit/migrations"
MIGRATION = CATALOG / "0060_skill_supply_chain.sql"


def test_0060_is_a_contiguous_checksum_bound_expand_only_catalog_asset() -> None:
    assert hashlib.sha256(MIGRATION.read_bytes()).hexdigest() == (
        SKILL_SUPPLY_CHAIN_MIGRATION_SHA256
    )
    report = SQLiteMigrationVersionGuard(migrations_dir=CATALOG).build_guard_report(
        request_id="task0490-catalog", ts_ms=1
    )
    assert report.status == STATUS_READY, report.as_dict()
    assert report.latest_version >= 60
    assets, _duplicates = SQLiteMigrationVersionGuard(
        migrations_dir=CATALOG
    ).discover_assets()
    matching = [asset for asset in assets if asset.path.name == MIGRATION.name]
    assert len(matching) == 1
    assert matching[0].migration_id == 60
    assert matching[0].checksum == SKILL_SUPPLY_CHAIN_MIGRATION_SHA256


def test_existing_0059_database_upgrades_through_0060_without_mutating_history(
    tmp_path: Path,
) -> None:
    pre_0060 = tmp_path / "pre-0060"
    shutil.copytree(CATALOG, pre_0060)
    for asset in pre_0060.glob("*.sql"):
        if int(asset.name[:4]) >= 60:
            asset.unlink()

    db_path = tmp_path / "central.sqlite3"
    before = SQLiteMigrationRunner(
        db_path=db_path, migrations_dir=pre_0060
    ).apply_migrations(request_id="task0490-before", ts_ms=10)
    assert before.success, before.as_dict()
    assert before.summary["current_version"] == 59
    history_before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in pre_0060.glob("*.sql")
    }

    after = SQLiteMigrationRunner(
        db_path=db_path, migrations_dir=CATALOG
    ).apply_migrations(request_id="task0490-after", ts_ms=11)
    assert after.success, after.as_dict()
    assert after.summary["current_version"] >= 60
    assert history_before == {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in CATALOG.glob("*.sql")
        if int(path.name[:4]) < 60
    }

    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            """
            SELECT checksum, disposition FROM schema_migration_assets
            WHERE asset_key = ?
            """,
            (SKILL_SUPPLY_CHAIN_MIGRATION_ASSET_KEY,),
        ).fetchone() == (SKILL_SUPPLY_CHAIN_MIGRATION_SHA256, "APPLIED")
        assert conn.execute(
            "SELECT COUNT(*) FROM skill_supply_chain_bundle"
        ).fetchone() == (0,)
        state_default = {
            str(row[1]): row[4]
            for row in conn.execute("PRAGMA table_info(skill_supply_chain_bundle)")
        }
        assert str(state_default["state"]).strip("'") == "QUARANTINED"


def test_database_constraints_forbid_direct_active_or_private_material_columns(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "0060-only.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(MIGRATION.read_text(encoding="utf-8"))
        columns = {
            str(row[1]).lower()
            for table in ("skill_supply_chain_bundle", "skill_supply_chain_operation")
            for row in conn.execute(f"PRAGMA table_info({table})")
        }
        assert not any(
            forbidden in column
            for column in columns
            for forbidden in (
                "raw_bundle",
                "signature_value",
                "signature_material",
                "private_key",
                "secret",
            )
        )
        try:
            conn.execute(
                """
                INSERT INTO skill_supply_chain_bundle(
                    bundle_id, bundle_kind, bundle_name, bundle_version,
                    publisher_id, bundle_digest, signature_verified,
                    quarantine_audit_ref, state, reason_code, latest_audit_ref,
                    intake_idempotency_digest, activation_generation,
                    activation_fencing_token, status_version, created_at_ms,
                    updated_at_ms
                ) VALUES (
                    'skill://unsafe/v1', 'SKILL', 'unsafe', 'v1', 'publisher://x',
                    ?, 0, 'audit://quarantine/x', 'ACTIVE', 'DIRECT_ACTIVE',
                    'audit://direct-active/x', ?, 0, 0, 0, 1, 1
                )
                """,
                ("sha256:" + "a" * 64, "sha256:" + "b" * 64),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("0060 allowed a bundle to bypass quarantine/staging")
