from __future__ import annotations

from types import SimpleNamespace

from host_brain.app.cortex_prompt_builder import (
    _build_interaction_state_summary,
    _build_soul_section_title,
    _extract_context_thread,
    _extract_context_topic,
    _should_use_compact_chat_lane,
)


def _context_plane(*, topic: str = "", move: str = "", stance: str = "", followup: str = "", thread: str = "", perception: str = "", emotion_anchor: str = "") -> SimpleNamespace:
    interaction_state_payload = {
        "current_topic": topic,
        "interaction_move": move,
        "user_stance": stance,
        "followup_target": followup,
        "ongoing_thread": thread,
        "perception_summary": perception,
    }
    interaction_thread_payload = {
        "thread_kind": thread,
        "emotion_anchor": emotion_anchor,
    }
    return SimpleNamespace(
        interaction_state=SimpleNamespace(
            state=SimpleNamespace(
                current_topic=topic,
                interaction_move=move,
                user_stance=stance,
                followup_target=followup,
                ongoing_thread=thread,
                perception_summary=perception,
                as_dict=lambda: interaction_state_payload,
            )
        ),
        interaction_thread=SimpleNamespace(
            state=SimpleNamespace(
                thread_kind=thread,
                emotion_anchor=emotion_anchor,
                as_dict=lambda: interaction_thread_payload,
            )
        ),
    )


def test_build_interaction_state_summary_covers_thread_specific_branches() -> None:
    repair = _build_interaction_state_summary(_context_plane(topic="emoji_social_followup", move="repair_needed", thread="image_social"))
    reconfirm = _build_interaction_state_summary(_context_plane(topic="emoji_social_followup", move="reconfirm", thread="image_social"))
    light_tease = _build_interaction_state_summary(_context_plane(topic="emoji_social_followup", move="light_tease", thread="image_social"))
    identity = _build_interaction_state_summary(_context_plane(thread="identity_loop"))
    boundary = _build_interaction_state_summary(_context_plane(thread="boundary_probe"))
    help_thread = _build_interaction_state_summary(_context_plane(thread="help_reassurance"))
    phatic = _build_interaction_state_summary(_context_plane(thread="phatic"))

    assert "嘴硬或否认" in repair
    assert "自然解释你的读法" in reconfirm
    assert "轻轻带过去" in light_tease
    assert "连续身份追问" in identity
    assert "关系/动机回访" in boundary
    assert "会不会理我/帮我" in help_thread
    assert "轻社交招呼" in phatic


def test_build_interaction_state_summary_covers_fact_fallback_branches() -> None:
    followup = _build_interaction_state_summary(
        _context_plane(topic="", followup="emoji_social_reply", perception="一张哭哭的图。")
    )
    topic_with_stance = _build_interaction_state_summary(
        _context_plane(topic="mood_reflection", stance="慢慢")
    )
    anchored = _build_interaction_state_summary(
        _context_plane(emotion_anchor="distress")
    )

    assert "接着“emoji_social_reply”往下说" in followup
    assert "当前话题是“mood_reflection”" in topic_with_stance
    assert "偏“distress”" in anchored
    assert "轻社交招呼" in _build_interaction_state_summary(_context_plane(topic="greeting"))


def test_builder_helpers_cover_empty_defaults_and_titles() -> None:
    empty_plane = SimpleNamespace(interaction_state=SimpleNamespace(state=None), interaction_thread=SimpleNamespace(state=None))

    assert _build_interaction_state_summary(empty_plane) == ""
    assert _extract_context_topic(empty_plane) == ""
    assert _extract_context_thread(empty_plane) == ""
    assert _build_soul_section_title(render_target_lang="zh") == "【人格核心】\n"
    assert _build_soul_section_title(render_target_lang="ja") == "【人格核心】\n"
