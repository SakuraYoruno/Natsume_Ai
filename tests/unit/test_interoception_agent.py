from __future__ import annotations

from host_brain.tribe.interoception_agent import InteroceptionAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


class _FakeSomatic:
    def export_runtime_state(self) -> dict:
        return {"fatigue_level": 0.42}


def test_interoception_agent_evaluate_profile(monkeypatch) -> None:
    registry = TribeStateRegistry()
    registry.set_state(
        "endocrine_agent",
        {"gate_state": "open", "stress_index": 0.52, "energy_reserve": 0.46, "homeostasis_ok": False},
        writer="unit",
    )
    registry.set_state("proprioception_agent", {"cognitive_load_index": 0.57, "readiness_hint": 0.4, "continuity_hint": 0.3}, writer="unit")
    registry.set_state("circadian_agent", {"phase": "night", "sleep_pressure": 0.76, "proactive_multiplier": 0.35}, writer="unit")
    registry.set_state("immune_agent", {"risk_level": "medium", "block_rate": 0.1, "counters": {"block": 1}}, writer="unit")
    monkeypatch.setattr("host_brain.tribe.somatic_agent.get_somatic_agent", lambda: _FakeSomatic())

    agent = InteroceptionAgent(state_registry=registry)
    profile = agent.evaluate_profile(source="unit_test", ts_ms=101)
    exported = agent.export_runtime_state()

    assert profile["interoceptive_index"] > 0.0
    assert profile["recovery_urgency"] > 0.0
    assert 0.0 <= exported["body_budget"] <= 1.0
    assert exported["sample_count"] >= 1
