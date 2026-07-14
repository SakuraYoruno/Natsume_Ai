from __future__ import annotations

import time

from host_brain.skills.impl.inner_world_skill import (
    execute_inner_world_awaken,
    execute_inner_world_monologue_tick,
    execute_inner_world_numeric_tick,
    execute_inner_world_session_end,
    execute_inner_world_tick,
)


class _FakeLimbicAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.tick_calls: list[dict[str, object]] = []
        self.session_end_calls: list[dict[str, object]] = []
        self.awaken_calls: list[dict[str, object]] = []

    def tick_inner_world(self, *, person_id: str | None):
        self.tick_calls.append({"person_id": person_id})
        return {
            "status": "OK",
            "user_id": person_id,
            "loneliness": 0.25,
            "anticipation": 0.5,
            "continuity_score": 0.7,
            "last_spontaneous_ts_ms": int(time.time() * 1000),
        }

    def resolve_inner_world_state(self, *, person_id: str | None):
        return {
            "user_id": str(person_id or "user_1"),
            "anticipation_topics": ["测试链路"],
            "inner_stream": [
                {
                    "focus": "topic",
                    "private_text": "我先把没说完的事放在心里。",
                    "emotion_tag": "calm",
                    "salience": 0.66,
                }
            ],
            "continuity_score": 0.7,
        }

    def handle_signal(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {"accepted": True, "event_type": kwargs.get("event_type")}

    def on_session_end(self, **kwargs):
        self.session_end_calls.append(dict(kwargs))
        user_id = str(kwargs.get("person_id") or "user_1")
        return {"user_id": user_id, "state": "ok"}

    def on_user_awaken(self, **kwargs):
        self.awaken_calls.append(dict(kwargs))
        return {"offline_hours": 2.0, "absence_band": "recent", "awakening_hint": "hi"}


def test_inner_world_tick_emits_tick_and_evolution_event(monkeypatch) -> None:
    fake_limbic = _FakeLimbicAgent()
    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: fake_limbic)

    result = execute_inner_world_tick(args={"user_id": "u1"})

    assert result["status"] == "OK"
    assert fake_limbic.tick_calls[0]["person_id"] == "u1"
    event_types = [str(item.get("event_type")) for item in fake_limbic.calls]
    assert event_types == ["INNER_WORLD_TICKED", "EVOLUTION_CYCLE_TRIGGERED"]


def test_inner_world_tick_maps_reserved_user_id_to_persona_self(monkeypatch) -> None:
    fake_limbic = _FakeLimbicAgent()
    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "true")
    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: fake_limbic)

    result = execute_inner_world_tick(args={"user_id": "user_1"})

    assert result["user_id"] == "natsume_ai"
    assert fake_limbic.tick_calls[0]["person_id"] == "natsume_ai"


def test_inner_world_tick_emits_only_tick_when_spontaneous_too_old(monkeypatch) -> None:
    fake_limbic = _FakeLimbicAgent()
    fake_limbic.tick_inner_world = lambda *, person_id: {  # type: ignore[method-assign]
        "status": "OK",
        "user_id": person_id,
        "last_spontaneous_ts_ms": int(time.time() * 1000) - 180_000,
    }
    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: fake_limbic)

    execute_inner_world_tick(args={"user_id": "u2"})

    event_types = [str(item.get("event_type")) for item in fake_limbic.calls]
    assert event_types == ["INNER_WORLD_TICKED"]


def test_inner_world_monologue_tick_updates_default_mode_when_heavy_background_disabled(monkeypatch) -> None:
    fake_limbic = _FakeLimbicAgent()
    default_calls: list[dict[str, object]] = []

    class _FakeDefaultMode:
        def tick_idle_cycle(self, **kwargs):  # noqa: ANN003
            default_calls.append(dict(kwargs))
            return {
                "status": "OK",
                "focus": "topic",
                "background_side_effects_suppressed": True,
                "self_directive": {"self_continuity_score": 0.72},
            }

    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "false")
    monkeypatch.delenv("HOST_CONTINUOUS_ORGAN_TICKS_ENABLED", raising=False)
    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: fake_limbic)
    monkeypatch.setattr("host_brain.tribe.default_mode_agent.get_default_mode_agent", lambda: _FakeDefaultMode())

    result = execute_inner_world_monologue_tick(args={"user_id": "u-inline"})

    assert result["status"] == "OK"
    assert default_calls
    assert default_calls[0]["person_id"] == "u-inline"
    assert default_calls[0]["topic_phrase"] == "测试链路"
    assert default_calls[0]["source"] == "inner_world_monologue_tick"
    assert result["default_mode_tick"]["self_continuity_score"] == 0.72


