from __future__ import annotations

from types import SimpleNamespace

from host_brain.app.context.affect_state import AffectStateBlock
from host_brain.app.context.conversation_interpretation import ConversationInterpretation
from host_brain.app.context.drive_state import DriveStateBlock
from host_brain.app.context.interaction_thread import InteractionThreadState
from host_brain.app.context.relationship_state import RelationshipStateBlock
from host_brain.persona.reply_plan import (
    ReplyContract,
    ResponseTrajectory,
    _build_reply_contract,
    _build_boundary_trajectory,
    _build_help_trajectory,
    _build_identity_trajectory,
    _build_topic_trajectory_options,
    _build_thread_fallback_plan_components,
    _decide_response_form,
    _prioritize_trajectory_options,
    _qualitative_affect_summary,
    _qualitative_relationship_summary,
    _refine_multimodal_contract,
    _select_trajectory_option,
    _should_force_buffering,
    _should_require_strict_validation,
    _trajectory_stage,
    build_reply_plan,
    build_reply_plan_candidates,
    build_reply_plan_from_inputs,
    coerce_reply_plan,
)
from host_brain.app.context.tactic_ledger import TacticLedgerBlock
from host_brain.app.context.topic_ledger import TopicLedgerBlock


def test_coerce_reply_plan_reads_nested_contract_and_trajectory() -> None:
    plan = coerce_reply_plan(
        {
            "primary_topic": "identity",
            "interaction_move": "reconfirm",
            "trajectory": {
                "response_goal": "identify",
                "tactic_family": "ack_repeat_then_light_probe",
                "tactic_variant": "confirm_part",
                "trajectory_stage": "mid_repeat",
            },
            "contract": {
                "text": "短短接住，不要整段重报名字。",
                "required_actions": ["name_only"],
                "forbidden_actions": ["full_identity_restate"],
                "realization_steps": ["ack_repeat", "light_probe"],
            },
            "response_form": "text_plus_sticker",
            "secondary_response_form": "sticker",
            "interaction_thread": {"thread_kind": "identity_loop"},
            "multimodal": {"primary_modality": "emoji"},
            "reason_tags": ["recent_identity_loop"],
        }
    )

    assert plan.primary_topic == "identity"
    assert plan.response_goal == "identify"
    assert plan.tactic_family == "ack_repeat_then_light_probe"
    assert plan.tactic_variant == "confirm_part"
    assert plan.trajectory_stage == "mid_repeat"
    assert plan.response_form == "text_plus_sticker"
    assert plan.secondary_response_form == "sticker"
    assert plan.contract == ReplyContract(
        text="短短接住，不要整段重报名字。",
        required_actions=("name_only",),
        forbidden_actions=("full_identity_restate",),
        realization_steps=("ack_repeat", "light_probe"),
    )
    assert plan.interaction_thread["thread_kind"] == "identity_loop"
    assert plan.multimodal["primary_modality"] == "emoji"


def test_coerce_reply_plan_clears_removed_emoji_output_aliases() -> None:
    plan = coerce_reply_plan(
        {
            "response_form": "text_plus_emoji",
            "secondary_response_form": "emoji",
        }
    )

    assert plan.response_form == "text"
    assert plan.secondary_response_form == ""


def test_qualitative_affect_summary_covers_zh_and_ja_branches() -> None:
    withdrawn = AffectStateBlock(withdraw_bias=0.6, warmth_bias=0.1)
    warm = AffectStateBlock(withdraw_bias=0.1, warmth_bias=0.6)
    neutral = AffectStateBlock(withdraw_bias=0.1, warmth_bias=0.2)

    assert _qualitative_affect_summary(affect=withdrawn, render_target_lang="zh") == "当前偏收着，别把语气抬得太热。"
    assert _qualitative_affect_summary(affect=warm, render_target_lang="zh") == "当前略柔和，可以温一点，但别说近。"
    assert _qualitative_affect_summary(affect=withdrawn, render_target_lang="ja") == "今は少し引き気味で、熱を上げすぎない。"
    assert _qualitative_affect_summary(affect=warm, render_target_lang="ja") == "今はやや柔らかめだが、踏み込みすぎない。"
    assert _qualitative_affect_summary(affect=neutral, render_target_lang="ja") == "今は落ち着いていて、静かに返す。"


