"""graph_executor.py checkpoint 集成测试（M39-J，T-CKP-INT-01~04）。

验证 GOV_AIRLOCK_REVIEW 触发 interrupt 的正确集成。
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

from host_brain.integrations.graph_contract import (
    STATUS_OK,
    INTERRUPT_REASON_AIRLOCK,
    GraphState,
)
from host_brain.integrations.graph_executor import GraphExecutor


def _make_state(**kwargs: Any) -> GraphState:
    now = int(time.time() * 1000)
    base: GraphState = {
        "request_id": "req-ckpint-m39",
        "graph_id": "graph-ckpint-m39",
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
    }
    base.update(kwargs)
    return base


def _noop_handler(payload, ctx):
    return {}


# ---------------------------------------------------------------------------
# T-CKP-INT-01：GOV_AIRLOCK_REVIEW 时 state.checkpoint_id 非空
# ---------------------------------------------------------------------------
def test_ckp_int_01_airlock_sets_checkpoint_id():
    """T-CKP-INT-01：GOV_AIRLOCK_REVIEW 时 state.checkpoint_id 非空"""
    state = _make_state(committed_route_token="GOV_AIRLOCK_REVIEW")
    executor = GraphExecutor(
        handlers={"handler_a": _noop_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    checkpoint_id = result.get("gov_checkpoint_id")
    assert checkpoint_id is not None
    assert isinstance(checkpoint_id, str)
    assert len(checkpoint_id) > 0


# ---------------------------------------------------------------------------
# T-CKP-INT-02：GOV_AIRLOCK_REVIEW 时 state.interrupt_reason = INTERRUPT_REASON_AIRLOCK
# ---------------------------------------------------------------------------
def test_ckp_int_02_airlock_sets_interrupt_reason():
    """T-CKP-INT-02：GOV_AIRLOCK_REVIEW 时 state.interrupt_reason = INTERRUPT_REASON_AIRLOCK"""
    state = _make_state(committed_route_token="GOV_AIRLOCK_REVIEW")
    executor = GraphExecutor(
        handlers={"handler_a": _noop_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    assert result.get("interrupt_reason") == INTERRUPT_REASON_AIRLOCK


# ---------------------------------------------------------------------------
# T-CKP-INT-03：GOV_AIRLOCK_REVIEW 时 state.resume_deadline_at 为合法时间戳
# ---------------------------------------------------------------------------
def test_ckp_int_03_airlock_sets_resume_deadline():
    """T-CKP-INT-03：GOV_AIRLOCK_REVIEW 时 state.resume_deadline_at 为合法时间戳"""
    now = int(time.time() * 1000)
    state = _make_state(committed_route_token="GOV_AIRLOCK_REVIEW")
    executor = GraphExecutor(
        handlers={"handler_a": _noop_handler},
        gateway_factory=MagicMock(),
        default_timeout_ms=5000,
    )
    result = executor._run_single_step(state)
    resume_deadline_at = result.get("resume_deadline_at")
    assert resume_deadline_at is not None
    assert isinstance(resume_deadline_at, int)
    # resume_deadline_at 应该在未来（至少 1 分钟后）
    assert resume_deadline_at > now + 60_000
