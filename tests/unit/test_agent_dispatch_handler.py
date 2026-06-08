"""P16：graph_executor agent_dispatch_node 动态路由单元测试（allowlist、need_judge 快路径、active_agent_id 同步）。

P3 的 agent_dispatch_handler 已在 P16 删除，分派逻辑统一到 graph_executor._dispatch_to_agent。
"""

from __future__ import annotations

import pytest

from host_brain.app.agents.agent_registry import AGENT_ID_INTENT, AGENT_ID_TRANSLATE, AGENT_ID_RETRIEVAL
from host_brain.integrations.graph_executor import _AGENT_HANDLER_MAP


def test_agent_handler_map_contains_core_agents() -> None:
    """_AGENT_HANDLER_MAP 包含所有核心 agent。"""
    assert AGENT_ID_TRANSLATE in _AGENT_HANDLER_MAP
    assert AGENT_ID_RETRIEVAL in _AGENT_HANDLER_MAP
    assert "surface_reply" in _AGENT_HANDLER_MAP
    assert "intent_agent" in _AGENT_HANDLER_MAP
    assert "judge_agent" in _AGENT_HANDLER_MAP


def test_agent_handler_map_does_not_contain_unimplemented_agents() -> None:
    """_AGENT_HANDLER_MAP 不含无 handler 的 agent（safety_agent / renderer_agent）。"""
    assert "safety_agent" not in _AGENT_HANDLER_MAP
    assert "renderer_agent" not in _AGENT_HANDLER_MAP


def test_dispatch_to_agent_returns_handler_id() -> None:
    """_dispatch_to_agent 对已注册 agent 返回正确 handler_id。"""
    from host_brain.integrations.graph_executor import GraphExecutor
    executor = GraphExecutor.__new__(GraphExecutor)
    state = {"active_agent_id": AGENT_ID_INTENT}
    assert executor._dispatch_to_agent(state) == "intent_agent"


def test_dispatch_to_agent_returns_empty_for_removed_legacy_agent() -> None:
    """已移除的历史 agent id 应触发降级。"""
    from host_brain.integrations.graph_executor import GraphExecutor
    executor = GraphExecutor.__new__(GraphExecutor)
    state = {"active_agent_id": "legacy_removed_agent"}
    assert executor._dispatch_to_agent(state) == ""


def test_dispatch_to_agent_returns_empty_for_unknown() -> None:
    """_dispatch_to_agent 对未注册 agent 返回空字符串（触发降级）。"""
    from host_brain.integrations.graph_executor import GraphExecutor
    executor = GraphExecutor.__new__(GraphExecutor)
    state = {"active_agent_id": "unknown_agent"}
    assert executor._dispatch_to_agent(state) == ""


def test_dispatch_to_agent_returns_empty_for_safety_agent() -> None:
    """safety_agent 无 handler，_dispatch_to_agent 返回空字符串。"""
    from host_brain.integrations.graph_executor import GraphExecutor
    executor = GraphExecutor.__new__(GraphExecutor)
    state = {"active_agent_id": "safety_agent"}
    assert executor._dispatch_to_agent(state) == ""


def test_graph_config_allowlist_contains_all_handler_map_agents() -> None:
    """graph_config_builder 的 agent_dispatch_allowlist 与 _AGENT_HANDLER_MAP 对齐。"""
    from host_brain.app.graph_config_builder import (
        AGENT_DISPATCH_NODE_ID,
        build_supervised_graph,
    )
    config = build_supervised_graph(
        provider_name="mock",
        model_id="mock",
        timeout_ms=5000,
        available=True,
        api_key=None,
        provider_fn=lambda _: None,
    )
    bindings = {b["node_id"]: b for b in config.get("node_bindings", [])}
    agent_binding = bindings.get(AGENT_DISPATCH_NODE_ID)
    assert agent_binding is not None, "agent_dispatch 节点应在配置中"
    allowlist = set(agent_binding.get("agent_dispatch_allowlist") or [])
    # allowlist 应包含 _AGENT_HANDLER_MAP 中的所有 agent
    for agent_id in _AGENT_HANDLER_MAP:
        assert agent_id in allowlist, f"{agent_id} 应在 agent_dispatch_allowlist 中"