def test_qualitative_relationship_summary_softens_micro_social_and_respects_stage() -> None:
    interpretation = ConversationInterpretation(literal_topic="emoji_social_followup")
    thread = InteractionThreadState(thread_kind="image_social", open=True)
    familiar = RelationshipStateBlock(bond_stage="FRIEND", boundary_softness=0.1)
    friend = RelationshipStateBlock(bond_stage="FRIEND", boundary_softness=0.6)

    assert (
        _qualitative_relationship_summary(
            relationship=familiar,
            interpretation=interpretation,
            interaction_thread=thread,
            render_target_lang="zh",
        )
        == "关系是熟起来的义兄妹：不用刻意拉开，轻轻回应眼前情绪。"
    )
    assert (
        _qualitative_relationship_summary(
            relationship=familiar,
            interpretation=ConversationInterpretation(literal_topic="motivation"),
            interaction_thread=InteractionThreadState(),
            render_target_lang="ja",
        )
        == "義兄妹としてかなり馴染んでいる。軽く返していいが、言い過ぎない。"
    )
    assert (
        _qualitative_relationship_summary(
            relationship=RelationshipStateBlock(bond_stage="CLOSE_FRIEND", boundary_softness=0.2),
            interpretation=ConversationInterpretation(literal_topic="greeting"),
            interaction_thread=InteractionThreadState(),
            render_target_lang="ja",
        )
        == "義兄妹として馴染んでいる。距離を作りすぎず、目の前の感情へ自然に返す。"
    )
    assert (
        _qualitative_relationship_summary(
            relationship=friend,
            interpretation=ConversationInterpretation(literal_topic="greeting"),
            interaction_thread=InteractionThreadState(),
            render_target_lang="zh",
        )
        == "关系是熟起来的义兄妹：不用刻意拉开，轻轻回应眼前情绪。"
    )
    assert (
        _qualitative_relationship_summary(
            relationship=RelationshipStateBlock(bond_stage="FRIEND", boundary_softness=0.2),
            interpretation=ConversationInterpretation(literal_topic="mood_reflection"),
            interaction_thread=InteractionThreadState(),
            render_target_lang="ja",
        )
        == "義兄妹としてかなり馴染んでいる。軽く返していいが、言い過ぎない。"
    )


def test_qualitative_relationship_summary_uses_vector_substage_not_only_stage() -> None:
    ambiguous = RelationshipStateBlock(
        bond_stage="CLOSE_FRIEND",
        familiarity=0.70,
        trust=0.62,
        romantic_tension=0.52,
        attachment_depth=0.58,
        relationship_substage="ambiguous_affection",
    )
    repairing = RelationshipStateBlock(
        bond_stage="CONFIDANT",
        conflict_rift=0.62,
        relationship_substage="repairing_rift",
    )

    assert "暧昧张力" in _qualitative_relationship_summary(
        relationship=ambiguous,
        interpretation=ConversationInterpretation(literal_topic="mood_reflection"),
        interaction_thread=InteractionThreadState(),
        render_target_lang="zh",
    )
    assert "刺しすぎず" in _qualitative_relationship_summary(
        relationship=repairing,
        interpretation=ConversationInterpretation(literal_topic="mood_reflection"),
        interaction_thread=InteractionThreadState(),
        render_target_lang="ja",
    )


def test_decide_response_form_covers_social_and_default_modes() -> None:
    familiar = RelationshipStateBlock(bond_stage="FRIEND")

    assert _decide_response_form(
        interpretation=ConversationInterpretation(literal_topic="emoji_social_followup"),
        relationship_state=familiar,
        multimodal={"primary_modality": "image"},
    ) == ("text", "", "image_social_followup", False)
    assert _decide_response_form(
        interpretation=ConversationInterpretation(literal_topic="emoji_social_reply"),
        relationship_state=familiar,
        multimodal={"primary_modality": "emoji"},
    ) == ("sticker", "", "social_reaction", True)
    assert _decide_response_form(
        interpretation=ConversationInterpretation(literal_topic="attachment_only_no_question"),
        relationship_state=familiar,
        multimodal={"primary_modality": "emoji"},
    ) == ("sticker", "", "social_reaction", True)
    assert _decide_response_form(
        interpretation=ConversationInterpretation(literal_topic="mood_reflection"),
        relationship_state=familiar,
        multimodal=None,
    ) == ("text", "", "default_text", False)


