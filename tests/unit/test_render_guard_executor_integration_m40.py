"""RenderGuard executor 集成测试（M40-M，T-RG-INT-01~03）。

验证 graph_executor.py 中 RenderGuard 的正确集成。
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
        "request_id": "req-rg-int-m40",
        "graph_id": "graph-rg-int-m40",
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


def _handler_with_render_output(payload, ctx):
    """返回合法的 render 输出（只修改允许字段）"""
    return {"render_text": "Hello World", "display_title": "Title"}


def _handler_with_illegal_render_output(payload, ctx):
    """返回违规的 render 输出（修改 intent_commit）"""
    return {"render_text": "ok", "intent_commit": "tampered-intent"}


# ---------------------------------------------------------------------------
# T-RG-INT-01：render_snapshot_ref 非空时 state.render_guard_result 非空
# ---------------------------------------------------------------------------
def test_rg_int_01_render_guard_result_set_when_snapshot_ref_exists():
    """T-RG-INT-01：render_snapshot_ref 非空时 state.render_guard_result 非空"""
    # 构造一个已有 commit_snapshot 和 render_snapshot_ref 的状态
    commit_snapshot = {
        "intent_commit": "intent-001",
        "s1_output_hash": "hash-001",
        "locked_fields": {"intent_commit": "intent-001"},
        "render_input_snapshot_ref": "snapshot::req-001::12345",
        "committed_at_ms": 12345,
    }
    state = _make_state(
        committed_route_token="GOV_OK_RENDER",
        commit_snapshot=commit_snapshot,
        render_snapshot_ref="snapshot::req-001::12345",
        payload={"intent_commit": "intent-001"},
    )
    executor = GraphExecutor(
        handlers={"handler_a": _noop_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    # render_snapshot_ref 非空时应触发 RenderGuard 校验
    assert result.get("render_guard_result") is not None
    assert isinstance(result["render_guard_result"], dict)


# ---------------------------------------------------------------------------
# T-RG-INT-02：render 输出合法时 render_guard_result.passed=True
# ---------------------------------------------------------------------------
def test_rg_int_02_legal_render_output_passed():
    """T-RG-INT-02：render 输出合法时 render_guard_result.passed=True"""
    commit_snapshot = {
        "intent_commit": "intent-001",
        "s1_output_hash": "hash-001",
        "locked_fields": {"s1_output": "output-001"},
        "render_input_snapshot_ref": "snapshot::req-002::12345",
        "committed_at_ms": 12345,
    }
    # payload 只包含允许的 render 字段（不包含 locked_fields 中的字段）
    state = _make_state(
        committed_route_token="GOV_OK_RENDER",
        commit_snapshot=commit_snapshot,
        render_snapshot_ref="snapshot::req-002::12345",
        payload={
            "render_text": "Hello World",
            "display_title": "Title",
        },
    )
    executor = GraphExecutor(
        handlers={"handler_a": _noop_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    rg = result.get("render_guard_result")
    assert rg is not None
    assert rg["passed"] is True


# ---------------------------------------------------------------------------
# T-RG-INT-03：render 输出违规时 render_guard_result.passed=False
# ---------------------------------------------------------------------------
def test_rg_int_03_illegal_render_output_failed():
    """T-RG-INT-03：render 输出违规时 render_guard_result.passed=False"""
    commit_snapshot = {
        "intent_commit": "intent-001",
        "s1_output_hash": "hash-001",
        "locked_fields": {"intent_commit": "intent-001", "s1_output": "output-001"},
        "render_input_snapshot_ref": "snapshot::req-003::12345",
        "committed_at_ms": 12345,
    }
    # payload 包含违规字段（修改 intent_commit）
    state = _make_state(
        committed_route_token="GOV_OK_RENDER",
        commit_snapshot=commit_snapshot,
        render_snapshot_ref="snapshot::req-003::12345",
        payload={
            "intent_commit": "tampered-intent",  # 违规：修改了 intent_commit
            "render_text": "ok",
        },
    )
    executor = GraphExecutor(
        handlers={"handler_a": _noop_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    rg = result.get("render_guard_result")
    assert rg is not None
    assert rg["passed"] is False
    assert "intent_commit" in rg["violated_fields"]
