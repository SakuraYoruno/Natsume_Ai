from __future__ import annotations

from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

import host_brain.audit.migration_runner as migration_runner_module
from host_brain.audit.migration_compat import (
    PLATFORM_REGISTRY_DRAFT_CHECKSUM,
    PLATFORM_REGISTRY_FINAL_CHECKSUM,
    is_exact_legacy_inline_schema_failure,
    legacy_inline_schema_postcondition_holds,
)
from host_brain.core.errors import MIGRATION_SCHEMA_DRIFT
from host_brain.audit.migration_guard import SQLiteMigrationVersionGuard
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger


def _asset(filename: str, checksum: str):
    migration, name = filename.removesuffix(".sql").split("_", 1)
    return SimpleNamespace(
        migration_id=int(migration),
        name=name,
        path=Path(filename),
        checksum=checksum,
    )


def test_legacy_inline_columns_are_checksum_bound_and_postcondition_checked() -> None:
    asset = _asset(
        "0041_canon_change_log_body.sql",
        "663fc6a8c069e8d7749cc580f347edc18ec89fb685f602f6e614e5c32524b06f",
    )
    failure = sqlite3.OperationalError("duplicate column name: before_body")
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE canon_change_log(
                id INTEGER PRIMARY KEY,
                before_body TEXT,
                after_body TEXT
            )
            """
        )
        assert is_exact_legacy_inline_schema_failure(asset, failure) is True
        assert legacy_inline_schema_postcondition_holds(conn, asset) is True

        conn.execute("ALTER TABLE canon_change_log RENAME TO old_canon_change_log")
        conn.execute(
            "CREATE TABLE canon_change_log(id INTEGER PRIMARY KEY, before_body TEXT)"
        )
        assert legacy_inline_schema_postcondition_holds(conn, asset) is False
        assert is_exact_legacy_inline_schema_failure(
            SimpleNamespace(**{**vars(asset), "checksum": "0" * 64}),
            failure,
        ) is False
    finally:
        conn.close()


def test_legacy_inline_ledger_replays_complete_catalog_without_duplicate_ddl(
    tmp_path,
) -> None:
    db_path = tmp_path / "legacy-inline.sqlite3"
    legacy = SQLiteLedger(db_path=db_path)
    legacy.close()

    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-legacy-inline-replay",
        ts_ms=1_800_000_000_000,
    )

    assert result.success is True, result.as_dict()
    with sqlite3.connect(db_path) as conn:
        dispositions = dict(
            conn.execute(
                """
                SELECT asset_key, disposition
                FROM schema_migration_assets
                WHERE asset_key IN (
                    '0041#canon_change_log_body',
                    '0043#canon_proposal_content',
                    '0069#life_episode_retention'
                )
                """
            ).fetchall()
        )
    assert dispositions == {
        "0041#canon_change_log_body": "APPLIED_WITH_COMPAT",
        "0043#canon_proposal_content": "APPLIED_WITH_COMPAT",
        "0069#life_episode_retention": "APPLIED_WITH_COMPAT",
    }


def test_capability_forge_proposal_log_is_migration_owned(tmp_path) -> None:
    db_path = tmp_path / "capability-forge-migrated.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-capability-forge-schema",
        ts_ms=1_800_000_000_000,
    )
    assert result.success is True, result.as_dict()
    assert result.summary["current_version"] >= 84

    ledger = SQLiteLedger(db_path=db_path, initialize_schema=False)
    try:
        ledger.log_capability_forge_proposal(
            {
                "proposal_id": "forge-proposal-1",
                "event_type": "CREATED",
                "status": "PENDING_APPROVAL",
                "stage": "SHADOW",
                "payload": {"proposal_id": "forge-proposal-1"},
                "ts_ms": 1,
            }
        )
        rows = ledger.list_capability_forge_proposals(
            proposal_id="forge-proposal-1",
        )
    finally:
        ledger.close()

    assert len(rows) == 1
    assert rows[0]["payload"]["proposal_id"] == "forge-proposal-1"


def test_capability_forge_migration_upgrades_an_existing_head_83_database(
    tmp_path,
) -> None:
    db_path = tmp_path / "head-83.sqlite3"
    runner = SQLiteMigrationRunner(db_path=db_path)
    seeded = runner.apply_migrations(
        request_id="aw03-capability-forge-seed",
        ts_ms=1_800_000_000_000,
    )
    assert seeded.success is True, seeded.as_dict()

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE capability_forge_proposal_log")
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id = 84")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id = 84")
        conn.commit()
        assert conn.execute(
            "SELECT MAX(migration_id) FROM schema_migrations"
        ).fetchone() == (83,)

    upgraded = runner.apply_migrations(
        request_id="aw03-capability-forge-upgrade",
        ts_ms=1_800_000_000_001,
    )

    assert upgraded.success is True, upgraded.as_dict()
    assert upgraded.summary["current_version"] == 84
    assert upgraded.summary["applied_count"] == 1
    assert upgraded.records[-1].migration_id == "0084_capability_forge_proposal_log"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'capability_forge_proposal_log'
            """
        ).fetchone() == ("capability_forge_proposal_log",)


