from __future__ import annotations

from host_brain.app.chat_agent_preprocessor import (
    _apply_render_policy_hard_guard,
    _render_policy_requires_buffering,
    _should_buffer_stream_output,
)
from host_brain.app.context.soul_runtime import get_compiled_soul
from host_brain.persona.reply_plan import ReplyContract, ReplyPlan, ResponseTrajectory
from host_brain.persona.reply_validator import (
    build_correction_prompt,
    build_semantic_rescue_seed,
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
            text=f"先自然认下“我是{get_compiled_soul().display_name}”，再轻轻接住他为什么又问这个，不要把名字展开成介绍。",
            required_actions=("repeat_ack", "light_probe"),
            forbidden_actions=("full_identity_intro", "bare_name_only"),
            realization_steps=("先接住这不是第一次问", "轻轻探一下对方到底想确认什么"),
        ),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
    )


def test_reply_validator_flags_identity_restate_when_restate_forbidden() -> None:
    validation = validate_reply(
        text=f"我刚刚说过了，我是{get_compiled_soul().display_name}。",
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
        text=f"{get_compiled_soul().display_name}。",
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
        contract=ReplyContract(text="先接住尼尼装生分的试探，再给一句短理由，不要滑成泛化陪聊句。"),
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


def test_reply_validator_accepts_natural_boundary_reason_surface() -> None:
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
        contract=ReplyContract(
            text="像新海天顺嘴解释那样，先接住尼尼装生分这件事，再给一句很日常的理由。"
        ),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
    )
    validation = validate_reply(
        text="别装生分了，尼尼。你把麻烦丢到我面前，我当然会管。",
        plan=plan,
        user_message="你为什么要帮我，我们很熟吗",
    )

    assert "boundary_reason_missing" not in validation.reason_codes
    assert "contract_actions_missing" not in validation.reason_codes


def test_reply_validator_accepts_daily_reason_boundary_surface() -> None:
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
        contract=ReplyContract(
            text="像新海天顺嘴解释那样，先接住尼尼装生分这件事，再给一句很日常的理由。"
        ),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
    )
    validation = validate_reply(
        text="别见外啦，尼尼。你都开口了，我当然会帮。",
        plan=plan,
        user_message="你为什么要帮我，我们很熟吗",
    )

    assert validation.is_valid is True
    assert validation.actual_tactic_family == "short_boundary_then_reason"


def test_semantic_rescue_seed_for_boundary_uses_goal_not_fixed_sentence() -> None:
    plan = ReplyPlan(
        primary_topic="motivation",
        interaction_move="first_ask",
        trajectory=ResponseTrajectory(
            response_goal="boundary_reason",
            tactic_family="short_boundary_then_reason",
            tactic_variant="plain_reason",
            trajectory_stage="first_turn",
        ),
    )

    seed = build_semantic_rescue_seed(plan=plan)

    assert "不会装作没看到" not in seed
    assert "义妹关系事实" in seed or "装生分" in seed


def test_semantic_rescue_seed_for_image_social_followup_deny_is_thread_specific() -> None:
    plan = ReplyPlan(
        primary_topic="emoji_social_followup",
        interaction_move="repair_needed",
        trajectory=ResponseTrajectory(
            response_goal="gentle_followup_reply",
            tactic_family="soft_emotion_ack",
            tactic_variant="backoff_hold",
            trajectory_stage="followup",
        ),
    )

    seed = build_semantic_rescue_seed(plan=plan)

    assert "嘴硬否认" in seed
    assert "台阶" in seed


def test_semantic_rescue_seed_for_image_social_followup_why_is_thread_specific() -> None:
    plan = ReplyPlan(
        primary_topic="emoji_social_followup",
        interaction_move="reconfirm",
        trajectory=ResponseTrajectory(
            response_goal="gentle_followup_reply",
            tactic_family="soft_emotion_ack",
            tactic_variant="explain_read",
            trajectory_stage="followup",
        ),
    )

    seed = build_semantic_rescue_seed(plan=plan)

    assert "很生活的话解释" in seed
    assert "不要重讲图片内容" in seed


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
        f"我刚刚说过了，我是{get_compiled_soul().display_name}。",
        {
            "max_chars": 320,
            "max_sentences": 4,
            "forbidden_patterns": ["full_identity_restate", "identity_name_restate"],
        },
        user_message="你是",
        render_target_lang="zh",
    )

    assert repaired == f"我刚刚说过了，我是{get_compiled_soul().display_name}。"


