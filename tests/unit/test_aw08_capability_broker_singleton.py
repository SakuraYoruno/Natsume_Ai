from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.composition import process_services as dependencies
from host_brain.capability_plane.journal import InMemoryCapabilityExecutionJournal
from host_brain.capability_plane.production_composition import (
    build_production_capability_runtime,
)
from host_brain.core.errors import MCP_TASK_AUTHORITY_REQUIRED
from host_brain.integrations.graph_governed_tool_node import (
    GovernedToolNode,
    REACT_TOOL_GOVERNED_ADMISSION_REQUIRED,
)
from host_brain.mcp.tool_broker import MCPToolBroker
from host_brain.mcp.tool_registry import MCPToolRegistry
from host_brain.skills.executor_registry import dispatch_executor_payload


ROOT = Path(__file__).resolve().parents[2]


def test_dependencies_owns_one_capability_runtime_composition(monkeypatch) -> None:  # noqa: ANN001
    journal = InMemoryCapabilityExecutionJournal()
    dependencies.reset_capability_runtime_composition()
    monkeypatch.setattr(dependencies, "get_capability_execution_journal", lambda: journal)
    try:
        first = dependencies.get_capability_runtime_composition()
        second = dependencies.get_capability_runtime_composition()
        assert first is second
        assert dependencies.get_capability_broker() is first.broker
    finally:
        dependencies.reset_capability_runtime_composition()


def test_graph_and_mcp_facades_are_inert_while_skill_uses_composed_broker() -> None:
    composition = build_production_capability_runtime(
        execution_journal=InMemoryCapabilityExecutionJournal(),
    )
    broker = composition.broker
    calls: list[str] = []

    class _Governor:
        def evaluate_tool_call(self, **_kwargs):  # noqa: ANN003
            return SimpleNamespace(token="GOV_APPROVE")

    graph = GovernedToolNode(
        tools=[SimpleNamespace(name="shared-demo", func=lambda **_kwargs: calls.append("graph") or "ok")],
        governor=_Governor(),
        capability_broker=broker,
    )
    graph_result = graph({
        "request_id": "aw08-shared-graph",
        "tool_calls": [{"tool": "shared-demo", "args": {}}],
    })

    skill_result = dispatch_executor_payload(
        {"tool": "notification", "title": "forged"},
        capability_broker=broker,
    )
    mcp = MCPToolBroker(
        registry=MCPToolRegistry(db_path=":memory:"),
        capability_broker=broker,
    )
    with pytest.raises(PermissionError, match=MCP_TASK_AUTHORITY_REQUIRED):
        mcp.register_handler(
            "provider",
            "tool",
            lambda _args: calls.append("mcp") or "must-not-run",
        )
    mcp_result = mcp.call(
        provider_id="provider",
        tool_id="tool",
        args={"value": 1},
    )

    assert not hasattr(graph, "_broker")
    assert not hasattr(mcp, "_broker")
    assert graph_result["committed_route_token"] == (
        REACT_TOOL_GOVERNED_ADMISSION_REQUIRED
    )
    assert graph_result["tool_results"][0]["result"]["side_effects_executed"] is False
    assert calls == []
    assert skill_result["reason_code"] == "SKILL_AUTHORITY_MISSING"
    assert mcp_result.reason_code == MCP_TASK_AUTHORITY_REQUIRED
    assert mcp_result.output is None


def test_production_has_one_broker_constructor_and_no_private_fallbacks() -> None:
    constructors: list[str] = []
    for path in (ROOT / "host_brain").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "CapabilityBroker":
                constructors.append(path.relative_to(ROOT).as_posix())

    assert constructors == ["host_brain/capability_plane/production_composition.py"]
    forbidden = (
        "_PRODUCTION_CAPABILITY_BROKER",
        "_SKILL_CAPABILITY_BROKER",
        "_get_skill_capability_broker",
        "capability_broker or CapabilityBroker",
    )
    targets = (
        ROOT / "host_brain" / "domain_packs" / "production_runtime.py",
        ROOT / "host_brain" / "integrations" / "graph_governed_tool_node.py",
        ROOT / "host_brain" / "mcp" / "tool_broker.py",
        ROOT / "host_brain" / "skills" / "executor_registry.py",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in targets)
    assert not [token for token in forbidden if token in combined]
