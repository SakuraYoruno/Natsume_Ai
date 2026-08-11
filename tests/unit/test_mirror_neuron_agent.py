from __future__ import annotations

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
