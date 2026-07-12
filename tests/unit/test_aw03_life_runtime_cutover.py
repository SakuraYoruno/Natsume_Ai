from __future__ import annotations

import inspect
from pathlib import Path

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.digital_life.contracts import LifeCommand, LifeCommandStatus
from host_brain.digital_life.service import LifeCommandService
from host_brain.persistence.life_command_repository import LifeCommandRepository
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
        assert initial["version"] == 0
        repository = LifeCommandRepository.from_ledger(ledger)
        service = LifeCommandService(repository)
        applied = service.submit_next(
            "person-1",
            lambda snapshot: LifeCommand(
                command_id="command-1",
                person_id="person-1",
                command_type="state_patch",
                payload={"state_patch": {"affect": {"mood": "warm"}}},
                expected_version=snapshot.version,
                created_at_ms=NOW,
            ),
        )
        assert applied.status is LifeCommandStatus.APPLIED
        assert repository.get_snapshot("person-1").state["affect"]["mood"] == "warm"
        conflict = service.submit(
            LifeCommand(
                command_id="command-stale",
                person_id="person-1",
                command_type="state_patch",
                payload={"state_patch": {"affect": {"mood": "stale"}}},
                expected_version=0,
                created_at_ms=NOW + 1,
            )
        )
        assert conflict.status is LifeCommandStatus.CONFLICT
        assert life_continuity.get_life_state(ledger, "person-1")["version"] == 1
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
