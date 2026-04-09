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