def test_aw03_migrations_require_an_explicit_immediate_transaction(tmp_path) -> None:
    catalog = tmp_path / "migrations"
    catalog.mkdir()
    (catalog / "0066_untransactional.sql").write_text(
        "CREATE TABLE unsafe_partial(id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    report = SQLiteMigrationVersionGuard(migrations_dir=catalog).build_guard_report(
        request_id="aw03-transaction-policy",
        ts_ms=1,
    )

    assert report.status == "FAILED"
    assert "migrations >= 0066 require explicit BEGIN IMMEDIATE/COMMIT" in report.detail
    result = SQLiteMigrationRunner(
        db_path=tmp_path / "runner-policy.sqlite3",
        migrations_dir=catalog,
    ).apply_migrations(request_id="aw03-runner-transaction-policy", ts_ms=2)
    assert result.success is False
    assert result.reason_code == MIGRATION_SCHEMA_DRIFT
    with sqlite3.connect(tmp_path / "runner-policy.sqlite3") as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'unsafe_partial'"
        ).fetchone() is None


def test_frozen_0067_runner_envelope_rolls_back_partial_schema(tmp_path) -> None:
    db_path = tmp_path / "0067-envelope.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE canon_journal(journal_id TEXT PRIMARY KEY)")
        conn.commit()

    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-0067-envelope-fault",
        ts_ms=1_800_000_000_000,
    )

    assert result.success is False
    assert result.records[-1].migration_id == "0067_canon_command_spine"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'canon_command'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = 67"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM schema_migration_assets WHERE asset_key = '0067#canon_command_spine'"
        ).fetchone() is None
        assert conn.execute("SELECT MAX(migration_id) FROM schema_migrations").fetchone() == (66,)


def test_frozen_0071_runner_envelope_rolls_back_partial_index(tmp_path) -> None:
    db_path = tmp_path / "0071-envelope.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE life_runtime_uow(
                uow_id TEXT PRIMARY KEY,
                aggregate_id TEXT,
                created_at_ms INTEGER
            )
            """
        )
        conn.commit()

    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-0071-envelope-fault",
        ts_ms=1_800_000_000_000,
    )

    assert result.success is False
    assert result.records[-1].migration_id == "0071_life_runtime_uow"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'index' AND name = 'idx_life_runtime_uow_aggregate'
            """
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = 71"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM schema_migration_assets WHERE asset_key = '0071#life_runtime_uow'"
        ).fetchone() is None
        assert conn.execute("SELECT MAX(migration_id) FROM schema_migrations").fetchone() == (70,)


def test_platform_registry_draft_is_repaired_once_then_strictly_validated(
    tmp_path,
) -> None:
    db_path = tmp_path / "platform-registry-draft.sqlite3"
    runner = SQLiteMigrationRunner(db_path=db_path)
    seeded = runner.apply_migrations(request_id="aw03-seed-final", ts_ms=10)
    assert seeded.success is True, seeded.as_dict()

    with sqlite3.connect(db_path) as conn:
        # Rewind to the pre-repair catalog. Later AW-03 migrations must be
        # replayed after 0075 instead of leaving an impossible history gap.
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id >= 75")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id >= 75")
        conn.execute(
            "UPDATE schema_migrations SET checksum = ? WHERE migration_id = 72",
            (PLATFORM_REGISTRY_DRAFT_CHECKSUM,),
        )
        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE migration_id = 72",
            (PLATFORM_REGISTRY_DRAFT_CHECKSUM,),
        )
        conn.execute(
            "DROP TRIGGER IF EXISTS trg_vts_expression_mapping_no_insert_after_journal"
        )
        conn.commit()

    repaired = runner.apply_migrations(request_id="aw03-repair-draft", ts_ms=11)
    assert repaired.success is True, repaired.as_dict()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT checksum FROM schema_migrations WHERE migration_id = 72"
        ).fetchone() == (PLATFORM_REGISTRY_FINAL_CHECKSUM,)
        assert conn.execute(
            "SELECT checksum FROM schema_migration_assets WHERE migration_id = 72"
        ).fetchone() == (PLATFORM_REGISTRY_FINAL_CHECKSUM,)
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            ("trg_vts_expression_mapping_no_insert_after_journal",),
        ).fetchone() == ("trg_vts_expression_mapping_no_insert_after_journal",)

    strict_replay = runner.apply_migrations(request_id="aw03-strict-replay", ts_ms=12)
    assert strict_replay.success is True, strict_replay.as_dict()

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE schema_migrations SET checksum = ? WHERE migration_id = 72",
            ("f" * 64,),
        )
        conn.commit()
    drift = runner.apply_migrations(request_id="aw03-reject-drift", ts_ms=13)
    assert drift.success is False
    assert drift.reason_code == MIGRATION_SCHEMA_DRIFT


