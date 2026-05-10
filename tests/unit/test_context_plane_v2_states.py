from __future__ import annotations

from host_brain.app.context.affect_state import build_affect_summary, resolve_affect_state
from host_brain.app.context.drive_state import resolve_drive_state
from host_brain.app.context.relationship_state import build_relationship_summary, resolve_relationship_state
from host_brain.app.context.topic_ledger import SessionTopicLedgerStore
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_topic_ledger_persists_answered_topics_beyond_short_window() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = SessionTopicLedgerStore(ledger)

    tracked = store.record_reply_plan(
        session_id="sess-topic",
        person_id="p1",
        request_id="req-1",
        turn_index=3,
        primary_topic="identity",
        answer_style="identify",
        confidence=0.92,
        metadata={"interaction_move": "first_ask"},
    )

    assert tracked == ("identity",)
    block = store.fetch_block(session_id="sess-topic", current_turn_index=20)
    assert "identity" in block.active_topics
    assert block.get("identity") is not None


def test_affect_state_resolver_outputs_qualitative_affect_summary() -> None:
    affect = resolve_affect_state(
        emotion_context={
            "emotion_tag": "CALM",
            "emotion_intensity": 0.22,
            "vad_vector": {"valence": 0.30, "arousal": 0.18},
            "turn_count": 2,
        },
        body_sense={"fatigue": 0.15, "drowsiness": 0.05},
        offline_state={"loneliness_level": 0.20, "anticipation_level": 0.12},
        silence_state={"response_willingness": 0.86},
    )

    summary = build_affect_summary(affect=affect, render_target_lang="zh")
    assert affect.emotion_tag == "CALM"
    assert 0.0 <= affect.social_energy <= 1.0
    assert affect.carryover_turns == 2
    assert "当前" in summary
    assert "0." not in summary


def test_relationship_and_drive_state_resolvers_produce_structured_vectors() -> None:
    relationship = resolve_relationship_state(
        bond_summary={"stage": "FRIEND", "score": 0.42, "shared_memory_count": 5},
        social_brain_state={"bond_trend": "rising", "bond_vector_trust": 0.44},
    )
    drive = resolve_drive_state(
        motive_state={
            "reach_out_drive": 0.18,
            "withdraw_drive": 0.10,
            "continuity_drive": 0.48,
            "self_protect_drive": 0.16,
            "care_drive": 0.32,
        },
        affect_state=resolve_affect_state(emotion_context={"emotion_tag": "CALM"}),
        relationship_state=relationship,
        dialogue_state={"current_user_act": "help_commitment", "repeat_question_count": 1},
    )

    summary = build_relationship_summary(relationship=relationship, render_target_lang="zh")
    assert relationship.bond_stage == "FRIEND"
    assert relationship.relationship_phase == "stepsibling"
    assert relationship.relationship_substage in {"playful_sibling", "protective_sibling"}
    assert relationship.playfulness > 0.0
    assert relationship.attachment_depth > 0.0
    assert relationship.trust >= 0.4
    assert "义兄妹" in summary
    assert "0." not in summary
    assert drive.continuity_drive >= 0.48
    assert drive.care_drive >= 0.20
