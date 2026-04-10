from __future__ import annotations

from host_brain.tribe.cerebellum_agent import CerebellumAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_cerebellum_agent_correction_on_failure_and_latency() -> None:
    agent = CerebellumAgent(state_registry=TribeStateRegistry())

    failed = agent.evaluate_correction(
        execution_feedback={"plan_id": "p1", "accepted": False, "dispatched": False, "latency_ms": 0, "reason": "PUSH_FAILED"},
        source="unit_test",
        ts_ms=100,
    )
    slow = agent.evaluate_correction(
        execution_feedback={"plan_id": "p2", "accepted": True, "dispatched": True, "latency_ms": 1300},
        source="unit_test",
        ts_ms=200,
    )
    exported = agent.export_runtime_state()

    assert failed["strategy"] in {"SAFE_FALLBACK", "RETRY_ONCE"}
    assert slow["strategy"] == "DEGRADE_OUTPUT"
    assert exported["correction_count"] >= 2