def test_validation_and_buffering_policy_respects_low_risk_and_late_repeat() -> None:
    low_risk = ConversationInterpretation(
        literal_topic="emoji_social_followup",
        interaction_move="repair_needed",
        freshness="recent_repeat",
        user_stance="neutral",
        repeat_question_count=1,
    )
    challenge = ConversationInterpretation(
        literal_topic="identity",
        interaction_move="reconfirm",
        freshness="recent_repeat",
        user_stance="challenging",
        repeat_question_count=3,
    )
    phatic = ResponseTrajectory(
        response_goal="phatic_reply",
        tactic_family="phatic_wave",
        tactic_variant="greeting",
        trajectory_stage="first_turn",
    )
    late = ResponseTrajectory(
        response_goal="identify",
        tactic_family="ack_repeat_then_light_probe",
        tactic_variant="confirm_real_question",
        trajectory_stage="late_repeat",
    )

    assert _should_require_strict_validation(topic="emoji_social_reply", interpretation=low_risk, trajectory=phatic) is False
    assert _should_require_strict_validation(topic="identity", interpretation=challenge, trajectory=late) is True
    assert _should_force_buffering(topic="emoji_social_followup", interpretation=low_risk, trajectory=late, requires_validation=True) is True
    assert _should_force_buffering(topic="greeting", interpretation=ConversationInterpretation(literal_topic="greeting"), trajectory=phatic, requires_validation=True) is False


def test_trajectory_selection_and_priority_cover_recent_filters() -> None:
    ranked = _prioritize_trajectory_options(
        topic="identity",
        candidates=[
            ("ack_repeat_then_light_probe", "confirm_part"),
            ("ack_repeat_then_light_probe", "confirm_part"),
            ("hold_name_then_open_floor", "open_real_question"),
            ("soft_close_then_open_floor", "settle_real_question"),
        ],
        used_recent_families=("ack_repeat_then_light_probe",),
        used_recent_keys=("hold_name_then_open_floor:open_real_question",),
        used_recent_signatures=("identity_settle_real_question",),
    )
    assert ranked == [
        ("ack_repeat_then_light_probe", "confirm_part"),
        ("hold_name_then_open_floor", "open_real_question"),
        ("soft_close_then_open_floor", "settle_real_question"),
    ]
    assert _select_trajectory_option(
        topic="identity",
        candidates=[],
        used_recent_families=(),
        used_recent_keys=(),
        used_recent_signatures=(),
    ) == ("plain_hold", "plain")


def test_build_reply_contract_covers_zh_specific_and_ja_mapping() -> None:
    zh_identity = _build_reply_contract(
        trajectory=ResponseTrajectory(
            response_goal="identify",
            tactic_family="brief_name_only",
            tactic_variant="plain_name",
            trajectory_stage="first_turn",
        ),
        render_target_lang="zh",
    )
    ja_social = _build_reply_contract(
        trajectory=ResponseTrajectory(
            response_goal="gentle_image_reply",
            tactic_family="soft_emotion_ack",
            tactic_variant="distress_image",
            trajectory_stage="first_turn",
        ),
        render_target_lang="ja",
    )

    assert "不要展开设定" in zh_identity.text or "不要继续解释" in zh_identity.text
    assert "画像から伝わる感情を受けて" in ja_social.text


