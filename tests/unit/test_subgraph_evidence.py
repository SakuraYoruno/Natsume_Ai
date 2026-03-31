"""Transparent Subgraph Evidence Plane 实时写入专项测试（M37-H，T-SGE-01~06）。

验证子图节点级 Evidence Plane 实时写入的正确性。
"""

from __future__ import annotations

import tempfile
import time
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from host_brain.integrations.graph_evidence import EvidencePlane
from host_brain.integrations.graph_subgraph import compile_governed_subgraph
from host_brain.integrations.graph_contract import STATUS_OK, GraphState


def _make_state(**kwargs: Any) -> GraphState:
    now = int(time.time() * 1000)
    base: GraphState = {
        "request_id": "req-sge-m37",
        "graph_id": "graph-sge-m37",
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
        "policy_start_ms": now,
        "policy_timeout_ms": 30_000,
        "clock_events": [],
        "node_exec_stats": {},
        "branch_specs": [],
        "branch_manifest": None,
    }
    base.update(kwargs)
    return base


def _make_evidence_plane() -> EvidencePlane:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return EvidencePlane(db_path=tmp.name)


def _ok_handler(payload, ctx):
    return {"result": "ok"}


def _fail_handler(payload, ctx):
    raise RuntimeError("子图节点失败（测试）")


# ---------------------------------------------------------------------------
# T-SGE-01：compile_governed_subgraph 接受 evidence_plane 参数
# ---------------------------------------------------------------------------
def test_sge_01_compile_governed_subgraph_accepts_evidence_plane():
    """T-SGE-01：compile_governed_subgraph 接受 evidence_plane 参数"""
    ep = _make_evidence_plane()
    sg = compile_governed_subgraph(
        subgraph_id="sg-001",
        node_ids=["n1", "n2"],
        handlers={"n1": _ok_handler, "n2": _ok_handler},
        evidence_plane=ep,
    )
    assert sg._evidence_plane is ep
    assert sg.subgraph_id == "sg-001"


# ---------------------------------------------------------------------------
# T-SGE-02：子图节点执行成功后调用 evidence_plane.write_trace()
# ---------------------------------------------------------------------------
def test_sge_02_successful_node_writes_to_evidence_plane():
    """T-SGE-02：子图节点执行成功后调用 evidence_plane.write_trace()"""
    ep = _make_evidence_plane()
    sg = compile_governed_subgraph(
        subgraph_id="sg-002",
        node_ids=["n1"],
        handlers={"n1": _ok_handler},
        evidence_plane=ep,
    )
    state = _make_state()
    sg.invoke(state)

    rows = ep.query_trace(request_id="req-sge-m37")
    assert len(rows) == 1
    assert rows[0]["node_id"] == "n1"
    assert rows[0]["status"] == "OK"


# ---------------------------------------------------------------------------
# T-SGE-03：子图节点执行失败后也调用 evidence_plane.write_trace()（记录失败）
# ---------------------------------------------------------------------------
def test_sge_03_failed_node_also_writes_to_evidence_plane():
    """T-SGE-03：子图节点执行失败后也调用 evidence_plane.write_trace()（记录失败）"""
    ep = _make_evidence_plane()
    sg = compile_governed_subgraph(
        subgraph_id="sg-003",
        node_ids=["n_fail"],
        handlers={"n_fail": _fail_handler},
        evidence_plane=ep,
    )
    state = _make_state()
    sg.invoke(state)

    rows = ep.query_trace(request_id="req-sge-m37")
    assert len(rows) == 1
    assert rows[0]["node_id"] == "n_fail"
    assert rows[0]["status"] == "FAILED"


# ---------------------------------------------------------------------------
# T-SGE-04：evidence_plane=None 时行为不变（向后兼容）
# ---------------------------------------------------------------------------
def test_sge_04_none_evidence_plane_backward_compatible():
    """T-SGE-04：evidence_plane=None 时行为不变（向后兼容）"""
    sg = compile_governed_subgraph(
        subgraph_id="sg-004",
        node_ids=["n1"],
        handlers={"n1": _ok_handler},
        evidence_plane=None,
    )
    state = _make_state()
    result = sg.invoke(state)
    # 子图正常执行，subgraph_trace 有记录
    assert len(result["subgraph_trace"]) == 1
    assert result["subgraph_trace"][0]["status"] == "OK"


# ---------------------------------------------------------------------------
# T-SGE-05：evidence_plane 写入失败时静默降级（不影响子图执行）
# ---------------------------------------------------------------------------
def test_sge_05_evidence_plane_write_failure_silent_degradation(monkeypatch: pytest.MonkeyPatch):
    """T-SGE-05：evidence_plane 写入失败时静默降级（不影响子图执行）"""
    ep = _make_evidence_plane()

    def _bad_write_trace(**kwargs):
        raise RuntimeError("模拟 Evidence Plane 写入失败")

    monkeypatch.setattr(ep, "write_trace", _bad_write_trace)

    sg = compile_governed_subgraph(
        subgraph_id="sg-005",
        node_ids=["n1"],
        handlers={"n1": _ok_handler},
        evidence_plane=ep,
    )
    state = _make_state()
    # 不应抛出异常，子图正常执行
    result = sg.invoke(state)
    assert len(result["subgraph_trace"]) == 1
    assert result["subgraph_trace"][0]["status"] == "OK"


# ---------------------------------------------------------------------------
# T-SGE-06：写入的 trace 记录包含正确的 node_id、status、ts_ms 字段
# ---------------------------------------------------------------------------
def test_sge_06_written_trace_has_correct_fields():
    """T-SGE-06：写入的 trace 记录包含正确的 node_id、status、ts_ms 字段"""
    ep = _make_evidence_plane()
    sg = compile_governed_subgraph(
        subgraph_id="sg-006",
        node_ids=["n1", "n2"],
        handlers={"n1": _ok_handler, "n2": _ok_handler},
        evidence_plane=ep,
    )
    state = _make_state()
    before = int(time.time() * 1000)
    sg.invoke(state)
    after = int(time.time() * 1000)

    rows = ep.query_trace(request_id="req-sge-m37")
    assert len(rows) == 2
    for row in rows:
        assert row["node_id"] in {"n1", "n2"}
        assert row["status"] == "OK"
        assert isinstance(row["ts_ms"], int)
        assert before <= row["ts_ms"] <= after + 100
