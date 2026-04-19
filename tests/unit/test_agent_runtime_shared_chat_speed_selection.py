from __future__ import annotations

from host_brain.app.agent_runtime_shared import (
    _should_use_context_chat_fast_mode,
    _should_use_direct_chat_fast_mode,
)


def test_direct_chat_fast_mode_respects_explicit_context_fast() -> None:
    assert not _should_use_direct_chat_fast_mode(
        intent_draft={"route": "chat", "chat_speed_profile": "context_fast"},
        user_message="我今天其实有点想和你认真聊聊最近的状态。",
    )


def test_context_chat_fast_mode_respects_explicit_context_fast() -> None:
    assert _should_use_context_chat_fast_mode(
        intent_draft={"route": "chat", "chat_speed_profile": "context_fast"},
        user_message="我今天其实有点想和你认真聊聊最近的状态。",
    )


def test_context_chat_fast_mode_respects_explicit_direct_fast() -> None:
    assert not _should_use_context_chat_fast_mode(
        intent_draft={"route": "chat", "chat_speed_profile": "direct_fast"},
        user_message="我今天其实有点想和你认真聊聊最近的状态。",
    )
