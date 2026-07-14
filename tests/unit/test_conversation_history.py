from __future__ import annotations

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.conversation import ConversationHistory
from host_brain.persistence.context_repository import ContextRepository


def _repository() -> ContextRepository:
    return ContextRepository.from_ledger(SQLiteLedger(":memory:", check_same_thread=False))


def _append(
    repository: ContextRepository,
    *,
    request_id: str,
    role: str,
    content: str,
    ts_ms: int,
    corrupted: bool = False,
) -> None:
    repository.append_generated_turn(
        {
            "turn_id": f"turn::{request_id}::{role}",
            "session_id": "sess",
            "person_id": "p1",
            "request_id": request_id,
            "role": role,
            "content": content,
            "ts_ms": ts_ms,
            "lang": "zh",
            "corrupted": corrupted,
        }
    )


def test_fetch_for_messages_dedupes_consecutive_same_role() -> None:
    repository = _repository()
    _append(repository, request_id="r1", role="user", content="一", ts_ms=1)
    _append(repository, request_id="r2", role="user", content="二", ts_ms=2)
    _append(repository, request_id="r3", role="assistant", content="三", ts_ms=3)

    turns = ConversationHistory(repository).fetch_for_messages(session_id="sess", max_turns=10)

    assert [(turn.role, turn.content) for turn in turns] == [("user", "二"), ("assistant", "三")]


def test_fetch_for_messages_filters_corrupted_turns() -> None:
    repository = _repository()
    _append(repository, request_id="r1", role="user", content="一", ts_ms=1)
    _append(repository, request_id="r2", role="assistant", content="坏数据", ts_ms=2, corrupted=True)

    turns = ConversationHistory(repository).fetch_for_messages(session_id="sess", max_turns=10)

    assert [(turn.role, turn.content) for turn in turns] == [("user", "一")]


def test_fetch_window_for_messages_builds_older_summary() -> None:
    repository = _repository()
    history = ConversationHistory(repository)
    for request_id, role, content, ts_ms in (
        ("r1", "user", "我昨天去超市了", 1),
        ("r2", "assistant", "嗯，你昨天去超市了。", 2),
        ("r3", "user", "还买了牛奶和面包", 3),
        ("r4", "assistant", "听起来挺顺利。", 4),
        ("r5", "user", "后来又去了一趟公园", 5),
    ):
        _append(repository, request_id=request_id, role=role, content=content, ts_ms=ts_ms)

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
    repository = _repository()
    history = ConversationHistory(repository)
    _append(repository, request_id="r1", role="user", content="昨日は駅前に行った", ts_ms=1)
    _append(repository, request_id="r2", role="assistant", content="駅前まで行ったんだね。", ts_ms=2)
    _append(repository, request_id="r3", role="user", content="そのあと本も買った", ts_ms=3)

    summary = history.build_summary_for_older(
        session_id="sess",
        before_turn_index=2,
        max_chars=120,
        render_target_lang="ja",
    )

    assert "少し前までの流れ" in summary
    assert "駅前" in summary
