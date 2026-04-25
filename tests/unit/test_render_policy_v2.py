from __future__ import annotations

from host_brain.app.context.soul_runtime import get_compiled_soul
from host_brain.app.context.tactic_ledger import TacticLedgerBlock, TacticLedgerEntry
from host_brain.app.context.topic_ledger import TopicLedgerBlock, TopicLedgerEntry
from host_brain.persona.reply_plan import (
    build_reply_plan_candidates_from_inputs,
    build_reply_plan_from_inputs,
    coerce_reply_plan,
)
from host_brain.persona.reply_validator import infer_reply_tactic_shape


def test_reply_plan_first_identity_uses_brief_name_only() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "STRANGER", "score": 0.12},
        motive_state={"continuity_drive": 0.42},
        conversation_interpretation={
            "literal_topic": "identity",
            "interaction_move": "first_ask",
            "freshness": "new",
            "user_stance": "probing",
        },
        render_target_lang="zh",
    )

    assert plan.response_goal == "identify"
    assert plan.tactic_family == "brief_name_only"
    assert plan.restate_level == "name_only"
    assert get_compiled_soul().display_name in plan.reply_contract
    assert "不要展开设定" in plan.reply_contract or "不要继续解释" in plan.reply_contract
    assert plan.contract.required_actions == ("name_only",)
    assert plan.as_dict()["contract"]["required_actions"] == ["name_only"]
    assert plan.contract.realization_steps
    assert plan.requires_validation is True
    assert plan.force_buffering is True


def test_reply_plan_rotates_identity_tactic_on_third_repeat() -> None:
    tactic_ledger = TacticLedgerBlock(
        entries=(
            TacticLedgerEntry(
                session_id="sess",
                request_id="req-2",
                topic_key="identity",
                interaction_move="reconfirm",
                tactic_family="ack_repeat_then_light_probe",
                tactic_variant="probing",
                trajectory_stage="mid_repeat",
                ts_ms=2000,
            ),
            TacticLedgerEntry(
                session_id="sess",
                request_id="req-1",
                topic_key="identity",
                interaction_move="reconfirm",
                tactic_family="ack_name_then_wait",
                tactic_variant="probing",
                trajectory_stage="early_repeat",
                ts_ms=1000,
            ),
        )
    )
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "STRANGER", "score": 0.12},
        motive_state={"continuity_drive": 0.42},
        conversation_interpretation={
            "literal_topic": "identity",
            "interaction_move": "long_repeat",
            "freshness": "recent_repeat",
            "user_stance": "probing",
            "repeat_question_count": 3,
        },
        topic_ledger={
            "active_topics": ["identity"],
            "recently_answered_topics": ["identity"],
        },
        tactic_ledger=tactic_ledger.as_dict(),
        render_target_lang="zh",
    )

    assert plan.response_goal == "identify"
    assert plan.tactic_family not in {"ack_repeat_then_light_probe", "ack_name_then_wait"}
    assert plan.trajectory_stage == "late_repeat"
    assert set(plan.used_tactics_recent) >= {"ack_repeat_then_light_probe", "ack_name_then_wait"}
    assert "repeated_tactic" in plan.forbidden_patterns
    assert plan.tactic_family in {"hold_name_then_open_floor", "soft_close_then_open_floor"}


