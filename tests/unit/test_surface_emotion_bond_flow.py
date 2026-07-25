from __future__ import annotations

from host_brain.persona.context.relationship_state import resolve_relationship_state
from host_brain.app.surface_emotion_bond_flow import (
    apply_surface_emotion_bond_flow,
    plan_surface_emotion_bond_update,
)
from host_brain.bond.bond_stage_engine import BondStageEngine, compute_affinity_delta


def test_negative_affect_with_supportive_reply_becomes_emotional_support() -> None:
    plan = plan_surface_emotion_bond_update(
        user_message="今天真的有点难受。",
        assistant_reply="哈？谁稀罕管你啊。\n……行，那你自己待会儿。\n难受了就叫我，听见没。",
        emotion_context={"emotion_tag": "SAD", "emotion_intensity": 0.8, "valence": -0.7},
        reply_plan={"primary_topic": "bad_mood"},
    )

    assert plan.applied is True
    assert plan.update_kind == "EMOTIONAL_SUPPORT"
    assert plan.emotion_valence < 0
    assert plan.affinity_delta_hint > 8.0
    assert plan.affinity_delta_hint == compute_affinity_delta(
        plan.update_kind,
        emotion_intensity=plan.emotion_intensity,
        emotion_valence=plan.emotion_valence,
    )
    assert "negative_affect_supported" in plan.reason_codes


def test_direct_conflict_becomes_negative_bond_event() -> None:
    plan = plan_surface_emotion_bond_update(
        user_message="你别管我了，真的很烦。",
        assistant_reply="行，我先收住。你要是难受了再叫我。",
        emotion_context={"emotion_tag": "ANGRY", "emotion_intensity": 0.75, "valence": -0.8},
    )

    assert plan.update_kind == "CONFLICT"
    assert plan.affinity_delta_hint < 0
    assert "direct_conflict" in plan.reason_codes


def test_surface_user_emotion_drives_bond_update_when_assistant_tag_is_calm() -> None:
    plan = plan_surface_emotion_bond_update(
        user_message="今天真的有点难受。",
        assistant_reply="哈？谁稀罕管你啊。\n……行，那你自己待会儿。\n难受了就叫我，听见没。",
        emotion_context={
            "emotion_tag": "CALM",
            "surface_natsume_emotion_tag": "CALM",
            "surface_user_emotion_tag": "SAD",
            "emotion_intensity": 0.78,
        },
        reply_plan={"primary_topic": "bad_mood"},
    )

    assert plan.update_kind == "EMOTIONAL_SUPPORT"
    assert plan.emotion_tag == "SAD"
    assert plan.user_emotion_tag == "SAD"
    assert plan.assistant_emotion_tag == "CALM"
    assert "surface_user_emotion_used" in plan.reason_codes


def test_apply_surface_emotion_bond_flow_enqueues_compressed_bond_payload() -> None:
    captured: dict[str, object] = {}

    class _Queue:
        def enqueue(self, payload):  # noqa: ANN001
            captured["payload"] = dict(payload)
            return {"status": "QUEUED", "queue_size": 1}

    result = apply_surface_emotion_bond_flow(
        request_id="req-surface-bond",
        person_id="user-1",
        session_id="sess-1",
        user_message="上次那个问题我又卡住了。",
        assistant_reply="上次卡在输入输出，这轮先看那一块。",
        emotion_context={"emotion_tag": "NEUTRAL", "emotion_intensity": 0.4},
        bond_summary={"capability_hint": "关系好的兄妹"},
        reply_plan={"primary_topic": "technical_help"},
        retrieval_evidence_items=[{"evidence_ref": "evd://memory/1"}],
        ts_ms=1234,
        queue=_Queue(),
    )

    assert result["status"] == "QUEUED"
    assert result["update_kind"] == "SHARED_MEMORY"
    assert captured["payload"]["update_kind"] == "SHARED_MEMORY"
    assert captured["payload"]["emotion_valence"] == 0.0
    assert captured["payload"]["evidence_refs"] == ["surface_turn:req-surface-bond", "evd://memory/1"]
    assert captured["payload"]["payload"]["source"] == "surface_main_chain"
    assert captured["payload"]["payload"]["user_emotion_tag"] == "NEUTRAL"
    assert captured["payload"]["payload"]["assistant_emotion_tag"] == "NEUTRAL"


def test_apply_surface_emotion_bond_flow_fails_closed_without_queue() -> None:
    result = apply_surface_emotion_bond_flow(
        request_id="req-surface-bond-no-queue",
        person_id="user-1",
        session_id="sess-1",
        user_message="这次也一起记住。",
        assistant_reply="好，我会把这次关联起来。",
        emotion_context={"emotion_tag": "NEUTRAL"},
        reply_plan={"primary_topic": "technical_help"},
    )

    assert result["status"] == "FAILED"
    assert result["queue_reason_code"] == "BOND_UPDATE_QUEUE_NOT_COMPOSED"


def test_bond_stage_engine_uses_real_valence_for_texture() -> None:
    engine = BondStageEngine()
    result = engine.apply_event(
        user_id="user-valence",
        update_kind="INTERACTION",
        evidence_refs=["ev-1"],
        emotion_intensity=0.9,
        emotion_valence=-0.8,
    )
    state = engine.get_state("user-valence")

    assert result.score_delta >= 1
    assert state.texture.recent_emotion_valence < 0


def test_chinese_bond_summary_stage_drives_relationship_state() -> None:
    relationship = resolve_relationship_state(
        bond_summary={"stage": "亲密恋人", "trend": "上升"},
        social_brain_state={},
    )
    stable = resolve_relationship_state(
        bond_summary={"stage": "亲密恋人", "trend": "稳定"},
        social_brain_state={},
    )

    assert relationship.bond_stage == "CONFIDANT"
    assert relationship.relationship_phase == "lover"
    assert relationship.relationship_substage in {"confessed_lovers", "stable_lovers"}
    assert relationship.openness > stable.openness