def test_platform_registry_strict_postcondition_rolls_back_wash_and_receipt(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "platform-registry-postcondition.sqlite3"
    runner = SQLiteMigrationRunner(db_path=db_path)
    seeded = runner.apply_migrations(request_id="aw03-seed-postcondition", ts_ms=14)
    assert seeded.success is True, seeded.as_dict()
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id >= 75")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id >= 75")
        conn.execute(
            "UPDATE schema_migrations SET checksum = ? WHERE migration_id = 72",
            (PLATFORM_REGISTRY_DRAFT_CHECKSUM,),
        )
        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE migration_id = 72",
            (PLATFORM_REGISTRY_DRAFT_CHECKSUM,),
        )
        conn.execute(
            "DROP TRIGGER IF EXISTS trg_vts_expression_mapping_no_insert_after_journal"
        )
        conn.commit()

    real_postcondition = (
        migration_runner_module.platform_registry_schema_postcondition_holds
    )
    calls = 0

    def fail_strict_postcondition(conn, *, allow_missing_repair_trigger):  # noqa: ANN001, ANN201
        nonlocal calls
        calls += 1
        valid = real_postcondition(
            conn,
            allow_missing_repair_trigger=allow_missing_repair_trigger,
        )
        return False if calls == 3 else valid

    monkeypatch.setattr(
        migration_runner_module,
        "platform_registry_schema_postcondition_holds",
        fail_strict_postcondition,
    )
    rejected = runner.apply_migrations(
        request_id="aw03-fail-strict-postcondition",
        ts_ms=15,
    )

    assert rejected.success is False
    assert rejected.reason_code == "MIGRATION_EXEC_FAILED"
    assert rejected.records[-1].migration_id == "0075_platform_registry_catalog_repair"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT checksum FROM schema_migrations WHERE migration_id = 72"
        ).fetchone() == (PLATFORM_REGISTRY_DRAFT_CHECKSUM,)
        assert conn.execute(
            "SELECT checksum FROM schema_migration_assets WHERE migration_id = 72"
        ).fetchone() == (PLATFORM_REGISTRY_DRAFT_CHECKSUM,)
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = 75"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM schema_migration_assets WHERE migration_id = 75"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            ("trg_vts_expression_mapping_no_insert_after_journal",),
        ).fetchone() is None


@pytest.mark.parametrize(
    "damage_sql",
    [
        "DROP TABLE mcp_tool_sync_journal",
        "ALTER TABLE mcp_tool_registry DROP COLUMN description",
        "DROP INDEX idx_mcp_registry_tool",
        "DROP TRIGGER trg_mcp_tool_sync_journal_no_delete",
    ],
    ids=["table", "column", "index", "trigger"],
)
def test_platform_registry_draft_repair_rejects_incomplete_physical_schema(
    tmp_path,
    damage_sql: str,
) -> None:
    db_path = tmp_path / "platform-registry-incomplete.sqlite3"
    runner = SQLiteMigrationRunner(db_path=db_path)
    seeded = runner.apply_migrations(request_id="aw03-seed-incomplete", ts_ms=20)
    assert seeded.success is True, seeded.as_dict()

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id >= 75")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id >= 75")
        conn.execute(
            "UPDATE schema_migrations SET checksum = ? WHERE migration_id = 72",
            (PLATFORM_REGISTRY_DRAFT_CHECKSUM,),
        )
        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE migration_id = 72",
            (PLATFORM_REGISTRY_DRAFT_CHECKSUM,),
        )
        conn.execute(
            "DROP TRIGGER IF EXISTS trg_vts_expression_mapping_no_insert_after_journal"
        )
        conn.execute(damage_sql)
        conn.commit()

    rejected = runner.apply_migrations(
        request_id="aw03-reject-incomplete",
        ts_ms=21,
    )

    assert rejected.success is False
    assert rejected.reason_code == MIGRATION_SCHEMA_DRIFT
    assert "checksum-bound postcondition" in rejected.detail
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT checksum FROM schema_migrations WHERE migration_id = 72"
        ).fetchone() == (PLATFORM_REGISTRY_DRAFT_CHECKSUM,)
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = 75"
        ).fetchone() is None
