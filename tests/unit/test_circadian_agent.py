from __future__ import annotations

from unittest.mock import patch

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


def test_circadian_agent_export_runtime_state_is_read_only() -> None:
    registry = TribeStateRegistry()
    agent = CircadianAgent(state_registry=registry)
    registry.set_state(
        "circadian_agent",
        {
            "phase": "night",
            "local_hour": 23,
            "phase_energy_baseline": 0.34,
            "proactive_multiplier": 0.36,
            "recovery_bias": 0.86,
            "sleep_pressure": 0.9,
            "response_latency_ms": 940,
            "ts_ms": 1712620800000,
        },
        writer="unit_test",
    )

    with patch.object(CircadianAgent, "refresh_state", side_effect=AssertionError("export must be read-only")):
        exported = agent.export_runtime_state()

    assert exported["phase"] in {"dawn", "day", "dusk", "night"}
    assert int(exported["response_latency_ms"]) >= 200