def test_reply_plan_keeps_expanding_identity_trajectory_after_four_turns() -> None:
    tactic_ledger = TacticLedgerBlock(
        entries=(
            TacticLedgerEntry(
                session_id="sess",
                request_id="req-4",
                topic_key="identity",
                interaction_move="reconfirm",
                tactic_family="soft_close_then_open_floor",
                tactic_variant="settle_real_question",
                trajectory_stage="late_repeat",
                ts_ms=4000,
            ),
            TacticLedgerEntry(
                session_id="sess",
                request_id="req-3",
                topic_key="identity",
                interaction_move="reconfirm",
                tactic_family="hold_name_then_open_floor",
                tactic_variant="open_other_topic",
                trajectory_stage="mid_repeat",
                ts_ms=3000,
            ),
            TacticLedgerEntry(
                session_id="sess",
                request_id="req-2",
                topic_key="identity",
                interaction_move="reconfirm",
                tactic_family="ack_repeat_then_light_probe",
                tactic_variant="confirm_part",
                trajectory_stage="early_repeat",
                ts_ms=2000,
            ),
            TacticLedgerEntry(
                session_id="sess",
                request_id="req-1",
                topic_key="identity",
                interaction_move="reconfirm",
                tactic_family="brief_name_only",
                tactic_variant="plain_name",
                trajectory_stage="first_turn",
                ts_ms=1000,
            ),
        )
    )
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "STRANGER", "score": 0.12},
        motive_state={"continuity_drive": 0.42},
        conversation_interpretation={
            "literal_topic": "identity",
            "interaction_move": "long_repeat",
            "freshness": "recent_repeat",
            "user_stance": "probing",
            "repeat_question_count": 4,
        },
        tactic_ledger=tactic_ledger.as_dict(),
        render_target_lang="zh",
    )

    assert plan.tactic_family in {
        "hold_name_then_open_floor",
        "soft_close_then_open_floor",
        "ack_repeat_then_light_probe",
    }
    assert "identity_plain_name" in set(plan.used_tactic_signatures_recent)
    assert "identity_confirm_part" in set(plan.used_tactic_signatures_recent)
    assert "identity_open_other_topic" in set(plan.used_tactic_signatures_recent)
    assert "identity_settle_real_question" in set(plan.used_tactic_signatures_recent)
    assert plan.used_tactic_signatures_recent
    assert plan.tactic_family != "ack_name_then_wait"
    assert plan.tactic_family != "name_only_then_pause"


def test_reply_plan_candidates_expand_identity_tactics_without_reusing_recent_signature() -> None:
    tactic_ledger = TacticLedgerBlock(
        entries=(
            TacticLedgerEntry(
                session_id="sess",
                request_id="req-4",
                topic_key="identity",
                interaction_move="reconfirm",
                tactic_family="hold_name_then_open_floor",
                tactic_variant="open_real_question",
                trajectory_stage="late_repeat",
                ts_ms=4000,
            ),
            TacticLedgerEntry(
                session_id="sess",
                request_id="req-3",
                topic_key="identity",
                interaction_move="reconfirm",
                tactic_family="hold_name_then_open_floor",
                tactic_variant="open_other_topic",
                trajectory_stage="late_repeat",
                ts_ms=3000,
            ),
            TacticLedgerEntry(
                session_id="sess",
                request_id="req-2",
                topic_key="identity",
                interaction_move="reconfirm",
                tactic_family="ack_repeat_then_light_probe",
                tactic_variant="confirm_part",
                trajectory_stage="mid_repeat",
                ts_ms=2000,
            ),
            TacticLedgerEntry(
                session_id="sess",
                request_id="req-1",
                topic_key="identity",
                interaction_move="reconfirm",
                tactic_family="brief_name_only",
                tactic_variant="plain_name",
                trajectory_stage="first_turn",
                ts_ms=1000,
            ),
        )
    )
    plans = build_reply_plan_candidates_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "STRANGER", "score": 0.12},
        motive_state={"continuity_drive": 0.42},
        conversation_interpretation={
            "literal_topic": "identity",
            "interaction_move": "long_repeat",
            "freshness": "recent_repeat",
            "user_stance": "probing",
            "repeat_question_count": 4,
        },
        tactic_ledger=tactic_ledger.as_dict(),
        render_target_lang="zh",
        limit=4,
    )

    signatures = [plan.trajectory.tactic_family + ":" + plan.trajectory.tactic_variant for plan in plans]
    assert len(plans) >= 3
    assert len(signatures) == len(set(signatures))
    assert "hold_name_then_open_floor:open_real_question" not in signatures[:3]


def test_infer_reply_tactic_shape_recognizes_confirm_part_as_light_probe() -> None:
    inferred = infer_reply_tactic_shape(
        text=f"{get_compiled_soul().display_name}呀。你还想确认哪一部分？",
        topic="identity",
    )

    assert inferred["family"] == "ack_repeat_then_light_probe"
    assert inferred["variant"] == "confirm_part"
    assert inferred["signature"] == "identity_confirm_part"


def test_infer_reply_tactic_shape_recognizes_name_probe_as_light_probe() -> None:
    inferred = infer_reply_tactic_shape(
        text=f"我是{get_compiled_soul().display_name}呀。你其实是想问别的吧？",
        topic="identity",
    )

    assert inferred["family"] in {"ack_repeat_then_light_probe", "hold_name_then_open_floor"}


def test_infer_reply_tactic_shape_recognizes_settle_then_open_as_open_floor() -> None:
    inferred = infer_reply_tactic_shape(
        text=f"都问我好几次啦。你还想问我什么呀？",
        topic="identity",
    )

    assert inferred["family"] in {"hold_name_then_open_floor", "soft_close_then_open_floor"}


