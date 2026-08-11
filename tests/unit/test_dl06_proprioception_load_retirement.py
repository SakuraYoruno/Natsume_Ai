from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.proprioception_agent import ProprioceptionAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_canonical_proprioception_retires_global_load_and_interruption_writes(monkeypatch) -> None:
    class _Bus:
        def __init__(self) -> None:
            self.subscriptions: list[tuple[str, object]] = []

        def subscribe(self, event_type: str, handler: object) -> None:
            self.subscriptions.append((event_type, handler))

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("canonical proprioception must not invoke a legacy affect dependency")

    registry = TribeStateRegistry()
    agent = ProprioceptionAgent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
        state_registry=registry,
    )
    bus = _Bus()
    monkeypatch.setattr("host_brain.tribe.event_bus.get_tribe_event_bus", lambda: bus)
    monkeypatch.setattr("host_brain.tribe.proprioception_agent.emit_tribe_event", _forbidden)
    monkeypatch.setattr("host_brain.tribe.proprioception_agent.get_limbic_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.proprioception_agent.get_interoception_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.proprioception_agent.get_endocrine_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.proprioception_agent.get_ans_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.proprioception_agent.get_pain_reward_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.proprioception_agent.get_default_mode_agent", _forbidden)

    agent.register_subscriptions()
    event = SimpleNamespace(
        payload={"user_id": "user-a", "session_id": "session-a", "intensity": 0.9},
        ts_ms=123,
    )
    results = [
        agent._update_state(changes={"active_session": True}, source="test"),
        asyncio.run(agent._on_conversation_started(event)),
        asyncio.run(agent._on_conversation_ended(event)),
        asyncio.run(agent._on_channel_message_received(event)),
        asyncio.run(agent._on_voice_pipeline_started(event)),
        asyncio.run(agent._on_voice_pipeline_ended(event)),
        agent.on_task_success(intensity=0.9),
        asyncio.run(agent._on_task_success(event)),
    ]
    exported = agent.export_runtime_state()

    assert bus.subscriptions == []
    assert {
        result["reason_code"] for result in results if result is not None
    } == {"LEGACY_PROPRIOCEPTION_LOAD_WRITER_RETIRED"}
    assert agent.generate_cognitive_load_hint(cognitive_load_index=0.9) is None
    assert exported["status"] == "RETIRED_CANONICAL_LIFE"
    assert exported["projection"] == "UNAVAILABLE"
    assert exported["cognitive_load_index"] is None
    assert registry.get_state("proprioception_agent") is None
