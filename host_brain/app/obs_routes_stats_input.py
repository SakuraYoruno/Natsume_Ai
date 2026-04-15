"""obs_routes_stats_input.py — 可观测性统计端点（从 obs_routes_stats.py 拆分）。

子模块：Input Dispatch统计、Humanoid Loop系列统计
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from host_brain.retrieval.model_suite import resolve_retrieval_model_suite

from host_brain.audit.sqlite_ledger import SQLiteLedger

logger = logging.getLogger("Natsume.obs")


from .obs_routes_core import (
    _build_trace_summary,
    router,
    _extract_input_dispatch_latency_ms,
    _fetch_request_rows,
    _get_ledger,
    _is_input_dispatch_event,
    _normalize_input_dispatch_source,
    _now_ms,
    _safe_json,
)


# ---------------------------------------------------------------------------
# Ref #48: GET /obs/input/dispatch-stats  — InputDispatch 生命周期统计
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

@router.get("/input/dispatch-stats", response_model=InputDispatchStatsResponse)
def get_input_dispatch_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=60, description="时序聚合间隔（分钟）"),
):
    """Ref #48: InputDispatch 生命周期统计 — 按 source 分组的 ACCEPTED/COMPLETED/FAILED/NOT_WIRED 数量、成功率、平均延迟。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # InputDispatch 事件实际落在 voice_event_log，并且 source/latency_ms 写在 payload 内。
    try:
        rows = ledger.fetch_by_time_range(
            "voice_event_log",
            start_ms=since_ms,
            end_ms=now,
            limit=10_000,
        )
        dispatch_rows = [r for r in rows if _is_input_dispatch_event(r)]
    except Exception:
        dispatch_rows = []

    # 按 source 分组统计
    from collections import defaultdict
    source_stats: dict = defaultdict(lambda: {"accepted": 0, "completed": 0, "failed": 0, "not_wired": 0, "latencies": []})

    for r in dispatch_rows:
        evt = str(r.get("event_type") or r.get("status") or "").upper()
        payload = _safe_json(r.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        src = _normalize_input_dispatch_source(
            payload_dict.get("source") or r.get("source") or r.get("channel"),
            evt,
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
            source=src,
            accepted=stats["accepted"],
            completed=stats["completed"],
            failed=stats["failed"],
            not_wired=stats["not_wired"],
            success_rate=sr,
            avg_latency_ms=avg_lat,
        ))

    # 时序聚合
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

    labels = []
    for i in range(bucket_count):
        ts_val = since_ms + i * interval_ms
        d = __import__("datetime").datetime.fromtimestamp(ts_val / 1000)
        if window_minutes <= 60:
            labels.append(d.strftime("%H:%M"))
        elif window_minutes <= 1440:
            labels.append(d.strftime("%m/%d %H:%M"))
        else:
            labels.append(d.strftime("%m/%d"))

    return InputDispatchStatsResponse(
        window_minutes=window_minutes,
        total_accepted=total_accepted,
        total_completed=total_completed,
        total_failed=total_failed,
        total_not_wired=total_not_wired,
        overall_success_rate=overall_success_rate,
        sources=sources,
        timeseries_labels=labels,
        timeseries_accepted=ts_accepted,
        timeseries_completed=ts_completed,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #48B: GET /obs/input/humanoid-loop-stats  — Gateway 人体闭环统计
# ---------------------------------------------------------------------------
class InputHumanoidLoopStageItem(BaseModel):
    stage: str
    count: int
    rate: Optional[float]


class InputHumanoidLoopStatsResponse(BaseModel):
    window_minutes: int
    total_gateway_requests: int
    completed_requests: int
    failed_requests: int
    ack_touched_requests: int
    ack_confirmed_requests: int
    progress_touched_requests: int
    deviation_detected_requests: int
    deviation_recovered_requests: int
    closed_loop_requests: int
    completion_rate: Optional[float]
    ack_touch_rate: Optional[float]
    progress_touch_rate: Optional[float]
    deviation_recovery_rate: Optional[float]
    closed_loop_rate: Optional[float]
    stage_counts: List[InputHumanoidLoopStageItem]
    timestamp: int


@router.get("/input/humanoid-loop-stats", response_model=InputHumanoidLoopStatsResponse)
def get_input_humanoid_loop_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #48B: Gateway 人体闭环统计（ACK / 进度回报 / 偏航恢复）。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range(
            "voice_event_log",
            start_ms=since_ms,
            end_ms=now,
            limit=20_000,
        )
        dispatch_rows = [r for r in rows if _is_input_dispatch_event(r)]
    except Exception:
        dispatch_rows = []

    from collections import Counter, defaultdict

    request_flags: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "accepted": False,
            "completed": False,
            "failed": False,
            "ack_pending": False,
            "acked": False,
            "progress_fastpath": False,
            "progress_pushed": False,
            "deviation_detected": False,
            "deviation_recovered": False,
        }
    )
    stage_counter: Counter[str] = Counter()

    for row in dispatch_rows:
        event_type = str(row.get("event_type") or row.get("status") or "").strip().upper()
        if not event_type:
            continue
        payload = _safe_json(row.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        source = _normalize_input_dispatch_source(
            payload_dict.get("source") or row.get("source") or row.get("channel"),
            event_type,
        )
        if source != "GATEWAY":
            continue

        request_id = str(row.get("request_id") or payload_dict.get("request_id") or "").strip()
        if not request_id:
            continue
        flags = request_flags[request_id]
        stage_counter[event_type] += 1

        status_upper = str(row.get("status") or "").strip().upper()
        if "ACCEPTED" in event_type:
            flags["accepted"] = True
        if "COMPLETED" in event_type:
            flags["completed"] = True
        if "FAILED" in event_type or "ERROR" in event_type:
            flags["failed"] = True
        if event_type == "INPUT_ACK_PENDING":
            flags["ack_pending"] = True
        if event_type == "INPUT_ACKED":
            flags["acked"] = True
        if event_type == "INPUT_PROGRESS_FASTPATH" and status_upper == "OK":
            flags["progress_fastpath"] = True
        if event_type == "INPUT_PROGRESS_PUSHED":
            flags["progress_pushed"] = True
        if event_type == "INPUT_DEVIATION_DETECTED":
            flags["deviation_detected"] = True
        if event_type == "INPUT_DEVIATION_RECOVERED":
            flags["deviation_recovered"] = True

    total_gateway_requests = len(request_flags)
    completed_requests = sum(1 for f in request_flags.values() if f.get("completed"))
    failed_requests = sum(1 for f in request_flags.values() if f.get("failed"))
    ack_touched_requests = sum(1 for f in request_flags.values() if f.get("ack_pending") or f.get("acked"))
    ack_confirmed_requests = sum(1 for f in request_flags.values() if f.get("acked"))
    progress_touched_requests = sum(
        1 for f in request_flags.values() if f.get("progress_fastpath") or f.get("progress_pushed")
    )
    deviation_detected_requests = sum(1 for f in request_flags.values() if f.get("deviation_detected"))
    deviation_recovered_requests = sum(1 for f in request_flags.values() if f.get("deviation_recovered"))
    closed_loop_requests = sum(
        1
        for f in request_flags.values()
        if f.get("completed")
        and (f.get("ack_pending") or f.get("acked") or f.get("progress_fastpath") or f.get("progress_pushed") or f.get("deviation_detected"))
        and (not f.get("deviation_detected") or f.get("deviation_recovered"))
    )

    completion_rate = (
        round(completed_requests / total_gateway_requests * 100, 1)
        if total_gateway_requests > 0
        else None
    )
    ack_touch_rate = (
        round(ack_touched_requests / total_gateway_requests * 100, 1)
        if total_gateway_requests > 0
        else None
    )
    progress_touch_rate = (
        round(progress_touched_requests / total_gateway_requests * 100, 1)
        if total_gateway_requests > 0
        else None
    )
    deviation_recovery_rate = (
        round(deviation_recovered_requests / deviation_detected_requests * 100, 1)
        if deviation_detected_requests > 0
        else None
    )
    closed_loop_rate = (
        round(closed_loop_requests / total_gateway_requests * 100, 1)
        if total_gateway_requests > 0
        else None
    )

    stage_counts = [
        InputHumanoidLoopStageItem(
            stage=stage,
            count=count,
            rate=(round(count / total_gateway_requests * 100, 1) if total_gateway_requests > 0 else None),
        )
        for stage, count in stage_counter.most_common(20)
    ]

    return InputHumanoidLoopStatsResponse(
        window_minutes=window_minutes,
        total_gateway_requests=total_gateway_requests,
        completed_requests=completed_requests,
        failed_requests=failed_requests,
        ack_touched_requests=ack_touched_requests,
        ack_confirmed_requests=ack_confirmed_requests,
        progress_touched_requests=progress_touched_requests,
        deviation_detected_requests=deviation_detected_requests,
        deviation_recovered_requests=deviation_recovered_requests,
        closed_loop_requests=closed_loop_requests,
        completion_rate=completion_rate,
        ack_touch_rate=ack_touch_rate,
        progress_touch_rate=progress_touch_rate,
        deviation_recovery_rate=deviation_recovery_rate,
        closed_loop_rate=closed_loop_rate,
        stage_counts=stage_counts,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #49: GET /obs/recall-pack/stats  — RecallPack token 预算统计
# ---------------------------------------------------------------------------
class InputHumanoidLoopTrendResponse(BaseModel):
    window_minutes: int
    interval_minutes: int
    timeseries_labels: List[str]
    timeseries_total_gateway_requests: List[int]
    timeseries_completed_requests: List[int]
    timeseries_failed_requests: List[int]
    timeseries_closed_loop_requests: List[int]
    timeseries_ack_touched_requests: List[int]
    timeseries_progress_touched_requests: List[int]
    timeseries_deviation_detected_requests: List[int]
    timeseries_deviation_recovered_requests: List[int]
    timeseries_completion_rate: List[Optional[float]]
    timeseries_closed_loop_rate: List[Optional[float]]
    timeseries_ack_touch_rate: List[Optional[float]]
    timeseries_progress_touch_rate: List[Optional[float]]
    timeseries_deviation_recovery_rate: List[Optional[float]]
    timestamp: int


@router.get("/input/humanoid-loop-trend", response_model=InputHumanoidLoopTrendResponse)
def get_input_humanoid_loop_trend(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=360, description="时序聚合间隔（分钟）"),
):
    """Ref #48C: Gateway 人体闭环趋势统计（按请求归一化聚合）。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range(
            "voice_event_log",
            start_ms=since_ms,
            end_ms=now,
            limit=30_000,
        )
        dispatch_rows = [r for r in rows if _is_input_dispatch_event(r)]
    except Exception:
        dispatch_rows = []

    from collections import defaultdict

    request_flags: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "first_ts": None,
            "accepted_ts": None,
            "completed": False,
            "failed": False,
            "ack_pending": False,
            "acked": False,
            "progress_fastpath": False,
            "progress_pushed": False,
            "deviation_detected": False,
            "deviation_recovered": False,
        }
    )

    for row in dispatch_rows:
        event_type = str(row.get("event_type") or row.get("status") or "").strip().upper()
        if not event_type:
            continue
        payload = _safe_json(row.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        source = _normalize_input_dispatch_source(
            payload_dict.get("source") or row.get("source") or row.get("channel"),
            event_type,
        )
        if source != "GATEWAY":
            continue

        request_id = str(row.get("request_id") or payload_dict.get("request_id") or "").strip()
        if not request_id:
            continue

        flags = request_flags[request_id]
        ts_val = int(row.get("ts_ms") or 0)
        if ts_val > 0:
            if flags["first_ts"] is None or ts_val < flags["first_ts"]:
                flags["first_ts"] = ts_val
            if "ACCEPTED" in event_type and (flags["accepted_ts"] is None or ts_val < flags["accepted_ts"]):
                flags["accepted_ts"] = ts_val

        status_upper = str(row.get("status") or "").strip().upper()
        if "COMPLETED" in event_type:
            flags["completed"] = True
        if "FAILED" in event_type or "ERROR" in event_type:
            flags["failed"] = True
        if event_type == "INPUT_ACK_PENDING":
            flags["ack_pending"] = True
        if event_type == "INPUT_ACKED":
            flags["acked"] = True
        if event_type == "INPUT_PROGRESS_FASTPATH" and status_upper == "OK":
            flags["progress_fastpath"] = True
        if event_type == "INPUT_PROGRESS_PUSHED":
            flags["progress_pushed"] = True
        if event_type == "INPUT_DEVIATION_DETECTED":
            flags["deviation_detected"] = True
        if event_type == "INPUT_DEVIATION_RECOVERED":
            flags["deviation_recovered"] = True

    interval_ms = interval_minutes * 60 * 1000
    bucket_count = max(1, (window_minutes * 60 * 1000) // interval_ms)
    ts_total_requests = [0] * bucket_count
    ts_completed = [0] * bucket_count
    ts_failed = [0] * bucket_count
    ts_closed_loop = [0] * bucket_count
    ts_ack_touched = [0] * bucket_count
    ts_progress_touched = [0] * bucket_count
    ts_deviation_detected = [0] * bucket_count
    ts_deviation_recovered = [0] * bucket_count

    for flags in request_flags.values():
        anchor_ts = flags.get("accepted_ts") or flags.get("first_ts")
        if not anchor_ts:
            continue
        idx = min(int((anchor_ts - since_ms) // interval_ms), bucket_count - 1)
        if idx < 0 or idx >= bucket_count:
            continue

        ack_touched = bool(flags.get("ack_pending") or flags.get("acked"))
        progress_touched = bool(flags.get("progress_fastpath") or flags.get("progress_pushed"))
        deviation_detected = bool(flags.get("deviation_detected"))
        deviation_recovered = bool(flags.get("deviation_recovered"))
        completed = bool(flags.get("completed"))
        failed = bool(flags.get("failed"))
        closed_loop = bool(
            completed
            and (ack_touched or progress_touched or deviation_detected)
            and (not deviation_detected or deviation_recovered)
        )

        ts_total_requests[idx] += 1
        if completed:
            ts_completed[idx] += 1
        if failed:
            ts_failed[idx] += 1
        if ack_touched:
            ts_ack_touched[idx] += 1
        if progress_touched:
            ts_progress_touched[idx] += 1
        if deviation_detected:
            ts_deviation_detected[idx] += 1
        if deviation_recovered:
            ts_deviation_recovered[idx] += 1
        if closed_loop:
            ts_closed_loop[idx] += 1

    ts_completion_rate: List[Optional[float]] = []
    ts_closed_loop_rate: List[Optional[float]] = []
    ts_ack_touch_rate: List[Optional[float]] = []
    ts_progress_touch_rate: List[Optional[float]] = []
    ts_deviation_recovery_rate: List[Optional[float]] = []
    for i in range(bucket_count):
        total = ts_total_requests[i]
        deviations = ts_deviation_detected[i]
        ts_completion_rate.append(round(ts_completed[i] / total * 100, 1) if total > 0 else None)
        ts_closed_loop_rate.append(round(ts_closed_loop[i] / total * 100, 1) if total > 0 else None)
        ts_ack_touch_rate.append(round(ts_ack_touched[i] / total * 100, 1) if total > 0 else None)
        ts_progress_touch_rate.append(round(ts_progress_touched[i] / total * 100, 1) if total > 0 else None)
        ts_deviation_recovery_rate.append(
            round(ts_deviation_recovered[i] / deviations * 100, 1) if deviations > 0 else None
        )

    labels = []
    for i in range(bucket_count):
        ts_val = since_ms + i * interval_ms
        d = __import__("datetime").datetime.fromtimestamp(ts_val / 1000)
        if window_minutes <= 60:
            labels.append(d.strftime("%H:%M"))
        elif window_minutes <= 1440:
            labels.append(d.strftime("%m/%d %H:%M"))
        else:
            labels.append(d.strftime("%m/%d"))

    return InputHumanoidLoopTrendResponse(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
        timeseries_labels=labels,
        timeseries_total_gateway_requests=ts_total_requests,
        timeseries_completed_requests=ts_completed,
        timeseries_failed_requests=ts_failed,
        timeseries_closed_loop_requests=ts_closed_loop,
        timeseries_ack_touched_requests=ts_ack_touched,
        timeseries_progress_touched_requests=ts_progress_touched,
        timeseries_deviation_detected_requests=ts_deviation_detected,
        timeseries_deviation_recovered_requests=ts_deviation_recovered,
        timeseries_completion_rate=ts_completion_rate,
        timeseries_closed_loop_rate=ts_closed_loop_rate,
        timeseries_ack_touch_rate=ts_ack_touch_rate,
        timeseries_progress_touch_rate=ts_progress_touch_rate,
        timeseries_deviation_recovery_rate=ts_deviation_recovery_rate,
        timestamp=now,
    )


class InputHumanoidLoopAlertItem(BaseModel):
    code: str
    severity: str
    title: str
    detail: str
    metric: str
    value: Optional[float]
    threshold: Optional[float]
    direction: str


class InputHumanoidLoopAlertsResponse(BaseModel):
    window_minutes: int
    interval_minutes: int
    evaluated_bucket_index: Optional[int]
    evaluated_label: Optional[str]
    sample_requests: int
    status: str
    severity: str
    summary: str
    latest_completion_rate: Optional[float]
    latest_closed_loop_rate: Optional[float]
    latest_ack_touch_rate: Optional[float]
    latest_progress_touch_rate: Optional[float]
    latest_failed_rate: Optional[float]
    latest_deviation_recovery_rate: Optional[float]
    alerts: List[InputHumanoidLoopAlertItem]
    timestamp: int


@router.get("/input/humanoid-loop-alerts", response_model=InputHumanoidLoopAlertsResponse)
def get_input_humanoid_loop_alerts(
    window_minutes: int = Query(360, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(30, ge=1, le=360, description="时序聚合间隔（分钟）"),
    min_bucket_requests: int = Query(3, ge=1, le=100, description="告警评估最小样本量"),
    completion_rate_warn: float = Query(85.0, ge=0.0, le=100.0),
    closed_loop_rate_warn: float = Query(70.0, ge=0.0, le=100.0),
    ack_touch_rate_warn: float = Query(75.0, ge=0.0, le=100.0),
    progress_touch_rate_warn: float = Query(50.0, ge=0.0, le=100.0),
    failed_rate_warn: float = Query(20.0, ge=0.0, le=100.0),
    deviation_recovery_rate_warn: float = Query(60.0, ge=0.0, le=100.0),
    closed_loop_drop_warn: float = Query(25.0, ge=0.0, le=100.0),
    baseline_buckets: int = Query(3, ge=1, le=24),
):
    """Ref #48D: Gateway 人体闭环趋势告警评估。"""
    now = _now_ms()
    trend = get_input_humanoid_loop_trend(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
    )

    labels = trend.timeseries_labels
    total_series = trend.timeseries_total_gateway_requests
    completed_rate_series = trend.timeseries_completion_rate
    closed_loop_rate_series = trend.timeseries_closed_loop_rate
    ack_touch_rate_series = trend.timeseries_ack_touch_rate
    progress_touch_rate_series = trend.timeseries_progress_touch_rate
    deviation_recovery_rate_series = trend.timeseries_deviation_recovery_rate
    failed_series = trend.timeseries_failed_requests

    idx = None
    for i in range(len(total_series) - 1, -1, -1):
        if int(total_series[i] or 0) >= min_bucket_requests:
            idx = i
            break

    if idx is None:
        return InputHumanoidLoopAlertsResponse(
            window_minutes=window_minutes,
            interval_minutes=interval_minutes,
            evaluated_bucket_index=None,
            evaluated_label=None,
            sample_requests=0,
            status="no_data",
            severity="info",
            summary="最近窗口内暂无满足最小样本量的 Gateway 闭环数据。",
            latest_completion_rate=None,
            latest_closed_loop_rate=None,
            latest_ack_touch_rate=None,
            latest_progress_touch_rate=None,
            latest_failed_rate=None,
            latest_deviation_recovery_rate=None,
            alerts=[],
            timestamp=now,
        )

    sample_requests = int(total_series[idx] or 0)
    latest_completion_rate = completed_rate_series[idx]
    latest_closed_loop_rate = closed_loop_rate_series[idx]
    latest_ack_touch_rate = ack_touch_rate_series[idx]
    latest_progress_touch_rate = progress_touch_rate_series[idx]
    latest_deviation_recovery_rate = deviation_recovery_rate_series[idx]
    latest_failed_rate = (
        round(int(failed_series[idx] or 0) / sample_requests * 100, 1)
        if sample_requests > 0
        else None
    )

    alerts: List[InputHumanoidLoopAlertItem] = []

    def _push_alert(
        *,
        code: str,
        severity: str,
        title: str,
        detail: str,
        metric: str,
        value: Optional[float],
        threshold: Optional[float],
        direction: str,
    ) -> None:
        alerts.append(
            InputHumanoidLoopAlertItem(
                code=code,
                severity=severity,
                title=title,
                detail=detail,
                metric=metric,
                value=value,
                threshold=threshold,
                direction=direction,
            )
        )

    if latest_completion_rate is not None and latest_completion_rate < completion_rate_warn:
        _push_alert(
            code="COMPLETION_LOW",
            severity="warning",
            title="完成率偏低",
            detail=f"当前桶完成率 {latest_completion_rate}% 低于阈值 {completion_rate_warn}%。",
            metric="completion_rate",
            value=latest_completion_rate,
            threshold=completion_rate_warn,
            direction="below",
        )

    if latest_closed_loop_rate is not None and latest_closed_loop_rate < closed_loop_rate_warn:
        _push_alert(
            code="CLOSED_LOOP_LOW",
            severity="critical",
            title="闭环率偏低",
            detail=f"当前桶闭环率 {latest_closed_loop_rate}% 低于阈值 {closed_loop_rate_warn}%。",
            metric="closed_loop_rate",
            value=latest_closed_loop_rate,
            threshold=closed_loop_rate_warn,
            direction="below",
        )

    if latest_ack_touch_rate is not None and latest_ack_touch_rate < ack_touch_rate_warn:
        _push_alert(
            code="ACK_TOUCH_LOW",
            severity="warning",
            title="ACK触达率偏低",
            detail=f"当前桶 ACK 触达率 {latest_ack_touch_rate}% 低于阈值 {ack_touch_rate_warn}%。",
            metric="ack_touch_rate",
            value=latest_ack_touch_rate,
            threshold=ack_touch_rate_warn,
            direction="below",
        )

    if latest_progress_touch_rate is not None and latest_progress_touch_rate < progress_touch_rate_warn:
        _push_alert(
            code="PROGRESS_TOUCH_LOW",
            severity="warning",
            title="进度触达率偏低",
            detail=f"当前桶进度触达率 {latest_progress_touch_rate}% 低于阈值 {progress_touch_rate_warn}%。",
            metric="progress_touch_rate",
            value=latest_progress_touch_rate,
            threshold=progress_touch_rate_warn,
            direction="below",
        )

    if latest_failed_rate is not None and latest_failed_rate > failed_rate_warn:
        _push_alert(
            code="FAILED_RATE_HIGH",
            severity="critical",
            title="失败率偏高",
            detail=f"当前桶失败率 {latest_failed_rate}% 高于阈值 {failed_rate_warn}%。",
            metric="failed_rate",
            value=latest_failed_rate,
            threshold=failed_rate_warn,
            direction="above",
        )

    if (
        latest_deviation_recovery_rate is not None
        and latest_deviation_recovery_rate < deviation_recovery_rate_warn
    ):
        _push_alert(
            code="DEVIATION_RECOVERY_LOW",
            severity="warning",
            title="偏航恢复率偏低",
            detail=(
                f"当前桶偏航恢复率 {latest_deviation_recovery_rate}% "
                f"低于阈值 {deviation_recovery_rate_warn}%。"
            ),
            metric="deviation_recovery_rate",
            value=latest_deviation_recovery_rate,
            threshold=deviation_recovery_rate_warn,
            direction="below",
        )

    baseline_values = []
    for j in range(max(0, idx - 24), idx):
        if int(total_series[j] or 0) < min_bucket_requests:
            continue
        val = closed_loop_rate_series[j]
        if val is None:
            continue
        baseline_values.append(float(val))
    baseline_values = baseline_values[-baseline_buckets:]
    if baseline_values and latest_closed_loop_rate is not None:
        baseline_avg = round(sum(baseline_values) / len(baseline_values), 1)
        drop = round(baseline_avg - float(latest_closed_loop_rate), 1)
        if drop >= closed_loop_drop_warn:
            severity = "critical" if drop >= closed_loop_drop_warn * 1.5 else "warning"
            _push_alert(
                code="CLOSED_LOOP_DROP",
                severity=severity,
                title="闭环率突降",
                detail=f"当前桶较基线下降 {drop}%（基线 {baseline_avg}% -> 当前 {latest_closed_loop_rate}%）。",
                metric="closed_loop_rate_drop",
                value=drop,
                threshold=closed_loop_drop_warn,
                direction="above",
            )

    if any(item.severity == "critical" for item in alerts):
        status = "critical"
        severity = "critical"
    elif alerts:
        status = "degraded"
        severity = "warning"
    else:
        status = "healthy"
        severity = "ok"

    if status == "healthy":
        summary = f"闭环趋势健康（样本 {sample_requests}，桶 {labels[idx]}）。"
    elif status == "degraded":
        summary = f"闭环趋势轻度异常（{len(alerts)} 项），建议关注触达链路。"
    else:
        summary = f"闭环趋势严重异常（{len(alerts)} 项），建议立即排查 Gateway 执行链。"

    return InputHumanoidLoopAlertsResponse(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
        evaluated_bucket_index=idx,
        evaluated_label=labels[idx] if idx < len(labels) else None,
        sample_requests=sample_requests,
        status=status,
        severity=severity,
        summary=summary,
        latest_completion_rate=latest_completion_rate,
        latest_closed_loop_rate=latest_closed_loop_rate,
        latest_ack_touch_rate=latest_ack_touch_rate,
        latest_progress_touch_rate=latest_progress_touch_rate,
        latest_failed_rate=latest_failed_rate,
        latest_deviation_recovery_rate=latest_deviation_recovery_rate,
        alerts=alerts,
        timestamp=now,
    )


class InputHumanoidLoopRemediationStepItem(BaseModel):
    step_id: str
    priority: int
    title: str
    detail: str
    category: str
    target_path: Optional[str]
    target_label: Optional[str]
    operator_hint: Optional[str]
    evidence_codes: List[str]


class InputHumanoidLoopRemediationRequestItem(BaseModel):
    request_id: str
    category: str
    reason: str
    target_path: str


class InputHumanoidLoopRemediationResponse(BaseModel):
    window_minutes: int
    interval_minutes: int
    status: str
    severity: str
    summary: str
    primary_focus: str
    next_review_hint: str
    representative_requests: List[InputHumanoidLoopRemediationRequestItem]
    steps: List[InputHumanoidLoopRemediationStepItem]
    timestamp: int


@router.get("/input/humanoid-loop-remediation", response_model=InputHumanoidLoopRemediationResponse)
def get_input_humanoid_loop_remediation(
    window_minutes: int = Query(360, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(30, ge=1, le=360, description="时序聚合间隔（分钟）"),
    min_bucket_requests: int = Query(3, ge=1, le=100, description="告警评估最小样本量"),
):
    """Ref #48E: Gateway 人体闭环告警驱动自愈建议。"""
    now = _now_ms()
    alerts_resp = get_input_humanoid_loop_alerts(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
        min_bucket_requests=min_bucket_requests,
        completion_rate_warn=85.0,
        closed_loop_rate_warn=70.0,
        ack_touch_rate_warn=75.0,
        progress_touch_rate_warn=50.0,
        failed_rate_warn=20.0,
        deviation_recovery_rate_warn=60.0,
        closed_loop_drop_warn=25.0,
        baseline_buckets=3,
    )

    step_map: Dict[str, InputHumanoidLoopRemediationStepItem] = {}
    representative_requests: List[InputHumanoidLoopRemediationRequestItem] = []

    def _collect_representative_requests() -> None:
        if alerts_resp.evaluated_bucket_index is None:
            return

        ledger = _get_ledger()
        since_ms = now - window_minutes * 60 * 1000
        interval_ms = interval_minutes * 60 * 1000
        bucket_start = since_ms + int(alerts_resp.evaluated_bucket_index) * interval_ms
        bucket_end = bucket_start + interval_ms

        try:
            rows = ledger.fetch_by_time_range(
                "voice_event_log",
                start_ms=since_ms,
                end_ms=now,
                limit=30_000,
            )
            dispatch_rows = [r for r in rows if _is_input_dispatch_event(r)]
        except Exception:
            dispatch_rows = []

        from collections import defaultdict

        request_flags: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "first_ts": None,
                "accepted_ts": None,
                "completed": False,
                "failed": False,
                "ack_pending": False,
                "acked": False,
                "progress_fastpath": False,
                "progress_pushed": False,
                "deviation_detected": False,
                "deviation_recovered": False,
            }
        )

        for row in dispatch_rows:
            event_type = str(row.get("event_type") or row.get("status") or "").strip().upper()
            if not event_type:
                continue
            payload = _safe_json(row.get("payload"))
            payload_dict = dict(payload) if isinstance(payload, dict) else {}
            source = _normalize_input_dispatch_source(
                payload_dict.get("source") or row.get("source") or row.get("channel"),
                event_type,
            )
            if source != "GATEWAY":
                continue

            request_id = str(row.get("request_id") or payload_dict.get("request_id") or "").strip()
            if not request_id:
                continue

            flags = request_flags[request_id]
            ts_val = int(row.get("ts_ms") or 0)
            if ts_val > 0:
                if flags["first_ts"] is None or ts_val < flags["first_ts"]:
                    flags["first_ts"] = ts_val
                if "ACCEPTED" in event_type and (flags["accepted_ts"] is None or ts_val < flags["accepted_ts"]):
                    flags["accepted_ts"] = ts_val

            status_upper = str(row.get("status") or "").strip().upper()
            if "COMPLETED" in event_type:
                flags["completed"] = True
            if "FAILED" in event_type or "ERROR" in event_type:
                flags["failed"] = True
            if event_type == "INPUT_ACK_PENDING":
                flags["ack_pending"] = True
            if event_type == "INPUT_ACKED":
                flags["acked"] = True
            if event_type == "INPUT_PROGRESS_FASTPATH" and status_upper == "OK":
                flags["progress_fastpath"] = True
            if event_type == "INPUT_PROGRESS_PUSHED":
                flags["progress_pushed"] = True
            if event_type == "INPUT_DEVIATION_DETECTED":
                flags["deviation_detected"] = True
            if event_type == "INPUT_DEVIATION_RECOVERED":
                flags["deviation_recovered"] = True

        ranked_candidates: list[tuple[int, int, str, str, str]] = []
        for request_id, flags in request_flags.items():
            anchor_ts = flags.get("accepted_ts") or flags.get("first_ts")
            if not anchor_ts or anchor_ts < bucket_start or anchor_ts >= bucket_end:
                continue

            ack_touched = bool(flags.get("ack_pending") or flags.get("acked"))
            progress_touched = bool(flags.get("progress_fastpath") or flags.get("progress_pushed"))
            deviation_detected = bool(flags.get("deviation_detected"))
            deviation_recovered = bool(flags.get("deviation_recovered"))
            completed = bool(flags.get("completed"))
            failed = bool(flags.get("failed"))
            closed_loop = bool(
                completed
                and (ack_touched or progress_touched or deviation_detected)
                and (not deviation_detected or deviation_recovered)
            )

            if failed:
                category = "failed"
                reason = "请求执行失败"
                rank = 0
            elif not closed_loop:
                category = "not_closed_loop"
                reason = "请求完成但未形成闭环触达"
                rank = 1
            elif not ack_touched and not progress_touched:
                category = "low_touch"
                reason = "触达信号不足"
                rank = 2
            else:
                continue

            ranked_candidates.append((rank, -int(anchor_ts), request_id, category, reason))

        ranked_candidates.sort()
        representative_requests.extend(
            InputHumanoidLoopRemediationRequestItem(
                request_id=request_id,
                category=category,
                reason=reason,
                target_path=f"/trace/{request_id}",
            )
            for _, _, request_id, category, reason in ranked_candidates[:3]
        )

    def _register_step(
        *,
        step_id: str,
        priority: int,
        title: str,
        detail: str,
        category: str,
        target_path: Optional[str],
        target_label: Optional[str],
        operator_hint: Optional[str],
        evidence_code: Optional[str] = None,
    ) -> None:
        existing = step_map.get(step_id)
        if existing is None:
            step_map[step_id] = InputHumanoidLoopRemediationStepItem(
                step_id=step_id,
                priority=priority,
                title=title,
                detail=detail,
                category=category,
                target_path=target_path,
                target_label=target_label,
                operator_hint=operator_hint,
                evidence_codes=[evidence_code] if evidence_code else [],
            )
            return

        if priority < existing.priority:
            existing.priority = priority
        if evidence_code and evidence_code not in existing.evidence_codes:
            existing.evidence_codes.append(evidence_code)

    _collect_representative_requests()

    if alerts_resp.status == "no_data":
        _register_step(
            step_id="generate_gateway_samples",
            priority=1,
            title="先生成可评估样本",
            detail="最近窗口内满足样本量的 Gateway 请求不足，先通过 QQ/微信等通道产生一批真实操作型请求。",
            category="sampling",
            target_path="/gateway",
            target_label="打开 Gateway",
            operator_hint="建议连续发送 3~5 条需要执行的操作请求，再观察闭环指标。",
        )
        return InputHumanoidLoopRemediationResponse(
            window_minutes=window_minutes,
            interval_minutes=interval_minutes,
            status=alerts_resp.status,
            severity=alerts_resp.severity,
            summary="当前暂无足够样本，先补齐真实 Gateway 请求后再做闭环排查。",
            primary_focus="补样本",
            next_review_hint="补齐样本后刷新数字生命体总览，观察闭环趋势与告警状态。",
            representative_requests=[],
            steps=sorted(step_map.values(), key=lambda item: (item.priority, item.step_id)),
            timestamp=now,
        )

    _register_step(
        step_id="inspect_gateway_recent_health",
        priority=1,
        title="先看 Gateway 最近窗口健康",
        detail="确认当前桶的请求量、失败量、闭环率和平台通道状态，先判断是整体退化还是局部触达失灵。",
        category="gateway",
        target_path="/gateway",
        target_label="打开 Gateway",
        operator_hint="重点对照闭环率、失败率与平台消息吞吐是否同步恶化。",
    )
    _register_step(
        step_id="open_trace_full",
        priority=2,
        title="打开全链路 Trace 排查断点",
        detail="沿输入接收、意图识别、技能执行、人格渲染逐层查看，确认闭环究竟断在 ACK、进度、执行还是最终回执。",
        category="trace",
        target_path="/trace/full",
        target_label="打开 Trace 总览",
        operator_hint="优先抽取最近一个失败请求和一个未闭环请求做对照。",
    )
    _register_step(
        step_id="inspect_alert_center",
        priority=3,
        title="回到告警中心按原因收敛问题",
        detail="把当前闭环异常与系统其它告警对齐，确认是否同时伴随服务降级、错误码聚集或 Forge 风险扩散。",
        category="alerts",
        target_path="/alerts",
        target_label="打开告警中心",
        operator_hint="若闭环异常与服务 DOWN 同时出现，优先处理底层通道问题。",
    )

    if alerts_resp.status == "healthy":
        _register_step(
            step_id="keep_ack_and_progress_path_warm",
            priority=4,
            title="维持 ACK 与进度路径热身",
            detail="当前闭环健康，但仍建议定期验证 ACK、进度查询与偏航恢复路径未被新改动破坏。",
            category="preventive",
            target_path="/digital-life",
            target_label="留在当前页",
            operator_hint="每次改路由、输入链路或人格渲染后都应回归验证。",
        )
    else:
        for alert in alerts_resp.alerts:
            code = str(alert.code or "").upper()
            if code in {"CLOSED_LOOP_LOW", "CLOSED_LOOP_DROP", "COMPLETION_LOW", "FAILED_RATE_HIGH"}:
                _register_step(
                    step_id="inspect_gateway_recent_health",
                    priority=1,
                    title="先看 Gateway 最近窗口健康",
                    detail="确认当前桶的请求量、失败量、闭环率和平台通道状态，先判断是整体退化还是局部触达失灵。",
                    category="gateway",
                    target_path="/gateway",
                    target_label="打开 Gateway",
                    operator_hint="重点对照闭环率、失败率与平台消息吞吐是否同步恶化。",
                    evidence_code=code,
                )
                _register_step(
                    step_id="open_trace_full",
                    priority=2,
                    title="打开全链路 Trace 排查断点",
                    detail="沿输入接收、意图识别、技能执行、人格渲染逐层查看，确认闭环究竟断在 ACK、进度、执行还是最终回执。",
                    category="trace",
                    target_path="/trace/full",
                    target_label="打开 Trace 总览",
                    operator_hint="优先抽取最近一个失败请求和一个未闭环请求做对照。",
                    evidence_code=code,
                )
            if code == "ACK_TOUCH_LOW":
                _register_step(
                    step_id="verify_ack_settings",
                    priority=2,
                    title="检查即时 ACK 配置与送达链路",
                    detail="确认 ACK 路径是否被关闭、超时过短或被误判为非动作意图，从而没有及时返回人格化确认。",
                    category="ack",
                    target_path="/system",
                    target_label="打开系统配置",
                    operator_hint=(
                        "重点检查 HOST_GATEWAY_IMMEDIATE_ACK_ENABLED、"
                        "HOST_GATEWAY_IMMEDIATE_ACK_WAIT_RESULT、HOST_GATEWAY_IMMEDIATE_ACK_TIMEOUT_MS。"
                    ),
                    evidence_code=code,
                )
            if code == "PROGRESS_TOUCH_LOW":
                _register_step(
                    step_id="verify_progress_fastpath",
                    priority=3,
                    title="检查进度查询快路径与主动推送",
                    detail="确认进度询问有没有被识别出来，以及长任务是否启动了主动进度回报循环。",
                    category="progress",
                    target_path="/gateway",
                    target_label="打开 Gateway",
                    operator_hint=(
                        "重点检查 HOST_GATEWAY_PROGRESS_FASTPATH_ENABLED、"
                        "HOST_GATEWAY_PROGRESS_FASTPATH_WAIT_RESULT 与主动进度推送配置。"
                    ),
                    evidence_code=code,
                )
            if code == "DEVIATION_RECOVERY_LOW":
                _register_step(
                    step_id="verify_deviation_guard",
                    priority=3,
                    title="检查偏航检测与重试自愈链路",
                    detail="确认偏航检测是否触发、提示是否送达、以及 realign 重试是否真正被执行并覆盖了原结果。",
                    category="deviation",
                    target_path="/trace/full",
                    target_label="打开 Trace 总览",
                    operator_hint=(
                        "重点检查 HOST_GATEWAY_DEVIATION_GUARD_ENABLED、"
                        "HOST_GATEWAY_DEVIATION_NOTICE_ENABLED、HOST_GATEWAY_DEVIATION_RETRY_ENABLED。"
                    ),
                    evidence_code=code,
                )

    status_to_focus = {
        "healthy": "维持稳定",
        "degraded": "恢复触达",
        "critical": "抢修闭环",
        "no_data": "补齐样本",
    }
    primary_focus = status_to_focus.get(str(alerts_resp.status or "").lower(), "闭环排查")
    next_review_hint = (
        "完成排查后重新发送一条操作型请求，并观察 ACK、进度触达与闭环率是否回升到正常区间。"
    )
    summary = (
        alerts_resp.summary
        if alerts_resp.alerts
        else "当前未检测到闭环异常，建议继续保持 ACK、进度与偏航恢复链路的回归验证。"
    )

    return InputHumanoidLoopRemediationResponse(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
        status=alerts_resp.status,
        severity=alerts_resp.severity,
        summary=summary,
        primary_focus=primary_focus,
        next_review_hint=next_review_hint,
        representative_requests=representative_requests,
        steps=sorted(step_map.values(), key=lambda item: (item.priority, item.step_id)),
        timestamp=now,
    )


