"""obs_routes_stats_governor.py — 可观测性统计端点（从 obs_routes_stats.py 拆分）。

子模块：Governor决策统计、全局错误统计、子系统健康统计
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

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.observability.projection_ledger_view import get_projection_ledger_view

logger = logging.getLogger("Natsume.obs")


from .obs_routes_core import (
    _build_trace_summary,
    router,
    _extract_input_dispatch_latency_ms,
    _fetch_request_rows,
    _is_input_dispatch_event,
    _normalize_input_dispatch_source,
    _now_ms,
    _safe_json,
)


# ---------------------------------------------------------------------------
# P1: GET /obs/governor/stats  — Governor 决策漏斗 + reason_code 分布
# ---------------------------------------------------------------------------
class GovernorReasonItem(BaseModel):
    reason_code: str
    count: int
    pct: float

class GovernorStatsResponse(BaseModel):
    window_minutes: int
    total: int
    approved: int
    rejected: int
    rejection_rate: float
    reason_codes: List[GovernorReasonItem]
    timeseries_labels: List[str]
    timeseries_approved: List[int]
    timeseries_rejected: List[int]
    timestamp: int

@router.get("/governor/stats", response_model=GovernorStatsResponse)
def get_governor_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=60, description="时序聚合间隔（分钟）"),
):
    """P1: Governor 决策漏斗 — 批准/拒绝数量、拒绝率、reason_code 分布、时序趋势。"""
    ledger = get_projection_ledger_view()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("governor_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    total = len(rows)
    approved = sum(1 for r in rows if str(r.get("decision", "")).upper() == "APPROVE")
    rejected = total - approved
    rejection_rate = round(rejected / total * 100, 1) if total > 0 else 0.0

    # reason_code 分布（仅拒绝行）
    from collections import Counter
    rej_rows = [r for r in rows if str(r.get("decision", "")).upper() != "APPROVE"]
    rc_counter = Counter(r.get("reason_code") or "UNKNOWN" for r in rej_rows)
    reason_codes = [
        GovernorReasonItem(
            reason_code=code,
            count=cnt,
            pct=round(cnt / rejected * 100, 1) if rejected > 0 else 0.0,
        )
        for code, cnt in rc_counter.most_common(10)
    ]

    # 时序聚合
    interval_ms = interval_minutes * 60 * 1000
    bucket_count = max(1, (window_minutes * 60 * 1000) // interval_ms)
    ts_approved = [0] * bucket_count
    ts_rejected = [0] * bucket_count
    for r in rows:
        ts = r.get("ts_ms") or 0
        idx = min(int((ts - since_ms) // interval_ms), bucket_count - 1)
        if 0 <= idx < bucket_count:
            if str(r.get("decision", "")).upper() == "APPROVE":
                ts_approved[idx] += 1
            else:
                ts_rejected[idx] += 1

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

    return GovernorStatsResponse(
        window_minutes=window_minutes,
        total=total,
        approved=approved,
        rejected=rejected,
        rejection_rate=rejection_rate,
        reason_codes=reason_codes,
        timeseries_labels=labels,
        timeseries_approved=ts_approved,
        timeseries_rejected=ts_rejected,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# P1: GET /obs/errors  — 全局错误日志统计
# ---------------------------------------------------------------------------
class ErrorStageItem(BaseModel):
    stage: str
    count: int
    pct: float

class ErrorCodeItem(BaseModel):
    reason_code: str
    count: int

class ErrorsResponse(BaseModel):
    window_minutes: int
    total_errors: int
    stages: List[ErrorStageItem]
    top_codes: List[ErrorCodeItem]
    timeseries_labels: List[str]
    timeseries_counts: List[int]
    recent: List[Dict[str, Any]]
    timestamp: int

@router.get("/errors", response_model=ErrorsResponse)
def get_errors(
    window_minutes: int = Query(60, ge=1, le=10080),
    interval_minutes: int = Query(5, ge=1, le=60),
    limit: int = Query(20, ge=1, le=100, description="最近错误条数"),
):
    """P1: 全局错误日志统计 — 错误总量、stage 分布、reason_code Top10、时序趋势、最近错误列表。"""
    ledger = get_projection_ledger_view()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("error_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    total = len(rows)

    from collections import Counter
    stage_counter = Counter(r.get("stage") or "UNKNOWN" for r in rows)
    stages = [
        ErrorStageItem(
            stage=s,
            count=c,
            pct=round(c / total * 100, 1) if total > 0 else 0.0,
        )
        for s, c in stage_counter.most_common(10)
    ]

    code_counter = Counter(r.get("reason_code") or "UNKNOWN" for r in rows)
    top_codes = [
        ErrorCodeItem(reason_code=code, count=cnt)
        for code, cnt in code_counter.most_common(10)
    ]

    # 时序聚合
    interval_ms = interval_minutes * 60 * 1000
    bucket_count = max(1, (window_minutes * 60 * 1000) // interval_ms)
    ts_counts = [0] * bucket_count
    for r in rows:
        ts = r.get("ts_ms") or 0
        idx = min(int((ts - since_ms) // interval_ms), bucket_count - 1)
        if 0 <= idx < bucket_count:
            ts_counts[idx] += 1

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

    # 最近 N 条错误（倒序）
    recent_rows = sorted(rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[:limit]
    recent = [
        {
            "request_id": r.get("request_id"),
            "stage": r.get("stage"),
            "reason_code": r.get("reason_code"),
            "detail": (r.get("detail") or "")[:200],
            "ts_ms": r.get("ts_ms"),
        }
        for r in recent_rows
    ]

    return ErrorsResponse(
        window_minutes=window_minutes,
        total_errors=total,
        stages=stages,
        top_codes=top_codes,
        timeseries_labels=labels,
        timeseries_counts=ts_counts,
        recent=recent,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# P2/P3: GET /obs/subsystems/stats  — 子系统健康统计（Memory/Retrieval/Skill/Agent Loop/Airlock/Bond）
# ---------------------------------------------------------------------------
class SubsystemStatItem(BaseModel):
    subsystem: str
    display_name: str
    total: int
    success: int
    degraded: int = 0
    failed: int
    success_rate: Optional[float]
    p95_ms: Optional[int]
    last_ts: Optional[int]
    status: str = "UNKNOWN"
    detail: Optional[str] = None
    blocked: Optional[int] = None

class SubsystemsStatsResponse(BaseModel):
    window_minutes: int
    subsystems: List[SubsystemStatItem]
    timestamp: int

def _classify_subsystem_row_status(value: Any) -> str:
    if value is None:
        return "neutral"
    if isinstance(value, bool):
        return "success" if value else "failed"
    raw = str(value).strip().upper()
    if raw in ("", "NONE", "NULL"):
        return "neutral"
    if raw in ("OK", "SUCCESS", "DONE", "APPROVED", "PASS", "PASSED", "LENIENT_OK", "STRICT_OK", "COMPLETED"):
        return "success"
    if raw in ("DEGRADED", "WARN", "WARNING", "PARTIAL", "FALLBACK", "SKIPPED"):
        return "degraded"
    if raw in ("FAIL", "FAILED", "ERROR", "DOWN", "REJECTED", "BLOCKED", "TIMEOUT", "DENIED"):
        return "failed"
    return "neutral"


def _derive_subsystem_status(total: int, success: int, degraded: int, failed: int) -> str:
    if total <= 0:
        return "UNKNOWN"
    if success == 0 and degraded == 0 and failed == 0:
        return "UNKNOWN"
    if failed > 0 and success == 0 and degraded == 0:
        return "DOWN"
    if failed > 0 or degraded > 0:
        return "DEGRADED"
    return "UP"


def _intent_lint_is_clean(value: Any) -> bool:
    if value is None:
        return True
    parsed = _safe_json(value)
    if isinstance(parsed, str):
        raw = parsed.strip()
        return raw == "" or raw.upper() in ("OK", "PASS", "SUCCESS", "CLEAN")
    if isinstance(parsed, list):
        return len(parsed) == 0
    if isinstance(parsed, dict):
        for item in parsed.values():
            normalized = _safe_json(item)
            if isinstance(normalized, list) and normalized:
                return False
            if isinstance(normalized, dict) and any(bool(v) for v in normalized.values()):
                return False
            if isinstance(normalized, str) and normalized.strip():
                return False
            if isinstance(normalized, (int, float)) and normalized != 0:
                return False
            if isinstance(normalized, bool) and normalized:
                return False
        return True
    return not bool(parsed)


def _intent_row_passed(row: Dict[str, Any]) -> bool:
    parse_stage = str(row.get("parse_stage") or "").strip().upper()
    parse_ok = not parse_stage or parse_stage in ("OK", "PASS", "SUCCESS") or parse_stage.endswith("_OK")
    return parse_ok and _intent_lint_is_clean(row.get("lint_result"))


def _compute_subsystem_health(rows: List[Dict], status_field: str = "status", latency_field: Optional[str] = None) -> Dict[str, Any]:
    """通用子系统健康计算：成功率、P95延迟、最后活跃时间。"""
    total = len(rows)
    if total == 0:
        return {
            "total": 0,
            "success": 0,
            "degraded": 0,
            "failed": 0,
            "success_rate": None,
            "p95_ms": None,
            "last_ts": None,
            "status": "UNKNOWN",
            "detail": "近窗口无调用",
            "blocked": None,
        }

    success = 0
    degraded = 0
    failed = 0
    for row in rows:
        bucket = _classify_subsystem_row_status(row.get(status_field))
        if bucket == "success":
            success += 1
        elif bucket == "degraded":
            degraded += 1
        elif bucket == "failed":
            failed += 1

    measured_total = success + degraded + failed
    success_rate = round(success / measured_total * 100, 1) if measured_total > 0 else None

    p95_ms = None
    if latency_field:
        latencies = [r[latency_field] for r in rows if r.get(latency_field) is not None]
        if latencies:
            latencies.sort()
            idx = max(0, int(len(latencies) * 0.95) - 1)
            p95_ms = latencies[idx]

    last_ts = max((r.get("ts_ms") or 0 for r in rows), default=None)
    return {
        "total": total,
        "success": success,
        "degraded": degraded,
        "failed": failed,
        "success_rate": success_rate,
        "p95_ms": p95_ms,
        "last_ts": last_ts,
        "status": _derive_subsystem_status(total, success, degraded, failed),
        "detail": None,
        "blocked": None,
    }

@router.get("/subsystems/stats", response_model=SubsystemsStatsResponse)
def get_subsystems_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
):
    """P2/P3: 子系统健康统计 — Memory/Retrieval/Skill/Agent Loop/Airlock/Bond/Vision/Sandbox/Channel。"""
    ledger = get_projection_ledger_view()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # 定义各子系统：(subsystem_id, display_name, table, status_field, latency_field)
    SUBSYSTEM_DEFS = [
        ("memory",    "Memory 操作",    "memory_ops_log",     "status",        None),
        ("retrieval", "向量检索",        "retrieval_event_log","status",        None),
        ("skill",     "Skill 执行",     "skill_exec_log",     "result_status", None),
        ("agent_loop","Agent Loop",     "agent_loop_log",     "status",        "latency_ms"),
        ("airlock",   "Airlock 过滤",   "airlock_log",        "blocked",       None),
        ("bond",      "Bond 关系更新",  "bond_update_log",    "status",        None),
        ("vision",    "Vision 视觉",    "vision_event_log",   "status",        None),
        ("sandbox",   "Sandbox 执行",   "sandbox_exec_log",   "status",        "duration_ms"),
        ("channel",   "Channel I/O",   "channel_io_log",     "status",        None),
        ("intent",    "Intent 解析",    "intent_log",         "lint_result",   None),
        ("qa",        "QA 违规检查",    "qa_violation_log",   None,            None),
    ]

    subsystems = []
    for sid, dname, table, status_field, latency_field in SUBSYSTEM_DEFS:
        try:
            rows = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
        except Exception:
            rows = []

        if sid == "airlock":
            # airlock: blocked=1 表示策略命中，但不代表子系统故障
            total = len(rows)
            blocked = sum(1 for r in rows if r.get("blocked") in (1, True, "1"))
            last_ts = max((r.get("ts_ms") or 0 for r in rows), default=None)
            h = {
                "total": total,
                "success": total,
                "degraded": 0,
                "failed": 0,
                "success_rate": 100.0 if total > 0 else None,
                "p95_ms": None,
                "last_ts": last_ts,
                "status": "UP" if total > 0 else "UNKNOWN",
                "detail": (
                    f"策略拦截 {blocked} 次"
                    if blocked > 0 else ("最近无策略拦截" if total > 0 else "近窗口无调用")
                ),
                "blocked": blocked,
            }
        elif sid == "qa":
            # qa_violation_log: 每行都是一次违规，代表内容质量告警，不代表 QA 子系统自身故障
            total = len(rows)
            h = {
                "total": total,
                "success": 0,
                "degraded": total,
                "failed": 0,
                "success_rate": 0.0 if total > 0 else None,
                "p95_ms": None,
                "last_ts": max((r.get("ts_ms") or 0 for r in rows), default=None),
                "status": "DEGRADED" if total > 0 else "UNKNOWN",
                "detail": f"检测到 {total} 条 QA 违规" if total > 0 else "近窗口无调用",
                "blocked": None,
            }
        elif sid == "intent":
            # intent_log: lint_result 可能是 {"args": [], "schema": []} 这类 JSON，空数组视为 lint 通过
            total = len(rows)
            success = sum(1 for r in rows if _intent_row_passed(r))
            failed = total - success
            last_ts = max((r.get("ts_ms") or 0 for r in rows), default=None)
            h = {
                "total": total,
                "success": success,
                "degraded": 0,
                "failed": failed,
                "success_rate": round(success / total * 100, 1) if total > 0 else None,
                "p95_ms": None,
                "last_ts": last_ts,
                "status": _derive_subsystem_status(total, success, 0, failed),
                "detail": f"{failed} 条 intent lint 未通过" if failed > 0 else None,
                "blocked": None,
            }
        else:
            h = _compute_subsystem_health(rows, status_field=status_field or "status", latency_field=latency_field)
            if sid == "retrieval" and h["degraded"] > 0 and h["failed"] == 0:
                h["detail"] = f"{h['degraded']} 次降级，最近多为检索链慢路径"

        subsystems.append(SubsystemStatItem(
            subsystem=sid,
            display_name=dname,
            total=h["total"],
            success=h["success"],
            degraded=h.get("degraded", 0),
            failed=h["failed"],
            success_rate=h["success_rate"],
            p95_ms=h.get("p95_ms"),
            last_ts=h.get("last_ts"),
            status=h.get("status", "UNKNOWN"),
            detail=h.get("detail"),
            blocked=h.get("blocked"),
        ))

    return SubsystemsStatsResponse(
        window_minutes=window_minutes,
        subsystems=subsystems,
        timestamp=now,
    )
