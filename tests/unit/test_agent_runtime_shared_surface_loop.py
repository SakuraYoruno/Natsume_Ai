from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from host_brain.app.agent_runtime_shared import (
    _run_surface_persona_session_end_sync,
    _write_surface_main_chain_memory_sync,
)


class _FakeEpisodicStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def write_memory(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(dict(kwargs))
        return SimpleNamespace(
            success=True,
            reason_code=None,
            detail="ok",
            memory_id="mem-surface-1",
        )


def test_surface_main_chain_memory_write_uses_surface_turn_summary_and_canonical_user(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOST_DEFAULT_USER_ID", "canon-user")
    store = _FakeEpisodicStore()

    result = _write_surface_main_chain_memory_sync(
        episodic_store=store,
        request_id="req-1",
        person_id="external-user",
        session_id="sess-1",
        user_message="刚才那句太生硬了",
        assistant_reply="哼，知道了。我改顺一点。",
        emotion_context={"surface_user_emotion_tag": "SAD", "emotion_intensity": 0.8},
        ts_ms=123,
        reply_plan={"task_mode": "memory_recall"},
    )

    assert result["success"] is True
    assert result["source"] == "surface_main_chain"
    assert result["person_id"] == "canon-user"
    assert store.calls
    call = store.calls[0]
    assert call["text"] == "当前回合摘要：用户提到「刚才那句太生硬了」；回复「哼，知道了。我改顺一点。」。"
    assert call["intent_id"] == "memory_recall"
    assert call["emotion_tag"] == "SAD"
    assert call["emotion_valence"] < 0
    assert call["emotion_arousal"] >= 0.8
    assert "surface://reply/req-1" in call["evidence_refs"]
    assert "chat://person/canon-user" in call["evidence_refs"]


def test_surface_session_end_uses_canonical_user_and_forwards_emotion(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOST_DEFAULT_USER_ID", "canon-user")
    calls: list[dict[str, Any]] = []

    class _FakeLimbic:
        def on_session_end(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(dict(kwargs))
            return {
                "continuity_score": 0.7,
                "anticipation_topics": list(kwargs.get("pending_topics") or []),
                "inner_stream_size": 3,
            }

    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: _FakeLimbic())

    result = _run_surface_persona_session_end_sync(
        request_id="req-1",
        person_id="external-user",
        session_id="sess-1",
        user_message="我有点难受",
        emotion_context={"surface_user_emotion_tag": "SAD", "emotion_intensity": 0.75},
    )

    assert calls
    assert calls[0]["person_id"] == "canon-user"
    assert calls[0]["pending_topics"] == ["我有点难受"]
    assert calls[0]["valence"] < 0
    assert calls[0]["arousal"] >= 0.75
    assert result["status"] == "OK"
    assert result["person_id"] == "canon-user"
    assert result["emotion_tag"] == "SAD"
    assert result["inner_world"]["anticipation_topics"] == ["我有点难受"]
