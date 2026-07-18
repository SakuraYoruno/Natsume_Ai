from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

from host_brain.app.agents.react_loop import run_react_loop
from host_brain.app.agents.tool_registry import (
    REACT_TOOL_GOVERNED_ADMISSION_REQUIRED,
    execute_tool,
    get_tools_for_agent,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.capability_plane.journal import SQLiteCapabilityExecutionJournal
from host_brain.capability_plane.production_composition import (
    build_production_capability_runtime,
)
from host_brain.integrations.graph_governed_tool_node import GovernedToolNode
from host_brain.tools.contracts import ToolSpec
from host_brain.app.handlers.emotion_agent_handler import (
    EMOTION_AGENT_GOVERNED_OUTPUT_REQUIRED,
    emotion_agent_handler,
)


class _ToolThenFinishGateway:
    def __init__(self, tool_id: str) -> None:
        self._tool_id = tool_id
        self.calls = 0

    def complete(self, _messages):  # noqa: ANN001, ANN201
        self.calls += 1
        if self.calls == 1:
            return {
                "content": json.dumps(
                    {
                        "thought": "propose a tool",
                        "action_type": "tool_call",
                        "action": {"tool": self._tool_id, "args": {"value": 1}},
                    }
                )
            }
        return {
            "content": json.dumps(
                {
                    "thought": "tool requires a new graph",
                    "action_type": "final_answer",
                    "final_answer": "blocked",
                }
            )
        }


def _approving_governor() -> SimpleNamespace:
    return SimpleNamespace(
        evaluate_tool_call=lambda **_kwargs: SimpleNamespace(token="GOV_APPROVE")
    )


def test_legacy_react_tool_spec_is_metadata_only_and_handler_never_runs() -> None:
    calls: list[dict] = []
    spec = ToolSpec(
        tool_id="legacy-read",
        display_name="legacy read",
        description="must not execute",
        parameters={"type": "object", "properties": {}},
        has_side_effects=False,
        executor=lambda args: calls.append(dict(args)) or {"ok": True},
    )
    gateway = _ToolThenFinishGateway(spec.tool_id)

    result = run_react_loop(
        agent_id="emotion_agent",
        user_input="inspect",
        tools=[spec],
        gateway=gateway,
        max_steps=2,
        context={
            "request_id": "forged-request",
            "graph_id": "forged-graph",
            "attempt_id": "forged-attempt",
            "lease_id": "forged-lease",
            "node_grant_ref": "forged-grant",
        },
        governor=_approving_governor(),
    )

    assert calls == []
    assert gateway.calls == 1
    assert result.stopped_reason == "TOOL_GOVERNED_ADMISSION_REQUIRED"
    assert result.steps[0].observation["reason_code"] == (
        REACT_TOOL_GOVERNED_ADMISSION_REQUIRED
    )
    assert result.steps[0].observation["side_effects_executed"] is False


def test_emotion_handler_does_not_publish_artifact_after_tool_proposal() -> None:
    gateway = _ToolThenFinishGateway("search_knowledge")

    result = emotion_agent_handler(
        {
            "request_id": "request-emotion-proposal-only",
            "user_input": "look up private history",
            "emotion_context": {"emotion_tag": "NEUTRAL"},
        },
        context={
            "request_id": "request-emotion-proposal-only",
            "gateway": gateway,
        },
    )

    assert gateway.calls == 1
    assert result.get("agent_artifacts") in (None, {})
    trace = result["emotion_agent_trace"]
    assert trace["status"] == "DEGRADED"
    assert trace["reason_code"] == EMOTION_AGENT_GOVERNED_OUTPUT_REQUIRED
    assert trace["stopped_reason"] == "TOOL_GOVERNED_ADMISSION_REQUIRED"
    assert trace["artifact_ref"] is None
    assert trace["side_effects_executed"] is False
    assert "recommended_emotion_tag" not in trace


def test_retired_graph_node_leaves_real_sqlite_capability_journal_empty_after_restart(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "capability.sqlite3"
    for _ in range(2):
        ledger = SQLiteLedger(db_path=db_path, check_same_thread=False)
        runtime = build_production_capability_runtime(
            execution_journal=SQLiteCapabilityExecutionJournal(ledger),
        )
        node = GovernedToolNode(
            tools=[SimpleNamespace(name="legacy-read", func=lambda: "must-not-run")],
            governor=_approving_governor(),
            capability_broker=runtime.broker,
        )
        result = node(
            {
                "request_id": "same-request",
                "tool_calls": [
                    {"tool": "legacy-read", "args": {"secret": "must-not-echo"}}
                ],
            }
        )
        assert result["committed_route_token"] == (
            REACT_TOOL_GOVERNED_ADMISSION_REQUIRED
        )
        assert "tool_calls" not in result
        assert "must-not-echo" not in json.dumps(result)
        assert ledger.fetch_by_time_range("capability_execution_claim", limit=10) == []
        assert ledger.fetch_by_time_range("capability_execution_receipt", limit=10) == []
        runtime.broker.close()


def test_react_metadata_and_raw_dispatcher_expose_no_callback() -> None:
    assert all(
        spec.executor is None
        for agent_id in ("intent_agent", "retrieval_agent", "judge_agent", "emotion_agent")
        for spec in get_tools_for_agent(agent_id, allow_side_effects=True)
    )
    rejected = execute_tool("search_knowledge", {"query": "private"})
    assert rejected["reason_code"] == REACT_TOOL_GOVERNED_ADMISSION_REQUIRED
    assert rejected["side_effects_executed"] is False


def test_production_sources_contain_no_nested_react_dispatch_authority() -> None:
    import host_brain.agent_runtime.agent_executor as agent_executor
    import host_brain.agent_runtime.agent_tools as agent_tools
    import host_brain.app.agents.react_loop as react_loop
    import host_brain.integrations.graph_governed_tool_node as graph_node

    react_source = inspect.getsource(react_loop)
    executor_source = inspect.getsource(agent_executor)
    agent_tools_source = inspect.getsource(agent_tools)
    graph_source = inspect.getsource(graph_node)

    assert "get_capability_broker" not in react_source
    assert "_execute_tools_parallel" not in react_source
    assert "execute_tool(" not in react_source
    assert "Execution boundary: every listed tool is proposal metadata only" in react_source
    assert "execute_agent_tool" not in executor_source
    assert "You are a code execution agent" not in executor_source
    assert "You are a proposal-only analysis agent" in executor_source
    assert "def _tool_" not in agent_tools_source
    assert "execute_browser_skill" not in agent_tools_source
    assert "_subprocess_run" not in agent_tools_source
    assert "_INVOCATIONS" not in graph_source
    assert "replace_existing" not in graph_source
    assert ".register(" not in graph_source
    assert ".execute(" not in graph_source
