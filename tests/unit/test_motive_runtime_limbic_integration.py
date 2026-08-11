from __future__ import annotations

from types import SimpleNamespace

from host_brain.persona.motive_runtime import compute_runtime_motive_state


def test_compute_runtime_motive_state_uses_limbic_wrappers_when_missing_inputs(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "host_brain.tribe.limbic_agent.get_limbic_agent",
        lambda: SimpleNamespace(
            resolve_silence_state=lambda current_topic="", person_id=None: {
                "response_willingness": 0.41,
                "willingness_level": "RELUCTANT",
                "topic": current_topic,
                "relationship_subject_id": person_id,
            },
            resolve_body_sense_state=lambda person_id=None: {
                "fatigue_level": "TIRED",
                "drowsiness_level": "SLEEPY",
                "relationship_subject_id": person_id,
            },
            resolve_inner_world_state=lambda person_id=None: {
                "last_interaction_ts_ms": 0,
                "total_offline_hours": 1.5,
                "loneliness": 0.2,
                "anticipation": 0.1,
                "anticipation_topics": ["topic-x"],
                "user_id": person_id,
            },
        ),
    )

    def _compute_motive_state(input_obj):
        captured["silence_state"] = dict(input_obj.silence_state or {})
        captured["body_sense"] = dict(input_obj.body_sense or {})
        captured["offline_state"] = dict(input_obj.offline_state or {})
        return SimpleNamespace(as_dict=lambda: {"dominant_motive": "reach_out"})

    monkeypatch.setattr(
        "host_brain.persona.motive_engine.compute_motive_state",
        _compute_motive_state,
    )

    result = compute_runtime_motive_state(
        persona_user_id="user-1",
        current_topic="project alpha",
        bond_summary={},
        emotion_context={},
        pending_topics=[],
    )

    assert result["dominant_motive"] == "reach_out"
    assert captured["silence_state"]["topic"] == "project alpha"
    assert captured["silence_state"]["relationship_subject_id"] == "user-1"
    assert captured["body_sense"]["fatigue_level"] == "TIRED"
    assert captured["offline_state"]["offline_hours"] == 1.5


def test_compute_runtime_motive_state_skips_limbic_when_inputs_provided(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "host_brain.tribe.limbic_agent.get_limbic_agent",
        lambda: (_ for _ in ()).throw(AssertionError("limbic should not be resolved")),
    )

    def _compute_motive_state(input_obj):
        return SimpleNamespace(
            as_dict=lambda: {
                "dominant_motive": "continuity",
                "silence_level": input_obj.silence_state.get("willingness_level"),
                "fatigue_level": input_obj.body_sense.get("fatigue_level"),
            }
        )

    monkeypatch.setattr(
        "host_brain.persona.motive_engine.compute_motive_state",
        _compute_motive_state,
    )

    result = compute_runtime_motive_state(
        persona_user_id="user-2",
        current_topic="",
        silence_state={"willingness_level": "NORMAL"},
        body_sense={"fatigue_level": "FRESH"},
        inner_state={"last_interaction_ts_ms": 0},
    )

    assert result["dominant_motive"] == "continuity"
    assert result["silence_level"] == "NORMAL"
    assert result["fatigue_level"] == "FRESH"