def test_refine_multimodal_contract_covers_image_reply_followup_attachment_and_japanese_passthrough() -> None:
    base = ReplyContract(
        text="先接住。",
        required_actions=("ack",),
        forbidden_actions=("generic_assistant_phrasing",),
        realization_steps=("先接住",),
    )
    image_reply = _refine_multimodal_contract(
        contract=base,
        topic="emoji_social_reply",
        multimodal={"primary_modality": "image"},
        trajectory=ResponseTrajectory(
            response_goal="gentle_image_reply",
            tactic_family="soft_emotion_ack",
            tactic_variant="distress_image",
            trajectory_stage="first_turn",
        ),
        render_target_lang="zh",
    )
    followup = _refine_multimodal_contract(
        contract=base,
        topic="emoji_social_followup",
        multimodal={"primary_modality": "image"},
        trajectory=ResponseTrajectory(
            response_goal="gentle_followup_reply",
            tactic_family="tease_image_ack",
            tactic_variant="light_explain",
            trajectory_stage="followup",
        ),
        render_target_lang="zh",
    )
    attachment = _refine_multimodal_contract(
        contract=base,
        topic="attachment_only_no_question",
        multimodal={"primary_modality": "image"},
        trajectory=ResponseTrajectory(),
        render_target_lang="zh",
    )
    ja_passthrough = _refine_multimodal_contract(
        contract=base,
        topic="emoji_social_reply",
        multimodal={"primary_modality": "image"},
        trajectory=ResponseTrajectory(),
        render_target_lang="ja",
    )

    assert "不要描述外貌" in image_reply.text
    assert "为什么会那样想" in followup.text
    assert "不要主动开始分析画面" in attachment.text
    assert ja_passthrough == base


def test_build_thread_fallback_plan_components_covers_all_thread_kinds() -> None:
    interpretation = ConversationInterpretation(freshness="recent_repeat", repeat_question_count=2)

    image_social = _build_thread_fallback_plan_components(
        interpretation=interpretation,
        interaction_thread=InteractionThreadState(thread_kind="image_social", open=True, last_user_reaction="why"),
    )
    identity = _build_thread_fallback_plan_components(
        interpretation=interpretation,
        interaction_thread=InteractionThreadState(thread_kind="identity_loop", open=True),
    )
    boundary = _build_thread_fallback_plan_components(
        interpretation=interpretation,
        interaction_thread=InteractionThreadState(thread_kind="boundary_probe", open=True),
    )
    help_thread = _build_thread_fallback_plan_components(
        interpretation=interpretation,
        interaction_thread=InteractionThreadState(thread_kind="help_reassurance", open=True),
    )
    phatic = _build_thread_fallback_plan_components(
        interpretation=interpretation,
        interaction_thread=InteractionThreadState(thread_kind="phatic", source_topic="greeting", open=True),
    )
    nothing_reaction = _build_thread_fallback_plan_components(
        interpretation=interpretation,
        interaction_thread=InteractionThreadState(thread_kind="image_social", open=True, last_user_reaction="nothing"),
    )

    assert image_social is not None and image_social[1].tactic_variant == "explain_read"
    assert identity is not None and identity[0] == "identity"
    assert boundary is not None and boundary[0] == "motivation"
    assert help_thread is not None and help_thread[0] == "help_commitment"
    assert phatic is None
    assert nothing_reaction is not None and nothing_reaction[1].tactic_variant == "let_it_pass"
    assert _build_thread_fallback_plan_components(
        interpretation=interpretation,
        interaction_thread=InteractionThreadState(thread_kind="phatic", open=False),
    ) is None


def test_identity_boundary_and_help_trajectory_helpers_cover_new_and_repeat_paths() -> None:
    fresh = ConversationInterpretation(freshness="new", repeat_question_count=0, user_stance="neutral")
    challenging = ConversationInterpretation(freshness="recent_repeat", repeat_question_count=2, user_stance="challenging")
    late = ConversationInterpretation(freshness="recent_repeat", repeat_question_count=3, user_stance="neutral")

    identity_new = _build_identity_trajectory(
        interpretation=fresh,
        used_recent_families=(),
        used_recent_keys=(),
        used_recent_signatures=(),
    )
    identity_challenging = _build_identity_trajectory(
        interpretation=challenging,
        used_recent_families=(),
        used_recent_keys=(),
        used_recent_signatures=(),
    )
    boundary_new = _build_boundary_trajectory(
        interpretation=fresh,
        used_recent_families=(),
        used_recent_keys=(),
        used_recent_signatures=(),
    )
    boundary_late = _build_boundary_trajectory(
        interpretation=late,
        used_recent_families=(),
        used_recent_keys=(),
        used_recent_signatures=(),
    )
    help_new = _build_help_trajectory(
        interpretation=fresh,
        used_recent_families=(),
        used_recent_keys=(),
        used_recent_signatures=(),
    )
    help_late = _build_help_trajectory(
        interpretation=late,
        used_recent_families=(),
        used_recent_keys=(),
        used_recent_signatures=(),
    )
    identity_late = _build_identity_trajectory(
        interpretation=late,
        used_recent_families=(),
        used_recent_keys=(),
        used_recent_signatures=(),
    )

    assert identity_new[0] == "brief_name_only"
    assert identity_challenging[2].trajectory_stage == "mid_repeat"
    assert identity_late[2].trajectory_stage == "late_repeat"
    assert boundary_new[0] == "short_boundary_then_reason"
    assert boundary_late[2].trajectory_stage == "late_repeat"
    assert help_new[0] == "short_conditional_commitment"
    assert help_late[2].trajectory_stage == "late_repeat"


