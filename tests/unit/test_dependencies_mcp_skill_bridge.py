from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import host_brain.app.dependencies as dep
from host_brain.mcp.tool_registry import ToolMeta


def _reset_dependency_singletons() -> None:
    queue = getattr(dep, "_bond_update_queue", None)
    if queue is not None:
        try:
            queue.shutdown()
        except Exception:
            pass
    registry = getattr(dep, "_mcp_tool_registry", None)
    if registry is not None:
        try:
            registry.close()
        except Exception:
            pass
    dep._ledger = None
    dep._intent_engine = None
    dep._skill_registry = None
    dep._policy_cache = None
    dep._mcp_tool_registry = None
    dep._mcp_tool_broker = None
    dep._tool_catalog = None
    dep._tool_broker = None
    dep._bond_stage_engine = None
    dep._bond_update_queue = None
    dep._episodic_store = None
    dep._retrieval_pipeline = None
    dep._retrieval_orchestrator = None
    dep._obs_metrics_collector = None
    dep._graph_runtime = None


def test_get_skill_registry_syncs_mcp_tools_incrementally(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("Natsume_AUDIT_DB", str(tmp_path / "audit.sqlite3"))
    monkeypatch.setenv("HOST_MCP_REGISTRY_DB", str(tmp_path / "mcp_registry.sqlite3"))
    monkeypatch.setenv("HOST_SKILL_MCP_BRIDGE_ENABLED", "1")
    _reset_dependency_singletons()
    try:
        skill_registry = dep.get_skill_registry()
        skill_name = "mcp::cursor::open_path"
        assert skill_registry.get(skill_name) is None

        mcp_registry = dep.get_mcp_tool_registry()
        mcp_registry.register(
            ToolMeta(
                provider_id="cursor",
                tool_id="open_path",
                name="Open Path",
                description="Open project path in Cursor",
                access_level="PUBLIC",
                risk_level="LOW",
                reversible=True,
            )
        )
        dep.get_mcp_tool_broker().register_handler(
            "cursor",
            "open_path",
            lambda args: {"opened": str(args.get("path") or "")},
        )

        skill_registry = dep.get_skill_registry()
        assert skill_registry.get(skill_name) is not None
        result = skill_registry.require(skill_name).handler(
            {"path": "D:/repo"},
            {
                "request_id": "req-1",
                "intent_id": "intent-1",
                "session_id": "sess-1",
                "seq": 3,
            },
        )
        assert result["status"] == "OK"
        assert result["output"]["opened"] == "D:/repo"
    finally:
        _reset_dependency_singletons()


def test_get_skill_registry_skips_mcp_bridge_when_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("Natsume_AUDIT_DB", str(tmp_path / "audit.sqlite3"))
    monkeypatch.setenv("HOST_MCP_REGISTRY_DB", str(tmp_path / "mcp_registry.sqlite3"))
    monkeypatch.setenv("HOST_SKILL_MCP_BRIDGE_ENABLED", "0")
    _reset_dependency_singletons()
    try:
        dep.get_mcp_tool_registry().register(
            ToolMeta(
                provider_id="cursor",
                tool_id="open_path",
                name="Open Path",
                description="Open project path in Cursor",
                access_level="PUBLIC",
                risk_level="LOW",
                reversible=True,
            )
        )
        skill_registry = dep.get_skill_registry()
        assert skill_registry.get("mcp::cursor::open_path") is None
    finally:
        _reset_dependency_singletons()
