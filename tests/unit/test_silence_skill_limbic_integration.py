from __future__ import annotations

from host_brain.skills.impl.silence_skill import (
    execute_silence_add_resistance,
    execute_silence_tick,
    execute_silence_update_emotion,
)


class _FakeLimbicAgent:
    def __init__(self) -> None:
        self.tick_calls: list[dict[str, object]] = []
        self.update_calls: list[dict[str, object]] = []
        self.resistance_calls: list[dict[str, object]] = []

    def tick_silence(self, **kwargs):  # noqa: ANN003
        self.tick_calls.append(dict(kwargs))
        return {"status": "OK", "silence_state": {"willingness_level": "NORMAL"}}

    def update_silence_emotion(self, **kwargs):  # noqa: ANN003
        self.update_calls.append(dict(kwargs))
        return {"status": "OK", "willingness": 0.42, "silence_state": {"response_willingness": 0.42}}

    def add_silence_topic_resistance(self, **kwargs):  # noqa: ANN003
        self.resistance_calls.append(dict(kwargs))
        if not str(kwargs.get("topic_label") or "").strip():
            return {"status": "REJECTED", "reason_code": "SILENCE_TOPIC_REQUIRED"}
        return {"status": "OK", "topic_label": kwargs.get("topic_label"), "strength": 0.8, "silence_state": {}}


def test_silence_tick_delegates_to_limbic(monkeypatch) -> None:
    fake_limbic = _FakeLimbicAgent()
    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: fake_limbic)

    result = execute_silence_tick(
        args={"current_topic": "topic-a"},
        context={"user_id": "u1", "emotion_context": {"emotion_tag": "CALM"}},
    )

    assert result["status"] == "OK"
    assert fake_limbic.tick_calls[0]["user_id"] == "u1"
    assert fake_limbic.tick_calls[0]["current_topic"] == "topic-a"


def test_silence_tick_skips_during_recent_foreground_chat(monkeypatch) -> None:
    fake_limbic = _FakeLimbicAgent()
    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: fake_limbic)
    monkeypatch.setattr(
        "host_brain.tribe.default_mode_agent.resolve_background_tick_guard_state",
        lambda **_kwargs: {
            "active": True,
            "cooldown_remaining_ms": 31000,
        },
    )

    result = execute_silence_tick(
        args={"current_topic": "topic-a"},
        context={"user_id": "u1"},
    )

    assert result["status"] == "SKIPPED"
    assert result["reason_code"] == "BACKGROUND_TICK_FOREGROUND_CHAT_COOLDOWN"
    assert result["cooldown_remaining_ms"] == 31000
    assert fake_limbic.tick_calls == []


def test_silence_update_emotion_delegates_to_limbic(monkeypatch) -> None:
    fake_limbic = _FakeLimbicAgent()
    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: fake_limbic)

    result = execute_silence_update_emotion(
        args={"emotion_tag": "sad", "emotion_valence": -0.2, "emotion_arousal": 0.4, "mood_state": "steady"},
        context={"current_topic": "topic-b"},
    )

    assert result["status"] == "OK"
    assert result["willingness"] == 0.42
    assert fake_limbic.update_calls[0]["emotion_tag"] == "SAD"
    assert fake_limbic.update_calls[0]["current_topic"] == "topic-b"


def test_silence_add_resistance_delegates_to_limbic(monkeypatch) -> None:
    fake_limbic = _FakeLimbicAgent()
    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: fake_limbic)

    result = execute_silence_add_resistance(args={"topic_label": "deadline", "strength": 0.8})

    assert result["status"] == "OK"
    assert fake_limbic.resistance_calls[0]["topic_label"] == "deadline"
