from __future__ import annotations

from host_brain.app.context.soul_runtime import get_compiled_soul
from host_brain.app.context.tactic_ledger import TacticLedgerBlock, TacticLedgerEntry
from host_brain.app.context.topic_ledger import TopicLedgerBlock, TopicLedgerEntry
from host_brain.persona.reply_plan import (
    build_reply_plan_candidates_from_inputs,
    build_reply_plan_from_inputs,
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
    assert plan.requires_validation is False
    assert plan.force_buffering is False


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


def test_reply_plan_mid_repeat_relational_chat_no_longer_forces_buffering_by_default() -> None:
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

    assert plan.requires_validation is False
    assert plan.force_buffering is False


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
