from __future__ import annotations

from host_brain.app.context.dialogue_state import analyze_dialogue_state


def test_dialogue_state_first_identity_probe_has_no_recent_identity_answer() -> None:
    state = analyze_dialogue_state(
        recent_turns=[],
        user_message="你是",
    )

    assert state.current_user_act == "identity_probe"
    assert state.recent_identity_answered is False
    assert state.repeat_question_count == 0
    assert state.user_stance == "probing"


def test_dialogue_state_repeated_identity_probe_detects_recent_answer() -> None:
    state = analyze_dialogue_state(
        recent_turns=[
            {"role": "user", "content": "你是"},
            {"role": "assistant", "content": "我是夏目蓝。"},
        ],
        user_message="你谁",
    )

    assert state.current_user_act == "identity_probe"
    assert state.last_assistant_act == "self_identify"
    assert state.recent_identity_answered is True
    assert state.repeat_question_count == 1
    assert state.repeat_surface_group == "identity_short"
    assert state.user_stance == "probing"
    assert "identity" in state.answered_topics_recent


def test_dialogue_state_boundary_and_motivation_repeat_is_grouped() -> None:
    state = analyze_dialogue_state(
        recent_turns=[
            {"role": "user", "content": "你为什么要帮我，我们很熟吗"},
            {"role": "assistant", "content": "我们并不熟。我只是觉得，既然你遇到了麻烦，能帮一把就帮一把。"},
        ],
        user_message="你为什么要帮我，我们很熟吗",
    )

    assert state.current_user_act == "motivation_probe"
    assert state.recent_boundary_answered is True
    assert state.repeat_question_count == 1
    assert state.repeat_surface_group == "relationship_motivation"
    assert "motivation" in state.answered_topics_recent
    assert "relationship_boundary" in state.answered_topics_recent


def test_dialogue_state_rejection_is_detected() -> None:
    state = analyze_dialogue_state(
        recent_turns=[
            {"role": "assistant", "content": "我是夏目蓝。有什么事吗？"},
        ],
        user_message="我不喜欢你这样",
    )

    assert state.current_user_act == "rejection"
    assert state.user_stance == "rejecting"
