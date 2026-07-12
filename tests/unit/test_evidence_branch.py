"""Evidence Plane branch_manifest 写入专项测试（M37-M，T-EVP-09~11）。

验证 EvidencePlane.write_branch_manifest 的写入正确性和静默降级行为。
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from host_brain.integrations.graph_evidence import EvidencePlane


def _make_evidence_plane() -> EvidencePlane:
    return EvidencePlane(db_path=":memory:")


def _make_manifest(total: int = 3, success: int = 2) -> dict[str, Any]:
    fail = total - success
    branches = [
        {"branch_id": f"b{i}", "status": "OK" if i < success else "FAILED",
         "output": {"r": i}, "exec_duration_ms": 10 + i, "error": None if i < success else "err"}
        for i in range(total)
    ]
    return {
        "branches": branches,
        "total_count": total,
        "success_count": success,
        "fail_count": fail,
        "committed_at_ms": int(time.time() * 1000),
    }


# ---------------------------------------------------------------------------
# T-EVP-09：write_branch_manifest 将 manifest 写入 SQLite branch_manifest 表
# ---------------------------------------------------------------------------
def test_evp_09_write_branch_manifest_inserts_row():
    """T-EVP-09：write_branch_manifest 将 manifest 写入 SQLite branch_manifest 表"""
    ep = _make_evidence_plane()
    manifest = _make_manifest(total=3, success=2)
    ep.write_branch_manifest(request_id="req-bm-001", graph_id="graph-001", manifest=manifest)

    rows = ep.query_branch_manifest(request_id="req-bm-001")
    assert len(rows) == 1
    assert rows[0]["total_count"] == 3
    assert rows[0]["success_count"] == 2
    assert rows[0]["fail_count"] == 1


# ---------------------------------------------------------------------------
# T-EVP-10：branch_manifest 表包含正确的字段
# ---------------------------------------------------------------------------
def test_evp_10_branch_manifest_has_correct_fields():
    """T-EVP-10：branch_manifest 表包含正确的字段（total_count、success_count、fail_count 等）"""
    ep = _make_evidence_plane()
    manifest = _make_manifest(total=2, success=2)
    ep.write_branch_manifest(request_id="req-bm-002", graph_id="graph-002", manifest=manifest)

    rows = ep.query_branch_manifest(request_id="req-bm-002")
    assert len(rows) == 1
    row = rows[0]
    assert row["request_id"] == "req-bm-002"
    assert row["graph_id"] == "graph-002"
    assert row["total_count"] == 2
    assert row["success_count"] == 2
    assert row["fail_count"] == 0
    assert isinstance(row["committed_at_ms"], int)
    # branches_json 应为合法 JSON
    branches = json.loads(row["branches_json"])
    assert len(branches) == 2
    assert branches[0]["branch_id"] == "b0"


# ---------------------------------------------------------------------------
# T-EVP-11：write_branch_manifest 写入失败时静默降级
# ---------------------------------------------------------------------------
def test_evp_11_write_branch_manifest_silent_degradation(monkeypatch: pytest.MonkeyPatch):
    """T-EVP-11：write_branch_manifest 写入失败时静默降级（不抛出异常）"""
    ep = _make_evidence_plane()

    def _bad_write(**_kwargs):
        raise RuntimeError("模拟数据库连接失败")

    assert ep._repository is not None
    monkeypatch.setattr(ep._repository, "append_branch_manifest", _bad_write)

    manifest = _make_manifest()
    # 不应抛出异常
    ep.write_branch_manifest(request_id="req-bm-fail", graph_id="g", manifest=manifest)
    # 查询返回空列表
    assert ep.query_branch_manifest(request_id="req-bm-fail") == []
