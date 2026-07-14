from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from host_brain.audit.migration_guard import (
    STATUS_FAILED as GUARD_FAILED,
    STATUS_READY,
    SQLiteMigrationVersionGuard,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.core.errors import MIGRATION_EXEC_FAILED, MIGRATION_SCHEMA_DRIFT


ROOT = Path(__file__).resolve().parents[2]
CENTRAL_CATALOG = ROOT / "host_brain" / "audit" / "migrations"
CENTRAL_ASSETS = tuple(sorted(CENTRAL_CATALOG.glob("*.sql")))
CENTRAL_VERSIONS = tuple(int(asset.name.split("_", 1)[0]) for asset in CENTRAL_ASSETS)
EXPECTED_LATEST_VERSION = max(CENTRAL_VERSIONS)
EXPECTED_ASSET_COUNT = len(CENTRAL_ASSETS)
EXPECTED_UNIQUE_VERSION_COUNT = len(set(CENTRAL_VERSIONS))


def _copy_catalog(tmp_path: Path) -> Path:
    target = tmp_path / "migrations"
    shutil.copytree(CENTRAL_CATALOG, target)
    return target


def test_central_catalog_guard_accepts_only_frozen_duplicate_and_gap() -> None:
    report = SQLiteMigrationVersionGuard(
        migrations_dir=CENTRAL_CATALOG
    ).build_guard_report(request_id="task0487-central-guard", ts_ms=1)

    assert report.status == STATUS_READY, report.as_dict()
    assert report.reason_code is None
    assert report.asset_count == EXPECTED_ASSET_COUNT
    assert report.latest_version == EXPECTED_LATEST_VERSION
    assert report.duplicate_versions == []
    assert report.missing_versions == []


def test_empty_database_replays_all_physical_assets_and_second_run_skips(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "audit.sqlite3"
    runner = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=CENTRAL_CATALOG,
    )

    first = runner.apply_migrations(request_id="task0487-empty-1", ts_ms=10)
    assert first.success, first.as_dict()
    assert first.summary == {
        "scope": "sqlite",
        "current_version": EXPECTED_LATEST_VERSION,
        "latest_version": EXPECTED_LATEST_VERSION,
        "applied_count": EXPECTED_ASSET_COUNT,
        "skipped_count": 0,
        "failed_count": 0,
    }

    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone() == (
            EXPECTED_UNIQUE_VERSION_COUNT,
        )
        assert conn.execute("SELECT COUNT(*) FROM schema_migration_assets").fetchone() == (
            EXPECTED_ASSET_COUNT,
        )
        assert conn.execute(
            "SELECT name FROM schema_migrations WHERE migration_id = 9"
        ).fetchone() == ("agent_loop_log",)
        assert conn.execute(
            """
            SELECT asset_key, disposition
            FROM schema_migration_assets
            WHERE migration_id = 9
            ORDER BY asset_key
            """
        ).fetchall() == [
            ("0009#agent_loop_log", "APPLIED"),
            ("0009#service_health_schema", "APPLIED"),
        ]
        assert conn.execute(
            """
            SELECT disposition
            FROM schema_migration_assets
            WHERE asset_key = '0021#episodic_fts5'
            """
        ).fetchone() == ("APPLIED_WITH_COMPAT",)
        table_names = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table'
                  AND name IN ('agent_loop_log', 'service_health_log', 'episodic_fts')
                """
            ).fetchall()
        }
        assert table_names == {"agent_loop_log", "service_health_log", "episodic_fts"}
    finally:
        conn.close()

    second = runner.apply_migrations(request_id="task0487-empty-2", ts_ms=11)
    assert second.success, second.as_dict()
    assert second.summary["current_version"] == EXPECTED_LATEST_VERSION
    assert second.summary["latest_version"] == EXPECTED_LATEST_VERSION
    assert second.summary["applied_count"] == 0
    assert second.summary["skipped_count"] == EXPECTED_ASSET_COUNT
    assert second.summary["failed_count"] == 0


def test_existing_0058_database_upgrades_in_place_through_latest(tmp_path: Path) -> None:
    pre_0059_catalog = _copy_catalog(tmp_path)
    for asset in pre_0059_catalog.glob("*.sql"):
        if int(asset.name.split("_", 1)[0]) >= 59:
            asset.unlink()
    db_path = tmp_path / "upgrade-from-0058.sqlite3"

    before = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=pre_0059_catalog,
    ).apply_migrations(request_id="task0488-upgrade-before", ts_ms=50)
    assert before.success, before.as_dict()
    assert before.summary["current_version"] == 58

    upgraded = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=CENTRAL_CATALOG,
    ).apply_migrations(request_id="task0488-upgrade-0059", ts_ms=51)
    assert upgraded.success, upgraded.as_dict()
    assert upgraded.summary["current_version"] == EXPECTED_LATEST_VERSION
    assert upgraded.summary["applied_count"] == EXPECTED_LATEST_VERSION - 58
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            """
            SELECT checksum, disposition
            FROM schema_migration_assets
            WHERE asset_key = '0059#gateway_transport'
            """
        ).fetchone() == (
            "bad91de55866c6e9ffffed8a8d249abf077b146bce6b3705a5f95d11b91a8944",
            "APPLIED",
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM gateway_transport_inbox"
        ).fetchone() == (0,)


def test_failed_explicit_transaction_does_not_commit_partial_business_ddl(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "partial-catalog"
    catalog.mkdir()
    (catalog / "0001_partial.sql").write_text(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE must_rollback(id INTEGER PRIMARY KEY);
        INSERT INTO table_that_does_not_exist(id) VALUES (1);
        COMMIT;
        """,
        encoding="utf-8",
    )
    db_path = tmp_path / "partial.sqlite3"

    result = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=catalog,
    ).apply_migrations(request_id="task0487-partial", ts_ms=12)

    assert result.success is False
    assert result.reason_code == MIGRATION_EXEC_FAILED
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='must_rollback'"
        ).fetchone() is None


