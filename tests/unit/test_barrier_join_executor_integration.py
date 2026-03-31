"""barrier_join executor 集成测试（M40-L，T-BJ-INT-01~03）。

验证 graph_executor.py 中 barrier_join 的正确集成。
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from host_brain.integrations.graph_contract import STATUS_OK, GraphState
from host_brain.integrations.graph_executor import GraphExecutor


def _make_state(**kwargs: Any) -> GraphState:
    now = int(time.time() * 1000)
    base: GraphState = {
        "request_id": "req-bj-int-m40",
        "graph_id": "graph-bj-int-m40",
        "timeout_ms": 5000,
        "payload": {},
        "trace": [],
        "status": STATUS_OK,
        "reason_code": None,
        "detail": "",
        "pending_node_id": "node_a",
        "edge_map": {"node_a": None},
        "fallback_map": {},
        "node_bindings": {
            "node_a": {"node_id": "node_a", "handler_id": "handler_a"},
        },
        "dependency_statuses": {},
        "model_nodes": {},
        "model_profile_id": None,
        "model_calls": [],
        "committed_route_token": None,
        "committed_route_token_detail": None,
        "subgraph_trace": [],
        "policy_start_ms": now,
        "policy_timeout_ms": 30_000,
        "clock_events": [],
        "node_exec_stats": {},
        "branch_specs": [],
        "branch_manifest": None,
        "presence_events": [],
        "commit_snapshot": None,
        "render_snapshot_ref": None,
        "streaming_phase": None,
        "gov_checkpoint_id": None,
        "interrupt_reason": None,
        "resume_allowed": None,
        "resume_deadline_at": None,
        "barrier_join_result": None,
        "render_guard_result": None,
    }
    base.update(kwargs)
    return base


def _noop_handler(payload, ctx):
    return {}


def _handler_with_audit_commit(payload, ctx):
    return {"audit_commit": f"commit-{ctx.get('branch_id', 'unknown')}"}


# ---------------------------------------------------------------------------
# T-BJ-INT-01：branch_manifest 非空时 state.barrier_join_result 非空
# ---------------------------------------------------------------------------
def test_bj_int_01_barrier_join_result_set_when_branches_exist():
    """T-BJ-INT-01：branch_manifest 非空时 state.barrier_join_result 非空"""
    state = _make_state(
        branch_specs=[
            {
                "branch_id": "b1",
                "handler": _handler_with_audit_commit,
                "input_payload": {},
                "timeout_ms": 1000,
            }
        ]
    )
    executor = GraphExecutor(
        handlers={"handler_a": _noop_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    assert result.get("barrier_join_result") is not None
    assert isinstance(result["barrier_join_result"], dict)


# ---------------------------------------------------------------------------
# T-BJ-INT-02：所有分支有 audit_commit 时 barrier_join_result.passed=True
# ---------------------------------------------------------------------------
def test_bj_int_02_all_branches_with_audit_commit_passed():
    """T-BJ-INT-02：所有分支有 audit_commit 时 barrier_join_result.passed=True"""
    state = _make_state(
        branch_specs=[
            {
                "branch_id": "b1",
                "handler": _handler_with_audit_commit,
                "input_payload": {},
                "timeout_ms": 1000,
            },
            {
                "branch_id": "b2",
                "handler": _handler_with_audit_commit,
                "input_payload": {},
                "timeout_ms": 1000,
            },
        ]
    )
    executor = GraphExecutor(
        handlers={"handler_a": _noop_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    bj = result.get("barrier_join_result")
    assert bj is not None
    assert bj["passed"] is True


# ---------------------------------------------------------------------------
# T-BJ-INT-03：任一分支缺 audit_commit 时 barrier_join_result.passed=False
# ---------------------------------------------------------------------------
def test_bj_int_03_missing_audit_commit_failed():
    """T-BJ-INT-03：任一分支缺 audit_commit 时 barrier_join_result.passed=False"""
    def _handler_no_audit(payload, ctx):
        return {"result": "ok"}  # 没有 audit_commit

    state = _make_state(
        branch_specs=[
            {
                "branch_id": "b1",
                "handler": _handler_with_audit_commit,
                "input_payload": {},
                "timeout_ms": 1000,
            },
            {
                "branch_id": "b2",
                "handler": _handler_no_audit,  # 缺 audit_commit
                "input_payload": {},
                "timeout_ms": 1000,
            },
        ]
    )
    executor = GraphExecutor(
        handlers={"handler_a": _noop_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    bj = result.get("barrier_join_result")
    assert bj is not None
    assert bj["passed"] is False
    assert "b2" in bj["failed_branches"]
