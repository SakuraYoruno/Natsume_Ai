from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from unittest.mock import patch

from host_brain.app.agents.react_loop import run_react_loop
from host_brain.app.agents.tool_registry import (
    REACT_DELEGATE_GOVERNED_ADMISSION_REQUIRED,
    REACT_SKILL_GOVERNED_ADMISSION_REQUIRED,
    _TOOL_REGISTRY,
    _tool_dispatch_sub_agent,
    _tool_run_skill,
    get_tools_for_agent,
)
from host_brain.capability_plane.broker import CapabilityBroker
from host_brain.capability_plane.policies import DescriptorCapabilityPolicy


class _TwoStepGateway:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, _messages):  # noqa: ANN001, ANN201
        self.calls += 1
        if self.calls == 1:
            return {
                "content": json.dumps(
                    {
                        "thought": "request a skill",
                        "action_type": "tool_call",
                        "action": {
                            "tool": "run_skill",
                            "args": {
                                "skill_name": "memory_store",
                                "task_text": "remember this",
                            },
                        },
                    }
                )
            }
        return {
            "content": json.dumps(
                {
                    "thought": "the action was blocked",
                    "action_type": "final_answer",
                    "final_answer": "The outer runtime must admit this request.",
                }
            )
        }


class _ApprovingGovernor:
    def evaluate_tool_call(self, **_kwargs):  # noqa: ANN003, ANN201
        return SimpleNamespace(token="GOV_APPROVE")


class _DelegateGateway:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, _messages):  # noqa: ANN001, ANN201
        self.calls += 1
        if self.calls == 1:
            return {
                "content": json.dumps(
                    {
                        "thought": "request another agent",
                        "action_type": "tool_call",
                        "action": {
                            "tool": "dispatch_sub_agent",
                            "args": {
                                "agent_id": "translate_agent",
                                "task": "translate this",
                            },
                        },
                    }
                )
            }
        return {
            "content": json.dumps(
                {
                    "thought": "the action was blocked",
                    "action_type": "final_answer",
                    "final_answer": "The outer runtime must admit this request.",
                }
            )
        }


def test_react_toolsets_do_not_advertise_nested_skill_execution() -> None:
    for agent_id in ("intent_agent", "emotion_agent", "retrieval_agent"):
        tool_ids = {
            tool.tool_id
            for tool in get_tools_for_agent(agent_id, allow_side_effects=True)
        }
        assert "run_skill" not in tool_ids
        assert "dispatch_sub_agent" not in tool_ids


def test_real_react_tool_path_fails_closed_before_legacy_skill_executor() -> None:
    broker = CapabilityBroker(policy=DescriptorCapabilityPolicy())
    gateway = _TwoStepGateway()

    with (
        patch(
            "host_brain.composition.process_services.get_capability_broker",
            return_value=broker,
        ),
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
            # Inject the retired spec deliberately. Production discovery omits
            # it; this proves the deeper execution boundary also fails closed.
            tools=[_TOOL_REGISTRY["run_skill"]],
            gateway=gateway,
            max_steps=2,
            allow_side_effects=True,
            context={"request_id": "req-ne01-react-skill-cutover"},
            governor=_ApprovingGovernor(),
        )

    execute_skill.assert_not_called()
    build_legacy_ports.assert_not_called()
    assert gateway.calls == 2
    assert result.stopped_reason == "FINAL_ANSWER"
    observation = result.steps[0].observation
    assert observation == {
        "error": REACT_SKILL_GOVERNED_ADMISSION_REQUIRED,
        "reason_code": REACT_SKILL_GOVERNED_ADMISSION_REQUIRED,
        "result": None,
        "side_effects_executed": False,
    }


def test_real_react_delegate_path_fails_closed_before_nested_dispatch() -> None:
    gateway = _DelegateGateway()

    with patch(
        "host_brain.app.sub_agent_protocol.get_sub_agent_dispatcher"
    ) as get_dispatcher:
        result = run_react_loop(
            agent_id="intent_agent",
            user_input="translate this",
            tools=[_TOOL_REGISTRY["dispatch_sub_agent"]],
            gateway=gateway,
            max_steps=2,
            allow_side_effects=True,
            context={"request_id": "req-ne01-react-delegate-cutover"},
            governor=_ApprovingGovernor(),
        )

    get_dispatcher.assert_not_called()
    assert gateway.calls == 2
    assert result.stopped_reason == "FINAL_ANSWER"
    assert result.steps[0].observation == {
        "error": REACT_DELEGATE_GOVERNED_ADMISSION_REQUIRED,
        "reason_code": REACT_DELEGATE_GOVERNED_ADMISSION_REQUIRED,
        "result": None,
        "side_effects_executed": False,
    }


def test_compatibility_executor_has_no_legacy_runtime_composition_or_call() -> None:
    source = inspect.getsource(_tool_run_skill)

    assert "build_executive_runtime_application_ports" not in source
    assert "from host_brain.executive_runtime.skill_execution" not in source
    assert "outcome = _execute_skill(" not in source
    blocked = _tool_run_skill({"skill_name": "memory_store"})
    assert blocked["reason_code"] == REACT_SKILL_GOVERNED_ADMISSION_REQUIRED
    assert blocked["execution_surface"] == "HostCommand -> immutable TaskGraph"
    assert blocked["side_effects_executed"] is False


def test_delegate_compatibility_executor_has_no_nested_dispatch() -> None:
    source = inspect.getsource(_tool_dispatch_sub_agent)

    assert "get_sub_agent_dispatcher" not in source
    assert "dispatcher.dispatch" not in source
    blocked = _tool_dispatch_sub_agent(
        {"agent_id": "translate_agent", "task": "translate this"}
    )
    assert blocked["reason_code"] == REACT_DELEGATE_GOVERNED_ADMISSION_REQUIRED
    assert blocked["execution_surface"] == "HostCommand -> immutable TaskGraph"
    assert blocked["side_effects_executed"] is False
