from __future__ import annotations

from host_brain.app.chat_agent_preprocessor import (
    _apply_render_policy_hard_guard,
    _render_policy_requires_buffering,
    _should_buffer_stream_output,
)
from host_brain.persona.reply_plan import ReplyContract, ReplyPlan, ResponseTrajectory
from host_brain.persona.reply_validator import (
    build_correction_prompt,
    build_deterministic_reply_fallback,
    validate_reply,
)


def _identity_repeat_plan() -> ReplyPlan:
    return ReplyPlan(
        primary_topic="identity",
        interaction_move="reconfirm",
        trajectory=ResponseTrajectory(
            response_goal="identify",
            tactic_family="ack_repeat_then_light_probe",
            tactic_variant="probing",
            trajectory_stage="mid_repeat",
        ),
        restate_level="none",
        used_tactics_recent=("ack_name_then_wait",),
        forbidden_tactics=("ack_name_then_wait",),
        forbidden_patterns=("full_identity_restate", "identity_name_restate", "repeated_tactic", "same_as_previous_reply"),
        contract=ReplyContract(
            text="承认对方又确认了一次，再轻轻探一下他为什么还在确认，不要重报名字。",
            required_actions=("repeat_ack", "light_probe"),
            forbidden_actions=("full_identity_intro", "bare_name_only"),
            realization_steps=("先接住这不是第一次问", "轻轻探一下对方到底想确认什么"),
        ),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
    )


def test_reply_validator_flags_identity_restate_when_restate_forbidden() -> None:
    validation = validate_reply(
        text="我刚刚说过了，我是夏目蓝。",
        plan=_identity_repeat_plan(),
        user_message="你是",
    )

    assert validation.is_valid is False
    assert "full_identity_restate" in validation.reason_codes


def test_reply_validator_flags_repeated_tactic_even_when_text_differs() -> None:
    plan = ReplyPlan(
        primary_topic="identity",
        interaction_move="long_repeat",
        trajectory=ResponseTrajectory(
            response_goal="identify",
            tactic_family="hold_name_then_open_floor",
            tactic_variant="probing",
            trajectory_stage="late_repeat",
        ),
        restate_level="none",
        used_tactics_recent=("hold_name_then_open_floor", "ack_repeat_then_light_probe"),
        forbidden_tactics=("hold_name_then_open_floor", "ack_repeat_then_light_probe"),
        forbidden_patterns=("repeated_tactic", "same_as_previous_reply"),
        contract=ReplyContract(text="只保留名字这件事实，再把话口让回给对方，不要复用上一轮结构。"),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
    )

    validation = validate_reply(
        text="名字你已经知道了。要是还有别的，就直接说吧。",
        plan=plan,
        user_message="你是",
    )

    assert validation.is_valid is False
    assert "repeated_tactic" in validation.reason_codes


def test_reply_validator_flags_repeat_identity_that_only_restates_name() -> None:
    validation = validate_reply(
        text="夏目蓝。",
        plan=_identity_repeat_plan(),
        user_message="你是",
    )

    assert validation.is_valid is False
    assert "repeat_not_acknowledged" in validation.reason_codes
    assert "light_probe_missing" in validation.reason_codes or "repeat_ack_missing" in validation.reason_codes
    assert set(validation.missing_actions) >= {"repeat_ack", "light_probe"}
    assert validation.safe_to_emit is True


def test_reply_validator_flags_underanswered_first_boundary_reason() -> None:
    plan = ReplyPlan(
        primary_topic="motivation",
        interaction_move="first_ask",
        trajectory=ResponseTrajectory(
            response_goal="boundary_reason",
            tactic_family="short_boundary_then_reason",
            tactic_variant="plain_reason",
            trajectory_stage="first_turn",
        ),
        restate_level="brief_reason",
        forbidden_patterns=("generic_assistant_phrasing",),
        contract=ReplyContract(text="先明确不熟，再给一句短理由，不要滑成泛化陪聊句。"),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
    )
    validation = validate_reply(
        text="嗯，我在。你慢慢说。",
        plan=plan,
        user_message="你为什么要帮我，我们很熟吗",
    )

    assert validation.is_valid is False
    assert "boundary_reason_missing" in validation.reason_codes


