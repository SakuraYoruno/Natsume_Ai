from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from host_brain.app.handlers.intent_agent_handler import intent_agent_handler


class _GatewayToolThenAnswer:
    def __init__(self, *, tool_name: str, action_args: dict[str, object], final_answer: str) -> None:
        self._tool_name = tool_name
        self._action_args = dict(action_args or {})
        self._final_answer = final_answer
        self._calls = 0

    def complete(self, _messages):  # noqa: ANN001
        self._calls += 1
        if self._calls == 1:
            return {
                "content": json.dumps(
                    {
                        "thought": "execute first",
                        "action_type": "tool_call",
                        "action": {
                            "tool": self._tool_name,
                            "args": self._action_args,
                        },
                    },
                    ensure_ascii=False,
                )
            }
        return {
            "content": json.dumps(
                {
                    "thought": "done",
                    "action_type": "final_answer",
                    "final_answer": self._final_answer,
                },
                ensure_ascii=False,
            )
        }


def test_intent_agent_handler_can_execute_run_skill_tool() -> None:
    fake_outcome = SimpleNamespace(
        success=True,
        result_text="memory stored",
        error_text="",
        skill_family="memory",
        route_type="skill",
        latency_ms=11,
    )
    gateway = _GatewayToolThenAnswer(
        tool_name="run_skill",
        action_args={"skill_name": "memory_store", "task_text": "remember this"},
        final_answer="stored",
    )

    with patch.dict("os.environ", {"INTENT_SUBGRAPH_ENABLED": "true", "INTENT_REACT_MAX_STEPS": "3"}, clear=False), patch(
        "host_brain.app.executive_runtime_core._execute_skill",
        return_value=fake_outcome,
    ):
        out = intent_agent_handler(
            {"request_id": "req_intent_tool_001", "user_input": "remember this"},
            context={"request_id": "req_intent_tool_001", "session_id": "sess_intent_tool_001", "gateway": gateway},
        )

    scratchpad = dict(out.get("react_scratchpad") or {})
    steps = list(scratchpad.get("steps") or [])
    assert len(steps) >= 2
    assert steps[0]["tool_id"] == "run_skill"
    assert steps[0]["outcome"] == "OBSERVED"
    assert steps[0]["payload_hash"]
    assert "action" not in steps[0]
    assert "observation" not in steps[0]
    assert scratchpad.get("decision_summary") == "stored"
    assert out["agent_output"]["text"] == "stored"


def test_intent_agent_handler_can_dispatch_sub_agent_tool() -> None:
    class _FakeDispatcher:
        async def dispatch(self, req):  # noqa: ANN001
            return SimpleNamespace(
                success=True,
                result=f"delegate_ok:{req.agent_id}",
                error=None,
                latency_ms=19,
                audit_refs={"mock": True},
            )

    gateway = _GatewayToolThenAnswer(
        tool_name="dispatch_sub_agent",
        action_args={"agent_id": "translate_agent", "task": "translate this"},
        final_answer="delegated",
    )

    with patch.dict("os.environ", {"INTENT_SUBGRAPH_ENABLED": "true", "INTENT_REACT_MAX_STEPS": "3"}, clear=False), patch(
        "host_brain.app.sub_agent_protocol.get_sub_agent_dispatcher",
        return_value=_FakeDispatcher(),
    ):
        out = intent_agent_handler(
            {"request_id": "req_intent_tool_002", "user_input": "fix crash"},
            context={"request_id": "req_intent_tool_002", "session_id": "sess_intent_tool_002", "gateway": gateway},
        )

    scratchpad = dict(out.get("react_scratchpad") or {})
    steps = list(scratchpad.get("steps") or [])
    assert len(steps) >= 2
    assert steps[0]["tool_id"] == "dispatch_sub_agent"
    assert steps[0]["outcome"] == "OBSERVED"
    assert steps[0]["payload_hash"]
    assert "action" not in steps[0]
    assert "observation" not in steps[0]
    assert out["agent_output"]["text"] == "delegated"
