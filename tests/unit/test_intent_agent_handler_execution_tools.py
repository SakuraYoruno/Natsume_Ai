from __future__ import annotations

from unittest.mock import patch

from host_brain.agent_runtime.agent_registry import AGENT_ID_INTENT
from host_brain.app.handlers.intent_agent_handler import (
    INTENT_GOVERNED_EXECUTION_REQUIRED,
    intent_agent_handler,
)


class _Gateway:
    def __init__(self) -> None:
        self.messages = []

    def complete(self, messages):  # noqa: ANN001, ANN201
        self.messages.append(messages)
        return {
            "content": "2+2 等于 4。",
            "model_attempt": {
                "request_id": "delegate-parent:subagent-model:1",
                "success": True,
                "budget_measurement": {
                    "schema_version": "Natsume.model_runtime.budget_measurement.v1",
                    "measurement_ref": "model-budget://delegate-parent",
                    "attempt_count": 1,
                    "retry_count": 0,
                    "token_count": 12,
                },
            },
        }


def _planned_payload(*, tool_id: str) -> dict[str, object]:
    return {
        "request_id": "intent-001",
        "user_input": "执行任务",
        "tool_planner": {
            "gateway_status": "OK",
            "payload": {
                "plan_summary": "执行前先申请受治理 capability",
                "tools": [{"tool_id": tool_id}],
            },
        },
        "safety_verifier": {"payload": {"risk_level": "LOW"}},
    }


def test_run_skill_request_is_handoff_not_direct_execution() -> None:
    with patch("host_brain.executive_runtime.skill_execution._execute_skill") as execute_skill:
        result = intent_agent_handler(_planned_payload(tool_id="run_skill"))

    execute_skill.assert_not_called()
    handoff = result["intent_execution_handoff"]
    assert handoff["request_material"]["requested_tools"] == ["run_skill"]
    assert handoff["requirements"] == {
        "durable_principal": True,
        "governor_grant": True,
        "capability_broker": True,
        "durable_receipt": True,
    }
    assert handoff["side_effects_executed"] is False
    assert result["intent_agent_trace"]["reason_code"] == INTENT_GOVERNED_EXECUTION_REQUIRED
    assert result["intent_agent_trace"]["side_effects_executed"] is False
    assert result["agent_artifacts"][AGENT_ID_INTENT]["artifact_ref"].endswith("_handoff")
    assert "react_scratchpad" not in result


def test_sub_agent_request_does_not_resolve_dispatcher() -> None:
    with patch("host_brain.app.sub_agent_protocol.get_sub_agent_dispatcher") as get_dispatcher:
        result = intent_agent_handler(_planned_payload(tool_id="dispatch_sub_agent"))

    get_dispatcher.assert_not_called()
    assert result["intent_execution_handoff"]["request_material"]["requested_tools"] == [
        "dispatch_sub_agent"
    ]
    assert result["intent_agent_trace"]["execution_surface"] == "executive_runtime"


def test_high_safety_risk_is_preserved_for_governed_execution() -> None:
    payload = _planned_payload(tool_id="run_skill")
    payload["safety_verifier"] = {"payload": {"risk_level": "HIGH"}}

    result = intent_agent_handler(payload)

    artifact = result["agent_artifacts"][AGENT_ID_INTENT]
    assert "SAFETY_RISK:HIGH" in artifact["risk_flags"]
    assert artifact["confidence"] == 0.2
    assert result["intent_execution_handoff"]["request_material"]["trusted_authority"] is False


def test_governed_direct_delegate_uses_bound_model_gateway() -> None:
    gateway = _Gateway()

    result = intent_agent_handler(
        {"request_id": "delegate-parent", "user_input": "2+2 等于多少？"},
        {
            "request_id": "delegate-parent",
            "source": "executive_router_dispatch",
            "gateway": gateway,
        },
    )

    assert gateway.messages[0][-1]["content"] == "2+2 等于多少？"
    assert result["agent_output"]["text"] == "2+2 等于 4。"
    assert result["intent_agent_trace"]["mode"] == "model_backed_governed_execution_handoff"
    assert result["intent_agent_trace"]["model_attempt"]["request_id"].startswith(
        "delegate-parent:"
    )
