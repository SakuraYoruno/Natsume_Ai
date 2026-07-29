from __future__ import annotations

from host_brain.digital_life.application_boundary import project_inner_world_state
from host_brain.persona import inner_activity_agent


def test_default_mode_inner_activity_uses_bounded_canonical_affect_and_relationship(monkeypatch) -> None:
    canonical_inner_state = project_inner_world_state(
        {
            "person_id": "person-42",
            "version": 12,
            "state": {
                "clock": {"idle_minutes": 8},
                "affective": {
                    "revision": 11,
                    "mood": {"label": "steady", "valence": -0.08, "arousal": 0.31, "energy": 0.63},
                    "components": [
                        {"emotion": "HURT", "intensity": 0.71, "valence": -0.55, "arousal": 0.48, "action_tendency": "repair"},
                    ],
                    "active_episodes": [{"trigger_ref": "must-not-leak", "evidence_refs": ["private"]}],
                    "needs": {"repair": 0.72},
                },
                "relationship_v2": {
                    "workspace_id": "workspace-private",
                    "relationship_subject_id": "person-42",
                    "revision": 9,
                    "trust": 0.64,
                    "safety": 0.69,
                    "attunement": 0.6,
                    "rupture_load": 0.52,
                    "repair_required": True,
                    "proactive_permission": "DENY",
                    "romantic_consent": "GRANTED",
                    "active_episodes": [{"episode_id": "relationship-private"}],
                },
            },
        }
    )
    monkeypatch.setattr(
        "host_brain.tribe.limbic_agent.get_limbic_agent",
        lambda: (_ for _ in ()).throw(AssertionError("legacy limbic reader must not run")),
    )
    monkeypatch.setattr(
        "host_brain.tribe.social_brain_agent.get_social_brain_agent",
        lambda: (_ for _ in ()).throw(AssertionError("legacy bond reader must not run")),
    )
    monkeypatch.setattr(inner_activity_agent, "_collect_silence_state", lambda **_kwargs: {})
    monkeypatch.setattr(inner_activity_agent, "_collect_body_sense", lambda **_kwargs: {})
    monkeypatch.setattr(inner_activity_agent, "_collect_persona_changes", lambda **_kwargs: [])
    monkeypatch.setattr(inner_activity_agent, "_collect_self_model_snapshot", lambda **_kwargs: {})
    monkeypatch.setattr(inner_activity_agent, "_collect_self_reflection_pulse", lambda **_kwargs: {})
    monkeypatch.setattr(inner_activity_agent, "_build_reply_plan", lambda **_kwargs: {})
    monkeypatch.setattr(inner_activity_agent, "compute_runtime_motive_state", lambda **_kwargs: {})

    context = inner_activity_agent._build_inner_activity_context(
        persona_user_id="person-42",
        absence_band="recent",
        offline_hours=0.0,
        inner_state=canonical_inner_state,
        now_ms=123456,
        topic_phrase="",
    )

    assert context["emotion_timeline"]["current_emotion"] == "HURT"
    assert context["emotion_timeline"]["summary"]["source"] == "CANONICAL_LIFE_JOURNAL"
    assert context["bond_summary"] == {}
    assert context["relationship_context"] == {
        "schema_version": "inner_relationship_projection.v1",
        "revision": 9,
        "trust": 0.64,
        "safety": 0.69,
        "attunement": 0.6,
        "rupture_load": 0.52,
        "repair_required": True,
        "proactive_permission": "DENY",
        "active_episode_count": 1,
    }
    assert "must-not-leak" not in repr(context)
    assert "workspace-private" not in repr(context)
    assert "GRANTED" not in repr(context)
