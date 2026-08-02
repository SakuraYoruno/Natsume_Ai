from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.default_mode_agent import DefaultModeAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


class _Bus:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[str, object]] = []

    def subscribe(self, event_type: str, handler: object) -> None:
        self.subscriptions.append((event_type, handler))


def test_canonical_default_mode_retires_unscoped_inner_activity_and_affect_fanout(monkeypatch) -> None:
    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("canonical DefaultMode must not revive legacy inner activity side effects")

    registry = TribeStateRegistry()
    agent = DefaultModeAgent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
        state_registry=registry,
    )
    bus = _Bus()
    monkeypatch.setattr("host_brain.tribe.event_bus.get_tribe_event_bus", lambda: bus)
    monkeypatch.setattr("host_brain.tribe.default_mode_agent.emit_tribe_event", _forbidden)
    monkeypatch.setattr("host_brain.tribe.default_mode_agent.get_glymphatic_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.default_mode_agent.get_ans_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.default_mode_agent.get_interoception_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.default_mode_agent.get_pain_reward_agent", _forbidden)
    monkeypatch.setattr(
        "host_brain.persona.inner_activity_agent.generate_inner_activity_note",
        _forbidden,
    )
    monkeypatch.setattr(
        "host_brain.persona.self_model.observe_persistent_self_event",
        _forbidden,
    )

    agent.register_subscriptions()
    tick = agent.tick_idle_cycle(
        person_id="person-42",
        topic_phrase="ignored",
        source="conversation_ended",
        now_ts_ms=123456,
    )
    asyncio.run(agent._on_inner_world_ticked(SimpleNamespace(payload={})))
    asyncio.run(agent._on_conversation_ended(SimpleNamespace(payload={})))
    projection = agent.export_runtime_state()

    assert bus.subscriptions == []
    assert tick["status"] == "RETIRED"
    assert tick["reason_code"] == "LEGACY_DEFAULT_MODE_INNER_ACTIVITY_RETIRED"
    assert tick["background_side_effects_suppressed"] is True
    assert registry.get_state("default_mode_agent") is None
    assert projection["reason_code"] == "LEGACY_DEFAULT_MODE_INNER_ACTIVITY_RETIRED"
    assert projection["inner_monologue"] == {}
    assert projection["proactive_voice_candidate"] is False