def test_reply_validator_builds_correction_prompt_from_contract() -> None:
    prompt = build_correction_prompt(
        plan=_identity_repeat_plan(),
        validation=validate_reply(
            text=f"我刚刚说过了，我是{get_compiled_soul().display_name}。",
            plan=_identity_repeat_plan(),
            user_message="你是",
        ),
    )

    assert "上一条没有按本轮回应动作完成" in prompt
    assert get_compiled_soul().display_name in prompt
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


def test_reply_validator_marks_symbol_only_reply_as_unsafe() -> None:
    validation = validate_reply(
        text="（。",
        plan=_identity_repeat_plan(),
        user_message="hello",
    )

    assert validation.is_valid is False
    assert "symbol_only_reply" in validation.reason_codes
    assert validation.safe_to_emit is False


def test_reply_validator_rejects_underanswered_phatic_wave() -> None:
    plan = ReplyPlan(
        primary_topic="greeting",
        interaction_move="light_ack",
        trajectory=ResponseTrajectory(
            response_goal="phatic_reply",
            tactic_family="phatic_wave",
            tactic_variant="greeting",
            trajectory_stage="first_turn",
        ),
        response_form="text",
        secondary_response_form="",
        contract=ReplyContract(
            text="先把这句招呼自然接住，再给一句有实质内容的短回应，不要只剩符号或空壳句。",
            required_actions=("phatic_ack",),
            forbidden_actions=("generic_companioning",),
        ),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
    )

    validation = validate_reply(
        text="（。",
        plan=plan,
        user_message="hello",
    )

    assert validation.is_valid is False
    assert "symbol_only_reply" in validation.reason_codes or "underanswered" in validation.reason_codes


def test_reply_validator_rejects_single_particle_phatic_wave() -> None:
    plan = ReplyPlan(
        primary_topic="greeting",
        interaction_move="light_ack",
        trajectory=ResponseTrajectory(
            response_goal="phatic_reply",
            tactic_family="phatic_wave",
            tactic_variant="greeting",
            trajectory_stage="first_turn",
        ),
        response_form="text",
        secondary_response_form="",
        contract=ReplyContract(
            text="先把这句招呼自然接住，再给一句有实质内容的短回应，不要只剩符号或空壳句。",
            required_actions=("phatic_ack",),
            forbidden_actions=("generic_companioning",),
        ),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
    )

    validation = validate_reply(
        text="呀。",
        plan=plan,
        user_message="hello",
    )

    assert validation.is_valid is False
    assert "underanswered" in validation.reason_codes


def test_reply_validator_rejects_hollow_phatic_even_with_emoji() -> None:
    plan = ReplyPlan(
        primary_topic="greeting",
        interaction_move="light_ack",
        trajectory=ResponseTrajectory(
            response_goal="phatic_reply",
            tactic_family="phatic_wave",
            tactic_variant="greeting",
            trajectory_stage="first_turn",
        ),
        response_form="text",
        contract=ReplyContract(
            text="把招呼自然回应，再回一句短短的在场回应，不要只剩感叹词或符号。",
        ),
        requires_validation=True,
    )

    validation = validate_reply(
        text="哥哥! 👋",
        plan=plan,
        user_message="hello",
    )

    assert validation.is_valid is False
    assert "underanswered" in validation.reason_codes


def test_reply_validator_rejects_meta_phatic_stock_phrase() -> None:
    plan = ReplyPlan(
        primary_topic="greeting",
        interaction_move="light_ack",
        trajectory=ResponseTrajectory(
            response_goal="phatic_reply",
            tactic_family="phatic_wave",
            tactic_variant="greeting",
            trajectory_stage="first_turn",
        ),
        response_form="text",
        contract=ReplyContract(
            text="把招呼自然回应，再回一句短短的在场回应，不要只剩感叹词或符号。",
        ),
        requires_validation=True,
    )

    validation = validate_reply(
        text="我刚刚在叫哥哥你呀！",
        plan=plan,
        user_message="嗯?",
    )

    assert validation.is_valid is False
    assert "low_quality_stock_phrase" in validation.reason_codes


