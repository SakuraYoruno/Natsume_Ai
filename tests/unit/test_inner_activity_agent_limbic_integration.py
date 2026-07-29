from __future__ import annotations

from host_brain.persona.inner_activity_agent import _collect_emotion_timeline


def test_collect_emotion_timeline_uses_limbic_agent(monkeypatch) -> None:
    class _Limbic:
        def resolve_emotion_timeline_snapshot(self):
            return {
                "summary": {"dominant_emotion": "joy", "energy": 0.8},
                "diary": {"events": 5},
                "dominant_emotion": "JOY",
            }

    monkeypatch.setattr(
        "host_brain.tribe.limbic_agent.get_limbic_agent",
        lambda: _Limbic(),
    )

    summary, diary, emotion = _collect_emotion_timeline()

    assert summary["energy"] == 0.8
    assert diary["events"] == 5
    assert emotion == "JOY"


def test_collect_emotion_timeline_degrades_when_limbic_fails(monkeypatch) -> None:
    class _Limbic:
        def resolve_emotion_timeline_snapshot(self):
            raise RuntimeError("timeline_unavailable")

    monkeypatch.setattr(
        "host_brain.tribe.limbic_agent.get_limbic_agent",
        lambda: _Limbic(),
    )

    summary, diary, emotion = _collect_emotion_timeline()

    assert summary == {}
    assert diary == {}
    assert emotion == "NEUTRAL"


def test_collect_emotion_timeline_uses_canonical_self_affect_without_limbic(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.tribe.limbic_agent.get_limbic_agent",
        lambda: (_ for _ in ()).throw(AssertionError("legacy timeline must not be read")),
    )

    summary, diary, emotion = _collect_emotion_timeline(
        canonical_self_affect={
            "revision": 8,
            "mood": {"label": "calm", "valence": 0.12, "arousal": 0.2, "energy": 0.7},
            "components": [
                {"emotion": "HURT", "intensity": 0.74},
                {"emotion": "CARE", "intensity": 0.52},
            ],
            "active_episodes": [{"trigger_ref": "not-exposed"}],
        }
    )

    assert summary == {
        "source": "CANONICAL_LIFE_JOURNAL",
        "revision": 8,
        "dominant_emotion": "HURT",
        "trend": "canonical_snapshot",
        "total_events": 1,
        "recent_events": 1,
    }
    assert diary["source"] == "CANONICAL_LIFE_JOURNAL"
    assert diary["entries"] == 1
    assert emotion == "HURT"
