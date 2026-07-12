from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.runtime import life_continuity as lc


MIGRATION = Path("host_brain/audit/migrations/0057_life_occ.sql")


def _apply_migration(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    finally:
        conn.close()


def _close_ledger(ledger: SQLiteLedger) -> None:
    ledger.close()


def _migrated_ledger(db_path: Path) -> SQLiteLedger:
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"nle-life-occ-fixture::{db_path.name}",
    )
    assert result.success, result.as_dict()
    return SQLiteLedger(
        db_path=str(db_path),
        check_same_thread=False,
        initialize_schema=False,
    )


def test_t_nle_14_life_occ_migration_is_idempotent_and_preserves_old_state(tmp_path: Path) -> None:
    db_path = tmp_path / "life_occ_migration.sqlite3"
    old_state = {"person_id": "default", "version": 7, "legacy": {"kept": True}}
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE digital_life_state (person_id TEXT PRIMARY KEY, version INTEGER NOT NULL DEFAULT 1, state_json TEXT NOT NULL, updated_at_ms INTEGER NOT NULL)"
        )
        conn.execute(
            "INSERT INTO digital_life_state(person_id, version, state_json, updated_at_ms) VALUES(?, ?, ?, ?)",
            ("default", 7, json.dumps(old_state, ensure_ascii=False), 123),
        )
        conn.commit()
    finally:
        conn.close()

    _apply_migration(db_path)
    _apply_migration(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT version, state_json FROM digital_life_state WHERE person_id='default'").fetchone()
        assert row is not None
        assert row[0] == 7
        assert json.loads(row[1]) == old_state
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='digital_life_state_occ_event'").fetchone()
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_digital_life_state_person_version'").fetchone()
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_digital_life_state_occ_person_ts'").fetchone()
    finally:
        conn.close()


def test_t_nle_14_command_facade_rejects_stale_expected_version_without_overwrite(tmp_path: Path) -> None:
    ledger = _migrated_ledger(tmp_path / "life_occ_command.sqlite3")
    try:
        service = lc.LifeStateCommandService(ledger)
        base = service.read_state("default")
        base_version = int(base["version"])

        applied = service.apply_patch(
            person_id="default",
            expected_version=base_version,
            state_patch={"occ_test": {"winner": "first"}},
            command_ref="cmd-first",
        )
        stale = service.apply_patch(
            person_id="default",
            expected_version=base_version,
            state_patch={"occ_test": {"loser": "stale"}},
            command_ref="cmd-stale",
        )

        persisted = service.read_state("default")
        assert applied["status"] == "APPLIED"
        assert applied["state_version_after"] == base_version + 1
        assert stale["status"] == "CONFLICT"
        assert stale["reason_code"] == lc.LIFE_STATE_OCC_CONFLICT
        assert stale["actual_version"] == base_version + 1
        assert persisted["version"] == base_version + 1
        assert persisted["occ_test"] == {"winner": "first"}
        conflict = ledger._conn.execute(
            "SELECT status, reason_code, expected_version, actual_version FROM digital_life_state_occ_event WHERE command_ref='cmd-stale'"
        ).fetchone()
        assert tuple(conflict) == ("CONFLICT", lc.LIFE_STATE_OCC_CONFLICT, base_version, base_version + 1)
    finally:
        _close_ledger(ledger)


def test_t_nle_14_twenty_concurrent_expected_version_commands_have_one_winner(tmp_path: Path) -> None:
    db_path = tmp_path / "life_occ_concurrent.sqlite3"
    seed_ledger = _migrated_ledger(db_path)
    try:
        seed_service = lc.LifeStateCommandService(seed_ledger)
        base = seed_service.read_state("default")
        base_version = int(base["version"])
    finally:
        _close_ledger(seed_ledger)

    def _worker(worker_id: int) -> dict[str, Any]:
        ledger = SQLiteLedger(
            db_path=str(db_path),
            check_same_thread=False,
            initialize_schema=False,
        )
        try:
            service = lc.LifeStateCommandService(ledger)
            return service.apply_patch(
                person_id="default",
                expected_version=base_version,
                state_patch={"occ_concurrency": {"worker_id": worker_id}},
                command_ref=f"concurrent-{worker_id}",
            )
        finally:
            _close_ledger(ledger)

    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(_worker, range(20)))

    statuses = [str(item["status"]) for item in results]
    assert statuses.count("APPLIED") == 1
    assert statuses.count("CONFLICT") == 19

    verify_ledger = SQLiteLedger(
        db_path=str(db_path),
        check_same_thread=False,
        initialize_schema=False,
    )
    try:
        final_state = lc.get_life_state(verify_ledger, "default")
        assert final_state["version"] == base_version + 1
        assert isinstance(final_state.get("occ_concurrency"), dict)
        assert final_state["occ_concurrency"]["worker_id"] in range(20)
        rows = verify_ledger._conn.execute(
            "SELECT status, COUNT(*) FROM digital_life_state_occ_event WHERE command_ref LIKE 'concurrent-%' GROUP BY status"
        ).fetchall()
        counts = {str(row[0]): int(row[1]) for row in rows}
        assert counts == {"APPLIED": 1, "CONFLICT": 19}
    finally:
        _close_ledger(verify_ledger)


def test_t_nle_14_single_writer_fallback_is_explicit_and_audited(tmp_path: Path) -> None:
    ledger = _migrated_ledger(tmp_path / "life_occ_fallback.sqlite3")
    try:
        state = lc.get_life_state(ledger, "default")
        state["legacy_writer"] = {"ok": True}
        result = lc._save_state(ledger, state, command_ref="legacy-test", source_kind="legacy_single_writer")
        persisted = lc.get_life_state(ledger, "default")
        audit = ledger._conn.execute(
            "SELECT status, reason_code FROM digital_life_state_occ_event WHERE command_ref='legacy-test'"
        ).fetchone()
        assert result["status"] == "FALLBACK_APPLIED"
        assert result["reason_code"] == lc.LIFE_STATE_SINGLE_WRITER_FALLBACK
        assert persisted["legacy_writer"] == {"ok": True}
        assert tuple(audit) == ("FALLBACK_APPLIED", lc.LIFE_STATE_SINGLE_WRITER_FALLBACK)
    finally:
        _close_ledger(ledger)
