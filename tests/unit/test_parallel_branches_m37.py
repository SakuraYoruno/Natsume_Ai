"""只读并行分支专项测试（M37-G，T-BRN-01~10）。

验证 run_parallel_branches、BranchManifest、只读约束的正确性。
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from host_brain.integrations.graph_branch import (
    BranchManifest,
    BranchResult,
    BranchSpec,
    build_branch_manifest_dict,
    run_parallel_branches,
)
from host_brain.integrations.graph_contract import (
    BRANCH_STATUS_FAILED,
    BRANCH_STATUS_OK,
    DEFAULT_BRANCH_MAX_WORKERS,
    STATUS_OK,
    GraphState,
)


def _make_state(**kwargs: Any) -> GraphState:
    now = int(time.time() * 1000)
    base: GraphState = {
        "request_id": "req-brn-m37",
        "graph_id": "graph-brn-m37",
        "timeout_ms": 5000,
        "payload": {"key": "value"},
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
        "policy_start_ms": now,
        "policy_timeout_ms": 30_000,
        "clock_events": [],
        "node_exec_stats": {},
        "branch_specs": [],
        "branch_manifest": None,
    }
    base.update(kwargs)
    return base


def _ok_handler(payload: dict, ctx: dict | None) -> dict:
    return {"result": f"ok_{ctx.get('branch_id', 'unknown') if ctx else 'unknown'}"}


def _fail_handler(payload: dict, ctx: dict | None) -> dict:
    raise RuntimeError("分支执行失败（测试）")


def _committed_handler(payload: dict, ctx: dict | None) -> dict:
    """尝试写入 committed_* 字段的分支（应被移除）"""
    return {
        "result": "ok",
        "committed_route_token": "SHOULD_BE_STRIPPED",
        "committed_route_token_detail": {"evil": True},
    }


# ---------------------------------------------------------------------------
# T-BRN-01：run_parallel_branches 并行执行所有分支
# ---------------------------------------------------------------------------
def test_brn_01_run_parallel_branches_executes_all():
    """T-BRN-01：run_parallel_branches 并行执行所有分支"""
    state = _make_state()
    specs = [
        BranchSpec(branch_id="b1", handler=_ok_handler),
        BranchSpec(branch_id="b2", handler=_ok_handler),
        BranchSpec(branch_id="b3", handler=_ok_handler),
    ]
    manifest = run_parallel_branches(branch_specs=specs, state=state)
    assert manifest.total_count == 3
    assert len(manifest.branches) == 3
    branch_ids = {r.branch_id for r in manifest.branches}
    assert branch_ids == {"b1", "b2", "b3"}


# ---------------------------------------------------------------------------
# T-BRN-02：分支成功时 BranchResult.status = "OK"
# ---------------------------------------------------------------------------
def test_brn_02_successful_branch_status_ok():
    """T-BRN-02：分支成功时 BranchResult.status = "OK" """
    state = _make_state()
    specs = [BranchSpec(branch_id="b1", handler=_ok_handler)]
    manifest = run_parallel_branches(branch_specs=specs, state=state)
    assert manifest.branches[0].status == BRANCH_STATUS_OK
    assert manifest.branches[0].error is None
    assert "result" in manifest.branches[0].output


# ---------------------------------------------------------------------------
# T-BRN-03：分支失败时 BranchResult.status = "FAILED"，不影响其他分支
# ---------------------------------------------------------------------------
def test_brn_03_failed_branch_does_not_affect_others():
    """T-BRN-03：分支失败时 BranchResult.status = "FAILED"，不影响其他分支"""
    state = _make_state()
    specs = [
        BranchSpec(branch_id="b_ok", handler=_ok_handler),
        BranchSpec(branch_id="b_fail", handler=_fail_handler),
    ]
    manifest = run_parallel_branches(branch_specs=specs, state=state)
    assert manifest.total_count == 2
    results = {r.branch_id: r for r in manifest.branches}
    assert results["b_ok"].status == BRANCH_STATUS_OK
    assert results["b_fail"].status == BRANCH_STATUS_FAILED
    assert results["b_fail"].error is not None
    assert "RuntimeError" in results["b_fail"].error


# ---------------------------------------------------------------------------
# T-BRN-04：分支输出中的 committed_* 字段被自动移除（只读约束）
# ---------------------------------------------------------------------------
def test_brn_04_committed_fields_stripped_from_branch_output():
    """T-BRN-04：分支输出中的 committed_* 字段被自动移除（只读约束）"""
    state = _make_state()
    specs = [BranchSpec(branch_id="b_committed", handler=_committed_handler)]
    manifest = run_parallel_branches(branch_specs=specs, state=state)
    output = manifest.branches[0].output
    assert "committed_route_token" not in output
    assert "committed_route_token_detail" not in output
    assert output.get("result") == "ok"


# ---------------------------------------------------------------------------
# T-BRN-05：BranchManifest.success_count + fail_count == total_count
# ---------------------------------------------------------------------------
def test_brn_05_manifest_counts_consistent():
    """T-BRN-05：BranchManifest.success_count + fail_count == total_count"""
    state = _make_state()
    specs = [
        BranchSpec(branch_id="b1", handler=_ok_handler),
        BranchSpec(branch_id="b2", handler=_fail_handler),
        BranchSpec(branch_id="b3", handler=_ok_handler),
    ]
    manifest = run_parallel_branches(branch_specs=specs, state=state)
    assert manifest.success_count + manifest.fail_count == manifest.total_count
    assert manifest.success_count == 2
    assert manifest.fail_count == 1


# ---------------------------------------------------------------------------
# T-BRN-06：build_branch_manifest_dict 返回正确的字典结构
# ---------------------------------------------------------------------------
def test_brn_06_build_branch_manifest_dict_structure():
    """T-BRN-06：build_branch_manifest_dict 返回正确的字典结构"""
    state = _make_state()
    specs = [BranchSpec(branch_id="b1", handler=_ok_handler)]
    manifest = run_parallel_branches(branch_specs=specs, state=state)
    d = build_branch_manifest_dict(manifest)
    assert "branches" in d
    assert "total_count" in d
    assert "success_count" in d
    assert "fail_count" in d
    assert "committed_at_ms" in d
    assert isinstance(d["branches"], list)
    assert d["total_count"] == 1
    assert d["success_count"] == 1
    assert d["fail_count"] == 0


# ---------------------------------------------------------------------------
# T-BRN-07：branch_specs 为空时 run_parallel_branches 返回空 BranchManifest
# ---------------------------------------------------------------------------
def test_brn_07_empty_branch_specs_returns_empty_manifest():
    """T-BRN-07：branch_specs 为空时 run_parallel_branches 返回空 BranchManifest"""
    state = _make_state()
    manifest = run_parallel_branches(branch_specs=[], state=state)
    assert manifest.total_count == 0
    assert manifest.success_count == 0
    assert manifest.fail_count == 0
    assert manifest.branches == []


# ---------------------------------------------------------------------------
# T-BRN-08：max_workers 参数控制并行线程数
# ---------------------------------------------------------------------------
def test_brn_08_max_workers_controls_parallelism():
    """T-BRN-08：max_workers 参数控制并行线程数（不超过分支数）"""
    state = _make_state()
    specs = [BranchSpec(branch_id=f"b{i}", handler=_ok_handler) for i in range(5)]
    # max_workers=2 时仍能正确执行所有分支
    manifest = run_parallel_branches(branch_specs=specs, state=state, max_workers=2)
    assert manifest.total_count == 5
    assert manifest.success_count == 5


# ---------------------------------------------------------------------------
# T-BRN-09：分支执行异常时 BranchResult.status = "FAILED"（含 error 信息）
# ---------------------------------------------------------------------------
def test_brn_09_exception_in_branch_captured_as_failed():
    """T-BRN-09：分支执行异常时 BranchResult.status = "FAILED"（含 error 信息）"""
    def raise_value_error(payload, ctx):
        raise ValueError("测试 ValueError")

    state = _make_state()
    specs = [BranchSpec(branch_id="b_err", handler=raise_value_error)]
    manifest = run_parallel_branches(branch_specs=specs, state=state)
    result = manifest.branches[0]
    assert result.status == BRANCH_STATUS_FAILED
    assert result.error is not None
    assert "ValueError" in result.error
    assert "测试 ValueError" in result.error


# ---------------------------------------------------------------------------
# T-BRN-10：BranchManifest.committed_at_ms 为合法时间戳
# ---------------------------------------------------------------------------
def test_brn_10_manifest_committed_at_ms_is_valid_timestamp():
    """T-BRN-10：BranchManifest.committed_at_ms 为合法时间戳"""
    before = int(time.time() * 1000)
    state = _make_state()
    specs = [BranchSpec(branch_id="b1", handler=_ok_handler)]
    manifest = run_parallel_branches(branch_specs=specs, state=state)
    after = int(time.time() * 1000)
    assert isinstance(manifest.committed_at_ms, int)
    assert before <= manifest.committed_at_ms <= after + 100
