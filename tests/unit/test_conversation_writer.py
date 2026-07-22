from __future__ import annotations

import sqlite3

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.app.conversation_writer import record_assistant_turn, record_user_turn
from host_brain.composition.life import ProcessLifeContainer, build_process_life_container
from host_brain.composition.process_service_container import get_process_service_container
from host_brain.persistence.context_repository import ContextRepository
from host_brain.persona.context.tactic_ledger import SessionTacticLedgerStore
from host_brain.persona.context.topic_ledger import SessionTopicLedgerStore


def _install_ledger(monkeypatch, tmp_path) -> tuple[SQLiteLedger, ContextRepository, ProcessLifeContainer]:
    db_path = tmp_path / "conversation.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="conversation-writer-test",
        ts_ms=0,
    )
    assert result.success is True, result.as_dict()
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    repository = ContextRepository.from_ledger(ledger)
    container = get_process_service_container()
    monkeypatch.setattr(container, "ledger", ledger)
    monkeypatch.setattr(container, "conversation_turn_store", repository)
    return ledger, repository, build_process_life_container(ledger=ledger, adopted_at_ms=0)


def test_record_turn_writers_persist_through_canonical_repositories(monkeypatch, tmp_path) -> None:
    ledger, repository, life = _install_ledger(monkeypatch, tmp_path)

    record_user_turn(
        ledger=ledger,
        life_service=life.command_service,
        life_reader=life.snapshot_reader,
        request_id="req-user",
        session_id="sess",
        person_id="p1",
        content="你好",
        ts_ms=1000,
        lang="zh",
    )
    record_assistant_turn(
        ledger=ledger,
        life_service=life.command_service,
        life_reader=life.snapshot_reader,
        session_topic_ledger_store=SessionTopicLedgerStore(ledger),
        session_tactic_ledger_store=SessionTacticLedgerStore(ledger),
        request_id="req-assistant",
        session_id="sess",
        person_id="p1",
        content="我是夏目蓝。",
        ts_ms=1001,
        lang="zh",
    )

    turns = repository.query_recent_turns(session_id="sess", limit=10)
    assert [(turn["role"], turn["content"]) for turn in turns] == [
        ("user", "你好"),
        ("assistant", "我是夏目蓝。"),
    ]
    with sqlite3.connect(str(ledger.canonical_db_path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM life_command WHERE command_type = 'conversation_turn_recorded'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM user_preference_event"
        ).fetchone()[0] == 0


def test_record_user_turn_is_idempotent_for_same_request_role(monkeypatch, tmp_path) -> None:
    ledger, repository, life = _install_ledger(monkeypatch, tmp_path)

    first = record_user_turn(
        ledger=ledger,
        life_service=life.command_service,
        life_reader=life.snapshot_reader,
        request_id="req-same",
        session_id="sess",
        person_id="p1",
        content="你好",
        ts_ms=1000,
        lang="zh",
    )
    replay = record_user_turn(
        ledger=ledger,
        life_service=life.command_service,
        life_reader=life.snapshot_reader,
        request_id="req-same",
        session_id="sess",
        person_id="p1",
        content="你好",
        ts_ms=1000,
        lang="zh",
    )

    turns = repository.query_recent_turns(session_id="sess", limit=10)
    assert first["status"] == "OK"
    assert replay["status"] == "SKIPPED"
    assert len(turns) == 1
    assert turns[0]["role"] == "user"
    assert turns[0]["request_id"] == "req-same"