def test_reply_plan_boundary_repeat_avoids_recent_boundary_tactic() -> None:
    tactic_ledger = TacticLedgerBlock(
        entries=(
            TacticLedgerEntry(
                session_id="sess",
                request_id="req-b1",
                topic_key="motivation",
                interaction_move="reconfirm",
                tactic_family="ack_still_care_then_new_reason",
                tactic_variant="challenging",
                trajectory_stage="early_repeat",
                ts_ms=1000,
            ),
        )
    )
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "STRANGER", "score": 0.12},
        motive_state={"continuity_drive": 0.22},
        conversation_interpretation={
            "literal_topic": "motivation",
            "interaction_move": "probe",
            "freshness": "recent_repeat",
            "user_stance": "challenging",
            "repeat_question_count": 2,
        },
        tactic_ledger=tactic_ledger.as_dict(),
        render_target_lang="zh",
    )

    assert plan.response_goal == "boundary_reason"
    assert plan.tactic_family != "ack_still_care_then_new_reason"
    assert plan.reply_contract


def test_reply_plan_help_repeat_does_not_repromise() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "ALLY", "score": 0.44},
        motive_state={"continuity_drive": 0.52, "care_drive": 0.30},
        conversation_interpretation={
            "literal_topic": "help_commitment",
            "interaction_move": "anxiety_check",
            "freshness": "recent_repeat",
            "user_stance": "probing",
        },
        topic_ledger={
            "active_topics": ["help_commitment"],
            "recently_answered_topics": ["help_commitment"],
        },
        render_target_lang="zh",
    )

    assert plan.response_goal == "soothe_anxiety"
    assert plan.tactic_family in {
        "ack_anxiety_without_repromise",
        "soft_reassure_then_open_floor",
        "hold_support_without_repromise",
        "steady_commitment_then_wait",
    }
    assert "promise_restate" in plan.forbidden_patterns


def test_reply_plan_mid_repeat_identity_now_keeps_quality_gate_enabled() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "STRANGER", "score": 0.22},
        motive_state={"continuity_drive": 0.41},
        conversation_interpretation={
            "literal_topic": "identity",
            "interaction_move": "reconfirm",
            "freshness": "recent_repeat",
            "user_stance": "probing",
            "repeat_question_count": 2,
        },
        render_target_lang="zh",
    )

    assert plan.requires_validation is True
    assert plan.force_buffering is True


def test_reply_plan_challenging_late_repeat_keeps_buffering_guard() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "STRANGER", "score": 0.22},
        motive_state={"continuity_drive": 0.18},
        conversation_interpretation={
            "literal_topic": "identity",
            "interaction_move": "probe",
            "freshness": "recent_repeat",
            "user_stance": "challenging",
            "repeat_question_count": 3,
        },
        render_target_lang="zh",
    )

    assert plan.requires_validation is True
    assert plan.force_buffering is True


def test_reply_plan_phatic_wave_stays_unbuffered() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "FRIEND", "score": 0.28},
        motive_state={"continuity_drive": 0.18},
        conversation_interpretation={
            "literal_topic": "greeting",
            "interaction_move": "light_ack",
            "freshness": "new",
            "user_stance": "neutral",
        },
        render_target_lang="zh",
    )

    assert plan.requires_validation is True
    assert plan.force_buffering is False


def test_reply_plan_phatic_punct_stays_unbuffered_and_not_plain_hold() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "FRIEND", "score": 0.28},
        motive_state={"continuity_drive": 0.18},
        conversation_interpretation={
            "literal_topic": "phatic_punct",
            "interaction_move": "light_ack",
            "freshness": "new",
            "user_stance": "neutral",
        },
        render_target_lang="zh",
    )

    assert plan.primary_topic == "phatic_punct"
    assert plan.tactic_family == "phatic_wave"
    assert plan.tactic_variant == "light_ack"
    assert plan.tactic_family != "plain_hold"
    assert plan.response_form == "text_plus_emoji"
    assert plan.requires_validation is True
    assert plan.force_buffering is False


def test_reply_plan_summaries_are_qualitative_chinese_only() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={
            "emotion_tag": "CALM",
            "emotion_intensity": 0.15,
            "vad_vector": {"valence": 0.2, "arousal": 0.18},
        },
        body_sense={"fatigue": 0.2},
        offline_state={"loneliness_level": 0.2},
        silence_state={"response_willingness": 0.55},
        bond_summary={"stage": "STRANGER", "score": 0.08},
        conversation_interpretation={
            "literal_topic": "identity",
            "interaction_move": "reconfirm",
            "freshness": "recent_repeat",
            "user_stance": "probing",
        },
        render_target_lang="zh",
    )

    assert "trust" not in plan.affect_summary.lower()
    assert "0." not in plan.affect_summary
    assert "trust" not in plan.relationship_summary.lower()
    assert "0." not in plan.relationship_summary


