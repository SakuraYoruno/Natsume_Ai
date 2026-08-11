from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.initiative_governor_agent import InitiativeGovernorAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_canonical_initiative_governor_cannot_create_a_second_companion_gate(monkeypatch) -> None:
    class _Bus:
        def __init__(self) -> None:
            self.subscriptions: list[tuple[str, object]] = []

        def subscribe(self, event_type: str, handler: object) -> None:
            self.subscriptions.append((event_type, handler))

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("canonical initiative governor must not invoke legacy affect dependencies")

    registry = TribeStateRegistry()
    agent = InitiativeGovernorAgent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
        state_registry=registry,
    )
    bus = _Bus()
    monkeypatch.setattr("host_brain.tribe.event_bus.get_tribe_event_bus", lambda: bus)
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.emit_tribe_event", _forbidden)
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_circadian_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_proprioception_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_social_brain_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_interoception_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_pain_reward_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_default_mode_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_vestibular_agent", _forbidden)
    monkeypatch.setattr(
        "host_brain.tribe.initiative_governor_agent.complete_tribe_compiled_sync",
        _forbidden,
    )

    agent.register_subscriptions()
    results = [
        agent.evaluate_initiative(action="proactive_greeting", motive=0.9),
        agent.get_last_decision(),
        asyncio.run(agent._on_glymphatic_cycle(SimpleNamespace(payload={"executed": True}))),
        asyncio.run(agent._on_state_signal(SimpleNamespace(event_type="BOND_VECTOR_UPDATED", ts_ms=123))),
    ]
    exported = agent.export_runtime_state()

    assert bus.subscriptions == []
    assert {result["reason_code"] for result in results} == {
        "LEGACY_INITIATIVE_GOVERNOR_RETIRED"
    }
    assert all(result["decision"] == "DEFER" for result in results)
    assert agent.generate_proactive_topic(loneliness_level=0.9) is None
    assert exported["status"] == "RETIRED_CANONICAL_LIFE"
    assert exported["authority"] == "CANONICAL_LIFE_JOURNAL"
    assert exported["last_decision"] == "DEFER"
    assert registry.get_state("initiative_governor_agent") is None
