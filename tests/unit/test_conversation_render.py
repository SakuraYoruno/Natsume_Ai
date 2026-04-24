from __future__ import annotations

from host_brain.conversation import Turn, build_chat_messages, turns_to_messages


def test_turns_to_messages_skips_corrupted() -> None:
    messages = turns_to_messages(
        [
            Turn(
                turn_id="t1",
                session_id="s",
                person_id="p",
                request_id="r1",
                role="user",
                content="你好",
                ts_ms=1,
                turn_index=0,
            ),
            Turn(
                turn_id="t2",
                session_id="s",
                person_id="p",
                request_id="r2",
                role="assistant",
                content="坏数据",
                ts_ms=2,
                turn_index=1,
                corrupted=True,
            ),
        ]
    )

    assert messages == [{"role": "user", "content": "你好"}]


def test_build_chat_messages_structure() -> None:
    messages = build_chat_messages(
        system_prompt="系统提示",
        history_turns=[
            {"role": "user", "content": "前一轮"},
            {"role": "assistant", "content": "前一轮回复"},
        ],
        user_message="当前问题",
    )

    assert messages == [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "前一轮"},
        {"role": "assistant", "content": "前一轮回复"},
        {"role": "user", "content": "当前问题"},
    ]
