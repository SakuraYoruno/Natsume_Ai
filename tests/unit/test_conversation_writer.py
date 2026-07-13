from __future__ import annotations

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.conversation_writer import record_assistant_turn, record_user_turn
from host_brain.composition.process_service_container import get_process_service_container
from host_brain.persistence.context_repository import ContextRepository


def _install_ledger(monkeypatch) -> tuple[SQLiteLedger, ContextRepository]:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    repository = ContextRepository.from_ledger(ledger)
    container = get_process_service_container()
    monkeypatch.setattr(container, "ledger", ledger)
    monkeypatch.setattr(container, "conversation_turn_store", repository)
    return ledger, repository


def test_record_turn_writers_persist_through_canonical_repositories(monkeypatch) -> None:
    _ledger, repository = _install_ledger(monkeypatch)

    record_user_turn(
        request_id="req-user",
        session_id="sess",
        person_id="p1",
        content="你好",
        ts_ms=1000,
        lang="zh",
    )
    record_assistant_turn(
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


def test_record_user_turn_is_idempotent_for_same_request_role(monkeypatch) -> None:
    _ledger, repository = _install_ledger(monkeypatch)

    first = record_user_turn(
        request_id="req-same",
        session_id="sess",
        person_id="p1",
        content="你好",
        ts_ms=1000,
        lang="zh",
    )
    replay = record_user_turn(
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
