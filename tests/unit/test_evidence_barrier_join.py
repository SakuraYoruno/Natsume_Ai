"""Evidence Plane barrier_join_log 写入专项测试（M40-N，T-EVP-18~20）。

验证 EvidencePlane.write_barrier_join 的写入正确性和静默降级行为。
"""

from __future__ import annotations

import time

import pytest

from host_brain.integrations.graph_evidence import EvidencePlane


def _make_evidence_plane() -> EvidencePlane:
    return EvidencePlane(db_path=":memory:")


def _make_barrier_join_result(passed: bool = True, failed_branches: list | None = None) -> dict:
    return {
        "passed": passed,
        "failed_branches": failed_branches or [],
        "join_policy_ref": "" if passed else "partial::12345::failed=b2",
        "reason": "校验通过" if passed else "b2 缺 audit_commit",
    }


# ---------------------------------------------------------------------------
# T-EVP-18：write_barrier_join 将结果写入 SQLite barrier_join_log 表
# ---------------------------------------------------------------------------
def test_evp_18_write_barrier_join_inserts_row():
    """T-EVP-18：write_barrier_join 将结果写入 SQLite barrier_join_log 表"""
    ep = _make_evidence_plane()
    ep.write_barrier_join(
        request_id="req-bj-001",
        graph_id="graph-bj-001",
        barrier_join_result=_make_barrier_join_result(passed=True),
    )
    rows = ep.query_barrier_join(request_id="req-bj-001")
    assert len(rows) == 1
    assert rows[0]["request_id"] == "req-bj-001"


# ---------------------------------------------------------------------------
# T-EVP-19：barrier_join_log 表包含正确的字段
# ---------------------------------------------------------------------------
def test_evp_19_barrier_join_log_has_correct_fields():
    """T-EVP-19：barrier_join_log 表包含正确的字段"""
    ep = _make_evidence_plane()
    ep.write_barrier_join(
        request_id="req-bj-002",
        graph_id="graph-bj-002",
        barrier_join_result=_make_barrier_join_result(passed=False, failed_branches=["b1", "b2"]),
    )
    rows = ep.query_barrier_join(request_id="req-bj-002")
    assert len(rows) == 1
    row = rows[0]
    assert row["request_id"] == "req-bj-002"
    assert row["graph_id"] == "graph-bj-002"
    assert row["passed"] is False
    assert "b1" in row["failed_branches"]
    assert "b2" in row["failed_branches"]
    assert isinstance(row["ts_ms"], int)


# ---------------------------------------------------------------------------
# T-EVP-20：write_barrier_join 写入失败时静默降级
# ---------------------------------------------------------------------------
def test_evp_20_write_barrier_join_silent_degradation(monkeypatch: pytest.MonkeyPatch):
    """T-EVP-20：write_barrier_join 写入失败时静默降级（不抛出异常）"""
    ep = _make_evidence_plane()

    def _bad_write(**_kwargs):
        raise RuntimeError("模拟数据库连接失败")

    assert ep._repository is not None
    monkeypatch.setattr(ep._repository, "append_barrier_join", _bad_write)

    # 不应抛出异常
    ep.write_barrier_join(
        request_id="req-bj-fail",
        graph_id="g",
        barrier_join_result=_make_barrier_join_result(),
    )
    # 查询返回空列表
    assert ep.query_barrier_join(request_id="req-bj-fail") == []