def test_reply_plan_emoji_social_reply_prefers_text_plus_emoji() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "PLAYFUL"},
        bond_summary={"stage": "FRIEND", "score": 0.48},
        motive_state={"continuity_drive": 0.38},
        conversation_interpretation={
            "literal_topic": "emoji_social_reply",
            "interaction_move": "playful_ping",
            "freshness": "new",
            "user_stance": "playful",
        },
        multimodal={
            "primary_modality": "emoji",
            "observed_types": ["emoji"],
        },
        render_target_lang="zh",
    )

    assert plan.response_form == "text_plus_emoji"
    assert plan.secondary_response_form == "emoji"
    assert plan.response_form_reason == "emoji_social_reply"


def test_reply_plan_social_image_prefers_text_only() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "PLAYFUL"},
        bond_summary={"stage": "FRIEND", "score": 0.48},
        motive_state={"continuity_drive": 0.38},
        conversation_interpretation={
            "literal_topic": "emoji_social_reply",
            "interaction_move": "playful_ping",
            "freshness": "new",
            "user_stance": "playful",
        },
        multimodal={
            "primary_modality": "image",
            "observed_types": ["image"],
        },
        render_target_lang="zh",
    )

    assert plan.response_form == "text"
    assert plan.secondary_response_form == ""
    assert plan.response_form_reason == "image_social_reply"
    assert "图里的情绪" in plan.reply_contract
    assert "不要解说画面" in plan.reply_contract
    assert plan.requires_validation is True
    assert plan.force_buffering is True


def test_reply_plan_social_image_rotates_tactic_family_when_recent_family_was_used() -> None:
    tactic_ledger = TacticLedgerBlock(
        entries=(
            TacticLedgerEntry(
                session_id="sess",
                request_id="req-img-1",
                topic_key="emoji_social_reply",
                interaction_move="playful_ping",
                tactic_family="cute_image_ack",
                tactic_variant="cute_ping",
                trajectory_stage="first_turn",
                ts_ms=1000,
            ),
        )
    )
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "PLAYFUL"},
        bond_summary={"stage": "FRIEND", "score": 0.48},
        motive_state={"continuity_drive": 0.38},
        conversation_interpretation={
            "literal_topic": "emoji_social_reply",
            "interaction_move": "playful_ping",
            "freshness": "new",
            "user_stance": "playful",
        },
        multimodal={
            "primary_modality": "image",
            "observed_types": ["image"],
            "emotion_signals": ["可爱、卖萌"],
            "core_observation": "可爱、卖萌；一张动漫风格的猫耳女孩表情图。",
        },
        tactic_ledger=tactic_ledger.as_dict(),
        render_target_lang="zh",
    )

    assert plan.response_form_reason == "image_social_reply"
    assert plan.tactic_family != "cute_image_ack"
    assert plan.tactic_family in {"soft_emotion_ack", "tease_image_ack"}


def test_reply_plan_presence_ping_uses_phatic_form() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "FRIEND", "score": 0.31},
        motive_state={"continuity_drive": 0.21},
        conversation_interpretation={
            "literal_topic": "presence_ping",
            "interaction_move": "light_ack",
            "freshness": "new",
            "user_stance": "neutral",
        },
        render_target_lang="zh",
    )

    assert plan.response_form == "text_plus_emoji"
    assert plan.secondary_response_form == "emoji"
    assert plan.tactic_family == "phatic_wave"


def test_reply_plan_screenshot_analysis_uses_multimodal_tactic() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "STRANGER", "score": 0.05},
        motive_state={"continuity_drive": 0.22},
        conversation_interpretation={
            "literal_topic": "screenshot_analysis",
            "interaction_move": "probe",
            "freshness": "new",
            "user_stance": "neutral",
        },
        multimodal={
            "primary_modality": "image",
            "artifact_type": "screenshot",
            "summary_for_chat": "用户发来一张截图，更像是在问界面上的提示是什么意思。",
            "observed_types": ["image"],
        },
        render_target_lang="zh",
    )

    assert plan.tactic_family == "observe_then_answer"
    assert plan.tactic_variant == "screenshot_analysis"
    assert plan.response_form == "text"