def test_trajectory_stage_handles_reopen_and_repeat_counts() -> None:
    assert _trajectory_stage(freshness="new", repeat_question_count=0) == "first_turn"
    assert _trajectory_stage(freshness="recent_repeat", repeat_question_count=2) == "mid_repeat"
    assert _trajectory_stage(freshness="recent_repeat", repeat_question_count=3) == "late_repeat"
    assert _trajectory_stage(freshness="long_gap_reopen", repeat_question_count=1) == "reopen"


def test_build_topic_trajectory_options_cover_multimodal_boundary_and_help_variants() -> None:
    interpretation = ConversationInterpretation(
        literal_topic="emoji_social_followup",
        freshness="recent_repeat",
        repeat_question_count=2,
        user_stance="challenging",
        reason_tags=("image_social_followup:why",),
    )
    thread = InteractionThreadState(thread_kind="image_social", open=True, last_user_reaction="why")
    followup_options = _build_topic_trajectory_options(
        topic="emoji_social_followup",
        interpretation=interpretation,
        interaction_thread=thread,
        multimodal={"primary_modality": "image", "emotion_signals": ["委屈"]},
        used_recent_families=(),
        used_recent_keys=(),
        used_recent_signatures=(),
    )
    image_reply_options = _build_topic_trajectory_options(
        topic="emoji_social_reply",
        interpretation=ConversationInterpretation(literal_topic="emoji_social_reply"),
        interaction_thread=InteractionThreadState(),
        multimodal={"primary_modality": "image", "emotion_signals": ["可爱", "猫耳"]},
        used_recent_families=(),
        used_recent_keys=(),
        used_recent_signatures=(),
    )
    attachment_options = _build_topic_trajectory_options(
        topic="attachment_only_no_question",
        interpretation=ConversationInterpretation(literal_topic="attachment_only_no_question"),
        interaction_thread=InteractionThreadState(),
        multimodal={"primary_modality": "image"},
        used_recent_families=(),
        used_recent_keys=(),
        used_recent_signatures=(),
    )
    boundary_options = _build_topic_trajectory_options(
        topic="motivation",
        interpretation=ConversationInterpretation(literal_topic="motivation", freshness="recent_repeat", repeat_question_count=3),
        interaction_thread=InteractionThreadState(),
        multimodal=None,
        used_recent_families=(),
        used_recent_keys=(),
        used_recent_signatures=(),
    )
    help_options = _build_topic_trajectory_options(
        topic="help_commitment",
        interpretation=ConversationInterpretation(literal_topic="help_commitment", freshness="recent_repeat", repeat_question_count=3),
        interaction_thread=InteractionThreadState(),
        multimodal=None,
        used_recent_families=(),
        used_recent_keys=(),
        used_recent_signatures=(),
    )

    assert followup_options[0][2].response_goal == "gentle_followup_reply"
    assert followup_options[0][2].tactic_variant == "explain_read"
    assert image_reply_options[0][2].response_goal in {"gentle_image_reply", "cute_image_reply", "playful_image_reply"}
    assert attachment_options[0][0] == "attachment_ack_then_wait"
    assert boundary_options[0][2].response_goal == "boundary_reason"
    assert help_options[0][2].response_goal == "soothe_anxiety"


