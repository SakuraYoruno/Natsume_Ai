from __future__ import annotations

from host_brain.app.agent_runtime_shared import _maybe_build_phatic_fastpath_reply
from host_brain.app.chat_agent import SubAgentContext, _build_deterministic_sub_agent_reply
from tests.text_quality_assertions import assert_natsume_style


def _no_turns(**_kwargs):
    return []


def test_maybe_build_phatic_fastpath_reply_returns_final_comfort_line() -> None:
    reply = _maybe_build_phatic_fastpath_reply(
        request_id="req-phatic-comfort-001",
        session_id="session-phatic-comfort-001",
        person_id="person-phatic-comfort-001",
        user_message="今天有点累，抱抱我吧。",
        render_target_lang="zh",
        load_continuity_turns=_no_turns,
        looks_like_active_task_context=lambda _turns: False,
    )

    assert reply
    assert "语义目标" not in str(reply)
    assert "说明：" not in str(reply)
    assert_natsume_style(
        user_message="今天有点累，抱抱我吧。",
        reply=str(reply),
        emotion_tag="EXHAUSTED",
        bond_stage="FRIEND",
        max_chars=40,
        min_warmth=0.55,
        min_sensitivity=0.45,
        min_playfulness=0.35,
    )


def test_maybe_build_phatic_fastpath_reply_skips_ack_during_active_task_context() -> None:
    reply = _maybe_build_phatic_fastpath_reply(
        request_id="req-phatic-ack-active-001",
        session_id="session-phatic-ack-active-001",
        person_id="person-phatic-ack-active-001",
        user_message="好的",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [{"role": "assistant", "content": "我先继续处理。"}],
        looks_like_active_task_context=lambda _turns: True,
    )

    assert reply is None


def test_build_deterministic_sub_agent_reply_passes_through_fastpath_text() -> None:
    phatic_ctx = SubAgentContext(agent_id="phatic_fastpath", result_text="过来，我抱抱你。今天先别硬撑了。")
    recent_turn_ctx = SubAgentContext(agent_id="recent_turn_direct", result_text="你刚才说的是“今天有点累”。")
    task_progress_ctx = SubAgentContext(agent_id="task_progress_direct", result_text="我现在做到一半了，再给我一点时间。")

    assert _build_deterministic_sub_agent_reply(phatic_ctx, render_target_lang="zh") == "过来，我抱抱你。今天先别硬撑了。"
    assert _build_deterministic_sub_agent_reply(recent_turn_ctx, render_target_lang="zh") == "你刚才说的是“今天有点累”。"
    assert _build_deterministic_sub_agent_reply(task_progress_ctx, render_target_lang="zh") == "我现在做到一半了，再给我一点时间。"
