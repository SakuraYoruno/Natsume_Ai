from __future__ import annotations

from host_brain.persona.inner_world import InnerWorldState, _generate_inner_stream_note_from_llm


def test_inner_world_llm_note_suppressed_during_recent_foreground_chat(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.tribe.default_mode_agent.resolve_recent_foreground_interaction_state",
        lambda **_kwargs: {"active": True, "speed_profile": "context_fast"},
    )
    called = {"count": 0}

    def _forbidden_generate(**kwargs):  # noqa: ANN003, ARG001
        called["count"] += 1
        return "不该被调用", "self", "", {}

    monkeypatch.setattr(
        "host_brain.persona.inner_activity_agent.generate_inner_activity_note",
        _forbidden_generate,
    )

    note, focus, reason = _generate_inner_stream_note_from_llm(
        state=InnerWorldState(user_id="user_1"),
        absence_band="recent",
        offline_hours=0.2,
        now_ms=123456,
    )

    assert note == ""
    assert focus == ""
    assert reason == "INNER_WORLD_FOREGROUND_CHAT_COOLDOWN"
    assert called["count"] == 0
