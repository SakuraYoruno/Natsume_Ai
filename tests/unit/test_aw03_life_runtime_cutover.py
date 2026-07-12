from __future__ import annotations

import inspect
from pathlib import Path

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.runtime import life_continuity, life_organs


NOW = 1_800_000_000_000


def _ledger(tmp_path: Path) -> SQLiteLedger:
    db_path = tmp_path / "life-cutover.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-life-cutover",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return SQLiteLedger(db_path, initialize_schema=False, check_same_thread=False)


def test_compatibility_api_uses_single_repository_owner(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    try:
        initial = life_continuity.get_life_state(ledger, "person-1")
        assert initial["version"] == 1
        applied = life_continuity.LifeStateCommandService(ledger).apply_state_command(
            person_id="person-1",
            expected_version=1,
            patch={"affect": {"mood": "warm"}},
            command_ref="command-1",
        )
        assert applied["status"] == "APPLIED"
        assert applied["state"]["affect"]["mood"] == "warm"
        conflict = life_continuity.LifeStateCommandService(ledger).apply_state_command(
            person_id="person-1",
            expected_version=1,
            patch={"affect": {"mood": "stale"}},
            command_ref="command-stale",
        )
        assert conflict["status"] == "CONFLICT"
        assert life_continuity.get_life_state(ledger, "person-1")["version"] == 2
    finally:
        ledger.close()


def test_organ_compatibility_api_is_exactly_once(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    try:
        event = life_organs.append_life_experience(
            ledger=ledger,
            person_id="person-1",
            source_kind="unit",
            event_kind="user_message",
            content_summary="hello",
            evidence={},
            salience=0.5,
            valence=0.2,
            consume=True,
        )
        assert event["organ_processing"]["status"] == "OK"
        assert life_organs.process_life_experience_event(
            ledger=ledger, event_id=event["event_id"]
        )["status"] == "ALREADY_CONSUMED"
        assert life_organs.get_organ_runtime_state(
            ledger=ledger, person_id="person-1"
        )["organ_count"] == len(life_organs.ORGAN_IDS)
    finally:
        ledger.close()


def test_runtime_modules_have_no_schema_or_private_connection_authority() -> None:
    for module in (life_continuity, life_organs):
        source = inspect.getsource(module)
        assert "CREATE TABLE" not in source
        assert "CREATE INDEX" not in source
        assert "ALTER TABLE" not in source
        assert "._conn" not in source
        assert "getattr(ledger, \"_conn\"" not in source
        assert ".execute(" not in source
        assert ".commit(" not in source
