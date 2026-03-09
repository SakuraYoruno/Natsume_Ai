from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.mcp.tool_registry import MCPToolRegistry, ToolMeta
from host_brain.skills.mcp_skill_bridge import (
    build_mcp_skill_name,
    register_mcp_tools_into_skill_registry,
)
from host_brain.skills.registry import SkillRegistry


class _FakeBroker:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def call(self, **kwargs):
        self.calls.append(dict(kwargs))
        return SimpleNamespace(
            success=True,
            reason_code=None,
            detail="ok",
            output={"echo": dict(kwargs.get("args") or {})},
        )


def _build_mcp_registry() -> MCPToolRegistry:
    registry = MCPToolRegistry(db_path=":memory:")
    registry.register(
        ToolMeta(
            provider_id="cursor",
            tool_id="open_path",
            name="Open Path",
            description="Open file in cursor",
            access_level="INTERNAL",
            risk_level="LOW",
            reversible=True,
        )
    )
    registry.register(
        ToolMeta(
            provider_id="codex",
            tool_id="submit_task",
            name="Submit Task",
            description="Submit task to codex",
            access_level="INTERNAL",
            risk_level="HIGH",
            reversible=False,
        )
    )
    return registry


def test_register_mcp_tools_into_skill_registry_without_broker_returns_skipped() -> None:
    mcp_registry = _build_mcp_registry()
    try:
        skill_registry = SkillRegistry()
        count = register_mcp_tools_into_skill_registry(
            skill_registry=skill_registry,
            mcp_registry=mcp_registry,
            broker=None,
        )
        assert count == 2
        name = build_mcp_skill_name(provider_id="cursor", tool_id="open_path")
        result = skill_registry.require(name).handler({"path": "D:/repo"}, {"request_id": "r1"})
        assert result["status"] == "SKIPPED"
        assert result["reason_code"] == "MCP_BROKER_UNBOUND"
    finally:
        mcp_registry.close()


def test_register_mcp_tools_into_skill_registry_with_broker_executes_call() -> None:
    mcp_registry = _build_mcp_registry()
    broker = _FakeBroker()
    try:
        skill_registry = SkillRegistry()
        register_mcp_tools_into_skill_registry(
            skill_registry=skill_registry,
            mcp_registry=mcp_registry,
            broker=broker,
        )
        name = build_mcp_skill_name(provider_id="codex", tool_id="submit_task")
        result = skill_registry.require(name).handler(
            {"task": "fix ci"},
            {"request_id": "req-1", "intent_id": "intent-1", "session_id": "sess-1", "seq": 3},
        )

        assert result["status"] == "OK"
        assert len(broker.calls) == 1
        assert broker.calls[0]["provider_id"] == "codex"
        assert broker.calls[0]["tool_id"] == "submit_task"
        assert broker.calls[0]["args"]["task"] == "fix ci"
    finally:
        mcp_registry.close()
