from __future__ import annotations

from host_brain.tribe.circadian_agent import CircadianAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_circadian_agent_refresh_and_export_runtime_state() -> None:
    registry = TribeStateRegistry()
    agent = CircadianAgent(state_registry=registry)

    state = agent.refresh_state(source="unit_test", ts_ms=1712620800000)  # 2024-04-09T00:00:00Z
    exported = agent.export_runtime_state()

    assert str(state["phase"]) in {"dawn", "day", "dusk", "night"}
    assert 0.0 <= float(exported["phase_energy_baseline"]) <= 1.0
    assert 0.0 <= float(exported["proactive_multiplier"]) <= 1.2
    assert 0.0 <= float(exported["sleep_pressure"]) <= 1.0
    assert int(exported["response_latency_ms"]) >= 200