def test_reply_validator_rejects_underanswered_multimodal_answer() -> None:
    plan = ReplyPlan(
        primary_topic="screenshot_analysis",
        interaction_move="probe",
        trajectory=ResponseTrajectory(
            response_goal="multimodal_answer",
            tactic_family="observe_then_answer",
            tactic_variant="screenshot_analysis",
            trajectory_stage="first_turn",
        ),
        response_form="text",
        contract=ReplyContract(
            text="先点到截图里的提示/界面，再给一句简短说明，不要只回“嗯”。",
            required_actions=("visual_reference", "content_clause"),
            forbidden_actions=("generic_companioning",),
        ),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
    )

    validation = validate_reply(
        text="嗯。",
        plan=plan,
        user_message="这是什么",
    )

    assert validation.is_valid is False
    assert "underanswered" in validation.reason_codes
    assert "multimodal_reference_missing" in validation.reason_codes or "contract_actions_missing" in validation.reason_codes


def test_reply_validator_rejects_generic_multimodal_followup_when_core_observation_exists() -> None:
    plan = ReplyPlan(
        primary_topic="literal_description",
        interaction_move="first_ask",
        trajectory=ResponseTrajectory(
            response_goal="multimodal_answer",
            tactic_family="observe_then_answer",
            tactic_variant="literal_description",
            trajectory_stage="first_turn",
        ),
        response_form="text",
        multimodal={
            "core_observation": "一张花的照片；花瓣颜色偏橙红",
            "summary_for_chat": "用户当前更像是在让你先看看图里有什么。当前能确认到的核心内容是：一张花的照片；花瓣颜色偏橙红。",
        },
        contract=ReplyContract(
            text="先点到图里看到的对象，再给一句简短说明，不要只回空壳句。",
            required_actions=("visual_reference", "content_clause"),
            forbidden_actions=("generic_companioning",),
        ),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
    )

    validation = validate_reply(
        text="我看到了这张图。你想让我先说图里有什么吗？",
        plan=plan,
        user_message="[图片消息]",
    )

    assert validation.is_valid is False
    assert "multimodal_content_missing" in validation.reason_codes
    assert "multimodal_generic_followup" in validation.reason_codes


def test_deterministic_fallback_uses_multimodal_core_observation() -> None:
    plan = ReplyPlan(
        primary_topic="literal_description",
        interaction_move="first_ask",
        trajectory=ResponseTrajectory(
            response_goal="multimodal_answer",
            tactic_family="observe_then_answer",
            tactic_variant="literal_description",
            trajectory_stage="first_turn",
        ),
        response_form="text",
        multimodal={
            "core_observation": "一张花的照片；花瓣颜色偏橙红",
        },
        contract=ReplyContract(
            text="先点到图里看到的对象，再给一句简短说明，不要只回空壳句。",
            required_actions=("visual_reference", "content_clause"),
            forbidden_actions=("generic_companioning",),
        ),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
    )

    fallback = build_deterministic_reply_fallback(
        plan=plan,
        render_target_lang="zh",
    )

    assert fallback == ""
    assert "你想让我先说图里有什么吗" not in fallback


def test_deterministic_fallback_is_minimal_skeleton_not_fixed_persona_line() -> None:
    fallback = build_deterministic_reply_fallback(
        plan=_identity_repeat_plan(),
        render_target_lang="zh",
    )

    assert get_compiled_soul().display_name not in fallback
    assert "哪一部分" not in fallback
    assert "真正想问" not in fallback


def test_image_social_reply_requires_validation_and_blocks_underanswered_stub() -> None:
    plan = ReplyPlan(
        primary_topic="emoji_social_reply",
        interaction_move="first_ask",
        trajectory=ResponseTrajectory(
            response_goal="phatic_reply",
            tactic_family="playful_wave",
            tactic_variant="image_social",
            trajectory_stage="first_turn",
        ),
        response_form="text",
        response_form_reason="image_social_reply",
        multimodal={
            "primary_modality": "image",
            "core_observation": "委屈；一个动漫女孩正在哭，表情很委屈。",
        },
        contract=ReplyContract(
            text="先接住这张图里的情绪，再顺着这个情绪回一句，不要解说画面，也不要挂固定开放问句。",
            required_actions=("phatic_ack",),
            forbidden_actions=("generic_companioning",),
        ),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
        force_buffering=True,
    )

    validation = validate_reply(
        text="唔。",
        plan=plan,
        user_message="[图片消息]",
    )

    assert validation.is_valid is False
    assert "underanswered" in validation.reason_codes
    assert "phatic_ack_missing" in validation.reason_codes

    fallback = build_deterministic_reply_fallback(
        plan=plan,
        render_target_lang="zh",
    )

    assert "唔" not in fallback
    assert fallback == ""
    assert "委屈" not in fallback


