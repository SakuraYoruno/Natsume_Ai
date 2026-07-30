from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.mirror_neuron_agent import MirrorNeuronAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


class _FakeSocialBrain:
    def get_bond_context(self, *, session_id: str, user_id: str):  # noqa: ANN001
        return {
            "bond_vector": {
                "trust": 0.76,
                "affinity": 0.72,
                "safety": 0.7,
                "repair_confidence": 0.66,
            }
        }


def test_mirror_neuron_agent_apply_resonance(monkeypatch) -> None:
    registry = TribeStateRegistry()
    registry.set_state(
        "endocrine_agent",
        {"gate_state": "open", "stress_index": 0.28, "energy_reserve": 0.8, "homeostasis_ok": True},
        writer="unit",
    )
    monkeypatch.setattr(
        "host_brain.tribe.mirror_neuron_agent.get_social_brain_agent",
        lambda: _FakeSocialBrain(),
    )

    agent = MirrorNeuronAgent(state_registry=registry)
    result = agent.apply_resonance(
        user_id="u-mirror",
        session_id="s-mirror",
        emotion_tag="SAD",
        emotion_intensity=0.86,
        source="unit_test",
        ts_ms=98765,
    )
    exported = agent.export_runtime_state()

    assert result["emotion_tag"] == "SAD"
    assert result["resonance_strength"] > 0.0
    assert result["co_regulation_hint"] in {"resonate", "stabilize"}
    assert exported["resonance_count"] >= 1
    assert exported["last_emotion_tag"] == "SAD"
    assert abs(float(exported["last_resonance_strength"]) - float(result["resonance_strength"])) < 1e-8


def test_mirror_neuron_retires_user_affect_and_legacy_bond_fanout_with_canonical_life(monkeypatch) -> None:
    registry = TribeStateRegistry(persist_throttle=999999)
    agent = MirrorNeuronAgent(
        state_registry=registry,
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
    )

    def _unexpected(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("canonical Life must not invoke legacy MirrorNeuron dependencies")

    monkeypatch.setattr("host_brain.tribe.mirror_neuron_agent.get_social_brain_agent", _unexpected)
    monkeypatch.setattr("host_brain.tribe.mirror_neuron_agent.get_limbic_agent", _unexpected)
    monkeypatch.setattr("host_brain.tribe.mirror_neuron_agent.get_endocrine_agent", _unexpected)
    monkeypatch.setattr("host_brain.tribe.mirror_neuron_agent.get_interoception_agent", _unexpected)
    monkeypatch.setattr("host_brain.tribe.mirror_neuron_agent.get_pain_reward_agent", _unexpected)
    monkeypatch.setattr("host_brain.tribe.mirror_neuron_agent.get_default_mode_agent", _unexpected)
    monkeypatch.setattr("host_brain.tribe.mirror_neuron_agent.emit_tribe_event", _unexpected)
    monkeypatch.setattr("host_brain.tribe.event_bus.get_tribe_event_bus", _unexpected)

    result = agent.apply_resonance(
        user_id="u-canonical",
        session_id="s-canonical",
        emotion_tag="SAD",
        emotion_intensity=0.91,
        source="unit_test",
        ts_ms=123,
    )
    assert result == {
        "status": "RETIRED_CANONICAL_LIFE",
        "authority": "CANONICAL_LIFE_JOURNAL",
        "reason_code": "LEGACY_MIRROR_NEURON_RESONANCE_RETIRED",
        "operation": "apply_resonance",
    }
    assert agent.generate_empathy_response_hint(
        resonance_intensity=0.95,
        user_emotion_tag="SAD",
        bond_stage="SOULMATE",
    ) is None
    assert agent.export_runtime_state()["status"] == "RETIRED_CANONICAL_LIFE"

    agent.register_subscriptions()
    event = SimpleNamespace(
        payload={
            "user_id": "u-canonical",
            "session_id": "s-canonical",
            "emotion_tag": "SAD",
            "emotion_intensity": 0.91,
        },
        ts_ms=124,
    )
    asyncio.run(agent._on_emotion_inference_updated(event))
    asyncio.run(agent._on_interaction_recorded(event))
    asyncio.run(agent._on_emotion_state_changed(event))

    assert registry.get_state("mirror_neuron_agent") is None
