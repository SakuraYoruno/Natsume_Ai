from __future__ import annotations

from host_brain.app.context.affect_state import build_affect_summary, resolve_affect_state
from host_brain.app.context.dialogue_state import DialogueStateBlock
from host_brain.app.context.drive_state import resolve_drive_state
from host_brain.app.context.relationship_state import build_relationship_summary, resolve_relationship_state
from host_brain.app.context.topic_state import SessionTopicStateStore
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_topic_state_store_persists_answered_topics_beyond_short_window() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = SessionTopicStateStore(ledger)

    tracked = store.upsert_answered_topics(
        session_id="sess-topic",
        person_id="p1",
        request_id="req-1",
        turn_index=3,
        assistant_text="我是夏目蓝。我们并不熟，只是你既然问了，我就答。",
    )

    assert "identity" in tracked
    block = store.fetch_block(session_id="sess-topic", current_turn_index=20)
    assert "identity" in block.active_topics
    assert "relationship_boundary" in block.active_topics


def test_affect_state_resolver_outputs_explicit_affect_vector() -> None:
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

    assert affect.emotion_tag == "CALM"
    assert 0.0 <= affect.social_energy <= 1.0
    assert affect.carryover_turns == 2
    assert "当前内在状态" in build_affect_summary(affect=affect, render_target_lang="zh")


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
        dialogue_state=DialogueStateBlock(current_user_act="help_continuity_probe"),
    )

    assert relationship.bond_stage == "FRIEND"
    assert relationship.trust >= 0.4
    assert "信任约" in build_relationship_summary(relationship=relationship, render_target_lang="zh")
    assert drive.continuity_drive >= 0.48
    assert drive.care_drive >= 0.20
