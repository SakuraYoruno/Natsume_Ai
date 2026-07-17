"""
T-OBS-TS-01~06 — MetricsTimeseriesService 单元测试
TASK-0191 / FR-OBS-011
"""
from __future__ import annotations

import time
import pytest
from unittest.mock import MagicMock

from host_brain.observability.metrics_timeseries import MetricsTimeseriesService, MAX_DATA_POINTS


def _make_ledger(gov_rows=None, attempt_rows=None):
    ledger = MagicMock()
    def fetch(table, start, end):
        if table == "governor_log":
            return gov_rows or []
        if table == "model_attempt_log":
            return attempt_rows or []
        return []
    ledger.fetch_by_time_range.side_effect = fetch
    return ledger


def _gov_rows(n=10, reject_every=3):
    now = int(time.time() * 1000)
    rows = []
    for i in range(n):
        rows.append({
            "request_id": f"req-{i}",
            "ts_ms":      now - i * 60_000,
            "decision":   "REJECT" if i % reject_every == 0 else "APPROVE",
            "reason_code": "RATE_LIMIT_EXCEEDED" if i % reject_every == 0 else None,
            "channel":    "voice",
        })
    return rows


# ---------------------------------------------------------------------------
# T-OBS-TS-01: 基本查询返回结构稳定
# ---------------------------------------------------------------------------
def test_timeseries_basic_structure():
    svc = MetricsTimeseriesService(_make_ledger(_gov_rows()))
    result = svc.query(time_range="1h")
    assert "metrics" in result
    assert "timeseries" in result
    assert "reason_codes" in result
    assert "latency" in result
    assert "summary" in result
    assert "timestamp" in result


# ---------------------------------------------------------------------------
# T-OBS-TS-02: 批量查询返回 {metrics: {metric_name: {data_points: [...]}}}
# ---------------------------------------------------------------------------
def test_timeseries_batch_query():
    svc = MetricsTimeseriesService(_make_ledger(_gov_rows()))
    result = svc.query(metrics=["request_count", "reject_rate"], time_range="1h")
    assert "request_count" in result["metrics"]
    assert "reject_rate"   in result["metrics"]
    assert "data_points"   in result["metrics"]["request_count"]
    assert "data_points"   in result["metrics"]["reject_rate"]


# ---------------------------------------------------------------------------
# T-OBS-TS-03: 数据点数量 ≤ MAX_DATA_POINTS
# ---------------------------------------------------------------------------
def test_timeseries_max_data_points():
    # 7d + 1m interval 会产生大量桶
    svc = MetricsTimeseriesService(_make_ledger())
    result = svc.query(interval="1m", time_range="7d")
    for metric_data in result["metrics"].values():
        assert len(metric_data["data_points"]) <= MAX_DATA_POINTS


# ---------------------------------------------------------------------------
# T-OBS-TS-04: 时序数据点时间间隔与 interval 参数一致
# ---------------------------------------------------------------------------
def test_timeseries_interval_consistency():
    svc = MetricsTimeseriesService(_make_ledger())
    result = svc.query(interval="5m", time_range="1h")
    points = result["metrics"].get("request_count", {}).get("data_points", [])
    if len(points) >= 2:
        interval_ms = 5 * 60 * 1000
        diff = abs(points[1]["ts"] - points[0]["ts"])
        # 允许 ±1ms 误差
        assert abs(diff - interval_ms) <= 1


# ---------------------------------------------------------------------------
# T-OBS-TS-05: 无数据时返回空 data_points 而非崩溃
# ---------------------------------------------------------------------------
def test_timeseries_empty_data():
    svc = MetricsTimeseriesService(_make_ledger())
    result = svc.query(time_range="1h")
    assert result["summary"]["total_requests"] == 0
    assert result["summary"]["rejection_rate"] == 0.0
    # data_points 可以为空列表，但不应崩溃
    for metric_data in result["metrics"].values():
        assert isinstance(metric_data["data_points"], list)


# ---------------------------------------------------------------------------
# T-OBS-TS-06: reason_code 分布正确统计
# ---------------------------------------------------------------------------
def test_timeseries_reason_code_distribution():
    rows = _gov_rows(n=9, reject_every=3)  # 3 条 REJECT，reason_code=RATE_LIMIT_EXCEEDED
    svc = MetricsTimeseriesService(_make_ledger(rows))
    result = svc.query(time_range="1h")
    rc_map = {r["code"]: r["count"] for r in result["reason_codes"]}
    assert "RATE_LIMIT_EXCEEDED" in rc_map
    assert rc_map["RATE_LIMIT_EXCEEDED"] == 3


def test_timeseries_latency_uses_canonical_model_attempts():
    now = int(time.time() * 1000)
    ledger = _make_ledger(
        _gov_rows(),
        [
            {"attempt_id": "attempt-1", "status": "started", "ts_ms": now - 1_100, "latency_ms": None},
            {"attempt_id": "attempt-1", "status": "succeeded", "ts_ms": now - 1_000, "latency_ms": 120},
            {"attempt_id": "attempt-2", "status": "succeeded", "ts_ms": now - 500, "latency_ms": 240},
        ],
    )

    result = MetricsTimeseriesService(ledger).query(time_range="1h")

    assert result["latency"] == [{"service": "Overall", "p50": 120, "p95": 120}]
    requested_tables = [call.args[0] for call in ledger.fetch_by_time_range.call_args_list]
    assert requested_tables == ["governor_log", "model_attempt_log"]