@pytest.mark.parametrize(
    "asset_name",
    ["0009_service_health_schema.sql", "0022_llm_route_log.sql"],
)
def test_frozen_compatibility_asset_tamper_fails_before_business_ddl(
    tmp_path: Path,
    asset_name: str,
) -> None:
    catalog = _copy_catalog(tmp_path)
    asset = catalog / asset_name
    asset.write_text(asset.read_text(encoding="utf-8-sig") + "\n-- tampered\n", encoding="utf-8")

    guard = SQLiteMigrationVersionGuard(migrations_dir=catalog).build_guard_report(ts_ms=20)
    assert guard.status == GUARD_FAILED, guard.as_dict()
    assert guard.reason_code == MIGRATION_SCHEMA_DRIFT

    db_path = tmp_path / "tampered.sqlite3"
    result = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=catalog,
    ).apply_migrations(request_id="task0487-tamper", ts_ms=21)
    assert not result.success
    assert result.reason_code == MIGRATION_SCHEMA_DRIFT

    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='agent_loop_log'"
        ).fetchone() == (0,)
    finally:
        conn.close()


def test_physical_asset_checksum_blocks_skip_after_catalog_drift(tmp_path: Path) -> None:
    catalog = _copy_catalog(tmp_path)
    db_path = tmp_path / "checksum.sqlite3"
    runner = SQLiteMigrationRunner(db_path=db_path, migrations_dir=catalog)
    first = runner.apply_migrations(request_id="task0487-checksum-1", ts_ms=30)
    assert first.success, first.as_dict()

    asset = catalog / "0057_life_occ.sql"
    asset.write_text(asset.read_text(encoding="utf-8-sig") + "\n-- drift\n", encoding="utf-8")

    second = runner.apply_migrations(request_id="task0487-checksum-2", ts_ms=31)
    assert not second.success
    assert second.reason_code == MIGRATION_SCHEMA_DRIFT
    assert "checksum" in second.detail


def test_0021_compatibility_requires_exact_checksum(tmp_path: Path) -> None:
    catalog = _copy_catalog(tmp_path)
    asset = catalog / "0021_episodic_fts5.sql"
    asset.write_text(asset.read_text(encoding="utf-8-sig") + "\n-- drift\n", encoding="utf-8")

    result = SQLiteMigrationRunner(
        db_path=tmp_path / "fts-drift.sqlite3",
        migrations_dir=catalog,
    ).apply_migrations(request_id="task0487-fts-drift", ts_ms=40)

    assert not result.success
    assert result.reason_code == MIGRATION_EXEC_FAILED
    assert result.records[-1].migration_id == "0021_episodic_fts5"
    assert "virtual tables may not be indexed" in result.detail