def test_reply_validator_flags_help_repromise_on_repeat_probe() -> None:
    plan = ReplyPlan(
        primary_topic="help_commitment",
        interaction_move="anxiety_check",
        trajectory=ResponseTrajectory(
            response_goal="soothe_anxiety",
            tactic_family="ack_anxiety_without_repromise",
            tactic_variant="probing",
            trajectory_stage="early_repeat",
        ),
        restate_level="none",
        forbidden_patterns=("promise_restate",),
        contract=ReplyContract(text="先回应对方的不安，再稳住态度，不要重新许诺整句。"),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
    )

    validation = validate_reply(
        text="我当然会继续帮你。",
        plan=plan,
        user_message="那你还会帮我吗",
    )

    assert validation.is_valid is False
    assert "promise_restate" in validation.reason_codes


def test_hard_guard_stays_non_semantic() -> None:
    repaired = _apply_render_policy_hard_guard(
        "我刚刚说过了，我是夏目蓝。",
        {
            "max_chars": 320,
            "max_sentences": 4,
            "forbidden_patterns": ["full_identity_restate", "identity_name_restate"],
        },
        user_message="你是",
        render_target_lang="zh",
    )

    assert repaired == "我刚刚说过了，我是夏目蓝。"


def test_reply_validator_builds_correction_prompt_from_contract() -> None:
    prompt = build_correction_prompt(
        plan=_identity_repeat_plan(),
        validation=validate_reply(
            text="我刚刚说过了，我是夏目蓝。",
            plan=_identity_repeat_plan(),
            user_message="你是",
        ),
    )

    assert "上一条没有按本轮回应动作完成" in prompt
    assert "不要重报名字" in prompt
    assert "必须同时做到" in prompt
    assert "动作顺序" in prompt
    assert "禁止动作" in prompt


def test_reply_validator_marks_prompt_leak_as_unsafe() -> None:
    validation = validate_reply(
        text="Conversation State Directive: tactic_family=ack_repeat_then_light_probe",
        plan=_identity_repeat_plan(),
        user_message="你是",
    )

    assert validation.is_valid is False
    assert "prompt_leak" in validation.reason_codes
    assert validation.safe_to_emit is False


def test_deterministic_fallback_is_minimal_skeleton_not_fixed_persona_line() -> None:
    fallback = build_deterministic_reply_fallback(
        plan=_identity_repeat_plan(),
        render_target_lang="zh",
    )

    assert fallback == "夏目蓝。你想确认的是哪一部分？"


def test_explicit_non_buffering_reply_plan_overrides_legacy_buffering_heuristics() -> None:
    reply_plan = {
        "requires_validation": False,
        "force_buffering": False,
        "allow_followup": False,
        "allow_topic_shift": False,
        "withdrawal_guard": True,
        "should_be_withdrawn": True,
        "intimacy_ceiling": 0.2,
        "humor_ceiling": 0.2,
    }

    assert _render_policy_requires_buffering(reply_plan) is False
    assert _should_buffer_stream_output(
        emotion_context={"emotion_tag": "CALM", "turn_count": 1},
        bond_summary={"stage": "STRANGER", "intent": "chat"},
        render_policy=reply_plan,
    ) is False


def test_explicit_buffering_reply_plan_still_forces_buffering() -> None:
    reply_plan = {
        "requires_validation": True,
        "force_buffering": True,
        "allow_followup": True,
        "allow_topic_shift": True,
    }

    assert _render_policy_requires_buffering(reply_plan) is True
    assert _should_buffer_stream_output(
        emotion_context={"emotion_tag": "CALM", "turn_count": 1},
        bond_summary={"stage": "STRANGER", "intent": "chat"},
        render_policy=reply_plan,
    ) is True