def test_image_social_reply_rejects_self_as_image_candidate() -> None:
    plan = ReplyPlan(
        primary_topic="emoji_social_reply",
        interaction_move="playful_ping",
        trajectory=ResponseTrajectory(
            response_goal="cute_image_reply",
            tactic_family="cute_image_ack",
            tactic_variant="cute_ping",
            trajectory_stage="first_turn",
        ),
        response_form="text",
        response_form_reason="image_social_reply",
        multimodal={
            "primary_modality": "image",
            "core_observation": "可爱、卖萌；猫耳女孩特写，蓝色眼睛。",
        },
        contract=ReplyContract(
            text="把表情包当成对方在装乖或卖萌，回这个情绪动作。",
            required_actions=("phatic_ack",),
            forbidden_actions=(),
        ),
        requires_validation=True,
        force_buffering=True,
    )

    validation = validate_reply(
        text="哥哥，人家今天也很可爱哦！",
        plan=plan,
        user_message="[图片消息]",
    )

    assert validation.is_valid is False
    assert "image_self_reference" in validation.reason_codes

    punctuated_validation = validate_reply(
        text="哥哥，人家今天：也很可爱哦！",
        plan=plan,
        user_message="[图片消息]",
    )

    assert punctuated_validation.is_valid is False
    assert "image_self_reference" in punctuated_validation.reason_codes

    suffix_validation = validate_reply(
        text="哥哥，上面看我是不是很可爱啊？",
        plan=plan,
        user_message="[图片消息]",
    )

    assert suffix_validation.is_valid is False
    assert "image_self_reference" in suffix_validation.reason_codes

    long_suffix_validation = validate_reply(
        text="哥哥，你看我这小猫耳朵是不是超可爱呀！",
        plan=plan,
        user_message="[图片消息]",
    )

    assert long_suffix_validation.is_valid is False
    assert long_suffix_validation.safe_to_emit is False
    assert "image_self_reference" in long_suffix_validation.reason_codes

    comparative_validation = validate_reply(
        text="哥哥，你又在看我的猫耳啦？是不是觉得我比这只猫猫还要可爱呀？哼，我才不信呢！不过……。",
        plan=plan,
        user_message="[图片消息]",
    )

    assert comparative_validation.is_valid is False
    assert comparative_validation.safe_to_emit is False
    assert "image_self_reference" in comparative_validation.reason_codes
    assert "unfinished_tail" in comparative_validation.reason_codes

    question_back_validation = validate_reply(
        text="哥哥，你是不是觉得这张图里的猫猫很可爱呀？",
        plan=plan,
        user_message="[图片消息]",
    )

    assert question_back_validation.is_valid is False
    assert question_back_validation.safe_to_emit is False
    assert "image_social_question_back" in question_back_validation.reason_codes

    indirect_probe_validation = validate_reply(
        text="哥哥，怎么突然不说话了。",
        plan=plan,
        user_message="[表情消息]",
    )

    assert indirect_probe_validation.is_valid is False
    assert indirect_probe_validation.safe_to_emit is False
    assert "image_social_question_back" in indirect_probe_validation.reason_codes

    mid_sentence_probe_validation = validate_reply(
        text="哎呀，怎么啦，感觉怪怪的。",
        plan=plan,
        user_message="[表情消息]",
    )

    assert mid_sentence_probe_validation.is_valid is False
    assert mid_sentence_probe_validation.safe_to_emit is False
    assert "image_social_question_back" in mid_sentence_probe_validation.reason_codes

    interrogative_frame_validation = validate_reply(
        text="哥哥这是什么表情呀。",
        plan=plan,
        user_message="[表情消息]",
    )

    assert interrogative_frame_validation.is_valid is False
    assert interrogative_frame_validation.safe_to_emit is False
    assert "image_social_question_back" in interrogative_frame_validation.reason_codes

    coaxing_probe_validation = validate_reply(
        text="接住哥哥，别憋着嘛，跟我说说。",
        plan=plan,
        user_message="[表情消息]",
    )

    assert coaxing_probe_validation.is_valid is False
    assert coaxing_probe_validation.safe_to_emit is False
    assert "image_social_question_back" in coaxing_probe_validation.reason_codes
    assert "image_social_problem_frame" in coaxing_probe_validation.reason_codes

    problem_frame_validation = validate_reply(
        text="哥哥这是又被什么事给难住了呀。",
        plan=plan,
        user_message="[表情消息]",
    )

    assert problem_frame_validation.is_valid is False
    assert problem_frame_validation.safe_to_emit is False
    assert "image_social_problem_frame" in problem_frame_validation.reason_codes

    verbal_misframe_validation = validate_reply(
        text="就知道哥哥会这么说。",
        plan=plan,
        user_message="[表情消息]",
    )

    assert verbal_misframe_validation.is_valid is False
    assert verbal_misframe_validation.safe_to_emit is False
    assert "image_social_verbal_misframe" in verbal_misframe_validation.reason_codes

    judgment_frame_validation = validate_reply(
        text="哥哥，你这样子可不太好哦。",
        plan=plan,
        user_message="[表情消息]",
    )

    assert judgment_frame_validation.is_valid is False
    assert judgment_frame_validation.safe_to_emit is False
    assert "image_social_judgment_frame" in judgment_frame_validation.reason_codes

    incomplete_deictic_validation = validate_reply(
        text="哎呀，这是。",
        plan=plan,
        user_message="[表情消息]",
    )

    assert incomplete_deictic_validation.is_valid is False
    assert incomplete_deictic_validation.safe_to_emit is False
    assert "unfinished_tail" in incomplete_deictic_validation.reason_codes

    incomplete_question_validation = validate_reply(
        text="哎呀，哥哥怎么。",
        plan=plan,
        user_message="[表情消息]",
    )

    assert incomplete_question_validation.is_valid is False
    assert incomplete_question_validation.safe_to_emit is False
    assert "unfinished_tail" in incomplete_question_validation.reason_codes

    incomplete_sudden_validation = validate_reply(
        text="哎呀，怎么突然。",
        plan=plan,
        user_message="[表情消息]",
    )

    assert incomplete_sudden_validation.is_valid is False
    assert incomplete_sudden_validation.safe_to_emit is False
    assert "unfinished_tail" in incomplete_sudden_validation.reason_codes

    who_is_this_validation = validate_reply(
        text="哎呀，这是谁。",
        plan=plan,
        user_message="[表情消息]",
    )

    assert who_is_this_validation.is_valid is False
    assert who_is_this_validation.safe_to_emit is False
    assert "image_social_descriptor_focus" in who_is_this_validation.reason_codes

    unsupported_action_validation = validate_reply(
        text="哥哥，我给你买了你爱吃的点心哦。",
        plan=plan,
        user_message="[表情消息]",
    )

    assert unsupported_action_validation.is_valid is False
    assert unsupported_action_validation.safe_to_emit is False
    assert "image_social_unsupported_action" in unsupported_action_validation.reason_codes

    generic_validation = validate_reply(
        text="哥哥，好可爱呀。",
        plan=plan,
        user_message="[图片消息]",
    )

    assert generic_validation.is_valid is False
    assert "image_social_descriptor_focus" in generic_validation.reason_codes

    seen_only_validation = validate_reply(
        text="哥哥，这张我看到了。",
        plan=plan,
        user_message="[图片消息]",
    )

    assert seen_only_validation.is_valid is False
    assert "image_social_emotion_missing" in seen_only_validation.reason_codes

    descriptive_suffix_validation = validate_reply(
        text="这张看我可爱的样子，猫耳都竖起来了。",
        plan=plan,
        user_message="[图片消息]",
    )

    assert descriptive_suffix_validation.is_valid is False
    assert "image_self_reference" in descriptive_suffix_validation.reason_codes

    overplay_validation = validate_reply(
        text="哎呀，哥哥，是想让我变成猫猫精赖在你身边不走吗？",
        plan=plan,
        user_message="[图片消息]",
    )

    assert overplay_validation.is_valid is False
    assert overplay_validation.safe_to_emit is False
    assert "image_social_overplayed" in overplay_validation.reason_codes

    degree_suffix_validation = validate_reply(
        text="看我今天可爱死了吧。",
        plan=plan,
        user_message="[图片消息]",
    )

    assert degree_suffix_validation.is_valid is False
    assert "image_self_reference" in degree_suffix_validation.reason_codes


