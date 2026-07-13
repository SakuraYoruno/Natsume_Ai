"""Observability — 时序指标端点。

拆分自 obs_routes_core.py（P2-7）：
  GET /obs/metrics/timeseries — 多维度时序数据（请求量/拒绝/延迟）
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

from fastapi import APIRouter, Query
from host_brain.observability.api_models import MetricsTimeseriesResponse, TimeseriesPoint
from host_brain.observability.projection_ledger_view import get_projection_ledger_view

logger = logging.getLogger("Natsume.obs")

router = APIRouter(prefix="/obs", tags=["observability"])


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# GET /obs/metrics/timeseries
# ---------------------------------------------------------------------------
@router.get("/metrics/timeseries", response_model=MetricsTimeseriesResponse)
def get_metrics_timeseries(
    range_minutes: int = Query(60, ge=5, le=10080, description="时间范围（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=60, description="聚合间隔（分钟）"),
):
    """多维度时序数据（请求量/拒绝/延迟）。"""
    from host_brain.observability.metrics_timeseries import MetricsTimeseriesService

    projection_view = get_projection_ledger_view()
    svc = MetricsTimeseriesService(projection_view)
    now = _now_ms()
    start_time = now - range_minutes * 60 * 1000
    interval_map = {1: "1m", 5: "5m", 60: "1h"}
    interval = interval_map.get(interval_minutes, "5m")

    result = svc.query(
        start_time=start_time,
        end_time=now,
        interval=interval,
    )

    timeseries = result.get("timeseries", {})
    timestamps = timeseries.get("timestamps", [])
    totals = timeseries.get("total", [])
    rej_rates = timeseries.get("rejection_rate", [])
    interval_ms = interval_minutes * 60 * 1000

    points = []
    for i, _ in enumerate(timestamps):
        req = totals[i] if i < len(totals) else 0
        rej_rate_val = rej_rates[i] if i < len(rej_rates) else 0.0
        rej = int(req * rej_rate_val / 100) if req > 0 else 0
        points.append(TimeseriesPoint(
            ts_ms=start_time + i * interval_ms,
            requests=req,
            rejections=rej,
            errors=0,
            avg_latency_ms=None,
        ))

    summary = result.get("summary", {})
    return MetricsTimeseriesResponse(
        range_ms=range_minutes * 60 * 1000,
        interval_ms=interval_ms,
        points=points,
        total_requests=summary.get("total_requests", 0),
        total_rejections=summary.get("total_rejections", 0),
        rejection_rate=summary.get("rejection_rate", 0.0),
        timestamp=now,
    )
