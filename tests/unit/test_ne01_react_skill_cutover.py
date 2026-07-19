from __future__ import annotations

import json
from unittest.mock import patch

from host_brain.app.agents.react_loop import run_react_loop
from host_brain.app.agents.tool_registry import get_tools_for_agent
from host_brain.integrations.graph_governed_tool_node import (
    REACT_TOOL_GOVERNED_ADMISSION_REQUIRED,
)
from host_brain.tools.contracts import ToolSpec


class _SingleProposalGateway:
    def __init__(self, *, tool_id: str, args: dict[str, object]) -> None:
        self._tool_id = tool_id
        self._args = dict(args)
        self.calls = 0

    def complete(self, _messages):  # noqa: ANN001, ANN201
        self.calls += 1
        return {
            "content": json.dumps(
                {
                    "thought": "request a nested action",
                    "action_type": "tool_call",
                    "action": {"tool": self._tool_id, "args": self._args},
                }
            )
        }


def _injected_spec(tool_id: str) -> ToolSpec:
    return ToolSpec(
        tool_id=tool_id,
        display_name=tool_id,
        description="retired nested action",
        parameters={"type": "object", "properties": {}, "required": []},
        has_side_effects=True,
        executor=lambda _args: {"must_not_run": True},
    )


def test_react_toolsets_do_not_advertise_nested_skill_or_delegate() -> None:
    for agent_id in ("intent_agent", "emotion_agent", "retrieval_agent"):
        tool_ids = {
            tool.tool_id
            for tool in get_tools_for_agent(agent_id, allow_side_effects=True)
        }
        assert "run_skill" not in tool_ids
        assert "dispatch_sub_agent" not in tool_ids


def test_injected_skill_proposal_stops_before_legacy_runtime() -> None:
    gateway = _SingleProposalGateway(
        tool_id="run_skill",
        args={"skill_name": "memory_store", "task_text": "remember this"},
    )

    with (
        patch(
            "host_brain.executive_runtime.skill_execution._execute_skill"
        ) as execute_skill,
        patch(
            "host_brain.app.runtime_port_wiring.build_executive_runtime_application_ports"
        ) as build_legacy_ports,
    ):
        result = run_react_loop(
            agent_id="intent_agent",
            user_input="remember this",
            tools=[_injected_spec("run_skill")],
            gateway=gateway,
            max_steps=2,
            allow_side_effects=True,
            context={"request_id": "req-ne01-react-skill-cutover"},
        )

    execute_skill.assert_not_called()
    build_legacy_ports.assert_not_called()
    assert gateway.calls == 1
    assert result.stopped_reason == "TOOL_GOVERNED_ADMISSION_REQUIRED"
    assert result.steps[0].observation == {
        "error": REACT_TOOL_GOVERNED_ADMISSION_REQUIRED,
        "reason_code": REACT_TOOL_GOVERNED_ADMISSION_REQUIRED,
        "result": None,
        "side_effects_executed": False,
        "required_execution_surface": "new immutable TaskGraph revision",
    }


def test_injected_delegate_proposal_stops_before_nested_dispatch() -> None:
    gateway = _SingleProposalGateway(
        tool_id="dispatch_sub_agent",
        args={"agent_id": "translate_agent", "task": "translate this"},
    )

    with patch(
        "host_brain.app.sub_agent_protocol.get_sub_agent_dispatcher"
    ) as get_dispatcher:
        result = run_react_loop(
            agent_id="intent_agent",
            user_input="translate this",
            tools=[_injected_spec("dispatch_sub_agent")],
            gateway=gateway,
            max_steps=2,
            allow_side_effects=True,
            context={"request_id": "req-ne01-react-delegate-cutover"},
        )

    get_dispatcher.assert_not_called()
    assert gateway.calls == 1
    assert result.stopped_reason == "TOOL_GOVERNED_ADMISSION_REQUIRED"
    assert result.steps[0].observation["reason_code"] == (
        REACT_TOOL_GOVERNED_ADMISSION_REQUIRED
    )
    assert result.steps[0].observation["side_effects_executed"] is False