def test_image_social_reply_allows_emotion_response_not_image_opinion() -> None:
    plan = ReplyPlan(
        primary_topic="emoji_social_reply",
        interaction_move="playful_ping",
        trajectory=ResponseTrajectory(
            response_goal="cute_image_reply",
            tactic_family="cute_image_ack",
            tactic_variant="cute_ping",
            trajectory_stage="first_turn",
        ),
        response_form="text",
        response_form_reason="image_social_reply",
        multimodal={
            "primary_modality": "image",
            "core_observation": "可爱、卖萌；猫耳女孩特写，蓝色眼睛。",
        },
        contract=ReplyContract(
            text="把表情包当成对方在装乖或卖萌，回这个情绪动作。",
            required_actions=("phatic_ack", "image_social_emotion_response"),
            forbidden_actions=(),
        ),
        requires_validation=True,
        force_buffering=True,
    )

    image_opinion_validation = validate_reply(
        text="哥哥，我也觉得这张猫耳很可爱，轻轻戳到了。",
        plan=plan,
        user_message="[图片消息]",
    )

    assert image_opinion_validation.is_valid is False
    assert image_opinion_validation.safe_to_emit is False
    assert "image_social_descriptor_focus" in image_opinion_validation.reason_codes

    familiar_image_validation = validate_reply(
        text="嗯？这张图......好像有点熟悉，又好像在哪儿见过似的。",
        plan=plan,
        user_message="[图片消息]",
    )

    assert familiar_image_validation.is_valid is False
    assert familiar_image_validation.safe_to_emit is False
    assert "image_social_descriptor_focus" in familiar_image_validation.reason_codes

    pun_validation = validate_reply(
        text="哥哥，我也萌生了想捏这张猫耳的冲动。",
        plan=plan,
        user_message="[图片消息]",
    )

    assert pun_validation.is_valid is False
    assert "image_social_descriptor_focus" in pun_validation.reason_codes

    emotion_validation = validate_reply(
        text="哥哥又开始装乖了，我接住还不行嘛。",
        plan=plan,
        user_message="[图片消息]",
    )

    assert "image_social_question_back" in emotion_validation.reason_codes

    safe_emotion_validation = validate_reply(
        text="哥哥又开始装乖了，这招太会了。",
        plan=plan,
        user_message="[图片消息]",
    )

    assert safe_emotion_validation.reason_codes == ()
    assert safe_emotion_validation.is_valid is True


