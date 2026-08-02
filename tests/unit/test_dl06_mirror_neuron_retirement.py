from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.mirror_neuron_agent import MirrorNeuronAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_canonical_life_retires_global_mirror_resonance_loop(monkeypatch) -> None:
    class _Bus:
        def __init__(self) -> None:
            self.subscriptions: list[tuple[str, object]] = []

        def subscribe(self, event_type: str, handler: object) -> None:
            self.subscriptions.append((event_type, handler))

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("canonical mirror path must not resolve legacy organs")

    bus = _Bus()
    registry = TribeStateRegistry()
    agent = MirrorNeuronAgent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
        state_registry=registry,
    )
    monkeypatch.setattr(
        "host_brain.tribe.event_bus.get_tribe_event_bus",
        lambda: bus,
    )
    monkeypatch.setattr(
        "host_brain.tribe.mirror_neuron_agent.get_social_brain_agent",
        _forbidden,
    )
    monkeypatch.setattr(
        "host_brain.tribe.mirror_neuron_agent.get_limbic_agent",
        _forbidden,
    )
    monkeypatch.setattr(
        "host_brain.tribe.mirror_neuron_agent.complete_tribe_compiled_sync",
        _forbidden,
    )

    agent.register_subscriptions()
    apply_result = agent.apply_resonance(
        user_id="person-dl06",
        session_id="session-dl06",
        emotion_tag="SAD",
        emotion_intensity=0.9,
        source="legacy-event",
    )
    event = SimpleNamespace(
        payload={"emotion_tag": "ANGRY", "emotion_intensity": 0.8},
        ts_ms=123,
    )
    handler_results = [
        asyncio.run(getattr(agent, handler_name)(event))
        for handler_name in (
            "_on_emotion_inference_updated",
            "_on_interaction_recorded",
            "_on_emotion_state_changed",
        )
    ]

    assert bus.subscriptions == []
    assert apply_result["reason_code"] == "LEGACY_MIRROR_RESONANCE_RETIRED"
    assert apply_result["relationship_subject_id"] == "person-dl06"
    assert {
        item["reason_code"] for item in handler_results
    } == {"LEGACY_MIRROR_RESONANCE_RETIRED"}
    assert agent.generate_empathy_response_hint(resonance_intensity=0.9) is None
    assert registry.get_state("mirror_neuron_agent") is None
    assert agent.export_runtime_state()["status"] == "RETIRED_CANONICAL_LIFE"
