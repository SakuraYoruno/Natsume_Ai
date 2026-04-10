from __future__ import annotations

import asyncio

from host_brain.tribe.proprioception_agent import ProprioceptionAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


class _Event:
    def __init__(self, payload: dict, ts_ms: int = 0) -> None:
        self.payload = payload
        self.ts_ms = ts_ms


def _run(coro):
    return asyncio.run(coro)


def test_proprioception_agent_tracks_session_load_and_continuity() -> None:
    agent = ProprioceptionAgent(state_registry=TribeStateRegistry())

    _run(agent._on_conversation_started(_Event(payload={"user_id": "u1", "session_id": "s1"}, ts_ms=1000)))
    _run(agent._on_channel_message_received(_Event(payload={"conversation_id": "s1", "ts_ms": 1100}, ts_ms=1100)))
    _run(agent._on_voice_pipeline_started(_Event(payload={}, ts_ms=1200)))
    exported_active = agent.export_runtime_state()

    assert exported_active["active_session"] is True
    assert exported_active["active_session_id"] == "s1"
    assert exported_active["voice_pipeline_active"] is True
    assert exported_active["interrupt_count_5m"] >= 1
    assert exported_active["cognitive_load_index"] > 0.0

    _run(agent._on_voice_pipeline_ended(_Event(payload={}, ts_ms=1300)))
    _run(agent._on_conversation_ended(_Event(payload={}, ts_ms=1400)))
    exported_idle = agent.export_runtime_state()
    assert exported_idle["active_session"] is False
    assert exported_idle["voice_pipeline_active"] is False