def test_reply_plan_preserves_multimodal_payload_for_runtime_and_coercion() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "STRANGER", "score": 0.12},
        motive_state={"continuity_drive": 0.42},
        conversation_interpretation={
            "literal_topic": "literal_description",
            "interaction_move": "probe",
            "freshness": "new",
            "user_stance": "probing",
        },
        multimodal={
            "core_observation": "一朵粉色花",
            "summary_for_chat": "用户发来一张图片，当前能确认到的核心内容是：一朵粉色花。",
        },
        render_target_lang="zh",
    )

    assert plan.multimodal["core_observation"] == "一朵粉色花"
    restored = coerce_reply_plan(plan.as_dict())
    assert restored.multimodal["core_observation"] == "一朵粉色花"


def test_reply_plan_image_social_followup_deny_uses_followup_contract() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "PLAYFUL"},
        bond_summary={"stage": "FRIEND", "score": 0.52},
        motive_state={"continuity_drive": 0.35},
        conversation_interpretation={
            "literal_topic": "emoji_social_followup",
            "interaction_move": "repair_needed",
            "freshness": "recent_repeat",
            "user_stance": "challenging",
            "reason_tags": ["image_social_followup:deny", "recent_image_social_context"],
        },
        interaction_thread={
            "thread_kind": "image_social",
            "source_topic": "emoji_social_reply",
            "source_modality": "image",
            "open": True,
            "last_assistant_move": "image_social_ack",
            "last_user_reaction": "deny",
        },
        multimodal={
            "primary_modality": "text",
            "core_observation": "委屈；一个哭哭的表情图。",
        },
        render_target_lang="zh",
    )

    assert plan.response_form == "text"
    assert plan.response_form_reason == "image_social_followup"
    assert plan.requires_validation is True
    assert plan.force_buffering is True
    assert plan.tactic_family in {"soft_emotion_ack", "tease_image_ack"}
    assert any(token in plan.reply_contract for token in ("刚才那张图", "顺着刚才那层情绪", "嘴硬否认", "给对方一个台阶"))


def test_reply_plan_image_social_followup_why_prefers_explain_variant() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "FRIEND", "score": 0.52},
        motive_state={"continuity_drive": 0.35},
        conversation_interpretation={
            "literal_topic": "emoji_social_followup",
            "interaction_move": "reconfirm",
            "freshness": "recent_repeat",
            "user_stance": "probing",
            "reason_tags": ["image_social_followup:why", "recent_image_social_context"],
        },
        interaction_thread={
            "thread_kind": "image_social",
            "source_topic": "emoji_social_reply",
            "source_modality": "image",
            "open": True,
            "last_assistant_move": "image_social_ack",
            "last_user_reaction": "why",
        },
        multimodal={
            "primary_modality": "text",
            "core_observation": "委屈；一个哭哭的表情图。",
        },
        render_target_lang="zh",
    )

    assert plan.response_form_reason == "image_social_followup"
    assert plan.tactic_variant in {"explain_read", "light_explain", "backoff_hold"}
    if plan.tactic_variant == "explain_read":
        assert "很生活的话解释你的读法" in plan.reply_contract


def test_reply_plan_image_social_reply_keeps_variant_specific_contract_after_multimodal_refine() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "SAD"},
        bond_summary={"stage": "FRIEND", "score": 0.52},
        motive_state={"continuity_drive": 0.2},
        conversation_interpretation={
            "literal_topic": "emoji_social_reply",
            "interaction_move": "playful_ping",
            "freshness": "new",
            "user_stance": "probing",
        },
        interaction_thread={
            "thread_kind": "image_social",
            "source_topic": "emoji_social_reply",
            "source_modality": "image",
            "open": True,
        },
        multimodal={
            "primary_modality": "image",
            "emotion_signals": ["委屈", "哭", "难过"],
            "core_observation": "一个哭哭的表情图。",
        },
        render_target_lang="zh",
    )

    assert plan.primary_topic == "emoji_social_reply"
    assert plan.response_form_reason == "image_social_reply"
    assert plan.max_sentences == 1
    assert plan.max_chars <= 52
    assert plan.should_be_brief is True
    assert "visual_reference" not in plan.contract.required_actions
    assert "image_social_emotion_response" in plan.contract.required_actions
    assert any(
        token in plan.reply_contract
        for token in ("委屈/难过", "只剩感叹词", "不要解说画面")
    )
    assert "只回一句短接话" in plan.reply_contract
    assert "表情包表达的情绪" in plan.reply_contract
    assert "不要评价这张图" in plan.reply_contract
    assert "不要把自己写成图片主体" in plan.reply_contract
    assert "不要把问题抛回给对方" in plan.reply_contract


