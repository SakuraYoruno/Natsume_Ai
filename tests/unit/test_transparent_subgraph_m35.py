"""Transparent Subgraph 专项测试（M35-H，T-TSG-01~08）。

验证 compile_governed_subgraph 工厂和 build_subgraph_commit_summary 的正确性。
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from host_brain.integrations.graph_contract import (
    STATUS_DEGRADED,
    STATUS_FAILED,
    STATUS_OK,
    GraphState,
    build_subgraph_commit_summary,
)
from host_brain.integrations.graph_subgraph import (
    CompiledSubgraph,
    compile_governed_subgraph,
)


def _make_state(**kwargs: Any) -> GraphState:
    """构建最小化 GraphState 用于测试。"""
    base: GraphState = {
        "request_id": "test-req-001",
        "graph_id": "test-graph",
        "timeout_ms": 5000,
        "payload": {},
        "trace": [],
        "status": STATUS_OK,
        "reason_code": None,
        "detail": "",
        "pending_node_id": None,
        "edge_map": {},
        "fallback_map": {},
        "node_bindings": {},
        "dependency_statuses": {},
        "model_nodes": {},
        "model_profile_id": None,
        "model_calls": [],
        "committed_route_token": None,
        "committed_route_token_detail": None,
        "subgraph_trace": [],
    }
    base.update(kwargs)
    return base


def _noop_handler(payload: dict, ctx: dict) -> dict:
    """空操作处理器，直接返回 payload。"""
    return {"node_output": f"ok:{ctx.get('node_id')}"}


def _fail_handler(payload: dict, ctx: dict) -> dict:
    """失败处理器，抛出异常。"""
    raise RuntimeError(f"节点执行失败: {ctx.get('node_id')}")


# ---------------------------------------------------------------------------
# T-TSG-01：子图每个节点执行后写入 subgraph_trace
# ---------------------------------------------------------------------------
def test_tsg_01_each_node_writes_to_subgraph_trace():
    """T-TSG-01：子图每个节点执行后写入 subgraph_trace"""
    subgraph = compile_governed_subgraph(
        subgraph_id="sg-001",
        node_ids=["node_a", "node_b", "node_c"],
        handlers={
            "node_a": _noop_handler,
            "node_b": _noop_handler,
            "node_c": _noop_handler,
        },
    )
    state = _make_state()
    result = subgraph.invoke(state)

    assert len(result["subgraph_trace"]) == 3
    node_ids_in_trace = [entry["node_id"] for entry in result["subgraph_trace"]]
    assert "node_a" in node_ids_in_trace
    assert "node_b" in node_ids_in_trace
    assert "node_c" in node_ids_in_trace


# ---------------------------------------------------------------------------
# T-TSG-02：subgraph_commit_summary 包含正确的 node_count
# ---------------------------------------------------------------------------
def test_tsg_02_commit_summary_has_correct_node_count():
    """T-TSG-02：subgraph_commit_summary 包含正确的 node_count"""
    subgraph = compile_governed_subgraph(
        subgraph_id="sg-002",
        node_ids=["node_a", "node_b"],
        handlers={"node_a": _noop_handler, "node_b": _noop_handler},
    )
    state = _make_state()
    result = subgraph.invoke(state)

    summary = result["payload"].get("subgraph_commit_summary", {})
    assert summary["node_count"] == 2
    assert summary["subgraph_id"] == "sg-002"


# ---------------------------------------------------------------------------
# T-TSG-03：subgraph_commit_summary 包含正确的 success_count 和 fail_count
# ---------------------------------------------------------------------------
def test_tsg_03_commit_summary_has_correct_success_fail_count():
    """T-TSG-03：subgraph_commit_summary 包含正确的 success_count 和 fail_count"""
    subgraph = compile_governed_subgraph(
        subgraph_id="sg-003",
        node_ids=["node_ok", "node_fail"],
        handlers={"node_ok": _noop_handler, "node_fail": _fail_handler},
    )
    state = _make_state()
    result = subgraph.invoke(state)

    summary = result["payload"].get("subgraph_commit_summary", {})
    assert summary["node_count"] == 2
    assert summary["success_count"] == 1
    assert summary["fail_count"] == 1


# ---------------------------------------------------------------------------
# T-TSG-04：graph_trace_flat 为展平后的节点执行记录
# ---------------------------------------------------------------------------
def test_tsg_04_graph_trace_flat_contains_node_records():
    """T-TSG-04：graph_trace_flat 为展平后的节点执行记录"""
    subgraph = compile_governed_subgraph(
        subgraph_id="sg-004",
        node_ids=["node_a", "node_b"],
        handlers={"node_a": _noop_handler, "node_b": _noop_handler},
    )
    state = _make_state()
    result = subgraph.invoke(state)

    summary = result["payload"].get("subgraph_commit_summary", {})
    flat = summary.get("graph_trace_flat", [])
    assert len(flat) == 2
    for entry in flat:
        assert "node_id" in entry
        assert "status" in entry
        assert "ts_ms" in entry


# ---------------------------------------------------------------------------
# T-TSG-05：子图不允许写入 committed_* 字段
# ---------------------------------------------------------------------------
def test_tsg_05_subgraph_cannot_write_committed_fields():
    """T-TSG-05：子图节点输出中的 committed_* 字段被自动移除"""
    def handler_with_committed(payload: dict, ctx: dict) -> dict:
        return {
            "node_output": "ok",
            "committed_route_token": "GOV_OK_RENDER",  # 尝试写入 committed_* 字段
            "committed_evil": "evil_value",
        }

    subgraph = compile_governed_subgraph(
        subgraph_id="sg-005",
        node_ids=["node_evil"],
        handlers={"node_evil": handler_with_committed},
    )
    state = _make_state()
    result = subgraph.invoke(state)

    # committed_* 字段应被移除
    assert "committed_route_token" not in result["payload"]
    assert "committed_evil" not in result["payload"]
    # 正常字段应保留
    assert result["payload"].get("node_output") == "ok"


# ---------------------------------------------------------------------------
# T-TSG-06：子图失败时 status = STATUS_DEGRADED，主图继续
# ---------------------------------------------------------------------------
def test_tsg_06_subgraph_failure_sets_degraded_not_failed():
    """T-TSG-06：子图失败时 status = STATUS_DEGRADED，不影响主图继续执行"""
    subgraph = compile_governed_subgraph(
        subgraph_id="sg-006",
        node_ids=["node_fail"],
        handlers={"node_fail": _fail_handler},
    )
    state = _make_state()
    result = subgraph.invoke(state)

    # 子图失败 → DEGRADED，不是 FAILED
    assert result["status"] == STATUS_DEGRADED
    # subgraph_trace 记录了失败
    assert len(result["subgraph_trace"]) == 1
    assert result["subgraph_trace"][0]["status"] == STATUS_FAILED


# ---------------------------------------------------------------------------
# T-TSG-07：build_subgraph_commit_summary 正确统计
# ---------------------------------------------------------------------------
def test_tsg_07_build_subgraph_commit_summary_correct():
    """T-TSG-07：build_subgraph_commit_summary 正确统计 success/fail"""
    trace = [
        {"node_id": "n1", "status": "OK", "ts_ms": 1000, "reason_code": None},
        {"node_id": "n2", "status": "FAILED", "ts_ms": 2000, "reason_code": "ERROR"},
        {"node_id": "n3", "status": "OK", "ts_ms": 3000, "reason_code": None},
    ]
    summary = build_subgraph_commit_summary(subgraph_id="sg-007", subgraph_trace=trace)

    assert summary["subgraph_id"] == "sg-007"
    assert summary["node_count"] == 3
    assert summary["success_count"] == 2
    assert summary["fail_count"] == 1
    assert len(summary["graph_trace_flat"]) == 3


# ---------------------------------------------------------------------------
# T-TSG-08：committed_at_ms 为正整数时间戳
# ---------------------------------------------------------------------------
def test_tsg_08_committed_at_ms_is_positive_int():
    """T-TSG-08：build_subgraph_commit_summary 的 committed_at_ms 为正整数时间戳"""
    trace = [{"node_id": "n1", "status": "OK", "ts_ms": 1000, "reason_code": None}]
    before = int(time.time() * 1000)
    summary = build_subgraph_commit_summary(subgraph_id="sg-008", subgraph_trace=trace)
    after = int(time.time() * 1000)

    assert isinstance(summary["committed_at_ms"], int)
    assert summary["committed_at_ms"] > 0
    assert before <= summary["committed_at_ms"] <= after + 10  # 允许 10ms 误差


# ---------------------------------------------------------------------------
# 额外：compile_governed_subgraph 参数校验
# ---------------------------------------------------------------------------
def test_tsg_extra_compile_validates_params():
    """compile_governed_subgraph 对空参数抛出 ValueError"""
    with pytest.raises(ValueError, match="subgraph_id"):
        compile_governed_subgraph(subgraph_id="", node_ids=["n1"], handlers={})

    with pytest.raises(ValueError, match="node_ids"):
        compile_governed_subgraph(subgraph_id="sg-x", node_ids=[], handlers={})
