from __future__ import annotations

from unittest.mock import patch

from host_brain.tribe.glymphatic_agent import GlymphaticAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


class _FakeHippo:
    def resolve_memory_layer_snapshot(self) -> dict:
        return {"episodic": {"content": "a"}, "semantic": {"fact": "b"}, "autobiographical": {}, "last_write_type": "semantic"}


class _FakeDefaultMode:
    def export_runtime_state(self) -> dict:
        return {"continuity_score": 0.72}


def test_glymphatic_agent_recovery_cycle(monkeypatch) -> None:
    registry = TribeStateRegistry()
    registry.set_state(
        "endocrine_agent",
        {"gate_state": "open", "stress_index": 0.26, "energy_reserve": 0.77, "homeostasis_ok": True},
        writer="unit",
    )
    registry.set_state("proprioception_agent", {"cognitive_load_index": 0.28, "continuity_hint": 0.6}, writer="unit")
    registry.set_state("circadian_agent", {"phase": "night", "sleep_pressure": 0.82, "proactive_multiplier": 0.4}, writer="unit")
    monkeypatch.setattr("host_brain.tribe.hippocampus_agent.get_hippocampus_agent", lambda: _FakeHippo())
    monkeypatch.setattr("host_brain.tribe.default_mode_agent.get_default_mode_agent", lambda: _FakeDefaultMode())

    agent = GlymphaticAgent(state_registry=registry)
    cycle = agent.run_recovery_cycle(source="unit_test", force=False, now_ts_ms=222)
    exported = agent.export_runtime_state()

    assert cycle["executed"] is True
    assert cycle["consolidation_score"] > 0.0
    assert exported["phase"] == "night"
    assert exported["consolidation_score"] > 0.0


def test_glymphatic_agent_suppresses_repeated_noop_cycle_event(monkeypatch) -> None:
    registry = TribeStateRegistry()
    registry.set_state("circadian_agent", {"phase": "day", "sleep_pressure": 0.1}, writer="unit")
    registry.set_state("endocrine_agent", {"stress_index": 0.2}, writer="unit")
    registry.set_state("proprioception_agent", {"cognitive_load_index": 0.2, "continuity_hint": 0.2}, writer="unit")
    monkeypatch.setattr("host_brain.tribe.hippocampus_agent.get_hippocampus_agent", lambda: _FakeHippo())
    monkeypatch.setattr("host_brain.tribe.default_mode_agent.get_default_mode_agent", lambda: _FakeDefaultMode())

    agent = GlymphaticAgent(state_registry=registry)

    with patch("host_brain.tribe.glymphatic_agent.emit_tribe_event") as mock_emit:
        first = agent.run_recovery_cycle(source="unit_test", force=False, now_ts_ms=1000)
        second = agent.run_recovery_cycle(source="unit_test", force=False, now_ts_ms=1001)

    assert first["executed"] is False
    assert first["event_suppressed"] is False
    assert second["executed"] is False
    assert second["event_suppressed"] is True
    assert mock_emit.call_count == 1
