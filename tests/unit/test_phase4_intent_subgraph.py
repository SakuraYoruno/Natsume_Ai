"""P4 结构性断言：Intent 图主图仅 4 节点；entry 输出 subgraph_commit_summary / subgraph_failure。"""

from __future__ import annotations

import pytest

from host_brain.app.execution_profile import PROFILE_INTENT_EXEC
from host_brain.model_runtime.prompt_builder_utils import (
    CHAT_RENDER_NODE_ID,
    SCENE_EXEC_INTENT,
)


def test_intent_graph_outer_nodes_unchanged() -> None:
    """外层 Intent 图仍为 intent_agent_entry → safety_verifier → judge_agent → renderer。"""
    from host_brain.app.graph_config_builder import (
        INTENT_AGENT_ENTRY_NODE_ID,
        JUDGE_AGENT_NODE_ID,
        build_chat_graph_config,
    )
    config = build_chat_graph_config(
        provider_name="mock",
        model_id="mock",
        timeout_ms=5000,
        available=True,
        scene_type=SCENE_EXEC_INTENT,
        execution_profile=PROFILE_INTENT_EXEC,
    )
    active = config.get("graph_meta", {}).get("active_nodes") or []
    assert active == [
        INTENT_AGENT_ENTRY_NODE_ID,
        "safety_verifier",
        JUDGE_AGENT_NODE_ID,
        CHAT_RENDER_NODE_ID,
    ], "主图 active_nodes 必须仅为 4 节点"
    edges = [(e["from"], e["to"]) for e in config.get("edges", [])]
    assert (INTENT_AGENT_ENTRY_NODE_ID, "safety_verifier") in edges
    assert ("safety_verifier", JUDGE_AGENT_NODE_ID) in edges
    assert (JUDGE_AGENT_NODE_ID, CHAT_RENDER_NODE_ID) in edges


def test_intent_graph_active_nodes_exclude_subgraph_internals() -> None:
    """主图 active_nodes 不包含 core_reasoner_primary / planner_constraints / tool_planner。"""
    from host_brain.app.graph_config_builder import build_chat_graph_config
    config = build_chat_graph_config(
        provider_name="mock",
        model_id="mock",
        timeout_ms=5000,
        available=True,
        scene_type=SCENE_EXEC_INTENT,
        execution_profile=PROFILE_INTENT_EXEC,
    )
    active = config.get("graph_meta", {}).get("active_nodes") or []
    assert "core_reasoner_primary" not in active
    assert "planner_constraints" not in active
    assert "tool_planner" not in active


def test_intent_graph_entry_uses_entry_handler() -> None:
    """intent_agent_entry 节点使用 intent_agent_entry handler（P4 子图实现）。"""
    from host_brain.app.graph_config_builder import (
        INTENT_AGENT_ENTRY_NODE_ID,
        INTENT_AGENT_ENTRY_HANDLER_ID,
        build_chat_graph_config,
    )
    config = build_chat_graph_config(
        provider_name="mock",
        model_id="mock",
        timeout_ms=5000,
        available=True,
        scene_type=SCENE_EXEC_INTENT,
        execution_profile=PROFILE_INTENT_EXEC,
    )
    bindings = {b["node_id"]: b for b in config.get("node_bindings", [])}
    entry_binding = bindings.get(INTENT_AGENT_ENTRY_NODE_ID)
    assert entry_binding is not None
    assert entry_binding.get("handler_id") == INTENT_AGENT_ENTRY_HANDLER_ID


def test_intent_entry_handler_writes_subgraph_commit_summary() -> None:
    """intent_agent_entry handler 成功路径写入 payload["subgraph_commit_summary"]。"""
    from host_brain.app.handlers.intent_agent_entry_handler import intent_agent_entry_handler

    payload = {"request_id": "p4-test"}
    ctx = {"request_id": "p4-test", "invoke_model_node": lambda nid, pl=None: {}}
    out = intent_agent_entry_handler(payload, ctx)
    assert "subgraph_commit_summary" in out
    summary = out["subgraph_commit_summary"]
    assert summary.get("subgraph_id") == "intent_agent_subgraph"
    assert "node_count" in summary
    assert "graph_trace_flat" in summary


def test_intent_entry_handler_failure_writes_subgraph_failure() -> None:
    """intent_agent_entry handler 失败时写入 payload["subgraph_failure"]（§4.2 形态）。"""
    from host_brain.app.handlers.intent_agent_entry_handler import intent_agent_entry_handler

    def fail_first(_nid, pl=None):
        raise RuntimeError("mock failure")

    payload = {"request_id": "p4-fail"}
    ctx = {"request_id": "p4-fail", "invoke_model_node": fail_first}
    out = intent_agent_entry_handler(payload, ctx)
    assert "subgraph_failure" in out
    failure = out["subgraph_failure"]
    assert failure.get("failed_node") == "core_reasoner_primary"
    assert "error_code" in failure
    assert "safe_fallback_reason" in failure
    assert "trace_ref" in failure
    assert "subgraph_commit_summary" in out


def test_intent_entry_handler_does_not_write_committed_route_token() -> None:
    """intent_agent_entry handler 不写入 committed_* 字段。"""
    from host_brain.app.handlers.intent_agent_entry_handler import intent_agent_entry_handler

    payload = {"request_id": "p4-committed"}
    ctx = {"request_id": "p4-committed", "invoke_model_node": lambda nid, pl=None: {}}
    out = intent_agent_entry_handler(payload, ctx)
    for k in out:
        assert not str(k).startswith("committed_"), f"子图不得写 {k}"
