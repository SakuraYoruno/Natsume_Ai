from __future__ import annotations

from host_brain.persona.inner_world import (
    InnerWorldState,
    _append_inner_stream_event,
    _generate_inner_stream_note_from_llm,
    _inner_world_llm_circuit_snapshot,
    _reset_inner_world_llm_circuit,
)


def test_inner_world_llm_note_suppressed_during_recent_foreground_chat(monkeypatch) -> None:
    _reset_inner_world_llm_circuit()
    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "true")
    monkeypatch.setattr(
        "host_brain.tribe.default_mode_agent.resolve_recent_foreground_interaction_state",
        lambda **_kwargs: {"active": True},
    )
    called = {"count": 0}

    def _forbidden_generate(**kwargs):  # noqa: ANN003, ARG001
        called["count"] += 1
        return "不该被调用", "self", "", {}

    monkeypatch.setattr(
        "host_brain.persona.inner_activity_agent.generate_inner_activity_note",
        _forbidden_generate,
    )

    note, focus, reason, meta = _generate_inner_stream_note_from_llm(
        state=InnerWorldState(user_id="user_1"),
        absence_band="recent",
        offline_hours=0.2,
        now_ms=123456,
    )

    assert note == ""
    assert focus == ""
    assert reason == "INNER_WORLD_FOREGROUND_CHAT_COOLDOWN"
    assert meta == {}
    assert called["count"] == 0


def test_inner_world_monologue_ignores_heavy_background_disable_when_core_ticks_enabled(monkeypatch) -> None:
    _reset_inner_world_llm_circuit()
    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "false")
    monkeypatch.delenv("HOST_CONTINUOUS_ORGAN_TICKS_ENABLED", raising=False)
    monkeypatch.setattr(
        "host_brain.tribe.default_mode_agent.resolve_recent_foreground_interaction_state",
        lambda **_kwargs: {"active": False},
    )
    monkeypatch.setattr(
        "host_brain.persona.inner_activity_agent.generate_inner_activity_note",
        lambda **_kwargs: ("我先把没说完的事放在心里。", "topic", "", {}),
    )
    monkeypatch.setattr(
        "host_brain.persona.inner_world._resolve_inner_world_llm_runtime",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("runtime should not resolve")),
    )

    note, focus, reason, meta = _generate_inner_stream_note_from_llm(
        state=InnerWorldState(user_id="user_1"),
        absence_band="recent",
        offline_hours=0.2,
        now_ms=123456,
    )

    assert note == "我先把没说完的事放在心里。"
    assert focus == "topic"
    assert reason == ""
    assert meta == {}


def test_inner_world_monologue_opens_circuit_when_model_unavailable(monkeypatch) -> None:
    _reset_inner_world_llm_circuit()
    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "true")
    monkeypatch.setattr(
        "host_brain.tribe.default_mode_agent.resolve_recent_foreground_interaction_state",
        lambda **_kwargs: {"active": False},
    )
    calls = {"count": 0}

    def _unavailable_generate(**kwargs):  # noqa: ANN003, ARG001
        calls["count"] += 1
        return "", "", "INNER_ACTIVITY_LLM_RUNTIME_UNAVAILABLE", {}

    monkeypatch.setattr(
        "host_brain.persona.inner_activity_agent.generate_inner_activity_note",
        _unavailable_generate,
    )
    monkeypatch.setattr(
        "host_brain.persona.inner_world._resolve_inner_world_llm_runtime",
        lambda **_kwargs: {"available": False, "route_reason_code": "NO_MODEL"},
    )

    note, focus, reason, meta = _generate_inner_stream_note_from_llm(
        state=InnerWorldState(user_id="user_1"),
        absence_band="recent",
        offline_hours=0.2,
        now_ms=123456,
    )

    assert note == ""
    assert focus == ""
    assert reason.startswith("INNER_WORLD_LLM_CIRCUIT_OPEN:")
    assert meta == {}
    assert _inner_world_llm_circuit_snapshot(now_ms=123456)["open"] is True
    assert calls["count"] == 1

    note2, _focus2, reason2, meta2 = _generate_inner_stream_note_from_llm(
        state=InnerWorldState(user_id="user_1"),
        absence_band="recent",
        offline_hours=0.2,
        now_ms=123556,
    )

    assert note2 == ""
    assert reason2 == "INNER_WORLD_LLM_CIRCUIT_OPEN"
    assert meta2 == {}
    assert calls["count"] == 1


def test_inner_world_monologue_retries_after_circuit_cooldown(monkeypatch) -> None:
    import host_brain.persona.inner_world as inner_world_module

    _reset_inner_world_llm_circuit()
    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "true")
    monkeypatch.setattr(
        "host_brain.tribe.default_mode_agent.resolve_recent_foreground_interaction_state",
        lambda **_kwargs: {"active": False},
    )
    calls = {"count": 0, "available": False}

    def _generate(**kwargs):  # noqa: ANN003, ARG001
        calls["count"] += 1
        if calls["available"]:
            return "我在按自己的修正方向重新整理。", "plan", "", {}
        return "", "", "INNER_ACTIVITY_LLM_RUNTIME_UNAVAILABLE", {}

    monkeypatch.setattr(
        "host_brain.persona.inner_activity_agent.generate_inner_activity_note",
        _generate,
    )
    monkeypatch.setattr(
        "host_brain.persona.inner_world._resolve_inner_world_llm_runtime",
        lambda **_kwargs: {"available": False, "route_reason_code": "NO_MODEL"},
    )

    _generate_inner_stream_note_from_llm(
        state=InnerWorldState(user_id="user_1"),
        absence_band="recent",
        offline_hours=0.2,
        now_ms=10_000,
    )
    opened_until = _inner_world_llm_circuit_snapshot(now_ms=10_000)["opened_until_ms"]
    calls["available"] = True

    note, focus, reason, meta = _generate_inner_stream_note_from_llm(
        state=InnerWorldState(user_id="user_1"),
        absence_band="recent",
        offline_hours=0.2,
        now_ms=int(opened_until) + 1,
    )

    assert note == "我在按自己的修正方向重新整理。"
    assert focus == "plan"
    assert reason == ""
    assert meta == {}
    assert _inner_world_llm_circuit_snapshot(now_ms=int(opened_until) + 1)["open"] is False
    assert calls["count"] == 2


def test_inner_world_append_does_not_template_fallback_when_model_down(monkeypatch) -> None:
    _reset_inner_world_llm_circuit()
    monkeypatch.setattr(
        "host_brain.persona.inner_world._generate_inner_stream_note_from_llm",
        lambda **_kwargs: ("", "", "INNER_WORLD_LLM_CIRCUIT_OPEN:NO_MODEL"),
    )
    state = InnerWorldState(
        user_id="user_1",
        anticipation=0.8,
        anticipation_topics=["自我核心"],
    )

    _append_inner_stream_event(
        state=state,
        now_ms=123456,
        absence_band="recent",
        offline_hours=1.0,
    )

    assert state.inner_stream == []
    assert state.spontaneous_event_count == 0