def test_build_reply_plan_from_inputs_refines_multimodal_contracts_and_japanese_relationship_copy() -> None:
    image_plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "FRIEND", "score": 0.12},
        conversation_interpretation={
            "literal_topic": "attachment_only_no_question",
            "interaction_move": "share_attachment",
            "freshness": "new",
            "user_stance": "neutral",
        },
        multimodal={"primary_modality": "image"},
        render_target_lang="zh",
    )
    ja_plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "CLOSE_FRIEND", "score": 0.2},
        conversation_interpretation={
            "literal_topic": "greeting",
            "interaction_move": "greeting",
            "freshness": "new",
            "user_stance": "neutral",
        },
        render_target_lang="ja",
    )

    assert "不要主动开始分析画面" in image_plan.reply_contract
    assert image_plan.contract.realization_steps
    assert "義兄妹" in ja_plan.relationship_summary or "義兄妹" in _qualitative_relationship_summary(
        relationship=RelationshipStateBlock(bond_stage="FRIEND", boundary_softness=0.2),
        interpretation=ConversationInterpretation(literal_topic="greeting"),
        interaction_thread=InteractionThreadState(),
        render_target_lang="ja",
    )


def test_build_reply_plan_and_candidates_cover_thread_fallback_and_plain_hold_paths() -> None:
    affect = AffectStateBlock()
    relationship = RelationshipStateBlock(bond_stage="FRIEND")
    drive = DriveStateBlock(continuity_drive=0.2)
    empty_topic_ledger = TopicLedgerBlock()
    empty_tactic_ledger = TacticLedgerBlock()

    thread_plan = build_reply_plan(
        affect_state=affect,
        relationship_state=relationship,
        drive_state=drive,
        interpretation=ConversationInterpretation(freshness="recent_repeat", repeat_question_count=1),
        interaction_thread=InteractionThreadState(thread_kind="phatic", source_topic="greeting", open=True),
        topic_ledger=empty_topic_ledger,
        tactic_ledger=empty_tactic_ledger,
        multimodal=None,
        render_target_lang="zh",
    )
    plain_plan = build_reply_plan(
        affect_state=affect,
        relationship_state=relationship,
        drive_state=drive,
        interpretation=ConversationInterpretation(literal_topic="", freshness="recent_repeat", user_stance="neutral"),
        interaction_thread=InteractionThreadState(),
        topic_ledger=empty_topic_ledger,
        tactic_ledger=empty_tactic_ledger,
        multimodal=None,
        render_target_lang="zh",
    )
    candidates = build_reply_plan_candidates(
        affect_state=affect,
        relationship_state=relationship,
        drive_state=drive,
        interpretation=ConversationInterpretation(literal_topic="", freshness="recent_repeat", repeat_question_count=1),
        interaction_thread=InteractionThreadState(thread_kind="image_social", open=True, last_user_reaction="nothing"),
        topic_ledger=empty_topic_ledger,
        tactic_ledger=empty_tactic_ledger,
        multimodal={"primary_modality": "image"},
        render_target_lang="zh",
        limit=2,
    )

    assert thread_plan.primary_topic == ""
    assert thread_plan.tactic_family == "plain_hold"
    assert plain_plan.tactic_family == "plain_hold"
    assert candidates[0].primary_topic == "emoji_social_followup"


def test_build_reply_plan_from_inputs_covers_rejection_and_japanese_identity() -> None:
    rejection_plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "FRIEND"},
        conversation_interpretation={
            "literal_topic": "rejection",
            "interaction_move": "repair_needed",
            "freshness": "new",
            "user_stance": "challenging",
        },
        render_target_lang="zh",
    )
    ja_identity = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "FRIEND"},
        conversation_interpretation={
            "literal_topic": "identity",
            "interaction_move": "first_ask",
            "freshness": "new",
            "user_stance": "neutral",
        },
        render_target_lang="ja",
    )

    assert rejection_plan.tone_band == "guarded_soft"
    assert "defensive_explanation" in rejection_plan.forbidden_patterns
    assert "名前だけを短く返し" in ja_identity.reply_contract
