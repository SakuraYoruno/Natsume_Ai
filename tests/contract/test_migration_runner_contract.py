import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from host_brain.audit.migration_runner import (
    S_MIGRATION_SQLITE,
    STATUS_APPLIED as SQLITE_STATUS_APPLIED,
    STATUS_FAILED as SQLITE_STATUS_FAILED,
    STATUS_SKIPPED as SQLITE_STATUS_SKIPPED,
    SQLiteMigrationRunner,
)
from host_brain.bond.migration_runner import (
    S_MIGRATION_BOND,
    STATUS_APPLIED as BOND_STATUS_APPLIED,
    STATUS_FAILED as BOND_STATUS_FAILED,
    STATUS_SKIPPED as BOND_STATUS_SKIPPED,
    BondMigrationRunner,
)
from host_brain.core.errors import (
    MIGRATION_ASSET_MISSING,
    MIGRATION_SCHEMA_DRIFT,
    MIGRATION_VERSION_ROLLBACK,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "migrations"


class TestMigrationRunnerContract(unittest.TestCase):
    def test_t_migrate_01_sqlite_idempotent_apply_and_audit_log(self) -> None:
        sqlite_fixtures = FIXTURES / "sqlite" / "good"
        fd, raw_path = tempfile.mkstemp(prefix="sqlite_migrate_", suffix=".sqlite3")
        os.close(fd)
        db_path = Path(raw_path)
        try:
            runner = SQLiteMigrationRunner(db_path=db_path, migrations_dir=sqlite_fixtures)

            first = runner.apply_migrations(request_id="req_migrate_sqlite_001", ts_ms=1_800_040_000_000)
            self.assertTrue(first.success, msg=first.as_dict())
            self.assertEqual(S_MIGRATION_SQLITE, first.stage)
            self.assertEqual(2, first.summary["applied_count"])
            self.assertEqual(0, first.summary["failed_count"])
            self.assertEqual(2, first.summary["current_version"])
            self.assertTrue(all(row.migration_id for row in first.records))
            self.assertTrue(all(isinstance(row.latency_ms, int) for row in first.records))
            self.assertTrue(all(row.status == SQLITE_STATUS_APPLIED for row in first.records))

            second = runner.apply_migrations(request_id="req_migrate_sqlite_002", ts_ms=1_800_040_000_001)
            self.assertTrue(second.success, msg=second.as_dict())
            self.assertEqual(0, second.summary["applied_count"])
            self.assertEqual(2, second.summary["skipped_count"])
            self.assertEqual(0, second.summary["failed_count"])
            self.assertTrue(all(row.status == SQLITE_STATUS_SKIPPED for row in second.records))

            migration_log = runner.read_migration_log()
            self.assertEqual(4, len(migration_log))
            self.assertEqual(
                {"req_migrate_sqlite_001", "req_migrate_sqlite_002"},
                {row["request_id"] for row in migration_log},
            )
            self.assertTrue(
                all(
                    {"migration_id", "status", "reason_code", "latency_ms"}.issubset(row.keys())
                    for row in migration_log
                ),
                msg=migration_log,
            )

            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute("SELECT COUNT(*) FROM sample_items").fetchone()
                self.assertEqual(0, int(row[0]))
                index_rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_sample_items_name'"
                ).fetchall()
                self.assertEqual(1, len(index_rows))
            finally:
                conn.close()
        finally:
            if db_path.exists():
                os.remove(db_path)

    def test_t_migrate_01_sqlite_missing_asset_gap_is_rejected(self) -> None:
        sqlite_fixtures = FIXTURES / "sqlite" / "bad_missing"
        fd, raw_path = tempfile.mkstemp(prefix="sqlite_migrate_gap_", suffix=".sqlite3")
        os.close(fd)
        db_path = Path(raw_path)
        try:
            runner = SQLiteMigrationRunner(db_path=db_path, migrations_dir=sqlite_fixtures)

            result = runner.apply_migrations(request_id="req_migrate_sqlite_gap", ts_ms=1_800_040_000_010)
            self.assertFalse(result.success)
            self.assertEqual(S_MIGRATION_SQLITE, result.stage)
            self.assertEqual(MIGRATION_ASSET_MISSING, result.reason_code)
            self.assertEqual(1, result.summary["failed_count"])
            self.assertEqual(SQLITE_STATUS_FAILED, result.records[0].status)
        finally:
            if db_path.exists():
                os.remove(db_path)

    def test_t_migrate_01_sqlite_rollback_guard_is_rejected(self) -> None:
        rollback_from = FIXTURES / "sqlite" / "rollback_from"
        rollback_to = FIXTURES / "sqlite" / "rollback_to"
        fd, raw_path = tempfile.mkstemp(prefix="sqlite_migrate_rollback_", suffix=".sqlite3")
        os.close(fd)
        db_path = Path(raw_path)
        try:
            runner = SQLiteMigrationRunner(db_path=db_path, migrations_dir=rollback_from)
            ok = runner.apply_migrations(request_id="req_migrate_sqlite_from", ts_ms=1_800_040_000_020)
            self.assertTrue(ok.success, msg=ok.as_dict())
            self.assertEqual(3, ok.summary["current_version"])

            rollback_runner = SQLiteMigrationRunner(db_path=db_path, migrations_dir=rollback_to)
            blocked = rollback_runner.apply_migrations(
                request_id="req_migrate_sqlite_to",
                ts_ms=1_800_040_000_021,
            )
            self.assertFalse(blocked.success)
            self.assertEqual(MIGRATION_VERSION_ROLLBACK, blocked.reason_code)
            self.assertEqual(SQLITE_STATUS_FAILED, blocked.records[0].status)
        finally:
            if db_path.exists():
                os.remove(db_path)

    def test_t_migrate_01_bond_idempotent_drift_and_rollback_guard(self) -> None:
        good = FIXTURES / "bond" / "good"
        bad_missing = FIXTURES / "bond" / "bad_missing"
        rollback_from = FIXTURES / "bond" / "rollback_from"
        rollback_to = FIXTURES / "bond" / "rollback_to"

        runner = BondMigrationRunner(migrations_dir=good)
        first = runner.apply_migrations(request_id="req_migrate_bond_001", ts_ms=1_800_040_000_030)
        self.assertTrue(first.success, msg=first.as_dict())
        self.assertEqual(S_MIGRATION_BOND, first.stage)
        self.assertEqual(2, first.summary["applied_count"])
        self.assertTrue(all(row.status == BOND_STATUS_APPLIED for row in first.records))

        second = runner.apply_migrations(request_id="req_migrate_bond_002", ts_ms=1_800_040_000_031)
        self.assertTrue(second.success, msg=second.as_dict())
        self.assertEqual(0, second.summary["applied_count"])
        self.assertEqual(2, second.summary["skipped_count"])
        self.assertTrue(all(row.status == BOND_STATUS_SKIPPED for row in second.records))

        drift_runner = BondMigrationRunner(migrations_dir=bad_missing)
        drift = drift_runner.apply_migrations(request_id="req_migrate_bond_gap", ts_ms=1_800_040_000_032)
        self.assertFalse(drift.success)
        self.assertEqual(MIGRATION_ASSET_MISSING, drift.reason_code)
        self.assertEqual(BOND_STATUS_FAILED, drift.records[0].status)

        rollback_runner = BondMigrationRunner(migrations_dir=rollback_from)
        ok = rollback_runner.apply_migrations(request_id="req_migrate_bond_from", ts_ms=1_800_040_000_033)
        self.assertTrue(ok.success, msg=ok.as_dict())
        rollback_runner.migrations_dir = rollback_to
        blocked = rollback_runner.apply_migrations(
            request_id="req_migrate_bond_to",
            ts_ms=1_800_040_000_034,
        )
        self.assertFalse(blocked.success)
        self.assertEqual(MIGRATION_VERSION_ROLLBACK, blocked.reason_code)
        self.assertEqual(BOND_STATUS_FAILED, blocked.records[0].status)

        log_rows = rollback_runner.read_migration_log()
        self.assertTrue(
            any(row["reason_code"] == MIGRATION_VERSION_ROLLBACK for row in log_rows),
            msg=log_rows,
        )
        self.assertTrue(
            all({"migration_id", "status", "reason_code", "latency_ms"}.issubset(row.keys()) for row in log_rows),
            msg=log_rows,
        )

    def test_t_migrate_01_bond_schema_drift_is_rejected(self) -> None:
        rollback_from = FIXTURES / "bond" / "rollback_from"
        drift_runner = BondMigrationRunner(
            migrations_dir=rollback_from,
            applied_migration_ids={1, 3},
        )
        blocked = drift_runner.apply_migrations(request_id="req_migrate_bond_drift", ts_ms=1_800_040_000_041)
        self.assertFalse(blocked.success)
        self.assertEqual(MIGRATION_SCHEMA_DRIFT, blocked.reason_code)
        self.assertEqual(BOND_STATUS_FAILED, blocked.records[0].status)


if __name__ == "__main__":
    unittest.main()