def test_assistant_reply_followup_blocks_underanswered_stub() -> None:
    plan = ReplyPlan(
        primary_topic="assistant_reply_followup",
        interaction_move="clarify_reference",
        trajectory=ResponseTrajectory(
            response_goal="clarify_previous_reply",
            tactic_family="explain_previous_reply",
            tactic_variant="clarify_object",
            trajectory_stage="followup",
        ),
        response_form="text",
        interaction_thread={
            "thread_kind": "assistant_reply_followup",
            "source_topic": "assistant_reply",
            "source_excerpt": "知道。",
            "source_context_excerpt": "你知道吗",
            "open": True,
            "last_user_reaction": "ask_object",
        },
        contract=ReplyContract(
            text="上一条助手回复前接的用户话是“你知道吗”。上一条助手回复是“知道。”。先接住对方是在追问上一句，再把上一句具体指代或意思补清楚。",
            required_actions=("previous_reference", "content_clause"),
            forbidden_actions=("generic_companioning",),
        ),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
        force_buffering=True,
    )

    validation = validate_reply(
        text="唔。",
        plan=plan,
        user_message="知道什么",
        previous_assistant_text="知道。",
    )

    assert validation.is_valid is False
    assert validation.should_retry is False
    assert "previous_reply_underanswered" in validation.reason_codes
    assert "previous_reference_missing" in validation.reason_codes

    fallback = build_deterministic_reply_fallback(plan=plan, render_target_lang="zh")
    assert fallback != "我知道了。"
    assert fallback == ""
    assert "你知道吗" not in fallback
    assert "知道。" not in fallback

    rescue_seed = build_semantic_rescue_seed(plan=plan)
    assert "你知道吗" in rescue_seed
    assert "知道。" in rescue_seed


