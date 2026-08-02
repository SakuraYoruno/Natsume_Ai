from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.peripheral_agent import PeripheralAgent
from host_brain.tribe.tribe_state import TribeStateRegistry
from host_brain.tribe.vestibular_agent import VestibularAgent


def _canonical_ports() -> TribeApplicationPorts:
    return TribeApplicationPorts(
        affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
    )


def test_canonical_vestibular_retires_global_jitter_to_affect_fanout(monkeypatch) -> None:
    class _Bus:
        def __init__(self) -> None:
            self.subscriptions: list[tuple[str, object]] = []

        def subscribe(self, event_type: str, handler: object) -> None:
            self.subscriptions.append((event_type, handler))

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("canonical vestibular must not invoke a legacy affect dependency")

    registry = TribeStateRegistry()
    agent = VestibularAgent(application_ports=_canonical_ports(), state_registry=registry)
    bus = _Bus()
    monkeypatch.setattr("host_brain.tribe.event_bus.get_tribe_event_bus", lambda: bus)
    monkeypatch.setattr("host_brain.tribe.vestibular_agent.emit_tribe_event", _forbidden)
    monkeypatch.setattr("host_brain.tribe.vestibular_agent.get_limbic_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.vestibular_agent.get_ans_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.vestibular_agent.get_endocrine_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.vestibular_agent.get_pain_reward_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.vestibular_agent.get_interoception_agent", _forbidden)

    agent.register_subscriptions()
    event = SimpleNamespace(payload={"decision": "DEFER"}, ts_ms=123)
    results = [
        agent.evaluate_stability(source="test"),
        asyncio.run(agent._on_mode_transition(event)),
        asyncio.run(agent._on_platform_signal(event)),
        asyncio.run(agent._on_initiative_decision(event)),
    ]
    exported = agent.export_runtime_state()

    assert bus.subscriptions == []
    assert {result["reason_code"] for result in results if result is not None} == {
        "LEGACY_VESTIBULAR_STABILITY_WRITER_RETIRED"
    }
    assert agent.generate_stability_assessment(stability_status="critical", jitter_index=0.9) is None
    assert exported["status"] == "RETIRED_CANONICAL_LIFE"
    assert exported["projection"] == "UNAVAILABLE"
    assert exported["jitter_index"] is None
    assert registry.get_state("vestibular_agent") is None


def test_canonical_peripheral_retires_global_expression_and_device_state(monkeypatch) -> None:
    class _Bus:
        def __init__(self) -> None:
            self.subscriptions: list[tuple[str, object]] = []

        def subscribe(self, event_type: str, handler: object) -> None:
            self.subscriptions.append((event_type, handler))

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("canonical peripheral must not invoke a legacy affect dependency")

    registry = TribeStateRegistry()
    agent = PeripheralAgent(application_ports=_canonical_ports(), state_registry=registry)
    bus = _Bus()
    monkeypatch.setattr("host_brain.tribe.event_bus.get_tribe_event_bus", lambda: bus)
    monkeypatch.setattr("host_brain.tribe.peripheral_agent.get_interoception_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.peripheral_agent.get_pain_reward_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.peripheral_agent.get_limbic_agent", _forbidden)

    agent.register_subscriptions()
    event = SimpleNamespace(
        payload={"emotion_tag": "JOY", "intensity": 0.9, "platform": "wechat", "gate": "open"},
        ts_ms=123,
    )
    results = [
        asyncio.run(getattr(agent, name)(event))
        for name in (
            "_on_emotion_state_changed",
            "_on_immune_block",
            "_on_platform_connected",
            "_on_platform_disconnected",
            "_on_peripheral_state_updated",
            "_on_gate_state_changed",
            "_on_platform_mute_changed",
        )
    ]
    exported = agent.export_runtime_state()

    assert bus.subscriptions == []
    assert {result["reason_code"] for result in results if result is not None} == {
        "LEGACY_PERIPHERAL_EXPRESSION_WRITER_RETIRED"
    }
    assert agent.get_peripheral_state()["reason_code"] == "LEGACY_PERIPHERAL_EXPRESSION_WRITER_RETIRED"
    assert agent.analyze_peripheral_context() is None
    assert exported["status"] == "RETIRED_CANONICAL_LIFE"
    assert exported["projection"] == "UNAVAILABLE"
    assert registry.get_state("peripheral_agent") is None
