from __future__ import annotations

import asyncio

from host_brain.app.life_projection_handlers import handle_life_projection
from host_brain.digital_life.contracts import (
    LifeIntentKind,
    LifeProjectionMessage,
)


def _message(target: str, payload: dict) -> LifeProjectionMessage:
    return LifeProjectionMessage(
        intent_id="intent-1",
        event_id="event-1",
        command_id="command-1",
        person_id="person-1",
        intent_kind=LifeIntentKind.PROJECTION,
        target=target,
        payload=payload,
        payload_digest="a" * 64,
        attempt_count=1,
        fencing_token=1,
    )


def test_surface_projection_runs_durable_only_post_process_mode(monkeypatch) -> None:
    captured: dict = {}

    async def _post_process(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._async_post_process",
        _post_process,
    )
    result = asyncio.run(
        handle_life_projection(
            _message(
                "surface.post_process",
                {
                    "request_id": "request-1",
                    "person_id": "person-1",
                    "session_id": "session-1",
                    "user_message": "hello",
                    "narrative": "reply",
                    "ts_ms": 100,
                    "emotion_context": {"emotion_tag": "HAPPY"},
                    "trace_stage_meta": {"surface_main_chain_used": True},
                },
            )
        )
    )

    assert result["status"] == "APPLIED"
    assert captured["narrative"] == "reply"
    trace = captured["trace_stage_meta"]
    assert trace["life_projection_mode"] is True
    assert trace["life_projection_intent_id"] == "intent-1"
    assert trace["skip_heavy_persona_postprocess"] is True
    assert trace["skip_default_mode_on_conversation_end"] is True


def test_journal_native_projection_targets_do_not_call_legacy_writers() -> None:
    result = asyncio.run(
        handle_life_projection(_message("life.experience", {"value": 1}))
    )
    assert result == {
        "status": "PROJECTED_FROM_LIFE_JOURNAL",
        "target": "life.experience",
        "intent_id": "intent-1",
    }