class InputHumanoidLoopDiagnosticItem(BaseModel):
    request_id: str
    category: str
    status: str
    input_text: Optional[str]
    output_text: Optional[str]
    suspected_breakpoint: str
    root_cause_code: str
    root_cause_summary: str
    reason_hint: Optional[str]
    evidence_codes: List[str]
    recommended_view_path: str
    recommended_view_label: str


class InputHumanoidLoopDiagnosticsResponse(BaseModel):
    window_minutes: int
    interval_minutes: int
    status: str
    severity: str
    summary: str
    diagnosed_count: int
    diagnostics: List[InputHumanoidLoopDiagnosticItem]
    timestamp: int


def _build_gateway_request_flags_from_voice_rows(voice_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    flags: Dict[str, Any] = {
        "accepted": False,
        "completed": False,
        "failed": False,
        "ack_pending": False,
        "acked": False,
        "progress_fastpath": False,
        "progress_pushed": False,
        "deviation_detected": False,
        "deviation_recovered": False,
        "reason_codes": [],
    }

    for row in sorted(
        [dict(item) for item in voice_rows if isinstance(item, dict)],
        key=lambda item: (
            int(item.get("ts_ms") or 0),
            int(item.get("seq") or 0),
            str(item.get("event_type") or ""),
        ),
    ):
        event_type = str(row.get("event_type") or row.get("status") or "").strip().upper()
        if not event_type:
            continue
        payload = _safe_json(row.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        source = _normalize_input_dispatch_source(
            payload_dict.get("source") or row.get("source") or row.get("channel"),
            event_type,
        )
        if source != "GATEWAY":
            continue

        status_upper = str(row.get("status") or "").strip().upper()
        if "ACCEPTED" in event_type:
            flags["accepted"] = True
        if "COMPLETED" in event_type:
            flags["completed"] = True
        if "FAILED" in event_type or "ERROR" in event_type:
            flags["failed"] = True
        if event_type == "INPUT_ACK_PENDING":
            flags["ack_pending"] = True
        if event_type == "INPUT_ACKED":
            flags["acked"] = True
        if event_type == "INPUT_PROGRESS_FASTPATH" and status_upper == "OK":
            flags["progress_fastpath"] = True
        if event_type == "INPUT_PROGRESS_PUSHED":
            flags["progress_pushed"] = True
        if event_type == "INPUT_DEVIATION_DETECTED":
            flags["deviation_detected"] = True
        if event_type == "INPUT_DEVIATION_RECOVERED":
            flags["deviation_recovered"] = True

        reason_code = str(payload_dict.get("reason_code") or row.get("reason_code") or "").strip().upper()
        if reason_code and reason_code not in flags["reason_codes"]:
            flags["reason_codes"].append(reason_code)

    flags["ack_touched"] = bool(flags["ack_pending"] or flags["acked"])
    flags["progress_touched"] = bool(flags["progress_fastpath"] or flags["progress_pushed"])
    flags["closed_loop"] = bool(
        flags["completed"]
        and (flags["ack_touched"] or flags["progress_touched"] or flags["deviation_detected"])
        and (not flags["deviation_detected"] or flags["deviation_recovered"])
    )

    if flags["failed"]:
        flags["status"] = "failed"
    elif flags["completed"] and flags["closed_loop"]:
        flags["status"] = "closed_loop"
    elif flags["completed"]:
        flags["status"] = "completed_without_loop"
    elif flags["accepted"]:
        flags["status"] = "in_progress"
    else:
        flags["status"] = "observed"
    return flags


def _collect_request_reason_codes(error_rows: List[Dict[str, Any]]) -> List[str]:
    codes: List[str] = []
    for row in reversed([dict(item) for item in error_rows if isinstance(item, dict)]):
        reason_code = str(row.get("reason_code") or "").strip().upper()
        if reason_code and reason_code not in codes:
            codes.append(reason_code)
        if len(codes) >= 4:
            break
    return codes


def _build_input_humanoid_loop_diagnostic_item(
    *,
    ledger: SQLiteLedger,
    request_id: str,
    category: str,
    reason: str,
) -> InputHumanoidLoopDiagnosticItem:
    intent_rows = _fetch_request_rows(ledger, "intent_log", request_id)
    governor_rows = _fetch_request_rows(ledger, "governor_log", request_id)
    retrieval_rows = _fetch_request_rows(ledger, "retrieval_event_log", request_id)
    llm_rows = _fetch_request_rows(ledger, "llm_node_call_log", request_id)
    airlock_rows = _fetch_request_rows(ledger, "airlock_log", request_id)
    bond_rows = _fetch_request_rows(ledger, "bond_update_log", request_id)
    memory_rows = _fetch_request_rows(ledger, "memory_ops_log", request_id)
    error_rows = _fetch_request_rows(ledger, "error_log", request_id)
    voice_rows = _fetch_request_rows(ledger, "voice_event_log", request_id)

    summary = _build_trace_summary(
        request_id=request_id,
        intent_rows=intent_rows,
        governor_rows=governor_rows,
        retrieval_rows=retrieval_rows,
        llm_rows=llm_rows,
        airlock_rows=airlock_rows,
        bond_rows=bond_rows,
        memory_rows=memory_rows,
        error_rows=error_rows,
        voice_rows=voice_rows,
    )
    flags = _build_gateway_request_flags_from_voice_rows(voice_rows)
    error_reason_codes = _collect_request_reason_codes(error_rows)
    governor_row = governor_rows[-1] if governor_rows else {}
    governor_decision = str(governor_row.get("decision") or "").strip().upper()
    governor_reason = str(governor_row.get("reason_code") or "").strip().upper() or None
    trace_reason = str(summary.get("reason_code") or "").strip().upper() or None
    failure_event = str(summary.get("failure_event") or "").strip() or None

    reason_hint = governor_reason or trace_reason or (error_reason_codes[0] if error_reason_codes else None) or failure_event or (reason or None)

    evidence_codes: List[str] = []

    def _push_evidence(code: Optional[str]) -> None:
        normalized = str(code or "").strip().upper()
        if normalized and normalized not in evidence_codes:
            evidence_codes.append(normalized)

    if flags.get("failed"):
        _push_evidence("FAILED_RATE_HIGH")
    if not flags.get("ack_touched"):
        _push_evidence("ACK_TOUCH_LOW")
    if not flags.get("progress_touched"):
        _push_evidence("PROGRESS_TOUCH_LOW")
    if flags.get("deviation_detected") and not flags.get("deviation_recovered"):
        _push_evidence("DEVIATION_RECOVERY_LOW")
    if not flags.get("closed_loop"):
        _push_evidence("CLOSED_LOOP_LOW")
    _push_evidence(governor_reason)
    _push_evidence(trace_reason)
    for code in error_reason_codes[:2]:
        _push_evidence(code)

    if governor_decision and governor_decision != "APPROVE":
        suspected_breakpoint = "governor_gate"
        root_cause_code = "GOVERNOR_BLOCKED"
        root_cause_summary = (
            f"请求在执行前被 Governor/Immune Gate 拦截，当前门控原因是 {governor_reason or 'UNKNOWN'}，"
            "需要先处理审批、风控或门禁规则。"
        )
        recommended_view_label = "打开门控链路"
    elif flags.get("failed"):
        suspected_breakpoint = "execution_stage"
        root_cause_code = "EXECUTION_FAILED"
        root_cause_summary = (
            f"请求已经进入执行阶段，但最终失败收敛；当前错误指纹为 {reason_hint or 'UNKNOWN'}，"
            "优先检查失败节点与错误日志。"
        )
        recommended_view_label = "打开失败链路"
    elif flags.get("deviation_detected") and not flags.get("deviation_recovered"):
        suspected_breakpoint = "deviation_recovery"
        root_cause_code = "DEVIATION_NOT_RECOVERED"
        root_cause_summary = (
            "链路中已经检测到偏航，但没有看到 recovered 信号，自愈重试没有真正闭合，"
            "需要复核 realign 提示和重试执行。"
        )
        recommended_view_label = "查看偏航修复"
    elif flags.get("completed") and not flags.get("ack_touched") and not flags.get("progress_touched"):
        suspected_breakpoint = "humanoid_touch"
        root_cause_code = "COMPLETED_WITHOUT_HUMANOID_TOUCH"
        root_cause_summary = (
            "请求虽然完成了，但整条链路缺少 ACK / Progress 生命体触达信号，"
            "用户体感上会像“系统做了事，却没有任何回应”。"
        )
        recommended_view_label = "检查触达链路"
    elif not flags.get("ack_touched"):
        suspected_breakpoint = "ack_touch_path"
        root_cause_code = "ACK_PATH_MISSING"
        root_cause_summary = (
            "请求已经进入 Gateway 执行，但未记录到 ACK 触达，"
            "优先排查即时确认链路是否被跳过、超时或错误归类。"
        )
        recommended_view_label = "检查 ACK 回路"
    elif not flags.get("progress_touched") and not flags.get("completed"):
        suspected_breakpoint = "progress_touch_path"
        root_cause_code = "PROGRESS_PATH_MISSING"
        root_cause_summary = (
            "请求仍在处理中，但没有进度触达信号，"
            "用户无法感知系统仍在继续执行。"
        )
        recommended_view_label = "检查进度回路"
    elif flags.get("completed") and not flags.get("closed_loop"):
        suspected_breakpoint = "closed_loop_convergence"
        root_cause_code = "CLOSED_LOOP_NOT_CONVERGED"
        root_cause_summary = (
            "请求已经收敛，但闭环条件没有全部满足，"
            "建议复核 ACK、Progress 与偏航恢复信号是否都完成落账。"
        )
        recommended_view_label = "复核闭环收敛"
    else:
        suspected_breakpoint = "trace_review"
        root_cause_code = "TRACE_REVIEW_REQUIRED"
        root_cause_summary = "当前请求存在异常样态，但还未命中稳定根因模板，建议直接打开链路回放逐节点复核。"
        recommended_view_label = "打开链路回放"

    input_text = str(summary.get("input_text") or "").strip() or None
    output_text = str(summary.get("output_text") or "").strip() or None

    return InputHumanoidLoopDiagnosticItem(
        request_id=request_id,
        category=category,
        status=str(flags.get("status") or "observed"),
        input_text=input_text,
        output_text=output_text,
        suspected_breakpoint=suspected_breakpoint,
        root_cause_code=root_cause_code,
        root_cause_summary=root_cause_summary,
        reason_hint=reason_hint,
        evidence_codes=evidence_codes[:6],
        recommended_view_path=f"/trace/{request_id}",
        recommended_view_label=recommended_view_label,
    )


@router.get("/input/humanoid-loop-diagnostics", response_model=InputHumanoidLoopDiagnosticsResponse)
def get_input_humanoid_loop_diagnostics(
    window_minutes: int = Query(360, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(30, ge=1, le=360, description="时序聚合间隔（分钟）"),
    min_bucket_requests: int = Query(3, ge=1, le=100, description="告警评估最小样本量"),
):
    """Ref #48F: Gateway 人体闭环异常回放摘要与根因归纳。"""
    now = _now_ms()
    remediation = get_input_humanoid_loop_remediation(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
        min_bucket_requests=min_bucket_requests,
    )

    if not remediation.representative_requests:
        summary = (
            "当前窗口未发现需要回放归因的代表性异常请求。"
            if str(remediation.status or "").lower() in {"healthy", "no_data"}
            else "当前窗口存在闭环异常，但暂时没有提取到可归纳的代表性请求，请继续结合修复建议排查。"
        )
        return InputHumanoidLoopDiagnosticsResponse(
            window_minutes=window_minutes,
            interval_minutes=interval_minutes,
            status=remediation.status,
            severity=remediation.severity,
            summary=summary,
            diagnosed_count=0,
            diagnostics=[],
            timestamp=now,
        )

    ledger = _get_ledger()
    diagnostics: List[InputHumanoidLoopDiagnosticItem] = []
    for item in remediation.representative_requests[:5]:
        request_id = str(item.request_id or "").strip()
        if not request_id:
            continue
        try:
            diagnostics.append(
                _build_input_humanoid_loop_diagnostic_item(
                    ledger=ledger,
                    request_id=request_id,
                    category=str(item.category or "").strip() or "unknown",
                    reason=str(item.reason or "").strip(),
                )
            )
        except Exception as exc:
            logger.warning("obs.humanoid_loop.diagnostics_build_failed request_id=%s error=%s", request_id, exc)
            diagnostics.append(
                InputHumanoidLoopDiagnosticItem(
                    request_id=request_id,
                    category=str(item.category or "").strip() or "unknown",
                    status="diagnostic_unavailable",
                    input_text=None,
                    output_text=None,
                    suspected_breakpoint="trace_review",
                    root_cause_code="TRACE_SUMMARY_UNAVAILABLE",
                    root_cause_summary="该请求的根因摘要暂时生成失败，请直接打开全链路 Trace 手动排查。",
                    reason_hint=str(item.reason or "").strip() or None,
                    evidence_codes=["TRACE_SUMMARY_UNAVAILABLE"],
                    recommended_view_path=f"/trace/{request_id}",
                    recommended_view_label="打开链路回放",
                )
            )

    summary = f"已为最近 {len(diagnostics)} 条代表性异常请求生成断点与根因摘要，可直接按 request 回放定位。"
    return InputHumanoidLoopDiagnosticsResponse(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
        status=remediation.status,
        severity=remediation.severity,
        summary=summary,
        diagnosed_count=len(diagnostics),
        diagnostics=diagnostics,
        timestamp=now,
    )


class InputHumanoidLoopHotspotRequestItem(BaseModel):
    request_id: str
    status: str
    target_path: str


class InputHumanoidLoopHotspotItem(BaseModel):
    hotspot_id: str
    organ_system: str
    organ_label: str
    root_cause_code: str
    suspected_breakpoint: str
    severity: str
    request_count: int
    share_pct: float
    narrative: str
    operator_focus: str
    evidence_codes: List[str]
    representative_requests: List[InputHumanoidLoopHotspotRequestItem]
    recommended_view_path: str
    recommended_view_label: str


class InputHumanoidLoopHotspotsResponse(BaseModel):
    window_minutes: int
    interval_minutes: int
    status: str
    severity: str
    summary: str
    diagnosed_count: int
    hotspot_count: int
    hotspots: List[InputHumanoidLoopHotspotItem]
    timestamp: int


def _resolve_humanoid_loop_hotspot_profile(
    *,
    root_cause_code: str,
    suspected_breakpoint: str,
) -> Dict[str, str]:
    profiles: Dict[str, Dict[str, str]] = {
        "ACK_PATH_MISSING": {
            "organ_system": "language_feedback",
            "organ_label": "语言回执系统",
            "operator_focus": "优先检查即时 ACK 开关、超时配置和人格化确认回路是否被短路。",
            "organ_story": "生命体已经收到请求，但没有先开口回应，用户体感会像神经已接入、语言器官却失声。",
            "recommended_view_path": "/system",
            "recommended_view_label": "检查 ACK 配置",
        },
        "PROGRESS_PATH_MISSING": {
            "organ_system": "somatic_progress",
            "organ_label": "体感进度系统",
            "operator_focus": "优先检查 progress fastpath、主动进度推送和长任务回报节律。",
            "organ_story": "身体还在执行，但没有把过程反馈给外界，表现为“人还活着，却像完全没动静”。",
            "recommended_view_path": "/gateway",
            "recommended_view_label": "检查进度回路",
        },
        "COMPLETED_WITHOUT_HUMANOID_TOUCH": {
            "organ_system": "language_feedback",
            "organ_label": "语言回执系统",
            "operator_focus": "检查 ACK 与 Progress 触达链路是否同时缺席，避免“做完了但一句话不说”。",
            "organ_story": "动作已经完成，但生命体没有留下任何触达痕迹，容易给用户造成冰冷的后台感。",
            "recommended_view_path": "/gateway",
            "recommended_view_label": "复核触达闭环",
        },
        "EXECUTION_FAILED": {
            "organ_system": "motor_execution",
            "organ_label": "运动执行系统",
            "operator_focus": "优先检查失败 skill、工具调用、超时和依赖服务状态。",
            "organ_story": "意图已经下达到身体，但真正动手的执行器失步了，表现为“知道该做，却做不成”。",
            "recommended_view_path": "",
            "recommended_view_label": "打开失败链路",
        },
        "DEVIATION_NOT_RECOVERED": {
            "organ_system": "immune_realign",
            "organ_label": "免疫纠偏系统",
            "operator_focus": "检查 deviation detect / notice / retry 是否真的形成了 recover 闭环。",
            "organ_story": "系统已经感知自己偏航，但免疫纠偏没有把轨道拉回来，属于自愈机制失手。",
            "recommended_view_path": "",
            "recommended_view_label": "查看偏航修复",
        },
        "GOVERNOR_BLOCKED": {
            "organ_system": "prefrontal_governor",
            "organ_label": "前额叶门控系统",
            "operator_focus": "检查 Governor/Immune Gate 决策、审批令牌与风控策略是否过严。",
            "organ_story": "动作还没开始，前额叶就先把身体刹住了，通常是门控、审批或免疫规则造成的阻断。",
            "recommended_view_path": "/gateway",
            "recommended_view_label": "检查门控链路",
        },
        "CLOSED_LOOP_NOT_CONVERGED": {
            "organ_system": "closed_loop_integration",
            "organ_label": "闭环整合系统",
            "operator_focus": "复核 ACK、Progress、Deviation Recovery 是否全部在同一次请求上完成闭合。",
            "organ_story": "链路各器官都动了一部分，但没能真正收束成一次完整的人体闭环。",
            "recommended_view_path": "/gateway",
            "recommended_view_label": "查看闭环总览",
        },
        "TRACE_REVIEW_REQUIRED": {
            "organ_system": "closed_loop_integration",
            "organ_label": "闭环整合系统",
            "operator_focus": "直接打开 Trace 逐节点回放，定位异常到底落在理解、执行还是回执层。",
            "organ_story": "当前异常还没有形成稳定模式，需要先通过回放把真正病灶定位出来。",
            "recommended_view_path": "",
            "recommended_view_label": "打开链路回放",
        },
    }
    fallback = {
        "organ_system": "closed_loop_integration",
        "organ_label": "闭环整合系统",
        "operator_focus": "先从 Trace 回放确认断点，再决定是补触达、修执行还是调门控。",
        "organ_story": "当前病灶尚未命中稳定模板，需要通过链路回放继续收敛。",
        "recommended_view_path": "",
        "recommended_view_label": "打开链路回放",
    }
    profile = dict(profiles.get(str(root_cause_code or "").strip().upper(), fallback))
    if not profile.get("organ_system") and suspected_breakpoint == "execution_stage":
        profile["organ_system"] = "motor_execution"
        profile["organ_label"] = "运动执行系统"
    return profile


def _classify_humanoid_loop_hotspot_severity(
    *,
    root_cause_code: str,
    request_count: int,
    overall_status: str,
) -> str:
    critical_codes = {"EXECUTION_FAILED", "DEVIATION_NOT_RECOVERED", "GOVERNOR_BLOCKED"}
    normalized_status = str(overall_status or "").strip().lower()
    normalized_code = str(root_cause_code or "").strip().upper()
    if normalized_code in critical_codes and request_count >= 1:
        return "critical"
    if normalized_status in {"critical", "degraded"} or request_count >= 2:
        return "warning"
    return "info"


@router.get("/input/humanoid-loop-hotspots", response_model=InputHumanoidLoopHotspotsResponse)
def get_input_humanoid_loop_hotspots(
    window_minutes: int = Query(360, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(30, ge=1, le=360, description="时序聚合间隔（分钟）"),
    min_bucket_requests: int = Query(3, ge=1, le=100, description="告警评估最小样本量"),
):
    """Ref #48G: Gateway 人体闭环病灶热区聚类。"""
    now = _now_ms()
    diagnostics_resp = get_input_humanoid_loop_diagnostics(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
        min_bucket_requests=min_bucket_requests,
    )

    if not diagnostics_resp.diagnostics:
        return InputHumanoidLoopHotspotsResponse(
            window_minutes=window_minutes,
            interval_minutes=interval_minutes,
            status=diagnostics_resp.status,
            severity=diagnostics_resp.severity,
            summary="当前窗口尚未形成系统级病灶热区。",
            diagnosed_count=0,
            hotspot_count=0,
            hotspots=[],
            timestamp=now,
        )

    grouped: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    diagnosed_count = len(diagnostics_resp.diagnostics)

    for item in diagnostics_resp.diagnostics:
        root_cause_code = str(item.root_cause_code or "").strip().upper() or "TRACE_REVIEW_REQUIRED"
        suspected_breakpoint = str(item.suspected_breakpoint or "").strip() or "trace_review"
        profile = _resolve_humanoid_loop_hotspot_profile(
            root_cause_code=root_cause_code,
            suspected_breakpoint=suspected_breakpoint,
        )
        key = (
            str(profile.get("organ_system") or "closed_loop_integration"),
            root_cause_code,
            suspected_breakpoint,
        )
        bucket = grouped.setdefault(
            key,
            {
                "profile": profile,
                "root_cause_code": root_cause_code,
                "suspected_breakpoint": suspected_breakpoint,
                "request_count": 0,
                "evidence_codes": [],
                "representative_requests": [],
            },
        )
        bucket["request_count"] += 1

        for code in list(item.evidence_codes or []):
            normalized = str(code or "").strip().upper()
            if normalized and normalized not in bucket["evidence_codes"]:
                bucket["evidence_codes"].append(normalized)

        bucket["representative_requests"].append(
            InputHumanoidLoopHotspotRequestItem(
                request_id=str(item.request_id or "").strip(),
                status=str(item.status or "").strip() or "observed",
                target_path=str(item.recommended_view_path or f"/trace/{item.request_id}"),
            )
        )

    hotspots: List[InputHumanoidLoopHotspotItem] = []
    ordered_groups = sorted(
        grouped.values(),
        key=lambda item: (
            -int(item.get("request_count") or 0),
            str(item.get("root_cause_code") or ""),
            str(item.get("suspected_breakpoint") or ""),
        ),
    )

    for group in ordered_groups:
        profile = dict(group.get("profile") or {})
        request_count = int(group.get("request_count") or 0)
        share_pct = round(request_count / diagnosed_count * 100, 1) if diagnosed_count > 0 else 0.0
        root_cause_code = str(group.get("root_cause_code") or "").strip().upper()
        suspected_breakpoint = str(group.get("suspected_breakpoint") or "").strip()
        representative_requests = list(group.get("representative_requests") or [])[:3]
        recommended_view_path = str(profile.get("recommended_view_path") or "").strip()
        if not recommended_view_path:
            recommended_view_path = representative_requests[0].target_path if representative_requests else "/gateway"
        severity = _classify_humanoid_loop_hotspot_severity(
            root_cause_code=root_cause_code,
            request_count=request_count,
            overall_status=diagnostics_resp.status,
        )
        organ_label = str(profile.get("organ_label") or "闭环整合系统")
        narrative = (
            f"最近 {request_count}/{diagnosed_count} 条代表性异常集中在{organ_label}，"
            f"主病灶是 {root_cause_code}。{str(profile.get('organ_story') or '').strip()}"
        )

        hotspots.append(
            InputHumanoidLoopHotspotItem(
                hotspot_id=f"{profile.get('organ_system') or 'closed_loop_integration'}:{root_cause_code}".lower(),
                organ_system=str(profile.get("organ_system") or "closed_loop_integration"),
                organ_label=organ_label,
                root_cause_code=root_cause_code,
                suspected_breakpoint=suspected_breakpoint,
                severity=severity,
                request_count=request_count,
                share_pct=share_pct,
                narrative=narrative,
                operator_focus=str(profile.get("operator_focus") or "优先打开 Trace 逐节点定位病灶。"),
                evidence_codes=list(group.get("evidence_codes") or [])[:6],
                representative_requests=representative_requests,
                recommended_view_path=recommended_view_path,
                recommended_view_label=str(profile.get("recommended_view_label") or "打开病灶回放"),
            )
        )

    top_hotspot = hotspots[0]
    summary = (
        f"当前主病灶集中在{top_hotspot.organ_label}，"
        f"最近 {top_hotspot.request_count}/{diagnosed_count} 条代表性异常都围绕 {top_hotspot.root_cause_code} 聚集。"
    )
    return InputHumanoidLoopHotspotsResponse(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
        status=diagnostics_resp.status,
        severity=diagnostics_resp.severity,
        summary=summary,
        diagnosed_count=diagnosed_count,
        hotspot_count=len(hotspots),
        hotspots=hotspots,
        timestamp=now,
    )


class InputHumanoidLoopHotspotTrendSeriesItem(BaseModel):
    organ_system: str
    organ_label: str
    request_counts: List[int]


class InputHumanoidLoopHotspotTrendBucketItem(BaseModel):
    bucket_index: int
    label: str
    sample_requests: int
    abnormal_requests: int
    dominant_organ_system: Optional[str]
    dominant_organ_label: Optional[str]
    dominant_root_cause_code: Optional[str]
    dominant_request_count: int
    dominant_share_pct: Optional[float]


class InputHumanoidLoopHotspotTrendResponse(BaseModel):
    window_minutes: int
    interval_minutes: int
    summary: str
    latest_dominant_organ_system: Optional[str]
    latest_dominant_organ_label: Optional[str]
    latest_dominant_root_cause_code: Optional[str]
    timeseries_labels: List[str]
    timeseries_total_requests: List[int]
    timeseries_abnormal_requests: List[int]
    dominant_buckets: List[InputHumanoidLoopHotspotTrendBucketItem]
    series: List[InputHumanoidLoopHotspotTrendSeriesItem]
    timestamp: int


def _group_rows_by_request_id(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        request_id = str(row.get("request_id") or "").strip()
        if not request_id:
            continue
        grouped.setdefault(request_id, []).append(dict(row))
    return grouped


def _collect_gateway_request_voice_groups(
    rows: List[Dict[str, Any]],
) -> tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Dict[str, Optional[int]]]]:
    request_rows: Dict[str, List[Dict[str, Any]]] = {}
    request_meta: Dict[str, Dict[str, Optional[int]]] = {}

    for row in sorted(
        [dict(item) for item in rows if isinstance(item, dict)],
        key=lambda item: (
            int(item.get("ts_ms") or 0),
            int(item.get("seq") or 0),
            str(item.get("event_type") or ""),
        ),
    ):
        event_type = str(row.get("event_type") or row.get("status") or "").strip().upper()
        if not event_type:
            continue
        payload = _safe_json(row.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        source = _normalize_input_dispatch_source(
            payload_dict.get("source") or row.get("source") or row.get("channel"),
            event_type,
        )
        if source != "GATEWAY":
            continue

        request_id = str(row.get("request_id") or payload_dict.get("request_id") or "").strip()
        if not request_id:
            continue

        request_rows.setdefault(request_id, []).append(row)
        meta = request_meta.setdefault(request_id, {"first_ts": None, "accepted_ts": None})
        ts_val = int(row.get("ts_ms") or 0)
        if ts_val > 0:
            if meta["first_ts"] is None or ts_val < int(meta["first_ts"] or 0):
                meta["first_ts"] = ts_val
            if "ACCEPTED" in event_type and (meta["accepted_ts"] is None or ts_val < int(meta["accepted_ts"] or 0)):
                meta["accepted_ts"] = ts_val
    return request_rows, request_meta


def _classify_humanoid_loop_root_cause_snapshot(
    *,
    flags: Dict[str, Any],
    governor_rows: List[Dict[str, Any]],
    error_rows: List[Dict[str, Any]],
    fallback_reason: str = "",
) -> Dict[str, Any]:
    governor_row = governor_rows[-1] if governor_rows else {}
    governor_decision = str(governor_row.get("decision") or "").strip().upper()
    governor_reason = str(governor_row.get("reason_code") or "").strip().upper() or None
    error_reason_codes = _collect_request_reason_codes(error_rows)
    reason_hint = governor_reason or (error_reason_codes[0] if error_reason_codes else None) or (str(fallback_reason or "").strip() or None)

    evidence_codes: List[str] = []

    def _push_evidence(code: Optional[str]) -> None:
        normalized = str(code or "").strip().upper()
        if normalized and normalized not in evidence_codes:
            evidence_codes.append(normalized)

    if flags.get("failed"):
        _push_evidence("FAILED_RATE_HIGH")
    if not flags.get("ack_touched"):
        _push_evidence("ACK_TOUCH_LOW")
    if not flags.get("progress_touched"):
        _push_evidence("PROGRESS_TOUCH_LOW")
    if flags.get("deviation_detected") and not flags.get("deviation_recovered"):
        _push_evidence("DEVIATION_RECOVERY_LOW")
    if not flags.get("closed_loop"):
        _push_evidence("CLOSED_LOOP_LOW")
    _push_evidence(governor_reason)
    for code in error_reason_codes[:2]:
        _push_evidence(code)

    if governor_decision and governor_decision != "APPROVE":
        suspected_breakpoint = "governor_gate"
        root_cause_code = "GOVERNOR_BLOCKED"
    elif flags.get("failed"):
        suspected_breakpoint = "execution_stage"
        root_cause_code = "EXECUTION_FAILED"
    elif flags.get("deviation_detected") and not flags.get("deviation_recovered"):
        suspected_breakpoint = "deviation_recovery"
        root_cause_code = "DEVIATION_NOT_RECOVERED"
    elif flags.get("completed") and not flags.get("ack_touched") and not flags.get("progress_touched"):
        suspected_breakpoint = "humanoid_touch"
        root_cause_code = "COMPLETED_WITHOUT_HUMANOID_TOUCH"
    elif not flags.get("ack_touched"):
        suspected_breakpoint = "ack_touch_path"
        root_cause_code = "ACK_PATH_MISSING"
    elif not flags.get("progress_touched") and not flags.get("completed"):
        suspected_breakpoint = "progress_touch_path"
        root_cause_code = "PROGRESS_PATH_MISSING"
    elif flags.get("completed") and not flags.get("closed_loop"):
        suspected_breakpoint = "closed_loop_convergence"
        root_cause_code = "CLOSED_LOOP_NOT_CONVERGED"
    else:
        suspected_breakpoint = "trace_review"
        root_cause_code = "TRACE_REVIEW_REQUIRED"

    return {
        "root_cause_code": root_cause_code,
        "suspected_breakpoint": suspected_breakpoint,
        "reason_hint": reason_hint,
        "evidence_codes": evidence_codes[:6],
    }


@router.get("/input/humanoid-loop-hotspot-trend", response_model=InputHumanoidLoopHotspotTrendResponse)
def get_input_humanoid_loop_hotspot_trend(
    window_minutes: int = Query(360, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(30, ge=1, le=360, description="时序聚合间隔（分钟）"),
):
    """Ref #48H: Gateway 人体闭环病灶热区时间演化趋势。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    interval_ms = interval_minutes * 60 * 1000
    bucket_count = max(1, (window_minutes * 60 * 1000) // interval_ms)

    try:
        voice_rows = ledger.fetch_by_time_range(
            "voice_event_log",
            start_ms=since_ms,
            end_ms=now,
            limit=30_000,
        )
    except Exception:
        voice_rows = []
    try:
        governor_rows = ledger.fetch_by_time_range(
            "governor_log",
            start_ms=since_ms,
            end_ms=now,
            limit=30_000,
        )
    except Exception:
        governor_rows = []
    try:
        error_rows = ledger.fetch_by_time_range(
            "error_log",
            start_ms=since_ms,
            end_ms=now,
            limit=30_000,
        )
    except Exception:
        error_rows = []

    request_voice_rows, request_meta = _collect_gateway_request_voice_groups(voice_rows)
    governor_by_request = _group_rows_by_request_id(governor_rows)
    error_by_request = _group_rows_by_request_id(error_rows)

    bucket_samples = [0] * bucket_count
    bucket_abnormal = [0] * bucket_count
    bucket_organ_counts: List[Dict[str, int]] = [dict() for _ in range(bucket_count)]
    bucket_root_counts: List[Dict[tuple[str, str], int]] = [dict() for _ in range(bucket_count)]
    organ_labels: Dict[str, str] = {}

    for request_id, rows in request_voice_rows.items():
        meta = request_meta.get(request_id) or {}
        anchor_ts = meta.get("accepted_ts") or meta.get("first_ts")
        if not anchor_ts:
            continue
        idx = min(int((int(anchor_ts) - since_ms) // interval_ms), bucket_count - 1)
        if idx < 0 or idx >= bucket_count:
            continue

        flags = _build_gateway_request_flags_from_voice_rows(rows)
        bucket_samples[idx] += 1

        if bool(flags.get("closed_loop")) and not bool(flags.get("failed")):
            continue

        snapshot = _classify_humanoid_loop_root_cause_snapshot(
            flags=flags,
            governor_rows=governor_by_request.get(request_id, []),
            error_rows=error_by_request.get(request_id, []),
        )
        root_cause_code = str(snapshot.get("root_cause_code") or "TRACE_REVIEW_REQUIRED")
        suspected_breakpoint = str(snapshot.get("suspected_breakpoint") or "trace_review")
        profile = _resolve_humanoid_loop_hotspot_profile(
            root_cause_code=root_cause_code,
            suspected_breakpoint=suspected_breakpoint,
        )
        organ_system = str(profile.get("organ_system") or "closed_loop_integration")
        organ_label = str(profile.get("organ_label") or "闭环整合系统")
        organ_labels[organ_system] = organ_label

        bucket_abnormal[idx] += 1
        bucket_organ_counts[idx][organ_system] = int(bucket_organ_counts[idx].get(organ_system, 0)) + 1
        root_key = (organ_system, root_cause_code)
        bucket_root_counts[idx][root_key] = int(bucket_root_counts[idx].get(root_key, 0)) + 1

    labels: List[str] = []
    for i in range(bucket_count):
        ts_val = since_ms + i * interval_ms
        d = __import__("datetime").datetime.fromtimestamp(ts_val / 1000)
        if window_minutes <= 60:
            labels.append(d.strftime("%H:%M"))
        elif window_minutes <= 1440:
            labels.append(d.strftime("%m/%d %H:%M"))
        else:
            labels.append(d.strftime("%m/%d"))

    total_organ_counts: Dict[str, int] = {}
    for organ_map in bucket_organ_counts:
        for organ_system, count in organ_map.items():
            total_organ_counts[organ_system] = int(total_organ_counts.get(organ_system, 0)) + int(count or 0)

    top_organ_systems = [
        organ_system
        for organ_system, _ in sorted(
            total_organ_counts.items(),
            key=lambda item: (-int(item[1] or 0), str(item[0])),
        )[:3]
    ]

    series = [
        InputHumanoidLoopHotspotTrendSeriesItem(
            organ_system=organ_system,
            organ_label=organ_labels.get(organ_system, organ_system),
            request_counts=[int(bucket_organ_counts[i].get(organ_system, 0)) for i in range(bucket_count)],
        )
        for organ_system in top_organ_systems
    ]

    dominant_buckets: List[InputHumanoidLoopHotspotTrendBucketItem] = []
    for idx in range(bucket_count):
        dominant_organ_system = None
        dominant_organ_label = None
        dominant_root_cause_code = None
        dominant_request_count = 0
        dominant_share_pct = None

        organ_map = bucket_organ_counts[idx]
        if organ_map:
            dominant_organ_system = sorted(
                organ_map.items(),
                key=lambda item: (-int(item[1] or 0), str(item[0])),
            )[0][0]
            dominant_request_count = int(organ_map.get(dominant_organ_system, 0))
            dominant_organ_label = organ_labels.get(dominant_organ_system, dominant_organ_system)
            root_items = [
                (root_code, count)
                for (organ_system, root_code), count in bucket_root_counts[idx].items()
                if organ_system == dominant_organ_system
            ]
            if root_items:
                dominant_root_cause_code = sorted(
                    root_items,
                    key=lambda item: (-int(item[1] or 0), str(item[0])),
                )[0][0]
            if bucket_abnormal[idx] > 0:
                dominant_share_pct = round(dominant_request_count / bucket_abnormal[idx] * 100, 1)

        dominant_buckets.append(
            InputHumanoidLoopHotspotTrendBucketItem(
                bucket_index=idx,
                label=labels[idx],
                sample_requests=int(bucket_samples[idx]),
                abnormal_requests=int(bucket_abnormal[idx]),
                dominant_organ_system=dominant_organ_system,
                dominant_organ_label=dominant_organ_label,
                dominant_root_cause_code=dominant_root_cause_code,
                dominant_request_count=dominant_request_count,
                dominant_share_pct=dominant_share_pct,
            )
        )

    latest_bucket = next((item for item in reversed(dominant_buckets) if item.abnormal_requests > 0), None)
    if latest_bucket is None:
        summary = "当前窗口尚未形成病灶时间演化趋势。"
    else:
        summary = (
            f"最近异常主病灶落在{latest_bucket.dominant_organ_label or latest_bucket.dominant_organ_system}，"
            f"根因主轴是 {latest_bucket.dominant_root_cause_code or 'UNKNOWN'}，可沿时间线回看病灶升温过程。"
        )

    return InputHumanoidLoopHotspotTrendResponse(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
        summary=summary,
        latest_dominant_organ_system=(latest_bucket.dominant_organ_system if latest_bucket else None),
        latest_dominant_organ_label=(latest_bucket.dominant_organ_label if latest_bucket else None),
        latest_dominant_root_cause_code=(latest_bucket.dominant_root_cause_code if latest_bucket else None),
        timeseries_labels=labels,
        timeseries_total_requests=[int(item) for item in bucket_samples],
        timeseries_abnormal_requests=[int(item) for item in bucket_abnormal],
        dominant_buckets=dominant_buckets,
        series=series,
        timestamp=now,
    )


class InputHumanoidLoopSourceCorrelationItem(BaseModel):
    source_id: str
    source_label: str
    source_kind: str
    confidence_pct: float
    impact_level: str
    source_status: Optional[str] = None
    correlated_hotspots: List[str]
    evidence: List[str]
    narrative: str
    operator_focus: str
    recommended_view_path: str
    recommended_view_label: str


class InputHumanoidLoopSourceCorrelationResponse(BaseModel):
    window_minutes: int
    interval_minutes: int
    status: str
    severity: str
    summary: str
    primary_source_system: Optional[str]
    primary_source_label: Optional[str]
    source_count: int
    items: List[InputHumanoidLoopSourceCorrelationItem]
    timestamp: int


def _build_humanoid_loop_gateway_source_snapshot(
    *,
    ledger: SQLiteLedger,
    since_ms: int,
    now_ms: int,
) -> Dict[str, Any]:
    try:
        voice_rows = ledger.fetch_by_time_range(
            "voice_event_log",
            start_ms=since_ms,
            end_ms=now_ms,
            limit=30_000,
        )
    except Exception:
        voice_rows = []
    try:
        channel_rows = ledger.fetch_by_time_range(
            "channel_io_log",
            start_ms=since_ms,
            end_ms=now_ms,
            limit=10_000,
        )
    except Exception:
        channel_rows = []

    request_voice_rows, _request_meta = _collect_gateway_request_voice_groups(voice_rows)
    total_requests = len(request_voice_rows)
    ack_missing = 0
    progress_missing = 0
    touchless_completed = 0
    failed = 0
    deviation_unrecovered = 0

    for rows in request_voice_rows.values():
        flags = _build_gateway_request_flags_from_voice_rows(rows)
        if not bool(flags.get("ack_touched")):
            ack_missing += 1
        if not bool(flags.get("progress_touched")):
            progress_missing += 1
        if bool(flags.get("completed")) and not bool(flags.get("ack_touched")) and not bool(flags.get("progress_touched")):
            touchless_completed += 1
        if bool(flags.get("failed")):
            failed += 1
        if bool(flags.get("deviation_detected")) and not bool(flags.get("deviation_recovered")):
            deviation_unrecovered += 1

    channel_errors = 0
    for row in channel_rows:
        status_upper = str(row.get("status") or "").strip().upper()
        reason_upper = str(row.get("reason_code") or "").strip().upper()
        if status_upper in {"ERROR", "FAILED", "REJECTED"} or "FAIL" in reason_upper or "ERROR" in reason_upper:
            channel_errors += 1

    if total_requests <= 0 and channel_errors <= 0:
        status = "UNKNOWN"
    elif channel_errors > 0 or touchless_completed > 0 or deviation_unrecovered > 0:
        status = "DEGRADED"
    else:
        status = "UP"

    return {
        "status": status,
        "total_requests": total_requests,
        "ack_missing": ack_missing,
        "progress_missing": progress_missing,
        "touchless_completed": touchless_completed,
        "failed": failed,
        "deviation_unrecovered": deviation_unrecovered,
        "channel_errors": channel_errors,
    }


def _build_humanoid_loop_governor_source_snapshot(
    *,
    window_minutes: int,
    interval_minutes: int,
) -> Dict[str, Any]:
    try:
        resp = get_governor_stats(
            window_minutes=window_minutes,
            interval_minutes=interval_minutes,
        )
    except Exception:
        return {
            "status": "UNKNOWN",
            "rejected": 0,
            "rejection_rate": 0.0,
            "top_reason_codes": [],
        }

    rejected = int(resp.rejected or 0)
    rejection_rate = float(resp.rejection_rate or 0.0)
    if rejected <= 0 and int(resp.total or 0) <= 0:
        status = "UNKNOWN"
    elif rejected > 0:
        status = "DEGRADED"
    else:
        status = "UP"

    return {
        "status": status,
        "rejected": rejected,
        "rejection_rate": rejection_rate,
        "top_reason_codes": [str(item.reason_code or "").strip().upper() for item in list(resp.reason_codes or [])[:3] if str(item.reason_code or "").strip()],
    }


def _build_humanoid_loop_error_source_snapshot(
    *,
    window_minutes: int,
    interval_minutes: int,
) -> Dict[str, Any]:
    try:
        resp = get_errors(
            window_minutes=window_minutes,
            interval_minutes=interval_minutes,
            limit=10,
        )
    except Exception:
        return {
            "status": "UNKNOWN",
            "total_errors": 0,
            "top_stage": None,
            "top_codes": [],
        }

    total_errors = int(resp.total_errors or 0)
    if total_errors <= 0:
        status = "UP" if int(window_minutes or 0) > 0 else "UNKNOWN"
    elif total_errors >= 3:
        status = "DOWN"
    else:
        status = "DEGRADED"

    top_stage = None
    if list(resp.stages or []):
        top_stage = str(resp.stages[0].stage or "").strip().upper() or None

    return {
        "status": status,
        "total_errors": total_errors,
        "top_stage": top_stage,
        "top_codes": [str(item.reason_code or "").strip().upper() for item in list(resp.top_codes or [])[:4] if str(item.reason_code or "").strip()],
    }


def _build_humanoid_loop_subsystem_source_snapshot(
    *,
    window_minutes: int,
) -> Dict[str, Any]:
    try:
        resp = get_subsystems_stats(window_minutes=window_minutes)
    except Exception:
        return {
            "status": "UNKNOWN",
            "problem_count": 0,
            "problem_subsystems": [],
        }

    problematic: List[Dict[str, Any]] = []
    for item in list(resp.subsystems or []):
        failed = int(item.failed or 0)
        degraded = int(item.degraded or 0)
        status = str(item.status or "").strip().upper()
        if failed > 0 or degraded > 0 or status in {"DOWN", "DEGRADED"}:
            problematic.append(
                {
                    "subsystem": str(item.subsystem or "").strip(),
                    "display_name": str(item.display_name or item.subsystem or "").strip(),
                    "status": status or "UNKNOWN",
                    "failed": failed,
                    "degraded": degraded,
                }
            )

    problematic.sort(
        key=lambda item: (
            -int(item.get("failed") or 0),
            -int(item.get("degraded") or 0),
            str(item.get("subsystem") or ""),
        )
    )

    if not problematic and list(resp.subsystems or []):
        status = "UP"
    elif problematic:
        status = "DEGRADED"
    else:
        status = "UNKNOWN"

    return {
        "status": status,
        "problem_count": len(problematic),
        "problem_subsystems": problematic[:4],
    }


def _build_humanoid_loop_services_source_snapshot(
    *,
    ledger: SQLiteLedger,
    since_ms: int,
    now_ms: int,
) -> Dict[str, Any]:
    try:
        llm_rows = ledger.fetch_by_time_range(
            "llm_node_call_log",
            start_ms=since_ms,
            end_ms=now_ms,
            limit=20_000,
        )
    except Exception:
        llm_rows = []

    problem_services: List[Dict[str, Any]] = []
    grouped: Dict[str, Dict[str, Any]] = {}
    success_statuses = {"OK", "SUCCESS", "DONE", "COMPLETED", "PASS", "PASSED"}

    for row in [dict(item) for item in llm_rows if isinstance(item, dict)]:
        node_id = str(row.get("node_id") or "").strip() or "unknown_model_node"
        bucket = grouped.setdefault(
            node_id,
            {
                "service_id": node_id,
                "display_name": node_id,
                "total": 0,
                "failed": 0,
                "max_latency_ms": None,
            },
        )
        bucket["total"] = int(bucket.get("total") or 0) + 1
        status_upper = str(row.get("status") or "UNKNOWN").strip().upper()
        if status_upper not in success_statuses:
            bucket["failed"] = int(bucket.get("failed") or 0) + 1
        latency_ms = row.get("latency_ms")
        try:
            latency_ms = int(latency_ms) if latency_ms is not None else None
        except (TypeError, ValueError):
            latency_ms = None
        if latency_ms is not None:
            current_max = bucket.get("max_latency_ms")
            bucket["max_latency_ms"] = latency_ms if current_max is None else max(int(current_max), latency_ms)

    degraded = 0
    down = 0
    for item in grouped.values():
        failed = int(item.get("failed") or 0)
        total = int(item.get("total") or 0)
        if failed <= 0:
            continue
        status = "DOWN" if failed >= total and total > 0 else "DEGRADED"
        if status == "DOWN":
            down += 1
        else:
            degraded += 1
        problem_services.append(
            {
                "service_id": str(item.get("service_id") or ""),
                "display_name": str(item.get("display_name") or ""),
                "status": status,
                "p95_ms": item.get("max_latency_ms"),
            }
        )

    problem_services.sort(
        key=lambda item: (
            0 if str(item.get("status") or "").upper() == "DOWN" else 1,
            str(item.get("service_id") or ""),
        )
    )

    if down > 0:
        status = "DOWN"
    elif degraded > 0:
        status = "DEGRADED"
    elif grouped:
        status = "UP"
    else:
        status = "UNKNOWN"

    return {
        "status": status,
        "degraded": degraded,
        "down": down,
        "problem_services": problem_services[:4],
    }


def _clamp_humanoid_loop_source_weight(value: float) -> float:
    return max(0.05, min(0.95, float(value or 0.0)))


def _classify_humanoid_loop_source_impact(
    *,
    confidence_pct: float,
    source_status: Optional[str],
    hotspot_codes: List[str],
) -> str:
    status_upper = str(source_status or "").strip().upper()
    hotspot_set = {str(code or "").strip().upper() for code in list(hotspot_codes or []) if str(code or "").strip()}
    if confidence_pct >= 75.0 or status_upper == "DOWN" or hotspot_set.intersection({"EXECUTION_FAILED", "GOVERNOR_BLOCKED", "DEVIATION_NOT_RECOVERED"}):
        return "critical"
    if confidence_pct >= 45.0 or status_upper == "DEGRADED":
        return "warning"
    return "info"


def _build_humanoid_loop_source_meta(source_id: str) -> Dict[str, str]:
    meta = {
        "gateway": {
            "source_label": "Gateway 通道反馈层",
            "source_kind": "gateway",
            "operator_focus": "优先复核 ACK、进度触达与平台消息回执是否真正形成回路。",
            "recommended_view_path": "/gateway",
            "recommended_view_label": "打开感官网关",
        },
        "governor": {
            "source_label": "Governor / 免疫门控层",
            "source_kind": "governor",
            "operator_focus": "优先检查审批令牌、风险规则与门控阈值是否过严或误伤。",
            "recommended_view_path": "/system",
            "recommended_view_label": "打开系统底座",
        },
        "errors": {
            "source_label": "错误执行层",
            "source_kind": "errors",
            "operator_focus": "优先围绕最近错误 stage / reason_code 回放失败链路，确认是技能、工具还是依赖失败。",
            "recommended_view_path": "/alerts",
            "recommended_view_label": "打开免疫告警",
        },
        "subsystems": {
            "source_label": "器官子系统健康层",
            "source_kind": "subsystems",
            "operator_focus": "优先检查 Skill、Sandbox、Agent Loop 等器官系统是否出现退化或失败尖峰。",
            "recommended_view_path": "/system",
            "recommended_view_label": "打开系统底座",
        },
        "services": {
            "source_label": "依赖服务健康层",
            "source_kind": "services",
            "operator_focus": "优先检查模型、边缘服务与基础依赖的降级/不可用情况，确认是否拖垮执行闭环。",
            "recommended_view_path": "/system",
            "recommended_view_label": "打开服务健康",
        },
    }
    return dict(meta.get(source_id, meta["errors"]))


def _append_humanoid_loop_source_contribution(
    *,
    grouped: Dict[str, Dict[str, Any]],
    diagnosed_count: int,
    source_id: str,
    hotspot: InputHumanoidLoopHotspotItem,
    weight: float,
    source_status: Optional[str],
    evidence: List[str],
    narrative: str,
    override_view_path: Optional[str] = None,
    override_view_label: Optional[str] = None,
) -> None:
    meta = _build_humanoid_loop_source_meta(source_id)
    bucket = grouped.setdefault(
        source_id,
        {
            "source_id": source_id,
            "source_label": meta["source_label"],
            "source_kind": meta["source_kind"],
            "source_status": source_status,
            "score": 0.0,
            "correlated_hotspots": [],
            "evidence": [],
            "narrative": "",
            "operator_focus": meta["operator_focus"],
            "recommended_view_path": override_view_path or meta["recommended_view_path"],
            "recommended_view_label": override_view_label or meta["recommended_view_label"],
        },
    )

    weighted_score = _clamp_humanoid_loop_source_weight(weight) * max(1, int(hotspot.request_count or 0))
    bucket["score"] = float(bucket.get("score") or 0.0) + weighted_score
    if bucket.get("source_status") in {None, "", "UNKNOWN"} and source_status:
        bucket["source_status"] = source_status

    root_cause_code = str(hotspot.root_cause_code or "").strip().upper()
    if root_cause_code and root_cause_code not in bucket["correlated_hotspots"]:
        bucket["correlated_hotspots"].append(root_cause_code)

    for item in list(evidence or []):
        normalized = str(item or "").strip()
        if normalized and normalized not in bucket["evidence"]:
            bucket["evidence"].append(normalized)
        if len(bucket["evidence"]) >= 8:
            break

    current_narrative = str(bucket.get("narrative") or "").strip()
    if not current_narrative or weighted_score >= float(bucket.get("narrative_score") or 0.0):
        bucket["narrative"] = narrative
        bucket["narrative_score"] = weighted_score
    if override_view_path:
        bucket["recommended_view_path"] = override_view_path
    if override_view_label:
        bucket["recommended_view_label"] = override_view_label

    confidence_pct = round(float(bucket.get("score") or 0.0) / max(1, diagnosed_count) * 100, 1)
    bucket["confidence_pct"] = min(99.0, confidence_pct)
    bucket["impact_level"] = _classify_humanoid_loop_source_impact(
        confidence_pct=float(bucket.get("confidence_pct") or 0.0),
        source_status=str(bucket.get("source_status") or ""),
        hotspot_codes=list(bucket.get("correlated_hotspots") or []),
    )


@router.get("/input/humanoid-loop-source-correlation", response_model=InputHumanoidLoopSourceCorrelationResponse)
async def get_input_humanoid_loop_source_correlation(
    window_minutes: int = Query(360, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(30, ge=1, le=360, description="时序聚合间隔（分钟）"),
    min_bucket_requests: int = Query(3, ge=1, le=100, description="告警评估最小样本量"),
):
    """Ref #48I: Gateway 人体闭环病灶源头归因。"""
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    hotspots_resp = get_input_humanoid_loop_hotspots(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
        min_bucket_requests=min_bucket_requests,
    )

    if not hotspots_resp.hotspots:
        return InputHumanoidLoopSourceCorrelationResponse(
            window_minutes=window_minutes,
            interval_minutes=interval_minutes,
            status=hotspots_resp.status,
            severity=hotspots_resp.severity,
            summary="当前窗口尚未形成可归因的人体闭环病灶源头。",
            primary_source_system=None,
            primary_source_label=None,
            source_count=0,
            items=[],
            timestamp=now,
        )

    ledger = _get_ledger()
    gateway_snapshot = _build_humanoid_loop_gateway_source_snapshot(
        ledger=ledger,
        since_ms=since_ms,
        now_ms=now,
    )
    governor_snapshot = _build_humanoid_loop_governor_source_snapshot(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
    )
    error_snapshot = _build_humanoid_loop_error_source_snapshot(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
    )
    subsystem_snapshot = _build_humanoid_loop_subsystem_source_snapshot(window_minutes=window_minutes)
    services_snapshot = _build_humanoid_loop_services_source_snapshot(
        ledger=ledger,
        since_ms=since_ms,
        now_ms=now,
    )

    grouped: Dict[str, Dict[str, Any]] = {}
    diagnosed_count = max(1, int(hotspots_resp.diagnosed_count or 0))

    for hotspot in list(hotspots_resp.hotspots or []):
        root_cause_code = str(hotspot.root_cause_code or "").strip().upper()
        hotspot_evidence = [str(code or "").strip().upper() for code in list(hotspot.evidence_codes or [])[:4] if str(code or "").strip()]
        representative_path = None
        representative_label = None
        if list(hotspot.representative_requests or []):
            representative_path = str(hotspot.representative_requests[0].target_path or "").strip() or None
            representative_label = "打开代表性链路"

        if root_cause_code == "EXECUTION_FAILED":
            error_weight = 0.72
            error_evidence = [f"hotspot={root_cause_code}", f"requests={int(hotspot.request_count or 0)}"]
            if int(error_snapshot.get("total_errors") or 0) > 0:
                error_weight += 0.1
                error_evidence.append(f"errors={int(error_snapshot.get('total_errors') or 0)}")
            if error_snapshot.get("top_stage"):
                if str(error_snapshot.get("top_stage")).startswith("S_EXEC"):
                    error_weight += 0.08
                error_evidence.append(f"stage={error_snapshot.get('top_stage')}")
            if list(error_snapshot.get("top_codes") or []):
                error_weight += 0.05
                error_evidence.append(f"reason={list(error_snapshot.get('top_codes') or [])[0]}")
            error_evidence.extend(hotspot_evidence[:2])
            _append_humanoid_loop_source_contribution(
                grouped=grouped,
                diagnosed_count=diagnosed_count,
                source_id="errors",
                hotspot=hotspot,
                weight=error_weight,
                source_status=str(error_snapshot.get("status") or ""),
                evidence=error_evidence,
                narrative=(
                    f"{hotspot.organ_label} 的失败病灶与错误执行层高度重合，最近异常主要集中在 "
                    f"{error_snapshot.get('top_stage') or 'UNKNOWN'} / "
                    f"{(list(error_snapshot.get('top_codes') or []) or ['UNKNOWN'])[0]}。"
                ),
                override_view_path=representative_path or "/alerts",
                override_view_label=representative_label or "打开失败链路",
            )

            subsystem_weight = 0.56
            subsystem_evidence = [f"hotspot={root_cause_code}"]
            if list(subsystem_snapshot.get("problem_subsystems") or []):
                subsystem_weight += 0.12
                top_subsystem = list(subsystem_snapshot.get("problem_subsystems") or [])[0]
                subsystem_evidence.append(f"subsystem={top_subsystem.get('subsystem')}")
                subsystem_evidence.append(f"status={top_subsystem.get('status')}")
            _append_humanoid_loop_source_contribution(
                grouped=grouped,
                diagnosed_count=diagnosed_count,
                source_id="subsystems",
                hotspot=hotspot,
                weight=subsystem_weight,
                source_status=str(subsystem_snapshot.get("status") or ""),
                evidence=subsystem_evidence,
                narrative="执行器官链路出现失败尖峰时，通常会同时在 Skill / Sandbox / Agent Loop 子系统上留下退化痕迹。",
            )

            if int(services_snapshot.get("down") or 0) > 0 or int(services_snapshot.get("degraded") or 0) > 0:
                service_weight = 0.44
                service_evidence = [f"hotspot={root_cause_code}"]
                if int(services_snapshot.get("down") or 0) > 0:
                    service_weight += 0.12
                    service_evidence.append(f"services_down={int(services_snapshot.get('down') or 0)}")
                elif int(services_snapshot.get("degraded") or 0) > 0:
                    service_weight += 0.07
                    service_evidence.append(f"services_degraded={int(services_snapshot.get('degraded') or 0)}")
                if list(services_snapshot.get("problem_services") or []):
                    top_service = list(services_snapshot.get("problem_services") or [])[0]
                    service_evidence.append(f"service={top_service.get('service_id')}")
                _append_humanoid_loop_source_contribution(
                    grouped=grouped,
                    diagnosed_count=diagnosed_count,
                    source_id="services",
                    hotspot=hotspot,
                    weight=service_weight,
                    source_status=str(services_snapshot.get("status") or ""),
                    evidence=service_evidence,
                    narrative="最近执行失败与下游依赖降级存在伴随关系，需要同步复核模型与基础服务健康。",
                )

        elif root_cause_code == "GOVERNOR_BLOCKED":
            governor_weight = 0.84
            governor_evidence = [f"hotspot={root_cause_code}", f"rejected={int(governor_snapshot.get('rejected') or 0)}"]
            if float(governor_snapshot.get("rejection_rate") or 0.0) > 0:
                governor_weight += 0.06
                governor_evidence.append(f"rejection_rate={round(float(governor_snapshot.get('rejection_rate') or 0.0), 1)}%")
            if list(governor_snapshot.get("top_reason_codes") or []):
                governor_weight += 0.05
                governor_evidence.append(f"reason={list(governor_snapshot.get('top_reason_codes') or [])[0]}")
            governor_evidence.extend(hotspot_evidence[:2])
            _append_humanoid_loop_source_contribution(
                grouped=grouped,
                diagnosed_count=diagnosed_count,
                source_id="governor",
                hotspot=hotspot,
                weight=governor_weight,
                source_status=str(governor_snapshot.get("status") or ""),
                evidence=governor_evidence,
                narrative="病灶集中在门控前额叶，说明请求尚未进入真正执行阶段就被审批或免疫规则拦下。",
            )

        elif root_cause_code in {"ACK_PATH_MISSING", "PROGRESS_PATH_MISSING", "COMPLETED_WITHOUT_HUMANOID_TOUCH"}:
            gateway_weight = 0.78
            gateway_evidence = [f"hotspot={root_cause_code}"]
            if int(gateway_snapshot.get("touchless_completed") or 0) > 0:
                gateway_weight += 0.08
                gateway_evidence.append(f"touchless={int(gateway_snapshot.get('touchless_completed') or 0)}")
            if int(gateway_snapshot.get("ack_missing") or 0) > 0:
                gateway_weight += 0.06
                gateway_evidence.append(f"ack_missing={int(gateway_snapshot.get('ack_missing') or 0)}")
            if int(gateway_snapshot.get("progress_missing") or 0) > 0:
                gateway_weight += 0.04
                gateway_evidence.append(f"progress_missing={int(gateway_snapshot.get('progress_missing') or 0)}")
            if int(gateway_snapshot.get("channel_errors") or 0) > 0:
                gateway_weight += 0.04
                gateway_evidence.append(f"channel_errors={int(gateway_snapshot.get('channel_errors') or 0)}")
            gateway_evidence.extend(hotspot_evidence[:2])
            _append_humanoid_loop_source_contribution(
                grouped=grouped,
                diagnosed_count=diagnosed_count,
                source_id="gateway",
                hotspot=hotspot,
                weight=gateway_weight,
                source_status=str(gateway_snapshot.get("status") or ""),
                evidence=gateway_evidence,
                narrative="语言回执或进度触达缺口通常发生在 Gateway 通道反馈层，表现为动作完成但没有像人一样反馈过程。",
            )

        elif root_cause_code == "DEVIATION_NOT_RECOVERED":
            gateway_evidence = [
                f"hotspot={root_cause_code}",
                f"deviation_unrecovered={int(gateway_snapshot.get('deviation_unrecovered') or 0)}",
            ]
            _append_humanoid_loop_source_contribution(
                grouped=grouped,
                diagnosed_count=diagnosed_count,
                source_id="gateway",
                hotspot=hotspot,
                weight=0.68 + (0.08 if int(gateway_snapshot.get("deviation_unrecovered") or 0) > 0 else 0.0),
                source_status=str(gateway_snapshot.get("status") or ""),
                evidence=gateway_evidence,
                narrative="偏航已被感知但没有被拉回，优先怀疑反馈-重试-恢复链条在 Gateway 侧没有真正闭合。",
            )
            _append_humanoid_loop_source_contribution(
                grouped=grouped,
                diagnosed_count=diagnosed_count,
                source_id="errors",
                hotspot=hotspot,
                weight=0.5 + (0.08 if int(error_snapshot.get("total_errors") or 0) > 0 else 0.0),
                source_status=str(error_snapshot.get("status") or ""),
                evidence=[f"hotspot={root_cause_code}", f"errors={int(error_snapshot.get('total_errors') or 0)}"] + hotspot_evidence[:2],
                narrative="若恢复链路伴随错误日志升高，说明自愈动作本身在执行期也遭遇了失败或超时。",
                override_view_path=representative_path or "/alerts",
                override_view_label=representative_label or "打开恢复链路",
            )
            _append_humanoid_loop_source_contribution(
                grouped=grouped,
                diagnosed_count=diagnosed_count,
                source_id="subsystems",
                hotspot=hotspot,
                weight=0.46 + (0.08 if int(subsystem_snapshot.get("problem_count") or 0) > 0 else 0.0),
                source_status=str(subsystem_snapshot.get("status") or ""),
                evidence=[f"hotspot={root_cause_code}", f"subsystems={int(subsystem_snapshot.get('problem_count') or 0)}"],
                narrative="全局纠偏没有收敛时，往往也意味着下层器官系统没有把恢复动作执行到底。",
            )

        elif root_cause_code == "CLOSED_LOOP_NOT_CONVERGED":
            _append_humanoid_loop_source_contribution(
                grouped=grouped,
                diagnosed_count=diagnosed_count,
                source_id="gateway",
                hotspot=hotspot,
                weight=0.62 + (0.06 if int(gateway_snapshot.get("ack_missing") or 0) > 0 else 0.0),
                source_status=str(gateway_snapshot.get("status") or ""),
                evidence=[
                    f"hotspot={root_cause_code}",
                    f"ack_missing={int(gateway_snapshot.get('ack_missing') or 0)}",
                    f"progress_missing={int(gateway_snapshot.get('progress_missing') or 0)}",
                ],
                narrative="闭环没有收束，通常不是单点故障，而是反馈链路多处轻微断裂叠加造成的整体不收敛。",
            )
            _append_humanoid_loop_source_contribution(
                grouped=grouped,
                diagnosed_count=diagnosed_count,
                source_id="subsystems",
                hotspot=hotspot,
                weight=0.42 + (0.06 if int(subsystem_snapshot.get("problem_count") or 0) > 0 else 0.0),
                source_status=str(subsystem_snapshot.get("status") or ""),
                evidence=[f"hotspot={root_cause_code}", f"subsystems={int(subsystem_snapshot.get('problem_count') or 0)}"],
                narrative="如果多个器官系统都轻度退化，也会让人体闭环看起来像是完成了动作，却始终没有完整回路。",
            )

        else:
            _append_humanoid_loop_source_contribution(
                grouped=grouped,
                diagnosed_count=diagnosed_count,
                source_id="errors",
                hotspot=hotspot,
                weight=0.36 + (0.06 if int(error_snapshot.get("total_errors") or 0) > 0 else 0.0),
                source_status=str(error_snapshot.get("status") or ""),
                evidence=[f"hotspot={root_cause_code}"] + hotspot_evidence[:2],
                narrative="当前病灶还没有稳定模板，优先从错误执行层与代表性链路回放收束真正源头。",
                override_view_path=representative_path or "/alerts",
                override_view_label=representative_label or "打开代表性链路",
            )
            _append_humanoid_loop_source_contribution(
                grouped=grouped,
                diagnosed_count=diagnosed_count,
                source_id="gateway",
                hotspot=hotspot,
                weight=0.32 + (0.04 if int(gateway_snapshot.get("channel_errors") or 0) > 0 else 0.0),
                source_status=str(gateway_snapshot.get("status") or ""),
                evidence=[f"hotspot={root_cause_code}", f"channel_errors={int(gateway_snapshot.get('channel_errors') or 0)}"],
                narrative="在没有稳定模式前，也要同时排除 Gateway 反馈层是否存在吞回执、断进度或通道抖动。",
            )

    items: List[InputHumanoidLoopSourceCorrelationItem] = []
    for item in sorted(
        grouped.values(),
        key=lambda value: (
            -float(value.get("confidence_pct") or 0.0),
            str(value.get("source_id") or ""),
        ),
    ):
        items.append(
            InputHumanoidLoopSourceCorrelationItem(
                source_id=str(item.get("source_id") or ""),
                source_label=str(item.get("source_label") or ""),
                source_kind=str(item.get("source_kind") or ""),
                confidence_pct=float(item.get("confidence_pct") or 0.0),
                impact_level=str(item.get("impact_level") or "info"),
                source_status=(str(item.get("source_status") or "").strip() or None),
                correlated_hotspots=list(item.get("correlated_hotspots") or [])[:4],
                evidence=list(item.get("evidence") or [])[:8],
                narrative=str(item.get("narrative") or ""),
                operator_focus=str(item.get("operator_focus") or ""),
                recommended_view_path=str(item.get("recommended_view_path") or "/alerts"),
                recommended_view_label=str(item.get("recommended_view_label") or "打开源头归因"),
            )
        )

    primary = items[0] if items else None
    if primary is None:
        summary = "当前窗口已识别病灶，但还没有形成稳定的源头归因结果。"
    else:
        summary = (
            f"当前主病灶最可能回落到 {primary.source_label}，"
            f"它与 {', '.join(primary.correlated_hotspots[:2]) or '当前异常'} 的关联度最高，"
            f"建议先沿 {primary.recommended_view_label} 收束根因。"
        )

    return InputHumanoidLoopSourceCorrelationResponse(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
        status=hotspots_resp.status,
        severity=hotspots_resp.severity,
        summary=summary,
        primary_source_system=(primary.source_id if primary else None),
        primary_source_label=(primary.source_label if primary else None),
        source_count=len(items),
        items=items,
        timestamp=now,
    )


class RecallPackSlotItem(BaseModel):
    slot: str
    avg_tokens: float
    truncation_rate: Optional[float]
    conflict_suppressed_count: int

class RecallPackStatsResponse(BaseModel):
    window_minutes: int
    total_packs: int
    avg_total_tokens: Optional[float]
    truncation_rate: Optional[float]
    conflict_rate: Optional[float]
    slots: List[RecallPackSlotItem]
    timestamp: int

@router.get("/recall-pack/stats", response_model=RecallPackStatsResponse)
def get_recall_pack_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #49: RecallPack token 预算统计 — 各槽位平均 token 消耗/截断率/冲突抑制率。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # Read recall-pack traces from audit/error storage.
    try:
        rows = ledger.fetch_by_time_range("error_log", start_ms=since_ms, end_ms=now)
        pack_rows = [
            r for r in rows
            if str(r.get("stage") or "").upper() in ("RECALL_PACK_TRACE", "RECALL_FUSION")
            and r.get("detail")
        ]
    except Exception:
        pack_rows = []

    # Read from the dedicated recall-pack log table when present.
    if not pack_rows:
        try:
            pack_rows = ledger.fetch_by_time_range("recall_pack_log", start_ms=since_ms, end_ms=now)
        except Exception:
            pack_rows = []

    total_packs = len(pack_rows)
    if total_packs == 0:
        return RecallPackStatsResponse(
            window_minutes=window_minutes,
            total_packs=0,
            avg_total_tokens=None,
            truncation_rate=None,
            conflict_rate=None,
            slots=[],
            timestamp=now,
        )

    # 解析 detail JSON，聚合各槽位统计
    from collections import defaultdict
    slot_tokens: dict = defaultdict(list)
    slot_truncated: dict = defaultdict(int)
    slot_conflict: dict = defaultdict(int)
    total_tokens_list: list = []
    conflict_pack_count = 0

    for r in pack_rows:
        try:
            detail_raw = r.get("detail") or "{}"
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
            trace = detail.get("trace") or []
            total_tokens = int(detail.get("total_tokens") or 0)
            if total_tokens > 0:
                total_tokens_list.append(total_tokens)
            conflict_events = int(detail.get("conflict_events") or 0)
            if conflict_events > 0:
                conflict_pack_count += 1
            for slot_info in trace:
                slot_name = str(slot_info.get("slot") or "unknown")
                actual_tokens = int(slot_info.get("actual_tokens") or 0)
                slot_tokens[slot_name].append(actual_tokens)
                if slot_info.get("truncated"):
                    slot_truncated[slot_name] += 1
                if slot_info.get("conflict_suppressed"):
                    slot_conflict[slot_name] += 1
        except Exception:
            continue

    avg_total_tokens = round(sum(total_tokens_list) / len(total_tokens_list), 1) if total_tokens_list else None
    truncation_rate = None
    if slot_tokens:
        total_slots = sum(len(v) for v in slot_tokens.values())
        total_truncated = sum(slot_truncated.values())
        truncation_rate = round(total_truncated / total_slots * 100, 1) if total_slots > 0 else None
    conflict_rate = round(conflict_pack_count / total_packs * 100, 1) if total_packs > 0 else None

    slots = [
        RecallPackSlotItem(
            slot=slot_name,
            avg_tokens=round(sum(tokens) / len(tokens), 1) if tokens else 0.0,
            truncation_rate=round(slot_truncated.get(slot_name, 0) / len(tokens) * 100, 1) if tokens else None,
            conflict_suppressed_count=slot_conflict.get(slot_name, 0),
        )
        for slot_name, tokens in sorted(slot_tokens.items())
    ]

    return RecallPackStatsResponse(
        window_minutes=window_minutes,
        total_packs=total_packs,
        avg_total_tokens=avg_total_tokens,
        truncation_rate=truncation_rate,
        conflict_rate=conflict_rate,
        slots=slots,
        timestamp=now,
    )


