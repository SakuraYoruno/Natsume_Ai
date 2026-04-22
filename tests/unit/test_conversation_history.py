from __future__ import annotations

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.conversation import ConversationHistory, TurnStore


def test_fetch_for_messages_dedupes_consecutive_same_role() -> None:
    store = TurnStore(SQLiteLedger(":memory:", check_same_thread=False))
    store.append_generated(session_id="sess", person_id="p1", request_id="r1", role="user", content="一", ts_ms=1)
    store.append_generated(session_id="sess", person_id="p1", request_id="r2", role="user", content="二", ts_ms=2)
    store.append_generated(session_id="sess", person_id="p1", request_id="r3", role="assistant", content="三", ts_ms=3)

    turns = ConversationHistory(store).fetch_for_messages(session_id="sess", max_turns=10)

    assert [(turn.role, turn.content) for turn in turns] == [("user", "二"), ("assistant", "三")]


def test_fetch_for_messages_filters_corrupted_turns() -> None:
    store = TurnStore(SQLiteLedger(":memory:", check_same_thread=False))
    store.append_generated(session_id="sess", person_id="p1", request_id="r1", role="user", content="一", ts_ms=1)
    store.append_generated(
        session_id="sess",
        person_id="p1",
        request_id="r2",
        role="assistant",
        content="坏数据",
        ts_ms=2,
        corrupted=True,
    )

    turns = ConversationHistory(store).fetch_for_messages(session_id="sess", max_turns=10)

    assert [(turn.role, turn.content) for turn in turns] == [("user", "一")]


def test_fetch_window_for_messages_builds_older_summary() -> None:
    store = TurnStore(SQLiteLedger(":memory:", check_same_thread=False))
    history = ConversationHistory(store)
    store.append_generated(session_id="sess", person_id="p1", request_id="r1", role="user", content="我昨天去超市了", ts_ms=1)
    store.append_generated(session_id="sess", person_id="p1", request_id="r2", role="assistant", content="嗯，你昨天去超市了。", ts_ms=2)
    store.append_generated(session_id="sess", person_id="p1", request_id="r3", role="user", content="还买了牛奶和面包", ts_ms=3)
    store.append_generated(session_id="sess", person_id="p1", request_id="r4", role="assistant", content="听起来挺顺利。", ts_ms=4)
    store.append_generated(session_id="sess", person_id="p1", request_id="r5", role="user", content="后来又去了一趟公园", ts_ms=5)

    window = history.fetch_window_for_messages(
        session_id="sess",
        max_turns=2,
        summary_threshold=1,
        render_target_lang="zh",
    )

    assert [(turn.role, turn.content) for turn in window.recent_turns] == [
        ("assistant", "听起来挺顺利。"),
        ("user", "后来又去了一趟公园"),
    ]
    assert window.older_turn_count == 3
    assert "更早些时候" in window.older_summary
    assert "超市" in window.older_summary


def test_build_summary_for_older_ja() -> None:
    store = TurnStore(SQLiteLedger(":memory:", check_same_thread=False))
    history = ConversationHistory(store)
    store.append_generated(session_id="sess", person_id="p1", request_id="r1", role="user", content="昨日は駅前に行った", ts_ms=1)
    store.append_generated(session_id="sess", person_id="p1", request_id="r2", role="assistant", content="駅前まで行ったんだね。", ts_ms=2)
    store.append_generated(session_id="sess", person_id="p1", request_id="r3", role="user", content="そのあと本も買った", ts_ms=3)

    summary = history.build_summary_for_older(
        session_id="sess",
        before_turn_index=2,
        max_chars=120,
        render_target_lang="ja",
    )

    assert "少し前までの流れ" in summary
    assert "駅前" in summary