def test_reply_plan_open_image_social_thread_never_falls_back_to_plain_hold() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "FRIEND", "score": 0.52},
        motive_state={"continuity_drive": 0.35},
        conversation_interpretation={
            "literal_topic": "",
            "interaction_move": "",
            "freshness": "recent_repeat",
            "user_stance": "challenging",
        },
        interaction_thread={
            "thread_kind": "image_social",
            "source_topic": "emoji_social_reply",
            "source_modality": "image",
            "open": True,
            "last_assistant_move": "image_social_ack",
            "last_user_reaction": "deny",
        },
        multimodal={
            "primary_modality": "text",
            "core_observation": "委屈；一个哭哭的表情图。",
        },
        render_target_lang="zh",
    )

    assert plan.tactic_family != "plain_hold"
    assert plan.primary_topic == "emoji_social_followup"
    assert plan.response_goal == "gentle_followup_reply"


def test_reply_plan_assistant_reply_followup_never_falls_back_to_plain_hold() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "STRANGER", "score": 0.08},
        motive_state={"continuity_drive": 0.35},
        conversation_interpretation={
            "literal_topic": "assistant_reply_followup",
            "interaction_move": "clarify_reference",
            "freshness": "new",
            "user_stance": "probing",
            "reason_tags": ["assistant_reply_followup:ask_object"],
        },
        interaction_thread={
            "thread_kind": "assistant_reply_followup",
            "source_topic": "assistant_reply",
            "source_excerpt": "知道。",
            "source_context_excerpt": "你知道吗",
            "source_modality": "text",
            "open": True,
            "last_assistant_move": "previous_reply",
            "last_user_reaction": "ask_object",
        },
        render_target_lang="zh",
    )

    assert plan.tactic_family == "explain_previous_reply"
    assert plan.tactic_variant == "clarify_object"
    assert plan.primary_topic == "assistant_reply_followup"
    assert plan.force_buffering is True
    assert "你知道吗" in plan.reply_contract
    assert "知道。" in plan.reply_contract
    assert "语气词" in plan.reply_contract


def test_reply_plan_assistant_filler_followup_keeps_source_context() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "STRANGER", "score": 0.08},
        motive_state={"continuity_drive": 0.35},
        conversation_interpretation={
            "literal_topic": "assistant_reply_followup",
            "interaction_move": "clarify_reference",
            "freshness": "recent_repeat",
            "user_stance": "probing",
            "reason_tags": ["assistant_reply_followup:clarify_meaning"],
        },
        interaction_thread={
            "thread_kind": "assistant_reply_followup",
            "source_topic": "assistant_reply",
            "source_excerpt": "唔。",
            "source_context_excerpt": "知道什么",
            "source_modality": "text",
            "open": True,
            "last_assistant_move": "previous_reply",
            "last_user_reaction": "clarify_meaning",
        },
        render_target_lang="zh",
    )

    assert plan.tactic_family == "explain_previous_reply"
    assert plan.tactic_variant == "clarify_meaning"
    assert plan.primary_topic == "assistant_reply_followup"
    assert "知道什么" in plan.reply_contract
    assert "唔。" in plan.reply_contract
    assert "语气词" in plan.reply_contract


def test_reply_plan_image_social_followup_softens_stranger_relationship_summary() -> None:
    plan = build_reply_plan_from_inputs(
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "STRANGER", "score": 0.08},
        motive_state={"continuity_drive": 0.35},
        conversation_interpretation={
            "literal_topic": "emoji_social_followup",
            "interaction_move": "repair_needed",
            "freshness": "recent_repeat",
            "user_stance": "challenging",
            "reason_tags": ["image_social_followup:deny", "recent_image_social_context"],
        },
        interaction_thread={
            "thread_kind": "image_social",
            "source_topic": "emoji_social_reply",
            "source_modality": "image",
            "open": True,
            "last_assistant_move": "image_social_ack",
            "last_user_reaction": "deny",
        },
        multimodal={
            "primary_modality": "text",
            "core_observation": "委屈；一个哭哭的表情图。",
        },
        render_target_lang="zh",
    )

    assert plan.relationship_summary == "保持自然分寸，但不用刻意拉开，顺着眼前这点情绪接住就好。"
