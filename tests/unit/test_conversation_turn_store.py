from __future__ import annotations

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.conversation import TurnStore


def test_turn_store_query_recent_is_session_strict() -> None:
    store = TurnStore(SQLiteLedger(":memory:", check_same_thread=False))
    store.append_generated(
        session_id="sess-a",
        person_id="p1",
        request_id="req-1",
        role="user",
        content="第一句",
        ts_ms=1000,
    )
    store.append_generated(
        session_id="sess-b",
        person_id="p1",
        request_id="req-2",
        role="user",
        content="别的会话",
        ts_ms=2000,
    )

    turns = store.query_recent(session_id="sess-a", limit=10)

    assert [turn.content for turn in turns] == ["第一句"]


def test_turn_store_turn_index_increments_per_session() -> None:
    store = TurnStore(SQLiteLedger(":memory:", check_same_thread=False))
    first = store.append_generated(
        session_id="sess-a",
        person_id="p1",
        request_id="req-1",
        role="user",
        content="你好",
        ts_ms=1000,
    )
    second = store.append_generated(
        session_id="sess-a",
        person_id="p1",
        request_id="req-2",
        role="assistant",
        content="在。",
        ts_ms=1001,
    )

    assert first.turn_index == 0
    assert second.turn_index == 1


def test_turn_store_query_before_turn_index_and_count() -> None:
    store = TurnStore(SQLiteLedger(":memory:", check_same_thread=False))
    for index, content in enumerate(("一", "二", "三", "四"), start=1):
        store.append_generated(
            session_id="sess-a",
            person_id="p1",
            request_id=f"req-{index}",
            role="user" if index % 2 else "assistant",
            content=content,
            ts_ms=index,
        )

    turns = store.query_before_turn_index(session_id="sess-a", before_turn_index=3, limit=10)

    assert [turn.content for turn in turns] == ["一", "二", "三"]
    assert store.count_before_turn_index(session_id="sess-a", before_turn_index=3) == 3
