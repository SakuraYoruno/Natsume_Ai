"""obs_routes_stats_input_dispatch.py — InputDispatch 生命周期统计端点。"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Annotated, Any, Dict, List, Optional

from fastapi import Depends, Query
from pydantic import BaseModel

from host_brain.app.obs_routes_core import (
    router,
    _extract_input_dispatch_latency_ms,
    _is_input_dispatch_event,
    _normalize_input_dispatch_source,
    _now_ms,
    _safe_json,
)
from host_brain.app.obs_route_ports import require_obs_projection_ledger_view
from host_brain.app.observability_runtime.projection_ledger_view import (
    fail_closed_projection_route,
)

logger = logging.getLogger("Natsume.obs")


# ---------------------------------------------------------------------------
# Pydantic 响应模型
# ---------------------------------------------------------------------------
class InputSourceItem(BaseModel):
    source: str
    accepted: int
    completed: int
    failed: int
    not_wired: int
    success_rate: Optional[float]
    avg_latency_ms: Optional[float]


class InputDispatchStatsResponse(BaseModel):
    window_minutes: int
    total_accepted: int
    total_completed: int
    total_failed: int
    total_not_wired: int
    overall_success_rate: Optional[float]
    sources: List[InputSourceItem]
    timeseries_labels: List[str]
    timeseries_accepted: List[int]
    timeseries_completed: List[int]
    timestamp: int


# ---------------------------------------------------------------------------
# GET /obs/input/dispatch-stats
# ---------------------------------------------------------------------------
@router.get("/input/dispatch-stats", response_model=InputDispatchStatsResponse)
@fail_closed_projection_route
def get_input_dispatch_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=60, description="时序聚合间隔（分钟）"),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #48: InputDispatch 生命周期统计 — 按 source 分组的 ACCEPTED/COMPLETED/FAILED/NOT_WIRED 数量、成功率、平均延迟。"""
    ledger = projection_view
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=now, limit=10_000)
        dispatch_rows = [r for r in rows if _is_input_dispatch_event(r)]
    except Exception:
        dispatch_rows = []

    source_stats: dict = defaultdict(lambda: {"accepted": 0, "completed": 0, "failed": 0, "not_wired": 0, "latencies": []})

    for r in dispatch_rows:
        evt = str(r.get("event_type") or r.get("status") or "").upper()
        payload = _safe_json(r.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        src = _normalize_input_dispatch_source(
            payload_dict.get("source") or r.get("source") or r.get("channel"), evt,
        )
        if "ACCEPTED" in evt:
            source_stats[src]["accepted"] += 1
        elif "COMPLETED" in evt or "DONE" in evt:
            source_stats[src]["completed"] += 1
            latency_ms = _extract_input_dispatch_latency_ms(r, payload_dict)
            if latency_ms is not None:
                source_stats[src]["latencies"].append(latency_ms)
        elif "FAILED" in evt or "ERROR" in evt:
            source_stats[src]["failed"] += 1
        elif "NOT_WIRED" in evt:
            source_stats[src]["not_wired"] += 1

    total_accepted = sum(v["accepted"] for v in source_stats.values())
    total_completed = sum(v["completed"] for v in source_stats.values())
    total_failed = sum(v["failed"] for v in source_stats.values())
    total_not_wired = sum(v["not_wired"] for v in source_stats.values())
    total_dispatched = total_completed + total_failed + total_not_wired
    overall_success_rate = round(total_completed / total_dispatched * 100, 1) if total_dispatched > 0 else None

    sources = []
    for src, stats in sorted(source_stats.items()):
        dispatched = stats["completed"] + stats["failed"] + stats["not_wired"]
        sr = round(stats["completed"] / dispatched * 100, 1) if dispatched > 0 else None
        avg_lat = round(sum(stats["latencies"]) / len(stats["latencies"]), 1) if stats["latencies"] else None
        sources.append(InputSourceItem(
            source=src, accepted=stats["accepted"], completed=stats["completed"],
            failed=stats["failed"], not_wired=stats["not_wired"],
            success_rate=sr, avg_latency_ms=avg_lat,
        ))

    interval_ms = interval_minutes * 60 * 1000
    bucket_count = max(1, (window_minutes * 60 * 1000) // interval_ms)
    ts_accepted = [0] * bucket_count
    ts_completed = [0] * bucket_count
    for r in dispatch_rows:
        ts = r.get("ts_ms") or 0
        idx = min(int((ts - since_ms) // interval_ms), bucket_count - 1)
        if 0 <= idx < bucket_count:
            evt = str(r.get("event_type") or r.get("status") or "").upper()
            if "ACCEPTED" in evt:
                ts_accepted[idx] += 1
            elif "COMPLETED" in evt or "DONE" in evt:
                ts_completed[idx] += 1

    import datetime as _dt
    labels = []
    for i in range(bucket_count):
        ts_val = since_ms + i * interval_ms
        d = _dt.datetime.fromtimestamp(ts_val / 1000)
        if window_minutes <= 60:
            labels.append(d.strftime("%H:%M"))
        elif window_minutes <= 1440:
            labels.append(d.strftime("%m/%d %H:%M"))
        else:
            labels.append(d.strftime("%m/%d"))

    return InputDispatchStatsResponse(
        window_minutes=window_minutes,
        total_accepted=total_accepted, total_completed=total_completed,
        total_failed=total_failed, total_not_wired=total_not_wired,
        overall_success_rate=overall_success_rate, sources=sources,
        timeseries_labels=labels, timeseries_accepted=ts_accepted,
        timeseries_completed=ts_completed, timestamp=now,
    )
