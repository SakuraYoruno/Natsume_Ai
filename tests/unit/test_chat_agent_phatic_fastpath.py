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


def test_maybe_build_phatic_fastpath_reply_returns_reunion_line() -> None:
    reply = _maybe_build_phatic_fastpath_reply(
        request_id="req-phatic-reunion-001",
        session_id="session-phatic-reunion-001",
        person_id="person-phatic-reunion-001",
        user_message="好久不见，这段时间你有想我吗？",
        render_target_lang="zh",
        load_continuity_turns=_no_turns,
        looks_like_active_task_context=lambda _turns: False,
    )

    assert reply
    assert_natsume_style(
        user_message="好久不见，这段时间你有想我吗？",
        reply=str(reply),
        emotion_tag="LONGING",
        bond_stage="FRIEND",
        max_chars=70,
        min_warmth=0.55,
        min_sensitivity=0.50,
        min_playfulness=0.35,
    )


def test_maybe_build_phatic_fastpath_reply_returns_rift_repair_line() -> None:
    reply = _maybe_build_phatic_fastpath_reply(
        request_id="req-phatic-rift-001",
        session_id="session-phatic-rift-001",
        person_id="person-phatic-rift-001",
        user_message="我还是有点在意之前那次争执。",
        render_target_lang="zh",
        load_continuity_turns=_no_turns,
        looks_like_active_task_context=lambda _turns: False,
    )

    assert reply
    assert_natsume_style(
        user_message="我还是有点在意之前那次争执。",
        reply=str(reply),
        emotion_tag="HURT",
        bond_stage="FRIEND",
        max_chars=80,
        min_warmth=0.50,
        min_sensitivity=0.58,
        min_playfulness=0.20,
    )


def test_maybe_build_phatic_fastpath_reply_keeps_rift_repair_ahead_of_comfort() -> None:
    reply = _maybe_build_phatic_fastpath_reply(
        request_id="req-phatic-rift-002",
        session_id="session-phatic-rift-002",
        person_id="person-phatic-rift-002",
        user_message="我还在想上次我们闹得有点不开心的事情。",
        render_target_lang="zh",
        load_continuity_turns=_no_turns,
        looks_like_active_task_context=lambda _turns: False,
    )

    assert reply
    assert "争执" in str(reply) or "不愉快" in str(reply) or "小刺" in str(reply)


def test_maybe_build_phatic_fastpath_reply_returns_playful_line() -> None:
    reply = _maybe_build_phatic_fastpath_reply(
        request_id="req-phatic-playful-001",
        session_id="session-phatic-playful-001",
        person_id="person-phatic-playful-001",
        user_message="你最近是不是更会逗我开心了？",
        render_target_lang="zh",
        load_continuity_turns=_no_turns,
        looks_like_active_task_context=lambda _turns: False,
    )

    assert reply
    assert_natsume_style(
        user_message="你最近是不是更会逗我开心了？",
        reply=str(reply),
        emotion_tag="HAPPY",
        bond_stage="FRIEND",
        max_chars=80,
        min_warmth=0.50,
        min_sensitivity=0.45,
        min_playfulness=0.55,
    )


def test_maybe_build_phatic_fastpath_reply_handles_playful_cute_teasing() -> None:
    reply = _maybe_build_phatic_fastpath_reply(
        request_id="req-phatic-playful-002",
        session_id="session-phatic-playful-002",
        person_id="person-phatic-playful-002",
        user_message="你是不是偷偷觉得我今天有点可爱？",
        render_target_lang="zh",
        load_continuity_turns=_no_turns,
        looks_like_active_task_context=lambda _turns: False,
    )

    assert reply
    assert_natsume_style(
        user_message="你是不是偷偷觉得我今天有点可爱？",
        reply=str(reply),
        emotion_tag="HAPPY",
        bond_stage="FRIEND",
        max_chars=80,
        min_warmth=0.50,
        min_sensitivity=0.45,
        min_playfulness=0.55,
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