def test_image_social_cute_fallback_uses_cute_branch_instead_of_generic_attachment_line() -> None:
    plan = ReplyPlan(
        primary_topic="emoji_social_reply",
        interaction_move="playful_ping",
        trajectory=ResponseTrajectory(
            response_goal="cute_image_reply",
            tactic_family="cute_image_ack",
            tactic_variant="cute_ping",
            trajectory_stage="first_turn",
        ),
        response_form="text",
        response_form_reason="image_social_reply",
        multimodal={
            "primary_modality": "image",
            "emotion_signals": ["可爱、卖萌"],
            "core_observation": "可爱、卖萌；一张猫耳女孩的可爱表情图。",
        },
        contract=ReplyContract(
            text="先接住这张图里的可爱和卖萌感，再顺着这个感觉回一句，不要解说画面。",
            required_actions=("phatic_ack",),
            forbidden_actions=("generic_companioning",),
        ),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
        force_buffering=True,
    )

    fallback = build_deterministic_reply_fallback(
        plan=plan,
        render_target_lang="zh",
    )

    assert "突然发这么一张图" not in fallback
    assert fallback == ""
    assert not any(token in fallback for token in ("哄你", "撒娇"))


def test_image_social_followup_deny_fallback_no_longer_collapses_to_hum_or_nothing() -> None:
    plan = ReplyPlan(
        primary_topic="emoji_social_followup",
        interaction_move="repair_needed",
        trajectory=ResponseTrajectory(
            response_goal="gentle_followup_reply",
            tactic_family="soft_emotion_ack",
            tactic_variant="backoff_hold",
            trajectory_stage="followup",
        ),
        response_form="text",
        response_form_reason="image_social_followup",
        contract=ReplyContract(
            text="先接住这是上一张图后的跟话，再顺着刚才那层情绪回一句。",
            required_actions=("phatic_ack",),
            forbidden_actions=("generic_companioning",),
        ),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
        force_buffering=True,
    )

    fallback = build_deterministic_reply_fallback(plan=plan, render_target_lang="zh")

    assert fallback not in {"哼。", "没什么。", "唔。"}
    assert "多想" not in fallback
    assert fallback == ""


def test_image_social_followup_why_fallback_explains_previous_read_instead_of_plain_hold() -> None:
    plan = ReplyPlan(
        primary_topic="emoji_social_followup",
        interaction_move="reconfirm",
        trajectory=ResponseTrajectory(
            response_goal="gentle_followup_reply",
            tactic_family="soft_emotion_ack",
            tactic_variant="explain_read",
            trajectory_stage="followup",
        ),
        response_form="text",
        response_form_reason="image_social_followup",
        contract=ReplyContract(
            text="先接住这是上一张图后的跟话，再顺着刚才那层情绪解释一句。",
            required_actions=("phatic_ack",),
            forbidden_actions=("generic_companioning",),
        ),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
        force_buffering=True,
    )

    fallback = build_deterministic_reply_fallback(plan=plan, render_target_lang="zh")

    assert fallback not in {"哼。", "没什么。", "唔。"}
    assert "刚才那张图" not in fallback
    assert fallback == ""


def test_deterministic_reply_fallback_prefers_cached_surface_text(monkeypatch) -> None:
    plan = ReplyPlan(
        primary_topic="emoji_social_followup",
        interaction_move="repair_needed",
        trajectory=ResponseTrajectory(
            response_goal="gentle_followup_reply",
            tactic_family="soft_emotion_ack",
            tactic_variant="backoff_hold",
            trajectory_stage="followup",
        ),
        response_form="text",
        response_form_reason="image_social_followup",
        contract=ReplyContract(
            text="先接住这是上一张图后的跟话，再顺着刚才那层情绪回一句。",
            required_actions=("phatic_ack",),
            forbidden_actions=("generic_companioning",),
        ),
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        requires_validation=True,
        force_buffering=True,
    )

    class _MemoryStoreStub:
        def sample(self, *, key: str, exclude=None) -> str:  # noqa: ANN001
            _ = key, exclude
            return "哥哥，好嘛，那我就当自己刚才多想了一点。"

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_reply_surface_memory_store",
        lambda: _MemoryStoreStub(),
    )

    fallback = build_deterministic_reply_fallback(plan=plan, render_target_lang="zh")

    assert fallback != "哥哥，好嘛，那我就当自己刚才多想了一点。"
    assert fallback == ""


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


def test_social_topic_requires_validation_still_buffers() -> None:
    reply_plan = {
        "primary_topic": "assistant_reply_followup",
        "requires_validation": True,
        "force_buffering": False,
        "allow_followup": True,
        "allow_topic_shift": True,
    }

    assert _should_buffer_stream_output(
        emotion_context={"emotion_tag": "CALM", "turn_count": 1},
        bond_summary={"stage": "FRIEND", "intent": "chat"},
        render_policy=reply_plan,
    ) is True
