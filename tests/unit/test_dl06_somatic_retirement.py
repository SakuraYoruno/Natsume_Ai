from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.somatic_agent import SomaticAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_canonical_somatic_retires_global_body_and_avatar_side_effects(monkeypatch) -> None:
    class _Bus:
        def __init__(self) -> None:
            self.subscriptions: list[tuple[str, object]] = []

        def subscribe(self, event_type: str, handler: object) -> None:
            self.subscriptions.append((event_type, handler))

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("canonical Somatic must not invoke a legacy affect dependency")

    registry = TribeStateRegistry()
    agent = SomaticAgent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
        state_registry=registry,
    )
    bus = _Bus()
    monkeypatch.setattr("host_brain.tribe.event_bus.get_tribe_event_bus", lambda: bus)
    monkeypatch.setattr("host_brain.tribe.somatic_agent.get_interoception_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.somatic_agent.get_endocrine_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.somatic_agent.get_limbic_agent", _forbidden)
    monkeypatch.setattr("host_brain.tribe.somatic_agent.get_pain_reward_agent", _forbidden)

    agent.register_subscriptions()
    event = SimpleNamespace(
        payload={
            "fatigue_level": 0.9,
            "loneliness": 0.9,
            "current_expression": "sad",
            "last_emotion_tag": "SAD",
        },
        ts_ms=123,
    )
    results = [
        asyncio.run(getattr(agent, name)(event))
        for name in (
            "_on_body_sense_updated",
            "_on_loneliness_signal",
            "_on_somatic_state_updated",
        )
    ]
    exported = agent.export_runtime_state()

    assert bus.subscriptions == []
    assert agent.get_somatic_state() == {}
    assert agent.generate_somatic_narrative(somatic_pressure=0.9, face_confidence=0.9) is None
    assert {
        result["reason_code"] for result in results if result is not None
    } == {"LEGACY_SOMATIC_GLOBAL_BODY_WRITE_RETIRED"}
    assert exported["status"] == "RETIRED_CANONICAL_LIFE"
    assert exported["projection"] == "UNAVAILABLE"
    assert exported["last_emotion_tag"] is None
    assert registry.get_state("somatic_agent") is None
