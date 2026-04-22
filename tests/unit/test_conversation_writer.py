from __future__ import annotations

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.conversation import TurnStore
from host_brain.conversation.writer import record_assistant_turn, record_user_turn


def test_record_turn_writers_soft_fail_and_persist(monkeypatch) -> None:
    store = TurnStore(SQLiteLedger(":memory:", check_same_thread=False))
    monkeypatch.setattr("host_brain.app.dependencies._conversation_turn_store", store, raising=False)

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

    turns = store.query_recent(session_id="sess", limit=10)
    assert [(turn.role, turn.content) for turn in turns] == [("user", "你好"), ("assistant", "我是夏目蓝。")]


def test_record_user_turn_is_idempotent_for_same_request_role(monkeypatch) -> None:
    store = TurnStore(SQLiteLedger(":memory:", check_same_thread=False))
    monkeypatch.setattr("host_brain.app.dependencies._conversation_turn_store", store, raising=False)

    record_user_turn(
        request_id="req-same",
        session_id="sess",
        person_id="p1",
        content="你好",
        ts_ms=1000,
        lang="zh",
    )
    record_user_turn(
        request_id="req-same",
        session_id="sess",
        person_id="p1",
        content="你好",
        ts_ms=1001,
        lang="zh",
    )

    turns = store.query_recent(session_id="sess", limit=10)
    assert len(turns) == 1
    assert turns[0].role == "user"
    assert turns[0].request_id == "req-same"
