"""graph_executor.py 并行分支集成测试（M37-I，T-BRN-INT-01~03）。

验证 _run_single_step 在 branch_specs 非空时正确调用 run_parallel_branches 并写入 branch_manifest。
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
        "request_id": "req-bint-m37",
        "graph_id": "graph-bint-m37",
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
    }
    base.update(kwargs)
    return base


def _noop_handler(payload, ctx):
    return {}


def _branch_handler(payload, ctx):
    return {"branch_result": f"ok_{ctx.get('branch_id', 'unknown') if ctx else 'unknown'}"}


# ---------------------------------------------------------------------------
# T-BRN-INT-01：branch_specs 非空时，_run_single_step 调用 run_parallel_branches
# ---------------------------------------------------------------------------
def test_brn_int_01_branch_specs_triggers_parallel_execution():
    """T-BRN-INT-01：branch_specs 非空时，_run_single_step 调用 run_parallel_branches"""
    state = _make_state(
        branch_specs=[
            {"branch_id": "b1", "handler": _branch_handler, "input_payload": {}},
            {"branch_id": "b2", "handler": _branch_handler, "input_payload": {}},
        ]
    )
    executor = GraphExecutor(
        handlers={"handler_a": _noop_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    # branch_manifest 应被写入
    assert result["branch_manifest"] is not None
    assert result["branch_manifest"]["total_count"] == 2
    assert result["branch_manifest"]["success_count"] == 2


# ---------------------------------------------------------------------------
# T-BRN-INT-02：branch_manifest 写入 state
# ---------------------------------------------------------------------------
def test_brn_int_02_branch_manifest_written_to_state():
    """T-BRN-INT-02：branch_manifest 写入 state"""
    state = _make_state(
        branch_specs=[
            {"branch_id": "b1", "handler": _branch_handler, "input_payload": {}},
        ]
    )
    executor = GraphExecutor(
        handlers={"handler_a": _noop_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    manifest = result["branch_manifest"]
    assert manifest is not None
    assert "branches" in manifest
    assert len(manifest["branches"]) == 1
    assert manifest["branches"][0]["branch_id"] == "b1"
    assert manifest["branches"][0]["status"] == "OK"


# ---------------------------------------------------------------------------
# T-BRN-INT-03：branch_specs 为空时，_run_single_step 行为不变
# ---------------------------------------------------------------------------
def test_brn_int_03_empty_branch_specs_no_change():
    """T-BRN-INT-03：branch_specs 为空时，_run_single_step 行为不变"""
    state = _make_state(branch_specs=[])
    executor = GraphExecutor(
        handlers={"handler_a": _noop_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    # branch_manifest 应保持 None
    assert result["branch_manifest"] is None
    # 主图正常执行
    assert result["status"] == STATUS_OK