def test_inner_world_monologue_tick_persists_recent_inner_stream_memory(monkeypatch) -> None:
    import host_brain.skills.impl.inner_world_skill as inner_world_skill

    fake_limbic = _FakeLimbicAgent()
    now_ms = int(time.time() * 1000)
    memory_calls: list[dict[str, object]] = []

    fake_limbic.tick_inner_world = lambda *, person_id: {  # type: ignore[method-assign]
        "status": "OK",
        "user_id": person_id,
        "last_spontaneous_ts_ms": now_ms,
        "last_spontaneous_note": "我先把没说完的事放在心里。",
        "last_spontaneous_source": "llm",
    }
    inner_world_skill._INNER_WORLD_MEMORY_WRITTEN_EVENT_IDS.clear()
    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "true")
    monkeypatch.setenv("HOST_NON_CHAT_MEMORY_ENABLED", "true")
    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: fake_limbic)
    persist_memory = (
        lambda **kwargs: memory_calls.append(dict(kwargs))
        or {"status": "SUCCESS", "memory_id": "mem_inner_1"}
    )

    result = execute_inner_world_monologue_tick(
        args={"user_id": "u-memory"},
        context={"persist_background_event_memory": persist_memory},
    )

    assert result["status"] == "OK"
    assert memory_calls
    assert memory_calls[0]["event_source"] == "inner_world"
    assert memory_calls[0]["person_id"] == "u-memory"
    assert memory_calls[0]["detail"] == "我先把没说完的事放在心里。"


def test_inner_world_memory_dedupes_same_event_across_tick_kinds(monkeypatch) -> None:
    import host_brain.skills.impl.inner_world_skill as inner_world_skill

    fake_limbic = _FakeLimbicAgent()
    event_ts = int(time.time() * 1000)
    memory_calls: list[dict[str, object]] = []

    fake_limbic.tick_inner_world = lambda *, person_id: {  # type: ignore[method-assign]
        "status": "OK",
        "user_id": person_id,
        "last_spontaneous_ts_ms": event_ts,
        "last_spontaneous_note": "我先把没说完的事放在心里。",
        "last_spontaneous_source": "llm",
        "last_spontaneous_event": {
            "focus": "topic",
            "emotion_tag": "calm",
            "salience": 0.74,
            "core_need": "continuity",
            "next_tendency": "continue_thread",
            "thought_form": "memory_flash",
            "novelty_score": 0.82,
            "liveliness_score": 0.76,
            "quality_flags": [],
            "should_distill_to_memory": True,
        },
    }
    inner_world_skill._INNER_WORLD_MEMORY_WRITTEN_EVENT_IDS.clear()
    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "true")
    monkeypatch.setenv("HOST_NON_CHAT_MEMORY_ENABLED", "true")
    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: fake_limbic)
    persist_memory = (
        lambda **kwargs: memory_calls.append(dict(kwargs))
        or {"status": "OK", "memory_id": "mem_inner_1"}
    )
    context = {"persist_background_event_memory": persist_memory}

    execute_inner_world_monologue_tick(
        args={"user_id": "u-memory-dedupe"},
        context=context,
    )
    execute_inner_world_numeric_tick(
        args={"user_id": "u-memory-dedupe"},
        context=context,
    )

    assert len(memory_calls) == 1
    metadata = memory_calls[0]["metadata"]
    assert metadata["core_need"] == "continuity"
    assert metadata["next_tendency"] == "continue_thread"
    assert metadata["thought_form"] == "memory_flash"
    assert metadata["liveliness_score"] == 0.76
    assert metadata["quality_flags"] == []
    assert metadata["should_distill_to_memory"] is True


def test_inner_world_tick_skips_during_recent_foreground_chat(monkeypatch) -> None:
    fake_limbic = _FakeLimbicAgent()
    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: fake_limbic)
    monkeypatch.setattr(
        "host_brain.tribe.default_mode_agent.resolve_background_tick_guard_state",
        lambda **_kwargs: {
            "active": True,
            "cooldown_remaining_ms": 42000,
        },
    )

    result = execute_inner_world_tick(args={"user_id": "u-skip"})

    assert result["status"] == "SKIPPED"
    assert result["reason_code"] == "BACKGROUND_TICK_FOREGROUND_CHAT_COOLDOWN"
    assert result["cooldown_remaining_ms"] == 42000
    assert fake_limbic.tick_calls == []
    assert fake_limbic.calls == []


def test_inner_world_session_end_emits_event_and_returns_state(monkeypatch) -> None:
    fake_limbic = _FakeLimbicAgent()
    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: fake_limbic)

    result = execute_inner_world_session_end(
        args={
            "user_id": "u3",
            "valence": 0.4,
            "arousal": 0.6,
            "pending_topics": ["music", "travel"],
        }
    )

    assert result["status"] == "OK"
    assert result["pending_topics"] == ["music", "travel"]
    assert fake_limbic.session_end_calls[0]["person_id"] == "u3"
    assert fake_limbic.calls[0]["event_type"] == "INNER_WORLD_SESSION_ENDED"


def test_inner_world_awaken_returns_offline_state_from_limbic(monkeypatch) -> None:
    fake_limbic = _FakeLimbicAgent()
    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: fake_limbic)

    result = execute_inner_world_awaken(args={"user_id": "u-awaken"})

    assert result["status"] == "OK"
    assert result["user_id"] == "u-awaken"
    assert result["offline_state"]["absence_band"] == "recent"
    assert fake_limbic.awaken_calls[0]["person_id"] == "u-awaken"
