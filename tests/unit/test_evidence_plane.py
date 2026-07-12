"""Evidence Plane SQLite 写入专项测试（M36-I，T-EVP-01~08）。

验证 EvidencePlane 的写入正确性和静默降级行为。
使用临时 SQLite 数据库（:memory: 或 tmp 文件）避免污染生产数据。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.integrations.graph_evidence import EvidencePlane


def _make_evidence_plane() -> EvidencePlane:
    """Use the adapter-owned in-memory schema asset for unit tests."""
    return EvidencePlane(db_path=":memory:")


def _make_trace(n: int = 3) -> list[dict[str, Any]]:
    """生成测试用 trace 列表。"""
    ts = int(time.time() * 1000)
    return [
        {"node_id": f"node_{i}", "status": "OK", "reason_code": None, "detail": f"node {i} ok", "ts_ms": ts + i}
        for i in range(n)
    ]


def _make_summary(subgraph_id: str = "sg-test") -> dict[str, Any]:
    """生成测试用 subgraph_commit_summary。"""
    return {
        "subgraph_id": subgraph_id,
        "node_count": 3,
        "success_count": 2,
        "fail_count": 1,
        "committed_at_ms": int(time.time() * 1000),
        "graph_trace_flat": [
            {"node_id": "n1", "status": "OK", "ts_ms": 1000, "reason_code": None},
            {"node_id": "n2", "status": "OK", "ts_ms": 2000, "reason_code": None},
            {"node_id": "n3", "status": "FAILED", "ts_ms": 3000, "reason_code": "ERROR"},
        ],
    }


def _make_clock_events() -> list[dict[str, Any]]:
    """生成测试用 clock_events 列表。"""
    ts = int(time.time() * 1000)
    return [
        {"event_type": "EXEC_TIMEOUT", "node_id": "node_a", "elapsed_ms": 1500, "threshold_ms": 1000, "ts_ms": ts},
        {"event_type": "POLICY_TIMEOUT", "node_id": "node_b", "elapsed_ms": 31000, "threshold_ms": 30000, "ts_ms": ts + 1},
    ]


# ---------------------------------------------------------------------------
# T-EVP-01：write_trace 将 trace 写入 SQLite graph_trace 表
# ---------------------------------------------------------------------------
def test_evp_01_write_trace_inserts_rows():
    """T-EVP-01：write_trace 将 trace 写入 SQLite graph_trace 表"""
    ep = _make_evidence_plane()
    trace = _make_trace(3)
    ep.write_trace(request_id="req-001", graph_id="graph-001", trace=trace)

    rows = ep.query_trace(request_id="req-001")
    assert len(rows) == 3
    node_ids = [r["node_id"] for r in rows]
    assert "node_0" in node_ids
    assert "node_1" in node_ids
    assert "node_2" in node_ids


# ---------------------------------------------------------------------------
# T-EVP-02：write_subgraph_summary 将 summary 写入 SQLite subgraph_summary 表
# ---------------------------------------------------------------------------
def test_evp_02_write_subgraph_summary_inserts_row():
    """T-EVP-02：write_subgraph_summary 将 summary 写入 SQLite subgraph_summary 表"""
    ep = _make_evidence_plane()
    summary = _make_summary("sg-001")
    ep.write_subgraph_summary(request_id="req-002", summary=summary)

    rows = ep.query_subgraph_summary(request_id="req-002")
    assert len(rows) == 1
    assert rows[0]["subgraph_id"] == "sg-001"
    assert rows[0]["node_count"] == 3
    assert rows[0]["success_count"] == 2
    assert rows[0]["fail_count"] == 1


# ---------------------------------------------------------------------------
# T-EVP-03：write_clock_events 将 clock_events 写入 SQLite clock_events 表
# ---------------------------------------------------------------------------
def test_evp_03_write_clock_events_inserts_rows():
    """T-EVP-03：write_clock_events 将 clock_events 写入 SQLite clock_events 表"""
    ep = _make_evidence_plane()
    events = _make_clock_events()
    ep.write_clock_events(request_id="req-003", graph_id="graph-003", clock_events=events)

    rows = ep.query_clock_events(request_id="req-003")
    assert len(rows) == 2
    event_types = [r["event_type"] for r in rows]
    assert "EXEC_TIMEOUT" in event_types
    assert "POLICY_TIMEOUT" in event_types


# ---------------------------------------------------------------------------
# T-EVP-04：写入失败时静默降级（不抛出异常）
# ---------------------------------------------------------------------------
def test_evp_04_write_failure_silent_degradation(monkeypatch: pytest.MonkeyPatch):
    """T-EVP-04：写入失败时静默降级（不抛出异常）"""
    ep = _make_evidence_plane()

    def _bad_write(**_kwargs):
        raise RuntimeError("模拟数据库连接失败")

    assert ep._repository is not None
    monkeypatch.setattr(ep._repository, "append_traces", _bad_write)
    monkeypatch.setattr(ep._repository, "append_subgraph_summary", _bad_write)
    monkeypatch.setattr(ep._repository, "append_clock_events", _bad_write)

    # 不应抛出异常（静默降级）
    ep.write_trace(request_id="req-fail", graph_id="g", trace=_make_trace(1))
    ep.write_subgraph_summary(request_id="req-fail", summary=_make_summary())
    ep.write_clock_events(request_id="req-fail", graph_id="g", clock_events=_make_clock_events())
    # 查询也静默降级，返回空列表
    assert ep.query_trace(request_id="req-fail") == []


# ---------------------------------------------------------------------------
# T-EVP-05：数据库路径可通过 EVIDENCE_DB_PATH 环境变量覆盖
# ---------------------------------------------------------------------------
def test_evp_05_db_path_from_env_var(tmp_path: Path):
    """T-EVP-05：数据库路径可通过 EVIDENCE_DB_PATH 环境变量覆盖"""
    custom_db = str(tmp_path / "custom_evidence.db")
    migrated = SQLiteMigrationRunner(db_path=custom_db).apply_migrations(
        request_id="evidence-env-test",
        ts_ms=1_900_000_000_000,
    )
    assert migrated.success is True, migrated.as_dict()
    os.environ["EVIDENCE_DB_PATH"] = custom_db
    try:
        ep = EvidencePlane()  # 不传 db_path，从环境变量读取
        assert str(ep._db_path) == custom_db
        # 验证可以正常写入
        ep.write_trace(request_id="req-env", graph_id="g", trace=_make_trace(1))
        rows = ep.query_trace(request_id="req-env")
        assert len(rows) == 1
    finally:
        del os.environ["EVIDENCE_DB_PATH"]


# ---------------------------------------------------------------------------
# T-EVP-06：graph_trace 表包含正确的字段
# ---------------------------------------------------------------------------
def test_evp_06_graph_trace_has_correct_fields():
    """T-EVP-06：graph_trace 表包含正确的字段"""
    ep = _make_evidence_plane()
    trace = [{"node_id": "node_x", "status": "OK", "reason_code": "SOME_CODE", "detail": "ok detail", "ts_ms": 12345}]
    ep.write_trace(request_id="req-006", graph_id="graph-006", trace=trace)

    rows = ep.query_trace(request_id="req-006")
    assert len(rows) == 1
    row = rows[0]
    assert row["request_id"] == "req-006"
    assert row["graph_id"] == "graph-006"
    assert row["node_id"] == "node_x"
    assert row["status"] == "OK"
    assert row["reason_code"] == "SOME_CODE"
    assert row["detail"] == "ok detail"
    assert row["ts_ms"] == 12345


# ---------------------------------------------------------------------------
# T-EVP-07：subgraph_summary 表包含正确的字段
# ---------------------------------------------------------------------------
def test_evp_07_subgraph_summary_has_correct_fields():
    """T-EVP-07：subgraph_summary 表包含正确的字段"""
    ep = _make_evidence_plane()
    summary = _make_summary("sg-007")
    ep.write_subgraph_summary(request_id="req-007", summary=summary)

    rows = ep.query_subgraph_summary(request_id="req-007")
    assert len(rows) == 1
    row = rows[0]
    assert row["request_id"] == "req-007"
    assert row["subgraph_id"] == "sg-007"
    assert row["node_count"] == 3
    assert row["success_count"] == 2
    assert row["fail_count"] == 1
    assert isinstance(row["committed_at_ms"], int)
    # graph_trace_flat_json 应为合法 JSON
    flat = json.loads(row["graph_trace_flat_json"])
    assert len(flat) == 3


# ---------------------------------------------------------------------------
# T-EVP-08：clock_events 表包含正确的字段
# ---------------------------------------------------------------------------
def test_evp_08_clock_events_has_correct_fields():
    """T-EVP-08：clock_events 表包含正确的字段"""
    ep = _make_evidence_plane()
    events = [{"event_type": "EXEC_TIMEOUT", "node_id": "node_z", "elapsed_ms": 2000, "threshold_ms": 1000, "ts_ms": 99999}]
    ep.write_clock_events(request_id="req-008", graph_id="graph-008", clock_events=events)

    rows = ep.query_clock_events(request_id="req-008")
    assert len(rows) == 1
    row = rows[0]
    assert row["request_id"] == "req-008"
    assert row["graph_id"] == "graph-008"
    assert row["event_type"] == "EXEC_TIMEOUT"
    assert row["node_id"] == "node_z"
    assert row["elapsed_ms"] == 2000
    assert row["threshold_ms"] == 1000
    assert row["ts_ms"] == 99999
