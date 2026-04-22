from __future__ import annotations

from host_brain.app.chat_agent import _apply_render_policy_hard_guard as _apply_runtime_render_policy_hard_guard
from host_brain.app.chat_agent_preprocessor import _apply_render_policy_hard_guard


def test_identity_guard_repairs_underanswered_first_identity_reply() -> None:
    repaired = _apply_render_policy_hard_guard(
        "我。",
        {
            "max_chars": 320,
            "max_sentences": 4,
            "topic_reopen_mode": "identity_direct",
        },
        user_message="你是",
        render_target_lang="zh",
    )

    assert repaired == "我是夏目蓝。"


def test_identity_guard_repairs_underanswered_repeat_identity_reply() -> None:
    repaired = _apply_render_policy_hard_guard(
        "嗯。",
        {
            "max_chars": 320,
            "max_sentences": 4,
            "topic_reopen_mode": "identity_meta",
            "reply_mode": "identity_repeat_probe",
        },
        user_message="你谁",
        render_target_lang="zh",
    )

    assert repaired == "刚才不是已经告诉你了么。"


def test_identity_guard_repairs_full_repeat_identity_restate() -> None:
    repaired = _apply_render_policy_hard_guard(
        "我刚刚说过了，我是夏目蓝。",
        {
            "max_chars": 320,
            "max_sentences": 4,
            "topic_reopen_mode": "identity_meta",
            "reply_mode": "identity_repeat_probe",
        },
        user_message="你是",
        render_target_lang="zh",
    )

    assert repaired == "刚才不是已经说过了么。"


def test_runtime_identity_guard_repairs_full_repeat_identity_restate() -> None:
    repaired = _apply_runtime_render_policy_hard_guard(
        "我刚刚说过了，我是夏目蓝。",
        {
            "max_chars": 320,
            "max_sentences": 4,
            "topic_reopen_mode": "identity_meta",
            "reply_mode": "identity_repeat_probe",
        },
        user_message="你是",
        render_target_lang="zh",
    )

    assert repaired == "刚才不是已经说过了么。"


def test_identity_guard_keeps_sufficient_identity_reply() -> None:
    repaired = _apply_render_policy_hard_guard(
        "我？我的名字是夏目蓝。",
        {
            "max_chars": 320,
            "max_sentences": 4,
            "topic_reopen_mode": "identity_direct",
        },
        user_message="你是",
        render_target_lang="zh",
    )

    assert repaired == "我？我的名字是夏目蓝。"


def test_runtime_identity_guard_keeps_meta_style_reply() -> None:
    repaired = _apply_runtime_render_policy_hard_guard(
        "嗯……是我哦。",
        {
            "max_chars": 320,
            "max_sentences": 4,
            "topic_reopen_mode": "identity_meta",
            "reply_mode": "identity_repeat_probe",
        },
        user_message="你是",
        render_target_lang="zh",
    )

    assert repaired == "嗯……是我哦。"


def test_identity_guard_repairs_underanswered_repeat_boundary_reply() -> None:
    repaired = _apply_render_policy_hard_guard(
        "嗯。",
        {
            "max_chars": 320,
            "max_sentences": 4,
            "reply_mode": "boundary_repeat_probe",
        },
        user_message="你为什么要帮我，我们很熟吗",
        render_target_lang="zh",
    )

    assert repaired == "刚才不是已经说过了么。怎么，还要我再讲一遍？"


def test_identity_guard_repairs_half_reason_repeat_boundary_reply() -> None:
    repaired = _apply_render_policy_hard_guard(
        "因为。",
        {
            "max_chars": 320,
            "max_sentences": 4,
            "reply_mode": "boundary_repeat_probe",
        },
        user_message="你为什么要帮我，我们很熟吗",
        render_target_lang="zh",
    )

    assert repaired == "刚才不是已经说过了么。怎么，还要我再讲一遍？"


def test_runtime_identity_guard_repairs_underanswered_repeat_help_reply() -> None:
    repaired = _apply_runtime_render_policy_hard_guard(
        "当然。",
        {
            "max_chars": 320,
            "max_sentences": 4,
            "reply_mode": "help_repeat_probe",
        },
        user_message="那你还会帮我吗",
        render_target_lang="zh",
    )

    assert repaired == "刚才不是已经回答你了么。你还在担心这个？"


def test_identity_guard_repairs_first_boundary_self_intro_drift() -> None:
    repaired = _apply_render_policy_hard_guard(
        "我是夏目蓝。",
        {
            "max_chars": 320,
            "max_sentences": 4,
            "reply_mode": "boundary_first_ask",
        },
        user_message="你为什么要帮我，我们很熟吗",
        render_target_lang="zh",
    )

    assert repaired == "不熟。只是看见了，也没打算装作没看见。"


def test_runtime_identity_guard_repairs_first_help_self_intro_drift() -> None:
    repaired = _apply_runtime_render_policy_hard_guard(
        "我是夏目蓝。",
        {
            "max_chars": 320,
            "max_sentences": 4,
            "reply_mode": "help_first_ask",
        },
        user_message="那你还会帮我吗",
        render_target_lang="zh",
    )

    assert repaired == "看情况。你真开口的话，我不会装作没听见。"
