from __future__ import annotations

from host_brain.tribe.motor_cortex_agent import MotorCortexAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


class _FakeGovernor:
    def evaluate_initiative(self, **kwargs):  # noqa: ANN003
        return {"decision": "BLOCK", "reason_code": "LOW_READINESS"}


def test_motor_cortex_agent_plan_and_dispatch() -> None:
    agent = MotorCortexAgent(state_registry=TribeStateRegistry())
    planned = agent.plan_action(
        action="push_gate_control",
        payload={"gate_open": True},
        target_agent="edge.channel",
        command_type="gate_control_command",
        session_id="sess-motor",
        user_id="u-motor",
    )
    assert planned["status"] == "PLANNED"
    outcome = agent.dispatch_action(plan=planned["plan"], executor=lambda plan: {"ok": True, "accepted": True, "dispatched": True})  # noqa: ARG005
    assert outcome["ok"] is True
    runtime_state = agent.export_runtime_state()
    assert runtime_state["last_dispatch_ok"] is True
    assert runtime_state["last_target_agent"] == "edge.channel"


def test_motor_cortex_agent_respects_gate(monkeypatch) -> None:
    monkeypatch.setattr("host_brain.tribe.motor_cortex_agent.get_initiative_governor_agent", lambda: _FakeGovernor())
    agent = MotorCortexAgent(state_registry=TribeStateRegistry())
    planned = agent.plan_action(
        action="proactive_voice",
        payload={"text": "hi"},
        require_gate=True,
        context={},
    )
    assert planned["status"] == "BLOCKED"
    assert planned["reason_code"] == "LOW_READINESS"
