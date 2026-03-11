"""resume_graph 集成测试（M39-L，T-CKP-RES-01~03）。

验证从 checkpoint 恢复图执行的正确性。
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from host_brain.integrations.graph_contract import (
    STATUS_FAILED,
    STATUS_OK,
    INTERRUPT_REASON_AIRLOCK,
)
from host_brain.integrations.graph_checkpoint import (
    CheckpointManager,
    InterruptSignal,
)
from host_brain.integrations.graph_runtime import GraphRuntimeContract


def _make_graph_config() -> dict[str, Any]:
    return {
        "graph_id": "graph-resume-m39",
        "version": "1.0",
        "entry_node": "node_a",
        "node_bindings": [
            {"node_id": "node_a", "handler_id": "handler_a"},
        ],
        "edges": [],
        "fallback_edges": [],
    }


def _noop_handler(payload, ctx):
    return {}


# ---------------------------------------------------------------------------
# T-CKP-RES-01：checkpoint_id 不存在时返回 STATUS_FAILED
# ---------------------------------------------------------------------------
def test_ckp_res_01_nonexistent_checkpoint_returns_failed():
    """T-CKP-RES-01：checkpoint_id 不存在时返回 STATUS_FAILED"""
    runtime = GraphRuntimeContract()
    runtime.register_node_handler("handler_a", _noop_handler)

    result = runtime.resume_graph(
        checkpoint_id="nonexistent-ckp-id-99999",
        request_id="req-res-001",
    )
    assert result.status == STATUS_FAILED
    assert result.reason_code == "CHECKPOINT_NOT_FOUND"


# ---------------------------------------------------------------------------
# T-CKP-RES-02：HostPolicyGuard 验证失败时返回 STATUS_FAILED + synthetic_event
# ---------------------------------------------------------------------------
def test_ckp_res_02_expired_checkpoint_returns_failed_with_synthetic_event():
    """T-CKP-RES-02：HostPolicyGuard 验证失败时返回 STATUS_FAILED + synthetic_event"""
    # 先创建一个 checkpoint
    manager = CheckpointManager()
    now = int(time.time() * 1000)
    state = {
        "request_id": "req-res-002",
        "graph_id": "graph-resume-m39",
        "status": STATUS_OK,
        "payload": {},
        "trace": [],
        "pending_node_id": None,  # 图已完成
        "policy_start_ms": now - 600_000,  # 10分钟前开始
        "policy_timeout_ms": 30_000,
        "resume_deadline_at": now - 1,  # 已过期
        "interrupt_reason": INTERRUPT_REASON_AIRLOCK,
    }
    checkpoint_id = manager._store.save(state)

    # 将 checkpoint 注入到 runtime 的 manager 中
    runtime = GraphRuntimeContract()
    runtime.register_node_handler("handler_a", _noop_handler)

    # 直接替换 runtime._checkpoint_manager 为能返回已过期 state 的 PatchedManager
    from host_brain.integrations.graph_checkpoint import CheckpointManager as CM

    class PatchedManager(CM):
        def load_state(self, ckp_id):
            return dict(state)

    runtime._checkpoint_manager = PatchedManager()

    result = runtime.resume_graph(
        checkpoint_id=checkpoint_id,
        request_id="req-res-002",
        now_ms=now,
    )
    assert result.status == STATUS_FAILED
    assert result.output.get("synthetic_event") is not None
    assert result.output["synthetic_event"]["node_result"] == "TIMEOUT_SYNTHETIC"


# ---------------------------------------------------------------------------
# T-CKP-RES-03：验证通过时从 checkpoint 恢复并继续执行（返回 STATUS_OK 或 STATUS_DEGRADED）
# ---------------------------------------------------------------------------
def test_ckp_res_03_valid_checkpoint_resumes_execution():
    """T-CKP-RES-03：验证通过时从 checkpoint 恢复并继续执行（返回 STATUS_OK 或 STATUS_DEGRADED）"""
    now = int(time.time() * 1000)
    # 构造一个已完成的 state（pending_node_id = None）
    state = {
        "request_id": "req-res-003",
        "graph_id": "graph-resume-m39",
        "timeout_ms": 5000,
        "status": STATUS_OK,
        "reason_code": None,
        "detail": "graph execution complete",
        "payload": {"result": "done"},
        "trace": [{"node_id": "node_a", "status": "OK"}],
        "pending_node_id": None,  # 图已完成
        "edge_map": {},
        "fallback_map": {},
        "node_bindings": {"node_a": {"node_id": "node_a", "handler_id": "handler_a"}},
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
        "resume_deadline_at": now + 300_000,  # 5分钟后截止（未过期）
    }

    import host_brain.integrations.graph_checkpoint as ckp_module
    original_class = ckp_module.CheckpointManager

    class PatchedManager(original_class):
        def load_state(self, ckp_id):
            return dict(state)

    ckp_module.CheckpointManager = PatchedManager
    try:
        runtime = GraphRuntimeContract()
        runtime.register_node_handler("handler_a", _noop_handler)
        result = runtime.resume_graph(
            checkpoint_id="ckp-valid-001",
            request_id="req-res-003",
            now_ms=now,
        )
        assert result.status in {STATUS_OK, "DEGRADED"}
        assert result.output.get("resume_decision") is not None
        assert result.output["resume_decision"]["allowed"] is True
    finally:
        ckp_module.CheckpointManager = original_class
