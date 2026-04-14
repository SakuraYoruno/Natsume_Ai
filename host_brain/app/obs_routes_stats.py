"""obs_routes_stats.py — 子系统统计端点（从 obs_routes.py 拆分）。

包含 33 个统计端点（governor/stats ~ semantic-recall/stats）。
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
    ledger = _get_ledger()
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
    ledger = _get_ledger()
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
    ledger = _get_ledger()
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


# ---------------------------------------------------------------------------
# P3: GET /obs/agent_loop/stats  — Agent Loop 详细统计
# ---------------------------------------------------------------------------
class AgentLoopRoundItem(BaseModel):
    request_id: str
    session_id: Optional[str]
    goal: str
    total_rounds: int
    goal_achieved: bool
    total_actions: int
    success_actions: int
    failed_actions: int
    total_latency_ms: Optional[int]
    ts_ms: int

class AgentLoopStatsResponse(BaseModel):
    window_minutes: int
    total_loops: int
    achieved: int
    failed: int
    achievement_rate: float
    avg_rounds: Optional[float]
    recent_loops: List[AgentLoopRoundItem]
    timestamp: int

@router.get("/agent_loop/stats", response_model=AgentLoopStatsResponse)
def get_agent_loop_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
    limit: int = Query(10, ge=1, le=50),
):
    """P3: Agent Loop 详细统计 — 目标达成率、平均轮次、最近执行列表。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("agent_loop_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    # 按 request_id 聚合（取 LOOP_END 或最后一行）
    from collections import defaultdict
    loop_map: Dict[str, List] = defaultdict(list)
    for r in rows:
        loop_map[r.get("request_id", "")].append(r)

    loops = []
    for req_id, loop_rows in loop_map.items():
        # 找 LOOP_END 行
        end_rows = [r for r in loop_rows if str(r.get("phase", "")).upper() in ("LOOP_END", "LOOP_SUCCESS", "LOOP_FAILED")]
        summary_row = end_rows[-1] if end_rows else loop_rows[-1]

        total_rounds = max((r.get("round") or 0 for r in loop_rows), default=0)
        goal_achieved = bool(summary_row.get("goal_achieved"))
        total_actions = sum(r.get("action_count") or 0 for r in loop_rows)
        success_actions = sum(r.get("success_count") or 0 for r in loop_rows)
        failed_actions = sum(r.get("failed_count") or 0 for r in loop_rows)
        total_latency = sum(r.get("latency_ms") or 0 for r in loop_rows if r.get("latency_ms"))

        loops.append({
            "request_id": req_id,
            "session_id": summary_row.get("session_id"),
            "goal": (summary_row.get("goal") or "")[:80],
            "total_rounds": total_rounds,
            "goal_achieved": goal_achieved,
            "total_actions": total_actions,
            "success_actions": success_actions,
            "failed_actions": failed_actions,
            "total_latency_ms": total_latency or None,
            "ts_ms": summary_row.get("ts_ms") or 0,
        })

    total_loops = len(loops)
    achieved = sum(1 for l in loops if l["goal_achieved"])
    failed_loops = total_loops - achieved
    achievement_rate = round(achieved / total_loops * 100, 1) if total_loops > 0 else 0.0
    avg_rounds = round(sum(l["total_rounds"] for l in loops) / total_loops, 1) if total_loops > 0 else None

    # 最近 N 条（按 ts_ms 倒序）
    recent = sorted(loops, key=lambda l: l["ts_ms"], reverse=True)[:limit]

    return AgentLoopStatsResponse(
        window_minutes=window_minutes,
        total_loops=total_loops,
        achieved=achieved,
        failed=failed_loops,
        achievement_rate=achievement_rate,
        avg_rounds=avg_rounds,
        recent_loops=[AgentLoopRoundItem(**l) for l in recent],
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #26: GET /obs/memory/stats  — Memory 子系统详细统计
# ---------------------------------------------------------------------------
class MemorySubsystemItem(BaseModel):
    subsystem: str
    display_name: str
    total: int
    success: int
    failed: int
    success_rate: Optional[float]
    p95_ms: Optional[int]
    last_ts: Optional[int]
    extra: Optional[Dict[str, Any]] = None

class MemoryStatsResponse(BaseModel):
    window_minutes: int
    subsystems: List[MemorySubsystemItem]
    # 聚合指标
    total_writes: int
    guard_reject_rate: Optional[float]
    compress_rate: Optional[float]
    distill_count: int
    timestamp: int

@router.get("/memory/stats", response_model=MemoryStatsResponse)
def get_memory_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #26: Memory 子系统详细统计。

    覆盖四个 Memory 子系统：
    - EpisodicStore：写入量、成功率
    - MemoryGuard：拒绝率（NARRATIVE_ONLY 过滤 + 宪法校验拒绝）
    - ContextCompressor：压缩率（压缩前/后 token 数比值）
    - LongTermDistiller：蒸馏次数、蒸馏成功率
    """
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # ── EpisodicStore 写入统计 ────────────────────────────────────────────
    try:
        episodic_rows = ledger.fetch_by_time_range("episodic_store_log", start_ms=since_ms, end_ms=now)
    except Exception:
        episodic_rows = []

    ep_total = len(episodic_rows)
    ep_success = sum(1 for r in episodic_rows if str(r.get("status", "")).upper() in ("OK", "WRITTEN", "SUCCESS"))
    ep_failed = ep_total - ep_success
    ep_rate = round(ep_success / ep_total * 100, 1) if ep_total > 0 else None
    ep_last = max((r.get("ts_ms") or 0 for r in episodic_rows), default=None)

    # ── MemoryGuard 拒绝统计 ──────────────────────────────────────────────
    try:
        guard_rows = ledger.fetch_by_time_range("memory_guard_log", start_ms=since_ms, end_ms=now)
    except Exception:
        guard_rows = []

    # 从 error_log 中补充 MemoryGuard 相关记录（MEMORY_GUARD_FILTERED 标签）
    try:
        error_rows = ledger.fetch_by_time_range("error_log", start_ms=since_ms, end_ms=now)
        guard_error_rows = [
            r for r in error_rows
            if "MEMORY_GUARD" in str(r.get("stage", "")).upper()
            or "MEMORY_GUARD" in str(r.get("reason_code", "")).upper()
        ]
    except Exception:
        guard_error_rows = []

    all_guard_rows = guard_rows + guard_error_rows
    guard_total = len(all_guard_rows)
    guard_rejected = sum(
        1 for r in all_guard_rows
        if str(r.get("status", "")).upper() in ("REJECTED", "BLOCKED", "FAILED")
        or str(r.get("reason_code", "")).upper().startswith("EPISODIC_")
        or str(r.get("reason_code", "")).upper().startswith("CANON_")
    )
    guard_reject_rate = round(guard_rejected / guard_total * 100, 1) if guard_total > 0 else None
    guard_last = max((r.get("ts_ms") or 0 for r in all_guard_rows), default=None)

    # ── ContextCompressor 压缩统计 ────────────────────────────────────────
    try:
        compress_rows = ledger.fetch_by_time_range("context_compress_log", start_ms=since_ms, end_ms=now)
    except Exception:
        compress_rows = []

    comp_total = len(compress_rows)
    comp_success = sum(1 for r in compress_rows if str(r.get("status", "")).upper() in ("OK", "DONE", "SUCCESS"))
    comp_failed = comp_total - comp_success
    comp_rate = None
    if compress_rows:
        # 计算平均压缩率：(1 - output_tokens / input_tokens) * 100
        ratios = []
        for r in compress_rows:
            inp = r.get("input_tokens") or r.get("input_chars")
            out = r.get("output_tokens") or r.get("output_chars")
            if inp and out and inp > 0:
                ratios.append(1.0 - out / inp)
        if ratios:
            comp_rate = round(sum(ratios) / len(ratios) * 100, 1)
    comp_last = max((r.get("ts_ms") or 0 for r in compress_rows), default=None)

    # ── LongTermDistiller 蒸馏统计 ────────────────────────────────────────
    try:
        distill_rows = ledger.fetch_by_time_range("distill_log", start_ms=since_ms, end_ms=now)
    except Exception:
        distill_rows = []

    distill_total = len(distill_rows)
    distill_success = sum(1 for r in distill_rows if str(r.get("status", "")).upper() in ("OK", "DONE", "SUCCESS"))
    distill_failed = distill_total - distill_success
    distill_rate = round(distill_success / distill_total * 100, 1) if distill_total > 0 else None
    distill_last = max((r.get("ts_ms") or 0 for r in distill_rows), default=None)

    subsystems = [
        MemorySubsystemItem(
            subsystem="episodic_store",
            display_name="EpisodicStore 写入",
            total=ep_total,
            success=ep_success,
            failed=ep_failed,
            success_rate=ep_rate,
            p95_ms=None,
            last_ts=ep_last,
            extra={"write_count": ep_success},
        ),
        MemorySubsystemItem(
            subsystem="memory_guard",
            display_name="MemoryGuard 宪法守卫",
            total=guard_total,
            success=guard_total - guard_rejected,
            failed=guard_rejected,
            success_rate=(100.0 - guard_reject_rate) if guard_reject_rate is not None else None,
            p95_ms=None,
            last_ts=guard_last,
            extra={"reject_rate": guard_reject_rate, "rejected_count": guard_rejected},
        ),
        MemorySubsystemItem(
            subsystem="context_compressor",
            display_name="ContextCompressor 压缩",
            total=comp_total,
            success=comp_success,
            failed=comp_failed,
            success_rate=round(comp_success / comp_total * 100, 1) if comp_total > 0 else None,
            p95_ms=None,
            last_ts=comp_last,
            extra={"avg_compress_rate_pct": comp_rate},
        ),
        MemorySubsystemItem(
            subsystem="long_term_distiller",
            display_name="LongTermDistiller 蒸馏",
            total=distill_total,
            success=distill_success,
            failed=distill_failed,
            success_rate=distill_rate,
            p95_ms=None,
            last_ts=distill_last,
            extra={"distill_count": distill_success},
        ),
    ]

    return MemoryStatsResponse(
        window_minutes=window_minutes,
        subsystems=subsystems,
        total_writes=ep_success,
        guard_reject_rate=guard_reject_rate,
        compress_rate=comp_rate,
        distill_count=distill_success,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #126: GET /obs/memory/resident-context — 常驻设定 / 人格风格注入快照
# ---------------------------------------------------------------------------
def _truncate_memory_preview(value: Any, limit: int = 96) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}…"


def _resolve_style_source_path(style_root: str, canon_id: str | None) -> str:
    normalized = str(canon_id or "").strip()
    if not normalized:
        return os.path.join(style_root, "style_profile_v5.json")
    if normalized.endswith("_v5") or normalized in {"natsume_ai_cn", "natsume_ai_ja"}:
        return os.path.join(style_root, "style_profile_v5.json")
    return os.path.join(style_root, f"{normalized}.json")


class ResidentCanonFileItem(BaseModel):
    kind: str
    doc_id: str
    title: Optional[str] = None
    source_path: Optional[str] = None
    version: Optional[int] = None
    status: Optional[str] = None
    updated_by: Optional[str] = None
    ts_ms: Optional[int] = None
    preview: Optional[str] = None


class ResidentStyleBindingItem(BaseModel):
    node_id: str
    canon_id: Optional[str] = None
    version: Optional[int] = None
    citation: Optional[str] = None
    source_path: Optional[str] = None
    tone_only: bool = True
    unsafe_as_fact: bool = True
    preview: Optional[str] = None


class ResidentMemoryPersonaEvidenceItem(BaseModel):
    kind: Optional[str] = None
    source_ref: Optional[str] = None
    evidence_ref: Optional[str] = None
    emotion_color_tag: Optional[str] = None
    suppression_flag: bool = False
    distortion_level: Optional[float] = None
    content: Optional[str] = None


class ResidentMemoryContextResponse(BaseModel):
    world_canon_documents: List[ResidentCanonFileItem]
    persona_resident_documents: List[ResidentCanonFileItem]
    style_canon_documents: List[ResidentCanonFileItem]
    active_style_bindings: List[ResidentStyleBindingItem]
    latest_style_pack_rows: List[ResidentStyleBindingItem]
    latest_memory_persona_evidence: List[ResidentMemoryPersonaEvidenceItem]
    latest_memory_recall_summary: Optional[str] = None
    latest_request_id: Optional[str] = None
    latest_session_id: Optional[str] = None
    latest_render_target_lang: Optional[str] = None
    latest_selected_tier: Optional[str] = None
    prompt_context_available: bool = False
    timestamp: int


@router.get("/memory/resident-context", response_model=ResidentMemoryContextResponse)
def get_memory_resident_context():
    """Ref #126: 常驻设定文件、风格绑定与最新人格证据快照。"""
    now = _now_ms()
    ledger = _get_ledger()
    world_root = str(os.environ.get("Natsume_WORLD_CANON_ROOT") or "host_brain/canon/world").strip()
    style_root = str(os.environ.get("Natsume_STYLE_CANON_ROOT") or "host_brain/canon/style").strip()
    character_profile_path = str(
        os.environ.get("NATSUME_CHARACTER_PROFILE_PATH")
        or os.path.join("host_brain", "canon", "character", "character_profile.json")
    ).strip()

    world_docs: List[ResidentCanonFileItem] = []
    try:
        from host_brain.memory.canon_world_store import WorldCanonStore  # noqa: PLC0415

        world_store = WorldCanonStore(world_root=world_root, ledger=ledger)
        for doc in world_store.list_documents():
            world_docs.append(
                ResidentCanonFileItem(
                    kind="world",
                    doc_id=str(getattr(doc, "doc_id", "") or ""),
                    title=str(getattr(doc, "title", "") or "").strip() or None,
                    source_path=str(getattr(doc, "source_path", "") or "").strip() or None,
                    version=int(getattr(doc, "version", 0) or 0) or None,
                    status=str(getattr(doc, "status", "") or "").strip() or None,
                    updated_by=str(getattr(doc, "updated_by", "") or "").strip() or None,
                    ts_ms=int(getattr(doc, "ts_ms", 0) or 0) or None,
                    preview=_truncate_memory_preview(getattr(doc, "body", ""), limit=120) or None,
                )
            )
    except Exception as exc:
        logger.debug("resident world canon context degraded: %s", exc)

    persona_docs: List[ResidentCanonFileItem] = []
    try:
        with open(character_profile_path, "r", encoding="utf-8-sig") as fp:
            character_payload = json.load(fp)
        persona_docs.append(
            ResidentCanonFileItem(
                kind="persona",
                doc_id=str(character_payload.get("doc_id") or "character-profile").strip(),
                title=str(character_payload.get("title") or "角色人设常驻文件").strip() or None,
                source_path=character_profile_path,
                version=(int(character_payload.get("version")) if character_payload.get("version") not in (None, "") else None),
                status=str(character_payload.get("status") or "ACTIVE").strip() or None,
                updated_by="character_profile",
                ts_ms=(int(os.path.getmtime(character_profile_path) * 1000) if os.path.exists(character_profile_path) else None),
                preview=_truncate_memory_preview(character_payload.get("instruction_cn"), limit=120) or None,
            )
        )
    except Exception as exc:
        logger.debug("resident persona context degraded: %s", exc)

    style_docs: List[ResidentCanonFileItem] = []
    try:
        from host_brain.memory.canon_style_store import StyleCanonStore  # noqa: PLC0415

        style_store = StyleCanonStore(style_root=style_root, ledger=ledger)
        for entry in style_store.list_entries():
            style_docs.append(
                ResidentCanonFileItem(
                    kind="style",
                    doc_id=str(getattr(entry, "canon_id", "") or ""),
                    title=str(getattr(entry, "canon_id", "") or "").strip() or None,
                    source_path=_resolve_style_source_path(style_root, getattr(entry, "canon_id", None)),
                    version=int(getattr(entry, "version", 0) or 0) or None,
                    status="ACTIVE",
                    updated_by=str(getattr(entry, "updated_by", "") or "").strip() or None,
                    ts_ms=int(getattr(entry, "ts_ms", 0) or 0) or None,
                    preview=_truncate_memory_preview(getattr(entry, "content", ""), limit=120) or None,
                )
            )
    except Exception as exc:
        logger.debug("resident style canon context degraded: %s", exc)

    latest_snapshot: dict[str, Any] = {}
    active_style_bindings_raw: Mapping[str, Any] | None = None
    latest_render_target_lang = "zh"
    try:
        from host_brain.app.chat_context_builder import (  # noqa: PLC0415
            build_node_style_bindings,
            get_latest_prompt_context_runtime_snapshot,
        )

        latest_snapshot = dict(get_latest_prompt_context_runtime_snapshot() or {})
        latest_render_target_lang = str(latest_snapshot.get("render_target_lang") or "zh").strip().lower() or "zh"
        active_style_bindings_raw = latest_snapshot.get("style_bindings")
        if not isinstance(active_style_bindings_raw, Mapping) or not active_style_bindings_raw:
            active_style_bindings_raw = build_node_style_bindings(render_target_lang=latest_render_target_lang)
    except Exception as exc:
        logger.debug("resident prompt-context snapshot unavailable: %s", exc)
        latest_snapshot = {}
        active_style_bindings_raw = {}

    active_style_bindings: List[ResidentStyleBindingItem] = []
    for node_id, row in dict(active_style_bindings_raw or {}).items():
        if not isinstance(row, Mapping):
            continue
        canon_id = str(row.get("canon_id") or "").strip() or None
        active_style_bindings.append(
            ResidentStyleBindingItem(
                node_id=str(node_id),
                canon_id=canon_id,
                version=(int(row.get("version")) if row.get("version") not in (None, "") else None),
                citation=str(row.get("citation") or "").strip() or None,
                source_path=_resolve_style_source_path(style_root, canon_id),
                tone_only=bool(row.get("tone_only", True)),
                unsafe_as_fact=bool(row.get("unsafe_as_fact", True)),
                preview=_truncate_memory_preview(
                    row.get("style_instruction") or row.get("instruction"),
                    limit=88,
                ) or None,
            )
        )

    style_pack_raw = latest_snapshot.get("style_pack")
    style_pack = dict(style_pack_raw) if isinstance(style_pack_raw, Mapping) else {}
    style_rows = list(style_pack.get("style_canon") or []) if isinstance(style_pack.get("style_canon"), list) else []
    latest_style_pack_rows: List[ResidentStyleBindingItem] = []
    for row in style_rows:
        if not isinstance(row, Mapping):
            continue
        canon_id = str(row.get("canon_id") or "").strip() or None
        latest_style_pack_rows.append(
            ResidentStyleBindingItem(
                node_id=str(row.get("node_id") or row.get("slot") or "style_pack"),
                canon_id=canon_id,
                version=(int(row.get("version")) if row.get("version") not in (None, "") else None),
                citation=str(row.get("citation") or "").strip() or None,
                source_path=_resolve_style_source_path(style_root, canon_id),
                tone_only=bool(row.get("tone_only", True)),
                unsafe_as_fact=bool(row.get("unsafe_as_fact", True)),
                preview=_truncate_memory_preview(
                    row.get("style_instruction") or row.get("instruction"),
                    limit=88,
                ) or None,
            )
        )

    memory_persona_evidence_raw = latest_snapshot.get("memory_persona_evidence")
    memory_persona_rows = (
        list(memory_persona_evidence_raw)
        if isinstance(memory_persona_evidence_raw, list)
        else []
    )
    latest_memory_persona_evidence: List[ResidentMemoryPersonaEvidenceItem] = []
    for row in memory_persona_rows:
        if not isinstance(row, Mapping):
            continue
        latest_memory_persona_evidence.append(
            ResidentMemoryPersonaEvidenceItem(
                kind=str(row.get("kind") or "").strip() or None,
                source_ref=str(row.get("source_ref") or "").strip() or None,
                evidence_ref=str(row.get("evidence_ref") or "").strip() or None,
                emotion_color_tag=str(row.get("emotion_color_tag") or "").strip() or None,
                suppression_flag=bool(row.get("suppression_flag", False)),
                distortion_level=(
                    float(row.get("distortion_level"))
                    if row.get("distortion_level") not in (None, "")
                    else None
                ),
                content=_truncate_memory_preview(row.get("content"), limit=120) or None,
            )
        )

    prompt_context_observability = (
        dict(latest_snapshot.get("prompt_context_observability") or {})
        if isinstance(latest_snapshot.get("prompt_context_observability"), Mapping)
        else {}
    )

    return ResidentMemoryContextResponse(
        world_canon_documents=world_docs,
        persona_resident_documents=persona_docs,
        style_canon_documents=style_docs,
        active_style_bindings=active_style_bindings,
        latest_style_pack_rows=latest_style_pack_rows,
        latest_memory_persona_evidence=latest_memory_persona_evidence,
        latest_memory_recall_summary=str(latest_snapshot.get("memory_recall_summary") or "").strip() or None,
        latest_request_id=str(latest_snapshot.get("request_id") or "").strip() or None,
        latest_session_id=str(latest_snapshot.get("session_id") or "").strip() or None,
        latest_render_target_lang=latest_render_target_lang or None,
        latest_selected_tier=str(prompt_context_observability.get("selected_tier") or "").strip() or None,
        prompt_context_available=bool(latest_snapshot),
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #30: GET /obs/vts/stats  — VTS 通道统计
# ---------------------------------------------------------------------------
class VtsEmotionItem(BaseModel):
    emotion_tag: str
    count: int
    pct: float

class VtsStatsResponse(BaseModel):
    window_minutes: int
    total_events: int
    applied: int
    degraded: int
    rejected: int
    apply_rate: Optional[float]
    degrade_rate: Optional[float]
    reject_rate: Optional[float]
    top_emotions: List[VtsEmotionItem]
    talk_gate_total: int
    talk_gate_allowed: int
    talk_gate_blocked: int
    timestamp: int

@router.get("/vts/stats", response_model=VtsStatsResponse)
def get_vts_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #30: VTS 通道统计 — 表情应用率/降级率/拒绝率、Top 情感标签、Talk Gate 事件统计。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # ── VTS 表情事件统计 ──────────────────────────────────────────────────
    try:
        vts_rows = ledger.fetch_by_time_range("vts_event_log", start_ms=since_ms, end_ms=now)
    except Exception:
        vts_rows = []

    # 补充从 voice_event_log 中过滤 VTS 相关事件
    try:
        voice_rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=now)
        vts_voice_rows = [
            r for r in voice_rows
            if str(r.get("event_type", "")).upper().startswith("VTS")
        ]
    except Exception:
        vts_voice_rows = []

    all_vts_rows = vts_rows + vts_voice_rows
    total_events = len(all_vts_rows)

    applied = sum(
        1 for r in all_vts_rows
        if str(r.get("status", "")).upper() in ("APPLIED", "OK", "SUCCESS")
        or str(r.get("result", "")).upper() == "APPLIED"
    )
    degraded = sum(
        1 for r in all_vts_rows
        if str(r.get("status", "")).upper() in ("DEGRADED", "FALLBACK")
        or str(r.get("result", "")).upper() == "DEGRADED"
    )
    rejected = sum(
        1 for r in all_vts_rows
        if str(r.get("status", "")).upper() in ("REJECTED", "BLOCKED", "FAILED")
        or str(r.get("result", "")).upper() == "REJECTED"
    )

    apply_rate = round(applied / total_events * 100, 1) if total_events > 0 else None
    degrade_rate = round(degraded / total_events * 100, 1) if total_events > 0 else None
    reject_rate = round(rejected / total_events * 100, 1) if total_events > 0 else None

    # Top 情感标签分布
    from collections import Counter
    emotion_counter = Counter(
        str(r.get("emotion_tag") or r.get("expression") or "UNKNOWN").upper()
        for r in all_vts_rows
    )
    top_emotions = [
        VtsEmotionItem(
            emotion_tag=tag,
            count=cnt,
            pct=round(cnt / total_events * 100, 1) if total_events > 0 else 0.0,
        )
        for tag, cnt in emotion_counter.most_common(8)
    ]

    # ── Talk Gate 事件统计 ────────────────────────────────────────────────
    try:
        talk_gate_rows = ledger.fetch_by_time_range("talk_gate_log", start_ms=since_ms, end_ms=now)
    except Exception:
        talk_gate_rows = []

    talk_gate_total = len(talk_gate_rows)
    talk_gate_allowed = sum(
        1 for r in talk_gate_rows
        if str(r.get("decision", "")).upper() in ("ALLOW", "ALLOWED", "APPROVE", "OK")
    )
    talk_gate_blocked = talk_gate_total - talk_gate_allowed

    return VtsStatsResponse(
        window_minutes=window_minutes,
        total_events=total_events,
        applied=applied,
        degraded=degraded,
        rejected=rejected,
        apply_rate=apply_rate,
        degrade_rate=degrade_rate,
        reject_rate=reject_rate,
        top_emotions=top_emotions,
        talk_gate_total=talk_gate_total,
        talk_gate_allowed=talk_gate_allowed,
        talk_gate_blocked=talk_gate_blocked,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #31: GET /obs/distillation/stats  — LongTermDistiller 蒸馏统计
# ---------------------------------------------------------------------------
class DistillationCycleItem(BaseModel):
    cycle_id: str
    topics_processed: int
    semantic_memories_created: int
    proposals_submitted: int
    proposals_skipped: int
    error_count: int
    ts_ms: int
    detail: Optional[str] = None

class DistillationStatsResponse(BaseModel):
    window_minutes: int
    total_cycles: int
    success_cycles: int
    failed_cycles: int
    success_rate: Optional[float]
    total_proposals_submitted: int
    total_semantic_created: int
    recent_cycles: List[DistillationCycleItem]
    timestamp: int

@router.get("/distillation/stats", response_model=DistillationStatsResponse)
def get_distillation_stats(
    window_minutes: int = Query(1440, ge=1, le=43200, description="统计窗口（分钟，默认 24h）"),
    limit: int = Query(10, ge=1, le=50, description="最近循环条数"),
):
    """Ref #31: LongTermDistiller 蒸馏统计 — 循环次数、提案提交量、蒸馏成功率。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("distillation_cycle_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    # 补充从 distill_log 查询（兼容旧表名）
    if not rows:
        try:
            rows = ledger.fetch_by_time_range("distill_log", start_ms=since_ms, end_ms=now)
        except Exception:
            rows = []

    total_cycles = len(rows)
    success_cycles = sum(
        1 for r in rows
        if (r.get("errors") in (None, [], "") or len(r.get("errors") or []) == 0)
        and int(r.get("proposals_submitted") or 0) >= 0
    )
    failed_cycles = total_cycles - success_cycles
    success_rate = round(success_cycles / total_cycles * 100, 1) if total_cycles > 0 else None

    total_proposals = sum(int(r.get("proposals_submitted") or 0) for r in rows)
    total_semantic = sum(int(r.get("semantic_memories_created") or 0) for r in rows)

    # 最近 N 条（倒序）
    recent_rows = sorted(rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[:limit]
    recent_cycles = [
        DistillationCycleItem(
            cycle_id=str(r.get("cycle_id") or r.get("request_id") or ""),
            topics_processed=int(r.get("topics_processed") or 0),
            semantic_memories_created=int(r.get("semantic_memories_created") or 0),
            proposals_submitted=int(r.get("proposals_submitted") or 0),
            proposals_skipped=int(r.get("proposals_skipped") or 0),
            error_count=len(r.get("errors") or []),
            ts_ms=int(r.get("ts_ms") or 0),
            detail=(r.get("detail") or "")[:200],
        )
        for r in recent_rows
    ]

    return DistillationStatsResponse(
        window_minutes=window_minutes,
        total_cycles=total_cycles,
        success_cycles=success_cycles,
        failed_cycles=failed_cycles,
        success_rate=success_rate,
        total_proposals_submitted=total_proposals,
        total_semantic_created=total_semantic,
        recent_cycles=recent_cycles,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #37: GET /obs/wechat/stats  — WeChat 通道统计
# ---------------------------------------------------------------------------
class WeChatRateLimitItem(BaseModel):
    wxid: str
    message_count: int
    last_ts: Optional[int]
    rate_limited: bool

class WeChatEventItem(BaseModel):
    request_id: Optional[str]
    wxid: Optional[str]
    direction: Optional[str]  # "inbound" | "outbound"
    status: str
    reason_code: Optional[str]
    ts_ms: int

class WeChatStatsResponse(BaseModel):
    window_minutes: int
    channel_status: str  # "UP" | "DEGRADED" | "DOWN" | "UNKNOWN"
    total_events: int
    inbound: int
    outbound: int
    success: int
    failed: int
    success_rate: Optional[float]
    rate_limited_count: int
    top_wxids: List[WeChatRateLimitItem]
    recent_events: List[WeChatEventItem]
    last_event_ts: Optional[int]
    timestamp: int

@router.get("/wechat/stats", response_model=WeChatStatsResponse)
def get_wechat_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    limit: int = Query(20, ge=1, le=100, description="最近事件条数"),
):
    """Ref #37: WeChat 通道统计 — 通道健康状态、频率限制统计、最近 channel_io_log 事件。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # ── channel_io_log 中过滤 WeChat 相关事件 ────────────────────────────
    try:
        channel_rows = ledger.fetch_by_time_range("channel_io_log", start_ms=since_ms, end_ms=now)
        wechat_rows = [
            r for r in channel_rows
            if str(r.get("channel") or r.get("source") or "").upper() in ("WECHAT", "WX", "WEIXIN")
            or str(r.get("event_type") or "").upper().startswith("WECHAT")
        ]
    except Exception:
        wechat_rows = []

    # 补充从 voice_event_log 中过滤 WECHAT 相关事件
    try:
        voice_rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=now)
        wechat_voice_rows = [
            r for r in voice_rows
            if str(r.get("event_type") or "").upper().startswith("WECHAT")
        ]
    except Exception:
        wechat_voice_rows = []

    all_rows = wechat_rows + wechat_voice_rows
    total_events = len(all_rows)

    inbound = sum(
        1 for r in all_rows
        if str(r.get("direction") or r.get("event_type") or "").upper()
        in ("INBOUND", "RECEIVE", "RECV", "WECHAT_RECV")
    )
    outbound = total_events - inbound

    success = sum(
        1 for r in all_rows
        if str(r.get("status") or "").upper() in ("OK", "SUCCESS", "SENT", "DELIVERED")
    )
    failed = total_events - success
    success_rate = round(success / total_events * 100, 1) if total_events > 0 else None

    # 频率限制统计
    rate_limited_count = sum(
        1 for r in all_rows
        if str(r.get("reason_code") or "").upper().startswith("RATE_LIMIT")
        or str(r.get("status") or "").upper() == "RATE_LIMITED"
    )

    # Top wxid 统计（按消息量排序）
    from collections import Counter, defaultdict
    wxid_counter: Counter = Counter()
    wxid_last_ts: dict = defaultdict(int)
    wxid_rate_limited: dict = defaultdict(bool)
    for r in all_rows:
        wxid = str(r.get("wxid") or r.get("from_wxid") or r.get("session_id") or "unknown")
        wxid_counter[wxid] += 1
        ts = r.get("ts_ms") or 0
        if ts > wxid_last_ts[wxid]:
            wxid_last_ts[wxid] = ts
        if str(r.get("reason_code") or "").upper().startswith("RATE_LIMIT"):
            wxid_rate_limited[wxid] = True

    top_wxids = [
        WeChatRateLimitItem(
            wxid=wxid,
            message_count=cnt,
            last_ts=wxid_last_ts.get(wxid),
            rate_limited=bool(wxid_rate_limited.get(wxid, False)),
        )
        for wxid, cnt in wxid_counter.most_common(10)
    ]

    # 通道健康状态
    if total_events == 0:
        channel_status = "UNKNOWN"
    elif success_rate is not None and success_rate >= 80:
        channel_status = "UP"
    elif success_rate is not None and success_rate >= 50:
        channel_status = "DEGRADED"
    else:
        channel_status = "DOWN"

    # 最近事件列表（倒序）
    recent_sorted = sorted(all_rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[:limit]
    recent_events = [
        WeChatEventItem(
            request_id=r.get("request_id"),
            wxid=str(r.get("wxid") or r.get("from_wxid") or r.get("session_id") or ""),
            direction=(
                "inbound"
                if str(r.get("direction") or r.get("event_type") or "").upper()
                in ("INBOUND", "RECEIVE", "RECV", "WECHAT_RECV")
                else "outbound"
            ),
            status=str(r.get("status") or "UNKNOWN").upper(),
            reason_code=r.get("reason_code"),
            ts_ms=int(r.get("ts_ms") or 0),
        )
        for r in recent_sorted
    ]

    last_event_ts = max((r.get("ts_ms") or 0 for r in all_rows), default=None)

    return WeChatStatsResponse(
        window_minutes=window_minutes,
        channel_status=channel_status,
        total_events=total_events,
        inbound=inbound,
        outbound=outbound,
        success=success,
        failed=failed,
        success_rate=success_rate,
        rate_limited_count=rate_limited_count,
        top_wxids=top_wxids,
        recent_events=recent_events,
        last_event_ts=last_event_ts,
        timestamp=now,
    )


@router.post("/wechat/reset", response_model=Dict[str, Any])
def reset_wechat_channel(
    reason: Optional[str] = Query(None, description="重置原因"),
):
    """Ref #37: 手动重置 WeChat 通道状态（清除频率限制计数器）。"""
    try:
        from host_brain.channels.wechat import WeChatChannel  # noqa: PLC0415
        _channel = WeChatChannel.get_instance()
        if _channel is not None and hasattr(_channel, "reset_rate_limits"):
            _channel.reset_rate_limits()
            return {
                "status": "OK",
                "reason_code": "WECHAT_CHANNEL_RESET",
                "detail": f"WeChat 通道频率限制已重置（原因：{reason or 'manual'}）",
                "timestamp": _now_ms(),
            }
    except (ImportError, AttributeError):
        pass
    except Exception as exc:
        logger.warning("WeChat 通道重置失败: %s", exc)
        return {
            "status": "DEGRADED",
            "reason_code": "WECHAT_RESET_FAILED",
            "detail": str(exc)[:200],
            "timestamp": _now_ms(),
        }
    return {
        "status": "OK",
        "reason_code": "WECHAT_CHANNEL_RESET_NOOP",
        "detail": "WeChat 通道重置（无状态实例，已记录）",
        "timestamp": _now_ms(),
    }


# ---------------------------------------------------------------------------
# Ref #34: GET /obs/retention/stats  — AuditRetention 保留策略统计
# ---------------------------------------------------------------------------
class RetentionTableItem(BaseModel):
    table: str
    candidate_count: int
    deleted_count: int
    protected: bool

class RetentionStatsResponse(BaseModel):
    last_run_ts: Optional[int]
    last_run_status: Optional[str]
    total_deleted: int
    total_candidates: int
    total_protected: int
    tables: List[RetentionTableItem]
    timestamp: int

@router.get("/retention/stats", response_model=RetentionStatsResponse)
def get_retention_stats(
    limit: int = Query(5, ge=1, le=20, description="最近执行记录条数"),
):
    """Ref #34: AuditRetention 保留策略执行统计。"""
    ledger = _get_ledger()
    now = _now_ms()

    # 从 error_log 中查询 S_AUDIT_RETENTION 阶段的记录
    try:
        rows = ledger.fetch_by_time_range("error_log", start_ms=0, end_ms=now)
        retention_rows = [
            r for r in rows
            if str(r.get("stage") or "").upper() == "S_AUDIT_RETENTION"
        ]
    except Exception:
        retention_rows = []

    # 取最近一次执行记录
    recent = sorted(retention_rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[:limit]
    last_run_ts = recent[0].get("ts_ms") if recent else None
    last_run_status = str(recent[0].get("reason_code") or "UNKNOWN") if recent else None

    # 解析最近一次执行的详情
    tables: list[RetentionTableItem] = []
    total_deleted = 0
    total_candidates = 0
    total_protected = 0

    if recent:
        try:
            detail_raw = recent[0].get("detail") or "{}"
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
            candidate_counts = dict(detail.get("candidate_counts") or {})
            deleted_counts = dict(detail.get("deleted_counts") or {})
            protected_counts = dict(detail.get("protected_counts") or {})

            all_tables = set(candidate_counts) | set(deleted_counts) | set(protected_counts)
            for tbl in sorted(all_tables):
                cand = int(candidate_counts.get(tbl) or 0)
                deleted = int(deleted_counts.get(tbl) or 0)
                protected = tbl in protected_counts
                tables.append(RetentionTableItem(
                    table=tbl,
                    candidate_count=cand,
                    deleted_count=deleted,
                    protected=protected,
                ))
                total_candidates += cand
                total_deleted += deleted
                total_protected += int(protected_counts.get(tbl) or 0)
        except Exception:
            pass

    return RetentionStatsResponse(
        last_run_ts=last_run_ts,
        last_run_status=last_run_status,
        total_deleted=total_deleted,
        total_candidates=total_candidates,
        total_protected=total_protected,
        tables=tables,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #40: GET /obs/qa/stats  — QA 违规统计
# ---------------------------------------------------------------------------
class QaViolationCodeItem(BaseModel):
    violation_code: str
    count: int
    pct: float

class QaStatsResponse(BaseModel):
    window_minutes: int
    total_violations: int
    degraded_count: int
    rewritten_count: int
    degrade_rate: Optional[float]
    rewrite_success_rate: Optional[float]
    violation_codes: List[QaViolationCodeItem]
    timeseries_labels: List[str]
    timeseries_counts: List[int]
    recent_violations: List[Dict[str, Any]]
    timestamp: int

@router.get("/qa/stats", response_model=QaStatsResponse)
def get_qa_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=60, description="时序聚合间隔（分钟）"),
    limit: int = Query(20, ge=1, le=100, description="最近违规条数"),
):
    """Ref #40: QA 违规统计 — 违规码分布、降级率、重写成功率、时序趋势、最近违规列表。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("qa_violation_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    # 兼容旧表名 qa_violation
    if not rows:
        try:
            rows = ledger.fetch_by_time_range("qa_violation", start_ms=since_ms, end_ms=now)
        except Exception:
            rows = []

    total = len(rows)
    degraded_count = sum(
        1 for r in rows
        if str(r.get("action") or r.get("result") or "").upper() in ("DEGRADED", "FALLBACK", "DOWNGRADE")
    )
    rewritten_count = sum(
        1 for r in rows
        if str(r.get("action") or r.get("result") or "").upper() in ("REWRITTEN", "REWRITE", "PATCHED")
    )
    degrade_rate = round(degraded_count / total * 100, 1) if total > 0 else None
    rewrite_success_rate = round(rewritten_count / (degraded_count + rewritten_count) * 100, 1) if (degraded_count + rewritten_count) > 0 else None

    # 违规码分布
    from collections import Counter
    code_counter = Counter(
        str(r.get("violation_code") or r.get("reason_code") or "UNKNOWN").upper()
        for r in rows
    )
    violation_codes = [
        QaViolationCodeItem(
            violation_code=code,
            count=cnt,
            pct=round(cnt / total * 100, 1) if total > 0 else 0.0,
        )
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

    # 最近 N 条（倒序）
    recent_sorted = sorted(rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[:limit]
    recent_violations = [
        {
            "request_id": r.get("request_id"),
            "node_id": r.get("node_id"),
            "violation_code": str(r.get("violation_code") or r.get("reason_code") or "UNKNOWN").upper(),
            "action": str(r.get("action") or r.get("result") or "").upper() or None,
            "detail": (r.get("detail") or "")[:200],
            "ts_ms": r.get("ts_ms"),
        }
        for r in recent_sorted
    ]

    return QaStatsResponse(
        window_minutes=window_minutes,
        total_violations=total,
        degraded_count=degraded_count,
        rewritten_count=rewritten_count,
        degrade_rate=degrade_rate,
        rewrite_success_rate=rewrite_success_rate,
        violation_codes=violation_codes,
        timeseries_labels=labels,
        timeseries_counts=ts_counts,
        recent_violations=recent_violations,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #41: GET /obs/llm/call-stats  — LLM 调用成本统计
# ---------------------------------------------------------------------------
class LlmModelCallItem(BaseModel):
    model_id: str
    node_id: Optional[str]
    call_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    avg_latency_ms: Optional[float]
    p95_latency_ms: Optional[int]

class LlmCallStatsResponse(BaseModel):
    window_minutes: int
    total_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    avg_latency_ms: Optional[float]
    models: List[LlmModelCallItem]
    timeseries_labels: List[str]
    timeseries_calls: List[int]
    timeseries_tokens: List[int]
    timestamp: int

@router.get("/llm/call-stats", response_model=LlmCallStatsResponse)
def get_llm_call_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=60, description="时序聚合间隔（分钟）"),
):
    """Ref #41: LLM 调用成本统计 — 按 model_id 分组的 token 消耗/平均延迟/调用次数。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("llm_call_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    # 兼容 llm_node_call_log 表名
    if not rows:
        try:
            rows = ledger.fetch_by_time_range("llm_node_call_log", start_ms=since_ms, end_ms=now)
        except Exception:
            rows = []

    total_calls = len(rows)
    total_prompt = sum(int(r.get("prompt_tokens") or 0) for r in rows)
    total_completion = sum(int(r.get("completion_tokens") or 0) for r in rows)
    total_tokens = total_prompt + total_completion
    latencies = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
    avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else None

    # 按 model_id 分组
    from collections import defaultdict
    model_map: Dict[str, List] = defaultdict(list)
    for r in rows:
        mid = str(r.get("model_id") or r.get("model") or "unknown")
        model_map[mid].append(r)

    models = []
    for mid, mrows in sorted(model_map.items()):
        m_prompt = sum(int(r.get("prompt_tokens") or 0) for r in mrows)
        m_completion = sum(int(r.get("completion_tokens") or 0) for r in mrows)
        m_latencies = [r["latency_ms"] for r in mrows if r.get("latency_ms") is not None]
        m_avg_lat = round(sum(m_latencies) / len(m_latencies), 1) if m_latencies else None
        m_p95 = None
        if m_latencies:
            sl = sorted(m_latencies)
            m_p95 = sl[max(0, int(len(sl) * 0.95) - 1)]
        node_ids = list({str(r.get("node_id") or "") for r in mrows if r.get("node_id")})
        models.append(LlmModelCallItem(
            model_id=mid,
            node_id=node_ids[0] if len(node_ids) == 1 else None,
            call_count=len(mrows),
            total_prompt_tokens=m_prompt,
            total_completion_tokens=m_completion,
            total_tokens=m_prompt + m_completion,
            avg_latency_ms=m_avg_lat,
            p95_latency_ms=m_p95,
        ))

    # 时序聚合
    interval_ms = interval_minutes * 60 * 1000
    bucket_count = max(1, (window_minutes * 60 * 1000) // interval_ms)
    ts_calls = [0] * bucket_count
    ts_tokens = [0] * bucket_count
    for r in rows:
        ts = r.get("ts_ms") or 0
        idx = min(int((ts - since_ms) // interval_ms), bucket_count - 1)
        if 0 <= idx < bucket_count:
            ts_calls[idx] += 1
            ts_tokens[idx] += int(r.get("prompt_tokens") or 0) + int(r.get("completion_tokens") or 0)

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

    return LlmCallStatsResponse(
        window_minutes=window_minutes,
        total_calls=total_calls,
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        total_tokens=total_tokens,
        avg_latency_ms=avg_latency,
        models=models,
        timeseries_labels=labels,
        timeseries_calls=ts_calls,
        timeseries_tokens=ts_tokens,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #42: GET /obs/checkpoints  — CheckpointManager 状态查询
# ---------------------------------------------------------------------------
class CheckpointItem(BaseModel):
    checkpoint_id: str
    request_id: Optional[str]
    interrupt_reason: Optional[str]
    resume_deadline_at: Optional[int]
    created_at_ms: Optional[int]
    is_expired: bool
    payload_summary: Optional[str]

class CheckpointsResponse(BaseModel):
    total: int
    items: List[CheckpointItem]
    timestamp: int

class CheckpointDetailResponse(BaseModel):
    checkpoint_id: str
    request_id: Optional[str]
    interrupt_reason: Optional[str]
    resume_deadline_at: Optional[int]
    created_at_ms: Optional[int]
    is_expired: bool
    payload: Optional[Any]
    timestamp: int

@router.get("/checkpoints", response_model=CheckpointsResponse)
def get_checkpoints(
    limit: int = Query(20, ge=1, le=100, description="返回条数"),
    include_expired: bool = Query(False, description="是否包含已超时的 checkpoint"),
):
    """Ref #42: 最近 checkpoint 列表（checkpoint_id/request_id/interrupt_reason/resume_deadline_at/是否已超时）。"""
    ledger = _get_ledger()
    now = _now_ms()

    try:
        rows = ledger.fetch_by_time_range("checkpoints", start_ms=0, end_ms=now)
    except Exception:
        rows = []

    # 兼容 graph_checkpoint 表名
    if not rows:
        try:
            rows = ledger.fetch_by_time_range("graph_checkpoint", start_ms=0, end_ms=now)
        except Exception:
            rows = []

    # 按创建时间倒序
    rows_sorted = sorted(rows, key=lambda r: r.get("created_at_ms") or r.get("ts_ms") or 0, reverse=True)

    items = []
    for r in rows_sorted:
        deadline = r.get("resume_deadline_at")
        is_expired = bool(deadline and int(deadline) < now)
        if not include_expired and is_expired:
            continue
        payload_raw = r.get("payload") or r.get("payload_json")
        payload_summary = None
        if payload_raw:
            try:
                ps = json.dumps(payload_raw) if not isinstance(payload_raw, str) else payload_raw
                payload_summary = ps[:120] + "…" if len(ps) > 120 else ps
            except Exception:
                pass
        items.append(CheckpointItem(
            checkpoint_id=str(r.get("checkpoint_id") or r.get("id") or ""),
            request_id=r.get("request_id"),
            interrupt_reason=r.get("interrupt_reason"),
            resume_deadline_at=int(deadline) if deadline else None,
            created_at_ms=int(r.get("created_at_ms") or r.get("ts_ms") or 0),
            is_expired=is_expired,
            payload_summary=payload_summary,
        ))
        if len(items) >= limit:
            break

    return CheckpointsResponse(total=len(items), items=items, timestamp=now)


@router.get("/checkpoints/{checkpoint_id}", response_model=CheckpointDetailResponse)
def get_checkpoint_detail(checkpoint_id: str):
    """Ref #42: 单个 checkpoint 详情（含完整 payload）。"""
    ledger = _get_ledger()
    now = _now_ms()

    rows = []
    for table in ("checkpoints", "graph_checkpoint"):
        try:
            all_rows = ledger.fetch_by_time_range(table, start_ms=0, end_ms=now)
            rows = [
                r for r in all_rows
                if str(r.get("checkpoint_id") or r.get("id") or "") == checkpoint_id
            ]
            if rows:
                break
        except Exception:
            continue

    if not rows:
        raise HTTPException(
            status_code=404,
            detail={"error": "checkpoint_not_found", "checkpoint_id": checkpoint_id},
        )

    r = rows[0]
    deadline = r.get("resume_deadline_at")
    is_expired = bool(deadline and int(deadline) < now)
    payload_raw = r.get("payload") or r.get("payload_json")
    payload = _safe_json(payload_raw) if payload_raw else None

    return CheckpointDetailResponse(
        checkpoint_id=checkpoint_id,
        request_id=r.get("request_id"),
        interrupt_reason=r.get("interrupt_reason"),
        resume_deadline_at=int(deadline) if deadline else None,
        created_at_ms=int(r.get("created_at_ms") or r.get("ts_ms") or 0),
        is_expired=is_expired,
        payload=payload,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #43: GET /obs/rate-limit/backend-stats  — RateLimitStore 后端状态
# ---------------------------------------------------------------------------
class RateLimitKeyItem(BaseModel):
    key: str
    current_count: int
    ttl_ms: Optional[int]
    window_ms: Optional[int]

class RateLimitBackendStatsResponse(BaseModel):
    backend_type: str          # "memory" | "sqlite" | "redis"
    is_degraded: bool
    degrade_reason: Optional[str]
    total_keys: int
    active_keys: int
    top_keys: List[RateLimitKeyItem]
    timestamp: int

@router.get("/rate-limit/backend-stats", response_model=RateLimitBackendStatsResponse)
def get_rate_limit_backend_stats(
    top_k: int = Query(20, ge=1, le=100, description="返回 Top-K 活跃 key"),
):
    """Ref #43: RateLimitStore 后端状态 — 当前后端类型/是否降级/各 key 当前计数/TTL。"""
    ledger = _get_ledger()
    now = _now_ms()

    # 尝试从 rate_limit_store_log 读取最新状态快照
    backend_type = "memory"
    is_degraded = False
    degrade_reason = None
    top_keys: List[RateLimitKeyItem] = []

    try:
        rows = ledger.fetch_by_time_range("rate_limit_store_log", start_ms=0, end_ms=now)
        if rows:
            latest = sorted(rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[0]
            backend_type = str(latest.get("backend_type") or "memory")
            is_degraded = bool(latest.get("is_degraded") or False)
            degrade_reason = latest.get("degrade_reason")
    except Exception:
        pass

    # 从 governor_log 中统计各 key 的速率限制计数（近 5 分钟）
    try:
        since_5m = now - 5 * 60 * 1000
        gov_rows = ledger.fetch_by_time_range("governor_log", start_ms=since_5m, end_ms=now)
        rl_rows = [
            r for r in gov_rows
            if str(r.get("reason_code") or "").upper().startswith("RATE_LIMIT")
        ]
        from collections import Counter
        key_counter = Counter(
            str(r.get("session_id") or r.get("wxid") or r.get("user_id") or "unknown")
            for r in rl_rows
        )
        top_keys = [
            RateLimitKeyItem(
                key=k,
                current_count=cnt,
                ttl_ms=None,
                window_ms=5 * 60 * 1000,
            )
            for k, cnt in key_counter.most_common(top_k)
        ]
    except Exception:
        pass

    total_keys = len(top_keys)
    active_keys = sum(1 for k in top_keys if k.current_count > 0)

    return RateLimitBackendStatsResponse(
        backend_type=backend_type,
        is_degraded=is_degraded,
        degrade_reason=degrade_reason,
        total_keys=total_keys,
        active_keys=active_keys,
        top_keys=top_keys,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #44: GET /obs/vault/stats  — ArtifactVault Manifest 统计
# ---------------------------------------------------------------------------
class VaultManifestItem(BaseModel):
    manifest_id: str
    artifact_type: Optional[str]
    status: str          # "ACTIVE" | "UNDONE" | "GC"
    created_at_ms: Optional[int]
    undone_at_ms: Optional[int]
    gc_at_ms: Optional[int]
    is_reversible: bool
    summary: Optional[str]

class VaultStatsResponse(BaseModel):
    total_manifests: int
    active_count: int
    undone_count: int
    gc_count: int
    reversible_count: int
    reversible_rate: Optional[float]
    recent_manifests: List[VaultManifestItem]
    timestamp: int

@router.get("/vault/stats", response_model=VaultStatsResponse)
def get_vault_stats(
    limit: int = Query(20, ge=1, le=100, description="最近 manifest 条数"),
):
    """Ref #44: ArtifactVault Manifest 统计 — ACTIVE/UNDONE/GC 数量、可逆比例、最近 manifest 列表。数据来自 vault_manifest_index。"""
    ledger = _get_ledger()
    now = _now_ms()

    rows = []
    try:
        # 表名为 vault_manifest_index（与 ledger_schema / _ALLOWED_TABLES 一致）
        rows = ledger.fetch_by_time_range(
            "vault_manifest_index",
            start_ms=0,
            end_ms=now,
            limit=10_000,
        )
    except Exception as e:
        logger.warning("get_vault_stats fetch vault_manifest_index failed: %s", e)

    total = len(rows)
    active_count = sum(1 for r in rows if str(r.get("status") or "").upper() == "ACTIVE")
    undone_count = sum(1 for r in rows if str(r.get("status") or "").upper() in ("UNDONE", "ROLLED_BACK"))
    gc_count = sum(1 for r in rows if str(r.get("status") or "").upper() in ("GC", "GARBAGE_COLLECTED", "DELETED"))
    reversible_count = sum(1 for r in rows if bool(r.get("is_reversible") or r.get("reversible")))
    reversible_rate = round(reversible_count / total * 100, 1) if total > 0 else None

    # 最近 N 条（按 ts_ms 倒序）；vault_manifest_index 字段: manifest_id, status, ts_ms, reversible, skill_name, manifest_path
    recent_sorted = sorted(rows, key=lambda r: int(r.get("ts_ms") or 0), reverse=True)[:limit]
    recent_manifests = [
        VaultManifestItem(
            manifest_id=str(r.get("manifest_id") or r.get("id") or ""),
            artifact_type=r.get("skill_name") or r.get("artifact_type"),
            status=str(r.get("status") or "UNKNOWN").upper(),
            created_at_ms=int(r.get("ts_ms") or 0) or None,
            undone_at_ms=int(r.get("undone_at_ms") or 0) or None,
            gc_at_ms=int(r.get("gc_at_ms") or 0) or None,
            is_reversible=bool(r.get("is_reversible") or r.get("reversible")),
            summary=(r.get("manifest_path") or r.get("skill_name") or r.get("summary") or "")[:120] or None,
        )
        for r in recent_sorted
    ]

    return VaultStatsResponse(
        total_manifests=total,
        active_count=active_count,
        undone_count=undone_count,
        gc_count=gc_count,
        reversible_count=reversible_count,
        reversible_rate=reversible_rate,
        recent_manifests=recent_manifests,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #47: GET /obs/llm/route-stats  — RoutingMatrix 路由决策统计
# ---------------------------------------------------------------------------
class RouteModelItem(BaseModel):
    model_target: str
    select_count: int
    fallback_count: int
    fallback_rate: Optional[float]

class RouteTaskTypeItem(BaseModel):
    task_type: str
    count: int
    pct: float

class LlmRouteStatsResponse(BaseModel):
    window_minutes: int
    total_decisions: int
    fallback_count: int
    fallback_rate: Optional[float]
    models: List[RouteModelItem]
    task_types: List[RouteTaskTypeItem]
    timestamp: int

@router.get("/llm/route-stats", response_model=LlmRouteStatsResponse)
def get_llm_route_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #47: RoutingMatrix 路由决策统计 — 按 model_id 分组的选择次数/降级次数/降级率/task_type 分布。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("llm_route_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    total = len(rows)
    fallback_count = sum(1 for r in rows if bool(r.get("is_fallback")))
    fallback_rate = round(fallback_count / total * 100, 1) if total > 0 else None

    # 按 model_target 分组
    from collections import Counter, defaultdict
    model_select: Counter = Counter()
    model_fallback: Counter = Counter()
    for r in rows:
        mid = str(r.get("selected_model") or r.get("model_target") or "unknown")
        model_select[mid] += 1
        if bool(r.get("is_fallback")):
            model_fallback[mid] += 1

    models = [
        RouteModelItem(
            model_target=mid,
            select_count=cnt,
            fallback_count=model_fallback.get(mid, 0),
            fallback_rate=round(model_fallback.get(mid, 0) / cnt * 100, 1) if cnt > 0 else None,
        )
        for mid, cnt in model_select.most_common()
    ]

    # task_type 分布
    task_counter: Counter = Counter(
        str(r.get("task_type") or "UNKNOWN").upper() for r in rows
    )
    task_types = [
        RouteTaskTypeItem(
            task_type=tt,
            count=cnt,
            pct=round(cnt / total * 100, 1) if total > 0 else 0.0,
        )
        for tt, cnt in task_counter.most_common(10)
    ]

    return LlmRouteStatsResponse(
        window_minutes=window_minutes,
        total_decisions=total,
        fallback_count=fallback_count,
        fallback_rate=fallback_rate,
        models=models,
        task_types=task_types,
        timestamp=now,
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


# ---------------------------------------------------------------------------
# Ref #50: GET /obs/ui-assertion/stats  — UIAssertionGuard 统计
# ---------------------------------------------------------------------------
class UIAssertionEventItem(BaseModel):
    audit_event: str
    count: int
    pct: float

class UIAssertionStatsResponse(BaseModel):
    window_minutes: int
    total_assertions: int
    sensitive_blocked: int
    precondition_failed: int
    postcondition_failed: int
    block_rate: Optional[float]
    top_events: List[UIAssertionEventItem]
    top_failed_reasons: List[Dict[str, Any]]
    timestamp: int

@router.get("/ui-assertion/stats", response_model=UIAssertionStatsResponse)
def get_ui_assertion_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #50: UIAssertionGuard 统计 — SENSITIVE_UI_BLOCKED/UI_PRECONDITION_FAILED/UI_POSTCONDITION_FAILED 分布。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # 从 error_log 中查询 UI 断言相关事件
    try:
        rows = ledger.fetch_by_time_range("error_log", start_ms=since_ms, end_ms=now)
        ui_rows = [
            r for r in rows
            if str(r.get("reason_code") or r.get("stage") or "").upper() in (
                "SENSITIVE_UI_BLOCKED",
                "UI_PRECONDITION_FAILED",
                "UI_POSTCONDITION_FAILED",
            )
        ]
    except Exception:
        ui_rows = []

    # 兼容 ui_assertion_log 表名
    if not ui_rows:
        try:
            ui_rows = ledger.fetch_by_time_range("ui_assertion_log", start_ms=since_ms, end_ms=now)
        except Exception:
            ui_rows = []

    total = len(ui_rows)
    sensitive_blocked = sum(
        1 for r in ui_rows
        if str(r.get("reason_code") or r.get("audit_event") or "").upper() == "SENSITIVE_UI_BLOCKED"
    )
    precondition_failed = sum(
        1 for r in ui_rows
        if str(r.get("reason_code") or r.get("audit_event") or "").upper() == "UI_PRECONDITION_FAILED"
    )
    postcondition_failed = sum(
        1 for r in ui_rows
        if str(r.get("reason_code") or r.get("audit_event") or "").upper() == "UI_POSTCONDITION_FAILED"
    )
    block_rate = round(sensitive_blocked / total * 100, 1) if total > 0 else None

    # 事件分布
    from collections import Counter
    event_counter = Counter(
        str(r.get("reason_code") or r.get("audit_event") or "UNKNOWN").upper()
        for r in ui_rows
    )
    top_events = [
        UIAssertionEventItem(
            audit_event=evt,
            count=cnt,
            pct=round(cnt / total * 100, 1) if total > 0 else 0.0,
        )
        for evt, cnt in event_counter.most_common(10)
    ]

    # 前置断言失败原因分布（从 detail 中解析 failed_assertions）
    failed_reason_counter: Counter = Counter()
    for r in ui_rows:
        if str(r.get("reason_code") or "").upper() != "UI_PRECONDITION_FAILED":
            continue
        try:
            detail_raw = r.get("detail") or "{}"
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
            for fa in (detail.get("failed_assertions") or []):
                failed_reason_counter[str(fa)] += 1
        except Exception:
            pass

    top_failed_reasons = [
        {"reason": reason, "count": cnt}
        for reason, cnt in failed_reason_counter.most_common(5)
    ]

    return UIAssertionStatsResponse(
        window_minutes=window_minutes,
        total_assertions=total,
        sensitive_blocked=sensitive_blocked,
        precondition_failed=precondition_failed,
        postcondition_failed=postcondition_failed,
        block_rate=block_rate,
        top_events=top_events,
        top_failed_reasons=top_failed_reasons,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #53: GET /obs/failure/stats  — FailureMatrix 失败事件统计
# ---------------------------------------------------------------------------
class FailureEventItem(BaseModel):
    event: str
    count: int
    pct: float
    degraded_mode: Optional[str]

class FailureStatsResponse(BaseModel):
    window_minutes: int
    total_failures: int
    abort_count: int
    degraded_count: int
    abort_rate: Optional[float]
    top_events: List[FailureEventItem]
    hourly_trend: List[Dict[str, Any]]
    timestamp: int

@router.get("/failure/stats", response_model=FailureStatsResponse)
def get_failure_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #53: FailureMatrix 失败事件统计 — 7 种失败事件分布、中止率、降级模式分布。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("failure_log", "error_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    # 过滤 FailureHandler 处理的事件（通过 audit_tag 前缀识别）
    _failure_events = {
        "GOVERNOR_BLOCK", "EVIDENCE_EMPTY", "MODEL_FALLBACK",
        "QA_REWRITE", "EDGE_UNREACHABLE", "VISION_LOW_CONFIDENCE", "MODULE_TIMEOUT",
    }
    failure_rows = [
        r for r in rows
        if str(r.get("reason_code") or r.get("event") or r.get("stage") or "").upper()
        in _failure_events
    ]

    total = len(failure_rows)
    from collections import Counter, defaultdict

    event_counter: Counter = Counter()
    degraded_mode_counter: Counter = Counter()
    abort_count = 0

    _abort_events = {"GOVERNOR_BLOCK", "VISION_LOW_CONFIDENCE", "MODULE_TIMEOUT"}
    _degraded_mode_map = {
        "EVIDENCE_EMPTY": "conservative_answer",
        "MODEL_FALLBACK": "fallback_model",
        "QA_REWRITE": "rewritten_output",
        "EDGE_UNREACHABLE": "text_only",
    }

    for r in failure_rows:
        evt = str(r.get("reason_code") or r.get("event") or r.get("stage") or "UNKNOWN").upper()
        event_counter[evt] += 1
        if evt in _abort_events:
            abort_count += 1
        mode = _degraded_mode_map.get(evt)
        if mode:
            degraded_mode_counter[mode] += 1

    degraded_count = sum(degraded_mode_counter.values())
    abort_rate = round(abort_count / total * 100, 1) if total > 0 else None

    top_events = [
        FailureEventItem(
            event=evt,
            count=cnt,
            pct=round(cnt / total * 100, 1) if total > 0 else 0.0,
            degraded_mode=_degraded_mode_map.get(evt),
        )
        for evt, cnt in event_counter.most_common(10)
    ]

    # 小时趋势（按小时分桶）
    hourly_buckets: dict = defaultdict(int)
    for r in failure_rows:
        ts = int(r.get("ts_ms") or r.get("created_at_ms") or 0)
        if ts > 0:
            hour_key = ts // (3600 * 1000)
            hourly_buckets[hour_key] += 1
    hourly_trend = [
        {"hour_ts_ms": h * 3600 * 1000, "count": c}
        for h, c in sorted(hourly_buckets.items())[-24:]
    ]

    return FailureStatsResponse(
        window_minutes=window_minutes,
        total_failures=total,
        abort_count=abort_count,
        degraded_count=degraded_count,
        abort_rate=abort_rate,
        top_events=top_events,
        hourly_trend=hourly_trend,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #54: GET /obs/permission/stats  — PermissionGuard 权限拒绝统计
# ---------------------------------------------------------------------------
class PermissionDeniedItem(BaseModel):
    tool_name: str
    deny_count: int
    pct: float
    proposer_class: Optional[str]

class PermissionStatsResponse(BaseModel):
    window_minutes: int
    total_checks: int
    allowed_count: int
    denied_count: int
    deny_rate: Optional[float]
    scheduler_denied_count: int
    top_denied_tools: List[PermissionDeniedItem]
    timestamp: int

@router.get("/permission/stats", response_model=PermissionStatsResponse)
def get_permission_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #54: PermissionGuard 权限拒绝统计 — 工具调用允许/拒绝分布、SCHEDULER 拒绝次数、Top 拒绝工具。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("permission_log", "audit_log", "error_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    # 过滤 PERMISSION_DENIED 事件
    denied_rows = [
        r for r in rows
        if "PERMISSION_DENIED" in str(r.get("audit_tag") or r.get("reason_code") or r.get("stage") or "").upper()
    ]
    allowed_rows = [
        r for r in rows
        if "PERMISSION_ALLOWED" in str(r.get("audit_tag") or r.get("stage") or "").upper()
    ]

    total_checks = len(denied_rows) + len(allowed_rows)
    denied_count = len(denied_rows)
    allowed_count = len(allowed_rows)
    deny_rate = round(denied_count / total_checks * 100, 1) if total_checks > 0 else None

    from collections import Counter
    tool_counter: Counter = Counter()
    scheduler_denied = 0
    proposer_map: dict = {}

    for r in denied_rows:
        detail_raw = r.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}
        tool_name = str(detail.get("tool") or r.get("tool_name") or "unknown")
        proposer = str(detail.get("proposer_class") or r.get("proposer_class") or "USER").upper()
        tool_counter[tool_name] += 1
        proposer_map[tool_name] = proposer
        if proposer == "SCHEDULER":
            scheduler_denied += 1

    top_denied_tools = [
        PermissionDeniedItem(
            tool_name=tool,
            deny_count=cnt,
            pct=round(cnt / denied_count * 100, 1) if denied_count > 0 else 0.0,
            proposer_class=proposer_map.get(tool),
        )
        for tool, cnt in tool_counter.most_common(10)
    ]

    return PermissionStatsResponse(
        window_minutes=window_minutes,
        total_checks=total_checks,
        allowed_count=allowed_count,
        denied_count=denied_count,
        deny_rate=deny_rate,
        scheduler_denied_count=scheduler_denied,
        top_denied_tools=top_denied_tools,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #55: GET /obs/scheduler/history  — TaskScheduler 执行历史趋势
# ---------------------------------------------------------------------------
class SchedulerTaskItem(BaseModel):
    task_id: str
    name: Optional[str]
    status: str
    run_count: int
    error_count: int
    error_rate: Optional[float]
    last_run_ts: Optional[float]
    last_error: Optional[str]
    last_reason_code: Optional[str]
    last_gate_state: Optional[str]
    last_mood_state: Optional[str]
    last_endocrine_cortisol: Optional[float]
    last_endocrine_energy: Optional[float]
    last_immune_decision: Optional[str]
    last_immune_risk_level: Optional[str]
    last_immune_reason_codes: List[str] = Field(default_factory=list)


class SchedulerReasonItem(BaseModel):
    reason_code: str
    count: int
    pct: float


class SchedulerGateItem(BaseModel):
    gate_state: str
    count: int
    pct: float

class SchedulerHistoryResponse(BaseModel):
    window_minutes: int
    total_tasks: int
    running_count: int
    success_count: int
    failed_count: int
    disabled_count: int
    overall_error_rate: Optional[float]
    gate_blocked_total: int
    gate_blocked_rate: Optional[float]
    blocked_reason_codes: List[SchedulerReasonItem]
    gate_states: List[SchedulerGateItem]
    tasks: List[SchedulerTaskItem]
    run_trend: List[Dict[str, Any]]
    timestamp: int

@router.get("/scheduler/history", response_model=SchedulerHistoryResponse)
def get_scheduler_history(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #55: TaskScheduler 执行历史趋势 — 任务状态分布、run_count/error_count 趋势。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("scheduler_log", "task_log", "audit_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    # 优先从 scheduler_routes 获取统一健康快照（包含 reason/gate 元数据）
    scheduler_tasks: list[dict] = []
    try:
        from host_brain.scheduler.scheduler_routes import get_scheduler_health_snapshot_safe  # noqa: PLC0415

        scheduler_snapshot = dict(get_scheduler_health_snapshot_safe() or {})
        scheduler_tasks = list(scheduler_snapshot.get("tasks") or [])
    except Exception:
        pass

    # 兼容旧版调度器快照来源
    if not scheduler_tasks:
        try:
            from host_brain.app.automation.task_scheduler import get_scheduler  # noqa: PLC0415

            _sched = get_scheduler()
            health = _sched.health()
            scheduler_tasks = health.get("tasks") or []
        except Exception:
            pass

    # 若无实时数据，从日志中聚合
    if not scheduler_tasks:
        from collections import defaultdict
        task_agg: dict = defaultdict(lambda: {
            "task_id": "", "name": None, "status": "UNKNOWN",
            "run_count": 0, "error_count": 0, "last_run_ts": None, "last_error": None,
        })
        for r in rows:
            tid = str(r.get("task_id") or r.get("id") or "unknown")
            task_agg[tid]["task_id"] = tid
            task_agg[tid]["name"] = r.get("name") or task_agg[tid]["name"]
            task_agg[tid]["status"] = str(r.get("status") or "UNKNOWN").upper()
            task_agg[tid]["run_count"] += int(r.get("run_count") or 1)
            task_agg[tid]["error_count"] += int(r.get("error_count") or 0)
            ts = r.get("last_run_ts") or r.get("ts_ms")
            if ts:
                task_agg[tid]["last_run_ts"] = float(ts)
            if r.get("last_error"):
                task_agg[tid]["last_error"] = str(r["last_error"])[:200]
        scheduler_tasks = list(task_agg.values())

    total_tasks = len(scheduler_tasks)
    running_count = sum(1 for t in scheduler_tasks if str(t.get("status") or "").upper() == "RUNNING")
    success_count = sum(1 for t in scheduler_tasks if str(t.get("status") or "").upper() == "SUCCESS")
    failed_count = sum(1 for t in scheduler_tasks if str(t.get("status") or "").upper() == "FAILED")
    disabled_count = sum(1 for t in scheduler_tasks if str(t.get("status") or "").upper() == "DISABLED")

    total_runs = sum(int(t.get("run_count") or 0) for t in scheduler_tasks)
    total_errors = sum(int(t.get("error_count") or 0) for t in scheduler_tasks)
    overall_error_rate = round(total_errors / total_runs * 100, 1) if total_runs > 0 else None

    tasks: list[SchedulerTaskItem] = []
    for t in scheduler_tasks:
        raw_immune_reason_codes = t.get("last_immune_reason_codes")
        if isinstance(raw_immune_reason_codes, (list, tuple, set)):
            immune_reason_codes = [
                str(code).strip().upper()[:80]
                for code in raw_immune_reason_codes
                if str(code).strip()
            ][:6]
        elif raw_immune_reason_codes:
            immune_reason_codes = [str(raw_immune_reason_codes).strip().upper()[:80]]
        else:
            immune_reason_codes = []

        tasks.append(
            SchedulerTaskItem(
                task_id=str(t.get("task_id") or ""),
                name=t.get("name"),
                status=str(t.get("status") or "UNKNOWN").upper(),
                run_count=int(t.get("run_count") or 0),
                error_count=int(t.get("error_count") or 0),
                error_rate=round(
                    int(t.get("error_count") or 0) / int(t.get("run_count") or 1) * 100, 1
                ) if int(t.get("run_count") or 0) > 0 else None,
                last_run_ts=float(t["last_run_ts"]) if t.get("last_run_ts") else None,
                last_error=str(t["last_error"])[:200] if t.get("last_error") else None,
                last_reason_code=(
                    str(t.get("last_reason_code") or "").strip()[:80] if t.get("last_reason_code") else None
                ),
                last_gate_state=(
                    str(t.get("last_gate_state") or "").strip().lower()[:32] if t.get("last_gate_state") else None
                ),
                last_mood_state=(
                    str(t.get("last_mood_state") or "").strip().lower()[:32] if t.get("last_mood_state") else None
                ),
                last_endocrine_cortisol=(
                    float(t["last_endocrine_cortisol"]) if t.get("last_endocrine_cortisol") is not None else None
                ),
                last_endocrine_energy=(
                    float(t["last_endocrine_energy"]) if t.get("last_endocrine_energy") is not None else None
                ),
                last_immune_decision=(
                    str(t.get("last_immune_decision") or "").strip().upper()[:16]
                    if t.get("last_immune_decision")
                    else None
                ),
                last_immune_risk_level=(
                    str(t.get("last_immune_risk_level") or "").strip().lower()[:16]
                    if t.get("last_immune_risk_level")
                    else None
                ),
                last_immune_reason_codes=immune_reason_codes,
            )
        )

    from collections import Counter

    reason_counter: Counter[str] = Counter()
    gate_counter: Counter[str] = Counter()
    for task in tasks:
        reason_code = str(task.last_reason_code or "").strip().upper()
        gate_state = str(task.last_gate_state or "").strip().lower()
        if reason_code:
            reason_counter[reason_code] += 1
        if gate_state:
            gate_counter[gate_state] += 1

    blocked_reason_total = sum(
        cnt
        for code, cnt in reason_counter.items()
        if code.endswith("_BLOCKED") or code in {"SYSMODE_FORBIDDEN", "IMMUNE_PROACTIVE_SUPPRESSED"}
    )
    gate_blocked_rate = round(blocked_reason_total / total_tasks * 100, 1) if total_tasks > 0 else None
    blocked_reason_codes = [
        SchedulerReasonItem(
            reason_code=code,
            count=cnt,
            pct=round(cnt / total_tasks * 100, 1) if total_tasks > 0 else 0.0,
        )
        for code, cnt in reason_counter.most_common(10)
        if code.endswith("_BLOCKED") or code in {"SYSMODE_FORBIDDEN", "IMMUNE_PROACTIVE_SUPPRESSED"}
    ]
    gate_states = [
        SchedulerGateItem(
            gate_state=gate_state,
            count=cnt,
            pct=round(cnt / total_tasks * 100, 1) if total_tasks > 0 else 0.0,
        )
        for gate_state, cnt in gate_counter.most_common(10)
    ]

    # 执行趋势（从日志按小时分桶）
    from collections import defaultdict
    hourly_runs: dict = defaultdict(int)
    hourly_errors: dict = defaultdict(int)
    for r in rows:
        ts = int(r.get("ts_ms") or r.get("last_run_ts") or 0)
        if ts > 0:
            hour_key = ts // (3600 * 1000)
            hourly_runs[hour_key] += int(r.get("run_count") or 1)
            hourly_errors[hour_key] += int(r.get("error_count") or 0)
    all_hours = sorted(set(list(hourly_runs.keys()) + list(hourly_errors.keys())))
    run_trend = [
        {
            "hour_ts_ms": h * 3600 * 1000,
            "run_count": hourly_runs.get(h, 0),
            "error_count": hourly_errors.get(h, 0),
        }
        for h in all_hours[-24:]
    ]

    return SchedulerHistoryResponse(
        window_minutes=window_minutes,
        total_tasks=total_tasks,
        running_count=running_count,
        success_count=success_count,
        failed_count=failed_count,
        disabled_count=disabled_count,
        overall_error_rate=overall_error_rate,
        gate_blocked_total=blocked_reason_total,
        gate_blocked_rate=gate_blocked_rate,
        blocked_reason_codes=blocked_reason_codes,
        gate_states=gate_states,
        tasks=tasks,
        run_trend=run_trend,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #56: GET /obs/action-plan/stats  — ActionPlan 执行成功率/指令类型分布
# ---------------------------------------------------------------------------
class ActionTypeItem(BaseModel):
    action_type: str
    count: int
    pct: float

class ActionPlanStatsResponse(BaseModel):
    window_minutes: int
    total_plans: int
    success_count: int
    fallback_count: int
    success_rate: Optional[float]
    avg_confidence: Optional[float]
    avg_action_count: Optional[float]
    top_action_types: List[ActionTypeItem]
    fallback_reasons: List[Dict[str, Any]]
    timestamp: int

@router.get("/action-plan/stats", response_model=ActionPlanStatsResponse)
def get_action_plan_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #56: ActionPlan 执行成功率/指令类型分布 — 成功率、平均置信度、指令类型分布、降级原因。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("action_plan_log", "vision_log", "audit_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    total = len(rows)
    if total == 0:
        return ActionPlanStatsResponse(
            window_minutes=window_minutes,
            total_plans=0,
            success_count=0,
            fallback_count=0,
            success_rate=None,
            avg_confidence=None,
            avg_action_count=None,
            top_action_types=[],
            fallback_reasons=[],
            timestamp=now,
        )

    from collections import Counter
    action_type_counter: Counter = Counter()
    fallback_reason_counter: Counter = Counter()
    confidence_list: list = []
    action_count_list: list = []
    success_count = 0
    fallback_count = 0

    for r in rows:
        detail_raw = r.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}

        # 判断是否降级计划
        extra = detail.get("extra") or {}
        is_fallback = bool(extra.get("fallback") or r.get("is_fallback"))
        if is_fallback:
            fallback_count += 1
            reason = str(extra.get("reason") or r.get("fallback_reason") or "UNKNOWN")
            fallback_reason_counter[reason] += 1
        else:
            success_count += 1

        # 置信度
        confidence = float(extra.get("confidence") or r.get("confidence") or 0.0)
        if confidence > 0:
            confidence_list.append(confidence)

        # 指令类型分布
        actions = detail.get("actions") or []
        action_count_list.append(len(actions))
        for action in actions:
            atype = str(action.get("type") or "unknown").lower()
            action_type_counter[atype] += 1

    success_rate = round(success_count / total * 100, 1) if total > 0 else None
    avg_confidence = round(sum(confidence_list) / len(confidence_list), 3) if confidence_list else None
    avg_action_count = round(sum(action_count_list) / len(action_count_list), 1) if action_count_list else None

    total_actions = sum(action_type_counter.values())
    top_action_types = [
        ActionTypeItem(
            action_type=atype,
            count=cnt,
            pct=round(cnt / total_actions * 100, 1) if total_actions > 0 else 0.0,
        )
        for atype, cnt in action_type_counter.most_common(10)
    ]

    fallback_reasons = [
        {"reason": reason, "count": cnt}
        for reason, cnt in fallback_reason_counter.most_common(5)
    ]

    return ActionPlanStatsResponse(
        window_minutes=window_minutes,
        total_plans=total,
        success_count=success_count,
        fallback_count=fallback_count,
        success_rate=success_rate,
        avg_confidence=avg_confidence,
        avg_action_count=avg_action_count,
        top_action_types=top_action_types,
        fallback_reasons=fallback_reasons,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #59: GET /obs/memory-guard/stats  — MemoryGuard 宪法校验统计
# ---------------------------------------------------------------------------
class MemoryGuardRejectItem(BaseModel):
    reason: str
    count: int
    pct: float

class MemoryGuardStatsResponse(BaseModel):
    window_minutes: int
    total_checks: int
    passed_count: int
    blocked_count: int
    block_rate: Optional[float]
    narrative_only_count: int
    dream_blocked_count: int
    distill_eligible_count: int
    top_reject_reasons: List[MemoryGuardRejectItem]
    timestamp: int

@router.get("/memory-guard/stats", response_model=MemoryGuardStatsResponse)
def get_memory_guard_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #59: MemoryGuard 宪法校验统计 — 过滤率/拒绝原因分布/NARRATIVE_ONLY 比例/蒸馏资格。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("memory_guard_log", "audit_log", "error_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    # 过滤 MemoryGuard 相关事件
    _mg_tags = {"MEMORY_GUARD_FILTERED", "MEMORY_GUARD_DREAM_BLOCKED",
                "MEMORY_GUARD_NARRATIVE_BLOCKED", "MEMORY_GUARD_DISTILL_ELIGIBLE",
                "EPISODIC_WRITTEN", "EPISODIC_SKIPPED"}
    mg_rows = [
        r for r in rows
        if any(tag in str(r.get("audit_tag") or r.get("stage") or r.get("reason_code") or "").upper()
               for tag in _mg_tags)
    ]

    # Supplement with memory_ops_log (actual write/skip records from MemoryGuard path)
    try:
        mem_ops = ledger.fetch_by_time_range("memory_ops_log", start_ms=since_ms, end_ms=now)
        if mem_ops:
            for row in mem_ops:
                op_kind = str(row.get("op_kind") or "").upper()
                status = str(row.get("status") or "").upper()
                if op_kind == "WRITE" and status == "SUCCESS":
                    mg_rows.append({**row, "audit_tag": "EPISODIC_WRITTEN"})
                elif op_kind == "WRITE" and status == "SKIPPED":
                    reason = str(row.get("reason_code") or "")
                    if "MEMORY_GUARD" in reason.upper():
                        mg_rows.append({**row, "audit_tag": "MEMORY_GUARD_FILTERED"})
                    else:
                        mg_rows.append({**row, "audit_tag": "EPISODIC_SKIPPED"})
    except Exception:
        pass

    from collections import Counter
    total = len(mg_rows)
    passed_count = sum(1 for r in mg_rows if "EPISODIC_WRITTEN" in str(r.get("audit_tag") or "").upper())
    blocked_count = sum(1 for r in mg_rows if any(
        t in str(r.get("audit_tag") or "").upper()
        for t in ("MEMORY_GUARD_FILTERED", "MEMORY_GUARD_DREAM_BLOCKED", "MEMORY_GUARD_NARRATIVE_BLOCKED")
    ))
    narrative_only_count = sum(1 for r in mg_rows if "NARRATIVE_BLOCKED" in str(r.get("audit_tag") or "").upper())
    dream_blocked_count = sum(1 for r in mg_rows if "DREAM_BLOCKED" in str(r.get("audit_tag") or "").upper())
    distill_eligible_count = sum(1 for r in mg_rows if "DISTILL_ELIGIBLE" in str(r.get("audit_tag") or "").upper())
    block_rate = round(blocked_count / total * 100, 1) if total > 0 else None

    reason_counter: Counter = Counter()
    for r in mg_rows:
        detail_raw = r.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}
        reason = str(detail.get("reason") or r.get("reason_code") or "UNKNOWN").upper()
        if reason != "UNKNOWN":
            reason_counter[reason] += 1

    top_reject_reasons = [
        MemoryGuardRejectItem(
            reason=reason,
            count=cnt,
            pct=round(cnt / blocked_count * 100, 1) if blocked_count > 0 else 0.0,
        )
        for reason, cnt in reason_counter.most_common(10)
    ]

    return MemoryGuardStatsResponse(
        window_minutes=window_minutes,
        total_checks=total,
        passed_count=passed_count,
        blocked_count=blocked_count,
        block_rate=block_rate,
        narrative_only_count=narrative_only_count,
        dream_blocked_count=dream_blocked_count,
        distill_eligible_count=distill_eligible_count,
        top_reject_reasons=top_reject_reasons,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #60: GET /obs/state-arbiter/stats  — StateArbiter 事实仲裁统计
# ---------------------------------------------------------------------------
class StateSourceItem(BaseModel):
    source: str
    hit_count: int
    pct: float

class StateArbiterStatsResponse(BaseModel):
    window_minutes: int
    total_resolutions: int
    conflict_count: int
    fallback_count: int
    conflict_rate: Optional[float]
    fallback_rate: Optional[float]
    session_override_count: int
    top_sources: List[StateSourceItem]
    top_conflict_keys: List[Dict[str, Any]]
    timestamp: int

@router.get("/state-arbiter/stats", response_model=StateArbiterStatsResponse)
def get_state_arbiter_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #60: StateArbiter 事实仲裁统计 — 冲突率/降级率/各源命中分布/SESSION_OVERRIDE 次数。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("state_arbiter_log", "audit_log", "error_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    # 过滤 StateArbiter 相关事件
    _sa_tags = {"STATE_SOURCE_CONFLICT", "STATE_SOURCE_FALLBACK",
                "STATE_ARBITER_CONFLICT", "SESSION_OVERRIDE_WRITTEN"}
    sa_rows = [
        r for r in rows
        if any(tag in str(r.get("audit_tag") or r.get("stage") or r.get("reason_code") or "").upper()
               for tag in _sa_tags)
    ]

    from collections import Counter
    total = len(sa_rows)
    conflict_count = sum(1 for r in sa_rows if "CONFLICT" in str(r.get("audit_tag") or "").upper())
    fallback_count = sum(1 for r in sa_rows if "FALLBACK" in str(r.get("audit_tag") or "").upper())
    session_override_count = sum(1 for r in sa_rows if "SESSION_OVERRIDE" in str(r.get("audit_tag") or "").upper())
    conflict_rate = round(conflict_count / total * 100, 1) if total > 0 else None
    fallback_rate = round(fallback_count / total * 100, 1) if total > 0 else None

    source_counter: Counter = Counter()
    conflict_key_counter: Counter = Counter()

    for r in sa_rows:
        detail_raw = r.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}
        winner_source = str(detail.get("winner_source") or r.get("source") or "UNKNOWN").upper()
        if winner_source != "UNKNOWN":
            source_counter[winner_source] += 1
        fact_key = str(detail.get("fact_key") or r.get("fact_key") or "")
        if fact_key and "CONFLICT" in str(r.get("audit_tag") or "").upper():
            conflict_key_counter[fact_key] += 1

    total_hits = sum(source_counter.values())
    top_sources = [
        StateSourceItem(
            source=src,
            hit_count=cnt,
            pct=round(cnt / total_hits * 100, 1) if total_hits > 0 else 0.0,
        )
        for src, cnt in source_counter.most_common(10)
    ]

    top_conflict_keys = [
        {"fact_key": key, "conflict_count": cnt}
        for key, cnt in conflict_key_counter.most_common(5)
    ]

    return StateArbiterStatsResponse(
        window_minutes=window_minutes,
        total_resolutions=total,
        conflict_count=conflict_count,
        fallback_count=fallback_count,
        conflict_rate=conflict_rate,
        fallback_rate=fallback_rate,
        session_override_count=session_override_count,
        top_sources=top_sources,
        top_conflict_keys=top_conflict_keys,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #61: GET /obs/context-compressor/stats  — ContextCompressor 压缩效率统计
# ---------------------------------------------------------------------------
class ContextCompressorStatsResponse(BaseModel):
    window_minutes: int
    total_compressions: int
    active_count: int
    degraded_count: int
    avg_compression_ratio: Optional[float]
    avg_token_saving: Optional[float]
    total_tokens_saved: int
    strategy_distribution: List[Dict[str, Any]]
    summary_generated_count: int
    timestamp: int

@router.get("/context-compressor/stats", response_model=ContextCompressorStatsResponse)
def get_context_compressor_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #61: ContextCompressor 压缩效率统计 — 压缩率/token 节省/策略分布/摘要生成次数。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("context_compressor_log", "audit_log", "error_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    # 过滤 ContextCompressor 相关事件
    cc_rows = [
        r for r in rows
        if "CTX_COMPRESSED" in str(r.get("audit_tag") or r.get("stage") or "").upper()
        or "CONTEXT_COMPRESSOR" in str(r.get("stage") or r.get("reason_code") or "").upper()
    ]

    from collections import Counter
    total = len(cc_rows)
    active_count = sum(1 for r in cc_rows if "CTX_COMPRESSED" in str(r.get("audit_tag") or "").upper())
    degraded_count = sum(1 for r in cc_rows if "DEGRADED" in str(r.get("audit_tag") or r.get("stage") or "").upper())

    compression_ratios: list = []
    token_savings: list = []
    total_tokens_saved = 0
    strategy_counter: Counter = Counter()
    summary_generated_count = 0

    for r in cc_rows:
        detail_raw = r.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}

        input_turns = int(detail.get("input_turns") or r.get("input_turns") or 0)
        output_turns = int(detail.get("output_turns") or r.get("output_turns") or 0)
        total_tokens = int(detail.get("total_tokens") or r.get("total_tokens") or 0)
        strategy = str(detail.get("strategy") or r.get("strategy") or "unknown").lower()
        has_summary = bool(detail.get("has_summary") or r.get("has_summary"))

        if input_turns > 0 and output_turns > 0:
            ratio = round(output_turns / input_turns, 3)
            compression_ratios.append(ratio)
            saved = input_turns - output_turns
            token_savings.append(saved)
            total_tokens_saved += saved

        if strategy and strategy != "unknown":
            strategy_counter[strategy] += 1
        if has_summary:
            summary_generated_count += 1

    avg_compression_ratio = round(sum(compression_ratios) / len(compression_ratios), 3) if compression_ratios else None
    avg_token_saving = round(sum(token_savings) / len(token_savings), 1) if token_savings else None

    total_strategies = sum(strategy_counter.values())
    strategy_distribution = [
        {
            "strategy": strategy,
            "count": cnt,
            "pct": round(cnt / total_strategies * 100, 1) if total_strategies > 0 else 0.0,
        }
        for strategy, cnt in strategy_counter.most_common(5)
    ]

    return ContextCompressorStatsResponse(
        window_minutes=window_minutes,
        total_compressions=total,
        active_count=active_count,
        degraded_count=degraded_count,
        avg_compression_ratio=avg_compression_ratio,
        avg_token_saving=avg_token_saving,
        total_tokens_saved=total_tokens_saved,
        strategy_distribution=strategy_distribution,
        summary_generated_count=summary_generated_count,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #65: GET /obs/prompt-builder/stats  — PromptBuilder 构建统计
# ---------------------------------------------------------------------------
class PromptBuilderStatsResponse(BaseModel):
    window_minutes: int
    total_builds: int
    injection_detected_count: int
    injection_blocked_count: int
    injection_detection_rate: Optional[float]
    high_risk_count: int
    critical_risk_count: int
    slot_fill_distribution: List[Dict[str, Any]]
    avg_user_message_len: Optional[float]
    degraded_count: int
    timestamp: int

@router.get("/prompt-builder/stats", response_model=PromptBuilderStatsResponse)
def get_prompt_builder_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #65: PromptBuilder 构建统计 — 注入检测率/slot 填充分布/高风险消息比例。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("prompt_builder_log", "audit_log", "error_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    # 过滤 PromptBuilder 相关事件
    pb_rows = [
        r for r in rows
        if any(tag in str(r.get("audit_tag") or r.get("stage") or r.get("reason_code") or "").upper()
               for tag in ("PROMPT_BUILT", "PROMPT_INJECTION", "PROMPT_BUILDER", "INJECTION_DETECTED"))
    ]

    from collections import Counter
    total = len(pb_rows)
    injection_detected_count = sum(1 for r in pb_rows if "INJECTION_DETECTED" in str(r.get("audit_tag") or "").upper())
    injection_blocked_count = sum(1 for r in pb_rows if "INJECTION_BLOCKED" in str(r.get("audit_tag") or "").upper())
    injection_detection_rate = round(injection_detected_count / total * 100, 1) if total > 0 else None
    degraded_count = sum(1 for r in pb_rows if "DEGRADED" in str(r.get("audit_tag") or r.get("stage") or "").upper())

    high_risk_count = 0
    critical_risk_count = 0
    slot_counter: Counter = Counter()
    msg_lens: list = []

    for r in pb_rows:
        detail_raw = r.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}

        risk_level = str(detail.get("risk_level") or r.get("risk_level") or "").upper()
        if risk_level == "HIGH":
            high_risk_count += 1
        elif risk_level == "CRITICAL":
            critical_risk_count += 1

        # slot 填充统计
        slots_filled = list(detail.get("slots_filled") or r.get("slots_filled") or [])
        for slot in slots_filled:
            slot_counter[str(slot)] += 1

        # 用户消息长度
        msg_len = int(detail.get("user_message_len") or r.get("user_message_len") or 0)
        if msg_len > 0:
            msg_lens.append(msg_len)

    avg_user_message_len = round(sum(msg_lens) / len(msg_lens), 1) if msg_lens else None
    total_slot_fills = sum(slot_counter.values())
    slot_fill_distribution = [
        {
            "slot": slot,
            "count": cnt,
            "pct": round(cnt / total_slot_fills * 100, 1) if total_slot_fills > 0 else 0.0,
        }
        for slot, cnt in slot_counter.most_common(10)
    ]

    return PromptBuilderStatsResponse(
        window_minutes=window_minutes,
        total_builds=total,
        injection_detected_count=injection_detected_count,
        injection_blocked_count=injection_blocked_count,
        injection_detection_rate=injection_detection_rate,
        high_risk_count=high_risk_count,
        critical_risk_count=critical_risk_count,
        slot_fill_distribution=slot_fill_distribution,
        avg_user_message_len=avg_user_message_len,
        degraded_count=degraded_count,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #66: GET /obs/episodic-store/stats  — EpisodicStore 写入统计
# ---------------------------------------------------------------------------
class EpisodicStoreStatsResponse(BaseModel):
    window_minutes: int
    total_attempts: int
    written_count: int
    skipped_count: int
    write_rate: Optional[float]
    memory_guard_blocked_count: int
    dream_blocked_count: int
    narrative_only_blocked_count: int
    distill_eligible_count: int
    avg_occurrence_count: Optional[float]
    timestamp: int

@router.get("/episodic-store/stats", response_model=EpisodicStoreStatsResponse)
def get_episodic_store_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #66: EpisodicStore 写入统计 — 写入率/MemoryGuard 拦截率/蒸馏资格率/dream 拦截次数。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    for table in ("episodic_store_log", "audit_log", "error_log"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    # 过滤 EpisodicStore 相关事件
    _ep_tags = {"EPISODIC_WRITTEN", "EPISODIC_SKIPPED", "MEMORY_GUARD_DREAM_BLOCKED",
                "MEMORY_GUARD_NARRATIVE_BLOCKED", "MEMORY_GUARD_DISTILL_ELIGIBLE",
                "SKIPPED_MEMORY_GUARD"}
    ep_rows = [
        r for r in rows
        if any(tag in str(r.get("audit_tag") or r.get("stage") or r.get("reason_code") or "").upper()
               for tag in _ep_tags)
    ]

    # Supplement with memory_ops_log (actual episodic write records)
    try:
        mem_ops = ledger.fetch_by_time_range("memory_ops_log", start_ms=since_ms, end_ms=now)
        if mem_ops:
            # Map memory_ops SUCCESS rows to EPISODIC_WRITTEN synthetic tags
            for row in mem_ops:
                if str(row.get("op_kind") or "").upper() == "WRITE" and str(row.get("status") or "").upper() == "SUCCESS":
                    ep_rows.append({**row, "audit_tag": "EPISODIC_WRITTEN"})
                elif str(row.get("op_kind") or "").upper() == "WRITE" and str(row.get("status") or "").upper() == "SKIPPED":
                    ep_rows.append({**row, "audit_tag": "EPISODIC_SKIPPED"})
    except Exception:
        pass

    total = len(ep_rows)
    written_count = sum(1 for r in ep_rows if "EPISODIC_WRITTEN" in str(r.get("audit_tag") or "").upper())
    skipped_count = sum(1 for r in ep_rows if any(
        t in str(r.get("audit_tag") or r.get("stage") or "").upper()
        for t in ("EPISODIC_SKIPPED", "SKIPPED_MEMORY_GUARD")
    ))
    memory_guard_blocked_count = sum(1 for r in ep_rows if any(
        t in str(r.get("audit_tag") or "").upper()
        for t in ("MEMORY_GUARD_DREAM_BLOCKED", "MEMORY_GUARD_NARRATIVE_BLOCKED", "MEMORY_GUARD_FILTERED")
    ))
    dream_blocked_count = sum(1 for r in ep_rows if "DREAM_BLOCKED" in str(r.get("audit_tag") or "").upper())
    narrative_only_blocked_count = sum(1 for r in ep_rows if "NARRATIVE_BLOCKED" in str(r.get("audit_tag") or "").upper())
    distill_eligible_count = sum(1 for r in ep_rows if "DISTILL_ELIGIBLE" in str(r.get("audit_tag") or "").upper())
    write_rate = round(written_count / total * 100, 1) if total > 0 else None

    occurrence_counts: list = []
    for r in ep_rows:
        detail_raw = r.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}
        occ = int(detail.get("occurrence_count") or r.get("occurrence_count") or 0)
        if occ > 0:
            occurrence_counts.append(occ)

    avg_occurrence_count = round(sum(occurrence_counts) / len(occurrence_counts), 1) if occurrence_counts else None

    return EpisodicStoreStatsResponse(
        window_minutes=window_minutes,
        total_attempts=total,
        written_count=written_count,
        skipped_count=skipped_count,
        write_rate=write_rate,
        memory_guard_blocked_count=memory_guard_blocked_count,
        dream_blocked_count=dream_blocked_count,
        narrative_only_blocked_count=narrative_only_blocked_count,
        distill_eligible_count=distill_eligible_count,
        avg_occurrence_count=avg_occurrence_count,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# GET /obs/chain/stats — Ref #82: 请求处理阶段统计（Phase XIV v4）
# ---------------------------------------------------------------------------

class ChainNodeStatItem(BaseModel):
    node_name: str
    total_calls: int
    degraded_count: int
    degraded_rate: float          # degraded / total * 100
    avg_latency_ms: Optional[float]
    p95_latency_ms: Optional[float]
    timeout_count: int


class ChainStatsResponse(BaseModel):
    window_minutes: int
    total_requests: int
    governor_block_count: int
    governor_block_rate: float
    recall_degraded_count: int
    episodic_written_count: int
    qa_rewrite_count: int
    canon_proposal_count: int
    node_stats: List[ChainNodeStatItem]
    timestamp: int


@router.get("/chain/stats", response_model=ChainStatsResponse)
def get_chain_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #82: 请求处理阶段统计（Phase XIV v4 cortex_orchestrator 架构）。

    从 audit_log（AUDIT_SNAPSHOT / COMMIT 阶段）中提取 audit_tags 和 node_timings，
    聚合各阶段的调用次数、降级率、平均耗时、P95 耗时、超时次数。

    Phase XIV v4 变更：
      - 旧固定 10 节点链路已废弃
      - 新架构阶段：airlock → governor → retrieval → prompt_builder → llm_stream
        → response_intelligence → airlock_output → episodic_store
      - 委托路径新增 sub_agent 阶段
      - 保留动态发现：audit_log 中出现的未预定义节点也会被统计
    """
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # 从 audit_log 中拉取 AUDIT_SNAPSHOT / COMMIT 记录
    try:
        rows = ledger.fetch_by_time_range("audit_log", start_ms=since_ms, end_ms=now)
        audit_rows = [
            r for r in rows
            if str(r.get("stage") or "").upper() in ("AUDIT_SNAPSHOT", "COMMIT")
        ]
    except Exception:
        audit_rows = []

    total_requests = len(audit_rows)

    # 解析 audit_tags 和 node_timings
    governor_block_count = 0
    recall_degraded_count = 0
    episodic_written_count = 0
    qa_rewrite_count = 0
    canon_proposal_count = 0

    # 节点耗时收集：node_name → [latency_ms, ...]
    node_latencies: dict[str, list[float]] = {}
    node_degraded: dict[str, int] = {}
    node_timeout: dict[str, int] = {}
    node_calls: dict[str, int] = {}

    # Phase XIV v4 cortex_orchestrator 架构阶段名（预定义，保证前端有数据）
    _KNOWN_NODES = [
        "airlock",              # 入口净化（含 PII 过滤）
        "governor",             # 轻量 Governor（速率限制 + sys_mode）
        "retrieval",            # RecallFusion 五路检索 + StateArbiter(bond)
        "prompt_builder",       # 完整 prompt 管线（20+ 槽位）
        "llm_stream",           # LLM 流式生成 + SentenceSplitter
        "response_intelligence",  # 反思链 + AI 话术清理 + 多模态后缀
        "sub_agent",            # 子 Agent 委托执行（code/translate/intent/vision）
        "airlock_output",       # 出口净化（含 PII 过滤）
        "episodic_store",       # 记忆写入（EpisodicStore + Bond）
    ]
    for n in _KNOWN_NODES:
        node_latencies[n] = []
        node_degraded[n] = 0
        node_timeout[n] = 0
        node_calls[n] = 0

    for row in audit_rows:
        detail_raw = row.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}

        audit_tags = list(detail.get("audit_tags") or [])
        node_timings_ms = dict(detail.get("node_timings_ms") or {})

        # 全局统计
        if any("GOVERNOR_BLOCK" in t or "GOVERNOR_REJECT" in t for t in audit_tags):
            governor_block_count += 1
        if any("RECALL_DEGRADED" in t or "RECALL_CIRCUIT_OPEN" in t for t in audit_tags):
            recall_degraded_count += 1
        if "EPISODIC_WRITTEN" in audit_tags:
            episodic_written_count += 1
        if any("QA_REWRITE" in t for t in audit_tags):
            qa_rewrite_count += 1
        if any("CANON_PROPOSAL_SUBMITTED" in t for t in audit_tags):
            canon_proposal_count += 1

        # 节点耗时
        for node_name, latency_ms in node_timings_ms.items():
            if node_name not in node_latencies:
                node_latencies[node_name] = []
                node_degraded[node_name] = 0
                node_timeout[node_name] = 0
                node_calls[node_name] = 0
            node_calls[node_name] += 1
            if isinstance(latency_ms, (int, float)) and latency_ms > 0:
                node_latencies[node_name].append(float(latency_ms))

        # 降级/超时标记
        for tag in audit_tags:
            if "DEGRADED_FALLBACK" in tag or "DEGRADED" in tag:
                for n in _KNOWN_NODES:
                    if n.upper().replace("_", "") in tag.upper().replace("_", ""):
                        node_degraded[n] += 1
                        break
            if "MODULE_TIMEOUT" in tag:
                for n in _KNOWN_NODES:
                    if n.upper().replace("_", "") in tag.upper().replace("_", ""):
                        node_timeout[n] += 1
                        break

    # 构建节点统计列表
    node_stats = []
    for node_name in _KNOWN_NODES:
        calls = node_calls.get(node_name, 0)
        latencies = node_latencies.get(node_name, [])
        degraded = node_degraded.get(node_name, 0)
        timeout = node_timeout.get(node_name, 0)

        avg_lat = round(sum(latencies) / len(latencies), 1) if latencies else None
        p95_lat = None
        if len(latencies) >= 5:
            sorted_lat = sorted(latencies)
            p95_idx = int(len(sorted_lat) * 0.95)
            p95_lat = round(sorted_lat[min(p95_idx, len(sorted_lat) - 1)], 1)

        node_stats.append(ChainNodeStatItem(
            node_name=node_name,
            total_calls=calls,
            degraded_count=degraded,
            degraded_rate=round(degraded / calls * 100, 1) if calls > 0 else 0.0,
            avg_latency_ms=avg_lat,
            p95_latency_ms=p95_lat,
            timeout_count=timeout,
        ))

    governor_block_rate = round(governor_block_count / total_requests * 100, 1) if total_requests > 0 else 0.0

    return ChainStatsResponse(
        window_minutes=window_minutes,
        total_requests=total_requests,
        governor_block_count=governor_block_count,
        governor_block_rate=governor_block_rate,
        recall_degraded_count=recall_degraded_count,
        episodic_written_count=episodic_written_count,
        qa_rewrite_count=qa_rewrite_count,
        canon_proposal_count=canon_proposal_count,
        node_stats=node_stats,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# GET /obs/agent-loop/history — Ref #86: Agent Loop 历史执行记录
# ---------------------------------------------------------------------------

class AgentLoopHistoryItem(BaseModel):
    request_id: str
    goal: Optional[str]
    final_status: Optional[str]
    goal_achieved: Optional[bool]
    total_rounds: Optional[int]
    total_latency_ms: Optional[int]
    ts_ms: Optional[int]
    client_mode: Optional[str]


class AgentLoopHistoryResponse(BaseModel):
    items: List[AgentLoopHistoryItem]
    total: int
    window_minutes: int
    timestamp: int


@router.get("/agent-loop/history", response_model=AgentLoopHistoryResponse)
def get_agent_loop_history(
    window_minutes: int = Query(1440, ge=1, le=10080, description="统计窗口（分钟）"),
    limit: int = Query(10, ge=1, le=50, description="最大返回条数"),
):
    """Ref #86: Agent Loop 历史执行记录（最近 N 次执行结果）。

    从 audit_log 中拉取 LOOP_DONE 阶段的记录，返回最近 limit 条。
    """
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("audit_log", start_ms=since_ms, end_ms=now)
        loop_rows = [r for r in rows if str(r.get("stage") or "").upper() == "LOOP_DONE"]
    except Exception:
        loop_rows = []

    # 按时间倒序，取最近 limit 条
    loop_rows.sort(key=lambda r: r.get("ts_ms") or 0, reverse=True)
    loop_rows = loop_rows[:limit]

    items = []
    for row in loop_rows:
        detail_raw = row.get("detail") or "{}"
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
        except Exception:
            detail = {}

        items.append(AgentLoopHistoryItem(
            request_id=str(row.get("request_id") or ""),
            goal=detail.get("goal") or row.get("goal"),
            final_status=detail.get("final_status") or row.get("final_status"),
            goal_achieved=detail.get("goal_achieved"),
            total_rounds=detail.get("total_rounds"),
            total_latency_ms=detail.get("total_latency_ms"),
            ts_ms=row.get("ts_ms"),
            client_mode=detail.get("client_mode"),
        ))

    return AgentLoopHistoryResponse(
        items=items,
        total=len(items),
        window_minutes=window_minutes,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# GET /obs/error-heatmap — Ref #88: 错误热力图端点（按小时/节点分布）
# ---------------------------------------------------------------------------

class ErrorHeatmapCell(BaseModel):
    hour: int                    # 0-23
    node_name: str
    error_count: int
    pct: float                   # 占该小时总错误的百分比


class ErrorHeatmapResponse(BaseModel):
    window_hours: int
    total_errors: int
    hours: List[int]             # 出现过错误的小时列表（0-23）
    nodes: List[str]             # 出现过错误的节点列表
    cells: List[ErrorHeatmapCell]
    hourly_totals: List[Dict[str, Any]]   # [{ hour, total }]
    node_totals: List[Dict[str, Any]]     # [{ node_name, total }]
    timestamp: int


@router.get("/error-heatmap", response_model=ErrorHeatmapResponse)
def get_error_heatmap(
    window_hours: int = Query(24, ge=1, le=168, description="统计窗口（小时），最大 7 天"),
):
    """Ref #88: 错误热力图端点 — 按小时 × 节点维度统计错误分布。

    从 error_log 中拉取近 window_hours 小时的错误记录，
    按 (hour, node_name) 聚合，返回热力图数据。
    """
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_hours * 3600 * 1000

    try:
        rows = ledger.fetch_by_time_range("error_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    # 按 (hour, node_name) 聚合
    from collections import defaultdict
    import datetime as _dt

    cell_counts: dict[tuple[int, str], int] = defaultdict(int)
    hourly_counts: dict[int, int] = defaultdict(int)
    node_counts: dict[str, int] = defaultdict(int)

    for row in rows:
        ts = row.get("ts_ms") or 0
        if ts <= 0:
            continue
        hour = _dt.datetime.fromtimestamp(ts / 1000).hour
        node_name = str(
            row.get("node_id") or row.get("stage") or row.get("source") or "unknown"
        )
        cell_counts[(hour, node_name)] += 1
        hourly_counts[hour] += 1
        node_counts[node_name] += 1

    total_errors = sum(hourly_counts.values())
    hours_list = sorted(hourly_counts.keys())
    nodes_list = sorted(node_counts.keys(), key=lambda n: node_counts[n], reverse=True)[:20]

    cells = []
    for (hour, node_name), cnt in cell_counts.items():
        if node_name not in nodes_list:
            continue
        hour_total = hourly_counts.get(hour, 1)
        cells.append(ErrorHeatmapCell(
            hour=hour,
            node_name=node_name,
            error_count=cnt,
            pct=round(cnt / hour_total * 100, 1),
        ))

    hourly_totals = [{"hour": h, "total": hourly_counts[h]} for h in range(24)]
    node_totals = [{"node_name": n, "total": node_counts[n]} for n in nodes_list]

    return ErrorHeatmapResponse(
        window_hours=window_hours,
        total_errors=total_errors,
        hours=hours_list,
        nodes=nodes_list,
        cells=cells,
        hourly_totals=hourly_totals,
        node_totals=node_totals,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #103: GET /obs/semantic-recall/stats — 语义召回器统计
# ---------------------------------------------------------------------------

@dataclass
class SemanticRecallStatsResponse:
    """语义召回器统计响应。"""
    use_mock: bool
    chroma_host: str
    chroma_port: int
    top_k: int
    similarity_threshold: float
    collection_name: str
    mock_doc_count: int
    """Mock 后端中的文档数量（use_mock=True 时有效）"""
    status: str
    """OK / DEGRADED"""
    timestamp: int


@router.get("/semantic-recall/stats")
def get_semantic_recall_stats():
    """Ref #103: 语义召回器统计端点 — 返回 SemanticRecaller 的配置和状态信息。"""
    now = _now_ms()
    try:
        from host_brain.app.dependencies import get_semantic_recaller  # noqa: PLC0415
        recaller = get_semantic_recaller()

        mock_doc_count = len(getattr(recaller, "_mock_store", []))
        return SemanticRecallStatsResponse(
            use_mock=recaller._use_mock,
            chroma_host=recaller._chroma_host,
            chroma_port=recaller._chroma_port,
            top_k=recaller._top_k,
            similarity_threshold=recaller._threshold,
            collection_name=recaller._collection_name,
            mock_doc_count=mock_doc_count,
            status="OK",
            timestamp=now,
        )
    except Exception as exc:
        return SemanticRecallStatsResponse(
            use_mock=True,
            chroma_host="localhost",
            chroma_port=8000,
            top_k=5,
            similarity_threshold=0.70,
            collection_name="episodic_memory",
            mock_doc_count=0,
            status=f"DEGRADED: {exc}",
            timestamp=now,
        )


# ---------------------------------------------------------------------------
# GET /obs/retrieval/runtime — 当前检索 embedding/rerank 配置 + 最近实际生效模型/降级情况
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# GET /obs/immune-gate/stats
# ---------------------------------------------------------------------------
class ImmuneGateReasonItem(BaseModel):
    reason_code: str
    count: int
    pct: float


class ImmuneGateRiskItem(BaseModel):
    risk_level: str
    count: int
    pct: float


class ImmuneGateRecentItem(BaseModel):
    request_id: str
    session_id: Optional[str] = None
    event_type: str
    decision: str
    risk_level: Optional[str] = None
    score: Optional[float] = None
    reason_codes: List[str] = Field(default_factory=list)
    detail: Optional[str] = None
    ts_ms: int


class ImmuneGateReplayDecisionItem(BaseModel):
    event_type: str
    stage: Optional[str] = None
    direction: Optional[str] = None
    phase: Optional[str] = None
    decision: str
    risk_level: Optional[str] = None
    score: Optional[float] = None
    reason_codes: List[str] = Field(default_factory=list)
    matched_rules: List[str] = Field(default_factory=list)
    input_preview: Optional[str] = None
    detail: Optional[str] = None
    ts_ms: int


class ImmuneGateReplaySummary(BaseModel):
    event_count: int
    first_decision: Optional[str] = None
    last_decision: Optional[str] = None
    max_score: Optional[float] = None
    reason_codes: List[str] = Field(default_factory=list)
    matched_rules: List[str] = Field(default_factory=list)


class ImmuneGateReplayResponse(BaseModel):
    request_id: str
    session_id: Optional[str] = None
    input_preview: Optional[str] = None
    decisions: List[ImmuneGateReplayDecisionItem]
    summary: ImmuneGateReplaySummary
    timestamp: int


class ImmuneGateRuntimeState(BaseModel):
    status: str
    enabled: Optional[bool] = None
    blocking_enabled: Optional[bool] = None
    warn_threshold: Optional[float] = None
    block_threshold: Optional[float] = None
    template_id: Optional[str] = None
    template_rule_count: int = 0
    total_rule_count: int = 0
    counters: Dict[str, int] = Field(default_factory=dict)
    last_decision: Optional[str] = None
    last_risk_level: Optional[str] = None
    last_score: Optional[float] = None
    last_reason_codes: List[str] = Field(default_factory=list)
    detail: Optional[str] = None


class ImmuneGateStatsResponse(BaseModel):
    window_minutes: int
    total: int
    allow: int
    report: int
    block: int
    block_rate: float
    reason_codes: List[ImmuneGateReasonItem]
    risk_levels: List[ImmuneGateRiskItem]
    recent: List[ImmuneGateRecentItem]
    runtime_state: ImmuneGateRuntimeState
    timestamp: int


def _build_immune_gate_runtime_state() -> ImmuneGateRuntimeState:
    try:
        from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415

        raw = dict(get_immune_agent().export_runtime_state() or {})
        counters = dict(raw.get("counters") or {})
        last = dict(raw.get("last_inspection") or {})
        return ImmuneGateRuntimeState(
            status="live",
            enabled=bool(raw.get("enabled", True)),
            blocking_enabled=bool(raw.get("blocking_enabled", False)),
            warn_threshold=(float(raw.get("warn_threshold")) if raw.get("warn_threshold") is not None else None),
            block_threshold=(float(raw.get("block_threshold")) if raw.get("block_threshold") is not None else None),
            template_id=(str(raw.get("template_id") or "").strip() or None),
            template_rule_count=int(raw.get("template_rule_count") or 0),
            total_rule_count=int(raw.get("total_rule_count") or 0),
            counters={
                "total": int(counters.get("total") or 0),
                "allow": int(counters.get("allow") or 0),
                "report": int(counters.get("report") or 0),
                "block": int(counters.get("block") or 0),
            },
            last_decision=(str(last.get("decision") or "").strip().upper() or None),
            last_risk_level=(str(last.get("risk_level") or "").strip().lower() or None),
            last_score=(float(last.get("score")) if last.get("score") is not None else None),
            last_reason_codes=[str(code).strip() for code in list(last.get("reason_codes") or []) if str(code).strip()],
        )
    except Exception as exc:
        return ImmuneGateRuntimeState(
            status="degraded",
            detail=str(exc)[:160],
        )


def _extract_immune_gate_event_fields(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event_type = str(row.get("event_type") or "").strip().upper()
    payload = _safe_json(row.get("payload"))
    payload_dict = dict(payload) if isinstance(payload, dict) else {}

    source_payload: Dict[str, Any] | None = None
    if event_type == "IMMUNE_CHAT_GATE":
        source_payload = payload_dict
    elif event_type.startswith("INPUT_"):
        gate_payload = payload_dict.get("immune_gate")
        if isinstance(gate_payload, dict):
            source_payload = dict(gate_payload)
    if source_payload is None:
        return None

    decision = str(source_payload.get("decision") or "").strip().upper()
    if decision not in {"ALLOW", "REPORT", "BLOCK"}:
        reason_code = str(row.get("reason_code") or "").strip().upper()
        if "IMMUNE_BLOCKED" in reason_code:
            decision = "BLOCK"
        else:
            decision = "ALLOW"

    risk_level = str(source_payload.get("risk_level") or "").strip().lower() or None
    score: float | None = None
    try:
        if source_payload.get("score") is not None:
            score = float(source_payload.get("score"))
    except (TypeError, ValueError):
        score = None

    raw_reason_codes = source_payload.get("reason_codes")
    reason_codes: list[str] = []
    if isinstance(raw_reason_codes, list):
        reason_codes = [str(code).strip() for code in raw_reason_codes if str(code).strip()]
    elif raw_reason_codes is not None:
        normalized = str(raw_reason_codes).strip()
        if normalized:
            reason_codes = [normalized]

    row_reason_code = str(row.get("reason_code") or "").strip()
    if row_reason_code and row_reason_code not in reason_codes and row_reason_code.upper().startswith("IMMUNE_"):
        reason_codes.append(row_reason_code)

    raw_matched_rules = source_payload.get("matched_rules")
    matched_rules: list[str] = []
    if isinstance(raw_matched_rules, list):
        matched_rules = [str(rule).strip() for rule in raw_matched_rules if str(rule).strip()]
    elif raw_matched_rules is not None:
        normalized_rule = str(raw_matched_rules).strip()
        if normalized_rule:
            matched_rules = [normalized_rule]

    input_preview = None
    for candidate in (
        payload_dict.get("input_preview"),
        payload_dict.get("text_preview"),
        payload_dict.get("raw_text"),
        source_payload.get("input_preview"),
    ):
        normalized_candidate = str(candidate or "").strip()
        if normalized_candidate:
            input_preview = normalized_candidate[:240]
            break

    return {
        "request_id": str(row.get("request_id") or ""),
        "session_id": (str(row.get("session_id") or "").strip() or None),
        "event_type": event_type,
        "stage": (str(row.get("stage") or "").strip() or None),
        "direction": (str(row.get("direction") or "").strip() or None),
        "phase": (str(payload_dict.get("phase") or "").strip() or None),
        "decision": decision,
        "risk_level": risk_level,
        "score": score,
        "reason_codes": reason_codes,
        "matched_rules": matched_rules,
        "input_preview": input_preview,
        "detail": (str(row.get("detail") or "").strip() or None),
        "ts_ms": int(row.get("ts_ms") or 0),
    }


def _dedupe_ordered_values(values: List[str], *, limit: int) -> List[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
        if len(deduped) >= limit:
            break
    return deduped


@router.get("/immune-gate/stats", response_model=ImmuneGateStatsResponse)
def get_immune_gate_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    limit: int = Query(20, ge=1, le=200, description="最近事件返回条数"),
):
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    observed: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = _extract_immune_gate_event_fields(row)
        if item is not None:
            observed.append(item)

    total = len(observed)
    allow = sum(1 for item in observed if item.get("decision") == "ALLOW")
    report = sum(1 for item in observed if item.get("decision") == "REPORT")
    block = sum(1 for item in observed if item.get("decision") == "BLOCK")
    block_rate = round(block / total * 100, 1) if total > 0 else 0.0

    from collections import Counter

    reason_counter = Counter()
    risk_counter = Counter()
    for item in observed:
        for reason_code in list(item.get("reason_codes") or []):
            reason_counter[reason_code] += 1
        risk_level = str(item.get("risk_level") or "").strip().lower()
        if risk_level:
            risk_counter[risk_level] += 1

    reason_codes = [
        ImmuneGateReasonItem(
            reason_code=code,
            count=cnt,
            pct=round(cnt / total * 100, 1) if total > 0 else 0.0,
        )
        for code, cnt in reason_counter.most_common(10)
    ]
    risk_levels = [
        ImmuneGateRiskItem(
            risk_level=level,
            count=cnt,
            pct=round(cnt / total * 100, 1) if total > 0 else 0.0,
        )
        for level, cnt in risk_counter.most_common(10)
    ]

    recent_rows = sorted(observed, key=lambda item: int(item.get("ts_ms") or 0), reverse=True)[:limit]
    recent = [
        ImmuneGateRecentItem(
            request_id=str(item.get("request_id") or ""),
            session_id=item.get("session_id"),
            event_type=str(item.get("event_type") or ""),
            decision=str(item.get("decision") or "ALLOW"),
            risk_level=item.get("risk_level"),
            score=item.get("score"),
            reason_codes=[str(code) for code in list(item.get("reason_codes") or [])],
            detail=item.get("detail"),
            ts_ms=int(item.get("ts_ms") or 0),
        )
        for item in recent_rows
    ]

    return ImmuneGateStatsResponse(
        window_minutes=window_minutes,
        total=total,
        allow=allow,
        report=report,
        block=block,
        block_rate=block_rate,
        reason_codes=reason_codes,
        risk_levels=risk_levels,
        recent=recent,
        runtime_state=_build_immune_gate_runtime_state(),
        timestamp=now,
    )


@router.get("/immune-gate/replay/{request_id}", response_model=ImmuneGateReplayResponse)
def get_immune_gate_replay(
    request_id: str,
    limit: int = Query(120, ge=1, le=500, description="回放最多返回多少条 immune 相关事件"),
):
    request_key = str(request_id or "").strip()
    if not request_key:
        raise HTTPException(
            status_code=422,
            detail={"reason_code": "IMMUNE_REPLAY_INVALID_REQUEST_ID", "detail": "request_id is required"},
        )

    ledger = _get_ledger()
    try:
        if hasattr(ledger, "fetch_by_request_id"):
            rows = ledger.fetch_by_request_id("voice_event_log", request_key)
        else:
            rows = []
    except Exception:
        rows = []

    observed: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        extracted = _extract_immune_gate_event_fields(row)
        if extracted is not None:
            observed.append(extracted)

    if not observed:
        raise HTTPException(
            status_code=404,
            detail={
                "reason_code": "IMMUNE_REPLAY_NOT_FOUND",
                "detail": f"no immune gate events for request_id={request_key}",
            },
        )

    ordered_rows = sorted(observed, key=lambda item: int(item.get("ts_ms") or 0))
    if len(ordered_rows) > limit:
        ordered_rows = ordered_rows[-limit:]

    decision_items = [
        ImmuneGateReplayDecisionItem(
            event_type=str(item.get("event_type") or ""),
            stage=item.get("stage"),
            direction=item.get("direction"),
            phase=item.get("phase"),
            decision=str(item.get("decision") or "ALLOW"),
            risk_level=item.get("risk_level"),
            score=item.get("score"),
            reason_codes=[str(code) for code in list(item.get("reason_codes") or [])][:8],
            matched_rules=[str(rule) for rule in list(item.get("matched_rules") or [])][:8],
            input_preview=item.get("input_preview"),
            detail=item.get("detail"),
            ts_ms=int(item.get("ts_ms") or 0),
        )
        for item in ordered_rows
    ]

    score_values = [float(item.score) for item in decision_items if item.score is not None]
    all_reason_codes = [
        str(code)
        for item in decision_items
        for code in list(item.reason_codes or [])
    ]
    all_matched_rules = [
        str(rule)
        for item in decision_items
        for rule in list(item.matched_rules or [])
    ]

    session_id = next(
        (str(item.get("session_id")).strip() for item in ordered_rows if str(item.get("session_id") or "").strip()),
        None,
    )
    input_preview = next(
        (str(item.get("input_preview")).strip() for item in ordered_rows if str(item.get("input_preview") or "").strip()),
        None,
    )

    return ImmuneGateReplayResponse(
        request_id=request_key,
        session_id=session_id,
        input_preview=input_preview,
        decisions=decision_items,
        summary=ImmuneGateReplaySummary(
            event_count=len(decision_items),
            first_decision=(decision_items[0].decision if decision_items else None),
            last_decision=(decision_items[-1].decision if decision_items else None),
            max_score=(max(score_values) if score_values else None),
            reason_codes=_dedupe_ordered_values(all_reason_codes, limit=12),
            matched_rules=_dedupe_ordered_values(all_matched_rules, limit=12),
        ),
        timestamp=_now_ms(),
    )


# ---------------------------------------------------------------------------
# GET /obs/retrieval/runtime
# ---------------------------------------------------------------------------
class RetrievalRuntimeRouteItem(BaseModel):
    route_id: str
    provider: str
    model_id: str
    dim: Optional[int] = None
    top_k: Optional[int] = None
    timeout_ms: int
    node_selector: str


class RetrievalRuntimeConfiguredSection(BaseModel):
    enabled: bool
    default_route: str
    routes: List[RetrievalRuntimeRouteItem]


class RetrievalRuntimeObservedItem(BaseModel):
    request_id: str
    status: str
    reason_code: Optional[str]
    detail: str
    candidate_count: int
    rerank_topk: int
    embedding_model_id: Optional[str]
    embedding_provider: Optional[str]
    effective_embedding_provider: Optional[str]
    fallback_embedding_used: bool
    fallback_embedding_reason_code: Optional[str]
    rerank_model_id: Optional[str]
    rerank_provider: Optional[str]
    effective_rerank_provider: Optional[str]
    fallback_reranker_used: bool
    fallback_reranker_reason_code: Optional[str]
    runtime_fields_complete: bool = False
    ts_ms: int


class RetrievalRuntimeResponse(BaseModel):
    suite_profile: Optional[str] = None
    suite_force: bool = False
    episodic_write_embedding_model_id: Optional[str] = None
    episodic_write_embedding_provider: Optional[str] = None
    read_write_model_aligned: bool = False
    embedding: RetrievalRuntimeConfiguredSection
    reranker: RetrievalRuntimeConfiguredSection
    latest: Optional[RetrievalRuntimeObservedItem]
    recent: List[RetrievalRuntimeObservedItem]
    recent_total: int
    recent_embedding_fallbacks: int
    recent_reranker_fallbacks: int
    timestamp: int


def _extract_default_embedding_write_hints(embed_cfg: Any) -> tuple[Optional[str], Optional[str]]:
    if embed_cfg is None:
        return None, None
    default_route_id = str(getattr(embed_cfg, "default_route", "") or "").strip()
    routes = getattr(embed_cfg, "routes", None)
    if not default_route_id or not isinstance(routes, Mapping):
        return None, None
    route = routes.get(default_route_id)
    if route is None:
        return None, None
    if isinstance(route, Mapping):
        model_id = str(route.get("model_id") or "").strip() or None
        provider = str(route.get("provider") or "").strip() or None
        return model_id, provider
    model_id = str(getattr(route, "model_id", "") or "").strip() or None
    provider = str(getattr(route, "provider", "") or "").strip() or None
    return model_id, provider


def _build_retrieval_runtime_route_items(config: Any, *, kind: str) -> List[RetrievalRuntimeRouteItem]:
    routes = []
    for route_id, route in sorted(dict(getattr(config, "routes", {}) or {}).items()):
        routes.append(
            RetrievalRuntimeRouteItem(
                route_id=str(route_id),
                provider=str(getattr(route, "provider", "") or ""),
                model_id=str(getattr(route, "model_id", "") or ""),
                dim=(int(getattr(route, "dim", 0)) if kind == "embedding" and getattr(route, "dim", None) is not None else None),
                top_k=(int(getattr(route, "top_k", 0)) if kind == "reranker" and getattr(route, "top_k", None) is not None else None),
                timeout_ms=int(getattr(route, "timeout_ms", 0) or 0),
                node_selector=str(getattr(route, "node_selector", "") or ""),
            )
        )
    return routes


def _build_retrieval_runtime_observed_item(row: dict[str, Any]) -> RetrievalRuntimeObservedItem:
    payload = _safe_json(row.get("payload"))
    payload_dict = dict(payload) if isinstance(payload, dict) else {}
    embedding_model_id = (str(payload_dict.get("embedding_model_id") or "").strip() or None)
    rerank_model_id = (str(payload_dict.get("rerank_model_id") or "").strip() or None)
    runtime_fields_complete = bool(embedding_model_id or rerank_model_id)
    return RetrievalRuntimeObservedItem(
        request_id=str(row.get("request_id") or ""),
        status=str(row.get("status") or "UNKNOWN"),
        reason_code=(str(row.get("reason_code") or "").strip() or None),
        detail=str(row.get("detail") or ""),
        candidate_count=int(payload_dict.get("candidate_count") or 0),
        rerank_topk=int(payload_dict.get("rerank_topk") or 0),
        embedding_model_id=embedding_model_id,
        embedding_provider=(str(payload_dict.get("embedding_provider") or "").strip() or None),
        effective_embedding_provider=(str(payload_dict.get("effective_embedding_provider") or "").strip() or None),
        fallback_embedding_used=bool(payload_dict.get("fallback_embedding_used", False)),
        fallback_embedding_reason_code=(str(payload_dict.get("fallback_embedding_reason_code") or "").strip() or None),
        rerank_model_id=rerank_model_id,
        rerank_provider=(str(payload_dict.get("rerank_provider") or "").strip() or None),
        effective_rerank_provider=(str(payload_dict.get("effective_rerank_provider") or "").strip() or None),
        fallback_reranker_used=bool(payload_dict.get("fallback_reranker_used", False)),
        fallback_reranker_reason_code=(str(payload_dict.get("fallback_reranker_reason_code") or "").strip() or None),
        runtime_fields_complete=runtime_fields_complete,
        ts_ms=int(row.get("ts_ms") or 0),
    )


def _get_retrieval_runtime_sync(
    request_id: Optional[str] = Query(None, description="可选：指定 request_id，优先返回该请求的运行时模型信息"),
    limit: int = Query(10, ge=1, le=100, description="最近返回多少条 PIPELINE 记录"),
    include_legacy: bool = Query(False, description="是否包含缺少运行时模型字段的历史旧记录"),
):
    request_id = request_id if isinstance(request_id, str) else getattr(request_id, "default", None)
    if request_id is not None:
        request_id = str(request_id).strip() or None
    limit = int(limit if isinstance(limit, int) else getattr(limit, "default", 10))
    include_legacy = bool(include_legacy if isinstance(include_legacy, bool) else getattr(include_legacy, "default", False))

    now = _now_ms()
    ledger = _get_ledger()

    from host_brain.retrieval.embedding import load_embedding_runtime_config  # noqa: PLC0415
    from host_brain.retrieval.reranker_config import load_reranker_runtime_config  # noqa: PLC0415

    suite = resolve_retrieval_model_suite(os.environ)
    embed_cfg_result = load_embedding_runtime_config(env=os.environ)
    rerank_cfg_result = load_reranker_runtime_config(env=os.environ)
    embed_cfg = embed_cfg_result.config
    rerank_cfg = rerank_cfg_result.config

    rows = []
    try:
        rows = [
            r for r in reversed(list(ledger.retrieval_event_log))
            if str(r.get("op_kind") or "").strip().upper() == "PIPELINE"
        ]
    except Exception:
        rows = []

    recent_items: List[RetrievalRuntimeObservedItem] = []
    seen_request_ids: set[str] = set()
    for row in rows:
        try:
            item = _build_retrieval_runtime_observed_item(dict(row))
        except Exception:
            continue
        if not include_legacy and not item.runtime_fields_complete:
            continue
        if item.request_id in seen_request_ids:
            continue
        seen_request_ids.add(item.request_id)
        recent_items.append(item)
        if len(recent_items) >= max(1, int(limit)):
            break

    latest_item: Optional[RetrievalRuntimeObservedItem] = None
    if request_id:
        normalized_request_id = str(request_id).strip()
        for row in rows:
            if str(row.get("request_id") or "").strip() != normalized_request_id:
                continue
            candidate = _build_retrieval_runtime_observed_item(dict(row))
            if include_legacy or candidate.runtime_fields_complete:
                latest_item = candidate
                break
        if latest_item is None:
            for row in rows:
                if str(row.get("request_id") or "").strip() == normalized_request_id:
                    latest_item = _build_retrieval_runtime_observed_item(dict(row))
                    break
    if latest_item is None and recent_items:
        latest_item = recent_items[0]

    cfg_model_id, cfg_provider = _extract_default_embedding_write_hints(embed_cfg)
    episodic_write_model_id = cfg_model_id
    episodic_write_provider = cfg_provider
    # 优先使用 runtime config；仅当配置缺失时再读取写侧 store hint，避免触发重型依赖初始化
    if not episodic_write_model_id or not episodic_write_provider:
        try:
            import host_brain.app.dependencies as _deps  # noqa: PLC0415

            episodic_store = _deps.get_episodic_store()
            store_model_id = getattr(episodic_store, "embedding_model_id_hint", None)
            store_provider = getattr(episodic_store, "embedding_provider_hint", None)
            if not episodic_write_model_id:
                episodic_write_model_id = store_model_id
            if not episodic_write_provider:
                episodic_write_provider = store_provider
        except Exception:
            pass

    latest_embed_model = latest_item.embedding_model_id if latest_item is not None else None
    return RetrievalRuntimeResponse(
        suite_profile=(suite.profile_id if suite is not None else None),
        suite_force=bool(str(os.environ.get("RETR_MODEL_SUITE_FORCE") or "").strip().lower() in {"1", "true", "yes", "on"}),
        episodic_write_embedding_model_id=episodic_write_model_id,
        episodic_write_embedding_provider=episodic_write_provider,
        read_write_model_aligned=bool(episodic_write_model_id and latest_embed_model and episodic_write_model_id == latest_embed_model),
        embedding=RetrievalRuntimeConfiguredSection(
            enabled=bool(getattr(embed_cfg, "enabled", False)),
            default_route=str(getattr(embed_cfg, "default_route", "") or ""),
            routes=_build_retrieval_runtime_route_items(embed_cfg, kind="embedding") if embed_cfg is not None else [],
        ),
        reranker=RetrievalRuntimeConfiguredSection(
            enabled=bool(getattr(rerank_cfg, "enabled", False)),
            default_route=str(getattr(rerank_cfg, "default_route", "") or ""),
            routes=_build_retrieval_runtime_route_items(rerank_cfg, kind="reranker") if rerank_cfg is not None else [],
        ),
        latest=latest_item,
        recent=recent_items,
        recent_total=len(recent_items),
        recent_embedding_fallbacks=sum(1 for item in recent_items if item.fallback_embedding_used),
        recent_reranker_fallbacks=sum(1 for item in recent_items if item.fallback_reranker_used),
        timestamp=now,
    )


@router.get("/retrieval/runtime", response_model=RetrievalRuntimeResponse)
async def get_retrieval_runtime(
    request_id: Optional[str] = Query(None, description="Optional request_id to return runtime model details for a specific request"),
    limit: int = Query(10, ge=1, le=100, description="Number of recent PIPELINE rows to return"),
    include_legacy: bool = Query(False, description="Include legacy rows missing runtime model fields"),
):
    return await asyncio.to_thread(
        _get_retrieval_runtime_sync,
        request_id,
        limit,
        include_legacy,
    )


class CapabilityGapStatItem(BaseModel):
    key: str
    count: int


class CapabilityGapHotspotItem(BaseModel):
    candidate_key: str
    reason_code: str
    suggested_family: str
    missing_capability_tags: List[str]
    detected_count: int
    executed_count: int
    failed_count: int
    success_count: int
    failure_rate: float
    impact_sessions: int
    last_detected_ts_ms: Optional[int]


class CapabilityGapCandidateThresholds(BaseModel):
    min_detected_count: int
    min_unique_sessions: int
    min_failure_rate: float
    min_failed_count: int


class CapabilityGapCandidateExecutorItem(BaseModel):
    key: str
    count: int


class CapabilityGapForgeCandidateItem(BaseModel):
    candidate_key: str
    stage: str
    reason_code: str
    suggested_family: str
    missing_capability_tags: List[str]
    detected_count: int
    executed_count: int
    success_count: int
    failed_count: int
    failure_rate: float
    success_rate: float
    impact_sessions: int
    impact_requests: int
    priority_score: float
    recommended_action: str
    top_executor_targets: List[CapabilityGapCandidateExecutorItem]
    first_detected_ts_ms: Optional[int]
    last_detected_ts_ms: Optional[int]
    last_execution_ts_ms: Optional[int]


class CapabilityGapForgeRuntimeQualityItem(BaseModel):
    proposal_id: str
    status: str
    stage: str
    health_score: float
    window_failure_rate: float
    window_p95_latency_ms: int
    adaptive_tune_count: int
    last_tune_action: str
    updated_ts_ms: int


class CapabilityGapForgeRuntimeQualitySnapshot(BaseModel):
    timestamp: int
    summary: Dict[str, Any]
    items: List[CapabilityGapForgeRuntimeQualityItem]


class CapabilityGapStatsResponse(BaseModel):
    timestamp: int
    detected_total: int
    executed_total: int
    success_total: int
    failed_total: int
    success_rate: float
    active_runtime_capabilities: int
    by_reason: List[CapabilityGapStatItem]
    by_family: List[CapabilityGapStatItem]
    by_executor: List[CapabilityGapStatItem]
    hotspots: List[CapabilityGapHotspotItem]
    forge_candidates: List[CapabilityGapForgeCandidateItem]
    forge_runtime_quality: CapabilityGapForgeRuntimeQualitySnapshot
    candidate_thresholds: CapabilityGapCandidateThresholds
    recent_events: List[Dict[str, Any]]


class CapabilityGapForgeCandidatesResponse(BaseModel):
    timestamp: int
    candidate_thresholds: CapabilityGapCandidateThresholds
    total_candidates: int
    candidates: List[CapabilityGapForgeCandidateItem]


def _build_capability_gap_hotspots(snapshot: Mapping[str, Any]) -> List[CapabilityGapHotspotItem]:
    return [
        CapabilityGapHotspotItem(
            candidate_key=str(item.get("candidate_key") or ""),
            reason_code=str(item.get("reason_code") or "UNKNOWN"),
            suggested_family=str(item.get("suggested_family") or "unknown"),
            missing_capability_tags=[
                str(tag)
                for tag in list(item.get("missing_capability_tags") or [])
                if str(tag or "").strip()
            ],
            detected_count=int(item.get("detected_count") or 0),
            executed_count=int(item.get("executed_count") or 0),
            failed_count=int(item.get("failed_count") or 0),
            success_count=int(item.get("success_count") or 0),
            failure_rate=float(item.get("failure_rate") or 0.0),
            impact_sessions=int(item.get("impact_sessions") or 0),
            last_detected_ts_ms=(
                int(item.get("last_detected_ts_ms"))
                if item.get("last_detected_ts_ms") is not None
                else None
            ),
        )
        for item in list(snapshot.get("hotspots") or [])
        if isinstance(item, Mapping)
    ]


def _build_capability_gap_forge_candidates(snapshot: Mapping[str, Any]) -> List[CapabilityGapForgeCandidateItem]:
    return [
        CapabilityGapForgeCandidateItem(
            candidate_key=str(item.get("candidate_key") or ""),
            stage=str(item.get("stage") or "OBSERVE"),
            reason_code=str(item.get("reason_code") or "UNKNOWN"),
            suggested_family=str(item.get("suggested_family") or "unknown"),
            missing_capability_tags=[
                str(tag)
                for tag in list(item.get("missing_capability_tags") or [])
                if str(tag or "").strip()
            ],
            detected_count=int(item.get("detected_count") or 0),
            executed_count=int(item.get("executed_count") or 0),
            success_count=int(item.get("success_count") or 0),
            failed_count=int(item.get("failed_count") or 0),
            failure_rate=float(item.get("failure_rate") or 0.0),
            success_rate=float(item.get("success_rate") or 0.0),
            impact_sessions=int(item.get("impact_sessions") or 0),
            impact_requests=int(item.get("impact_requests") or 0),
            priority_score=float(item.get("priority_score") or 0.0),
            recommended_action=str(item.get("recommended_action") or "observe"),
            top_executor_targets=[
                CapabilityGapCandidateExecutorItem(
                    key=str(executor_item.get("key") or "unknown"),
                    count=int(executor_item.get("count") or 0),
                )
                for executor_item in list(item.get("top_executor_targets") or [])
                if isinstance(executor_item, Mapping)
            ],
            first_detected_ts_ms=(
                int(item.get("first_detected_ts_ms"))
                if item.get("first_detected_ts_ms") is not None
                else None
            ),
            last_detected_ts_ms=(
                int(item.get("last_detected_ts_ms"))
                if item.get("last_detected_ts_ms") is not None
                else None
            ),
            last_execution_ts_ms=(
                int(item.get("last_execution_ts_ms"))
                if item.get("last_execution_ts_ms") is not None
                else None
            ),
        )
        for item in list(snapshot.get("forge_candidates") or [])
        if isinstance(item, Mapping)
    ]


def _build_capability_gap_thresholds(snapshot: Mapping[str, Any]) -> CapabilityGapCandidateThresholds:
    thresholds = snapshot.get("candidate_thresholds")
    parsed = dict(thresholds) if isinstance(thresholds, Mapping) else {}
    return CapabilityGapCandidateThresholds(
        min_detected_count=int(parsed.get("min_detected_count") or 0),
        min_unique_sessions=int(parsed.get("min_unique_sessions") or 0),
        min_failure_rate=float(parsed.get("min_failure_rate") or 0.0),
        min_failed_count=int(parsed.get("min_failed_count") or 0),
    )


def _build_capability_gap_runtime_quality(
    snapshot: Mapping[str, Any] | None,
) -> CapabilityGapForgeRuntimeQualitySnapshot:
    parsed = dict(snapshot) if isinstance(snapshot, Mapping) else {}
    summary = dict(parsed.get("summary") or {}) if isinstance(parsed.get("summary"), Mapping) else {}
    return CapabilityGapForgeRuntimeQualitySnapshot(
        timestamp=int(parsed.get("timestamp") or _now_ms()),
        summary=summary,
        items=[
            CapabilityGapForgeRuntimeQualityItem(
                proposal_id=str(item.get("proposal_id") or ""),
                status=str(item.get("status") or "UNKNOWN"),
                stage=str(item.get("stage") or "PROPOSED"),
                health_score=float(item.get("health_score") or 0.0),
                window_failure_rate=float(item.get("window_failure_rate") or 0.0),
                window_p95_latency_ms=int(item.get("window_p95_latency_ms") or 0),
                adaptive_tune_count=int(item.get("adaptive_tune_count") or 0),
                last_tune_action=str(item.get("last_tune_action") or "NONE"),
                updated_ts_ms=int(item.get("updated_ts_ms") or 0),
            )
            for item in list(parsed.get("items") or [])
            if isinstance(item, Mapping)
        ],
    )


@router.get("/capability-gaps/stats", response_model=CapabilityGapStatsResponse)
def get_capability_gaps_stats(
    limit_recent: int = Query(20, ge=1, le=200, description="Recent capability gap events to return"),
    limit_hotspots: int = Query(8, ge=1, le=50, description="Hotspot rows to return"),
    limit_candidates: int = Query(8, ge=1, le=50, description="Forge candidate rows to return"),
    min_candidate_detected: int = Query(3, ge=1, le=100, description="Min detections for forge candidate"),
    min_candidate_sessions: int = Query(2, ge=1, le=100, description="Min impacted sessions for forge candidate"),
    min_candidate_failure_rate: float = Query(0.35, ge=0.0, le=1.0, description="Min failure rate for forge candidate"),
    min_candidate_failed: int = Query(2, ge=0, le=100, description="Min failed executions for forge candidate"),
):
    from host_brain.app.dependencies import get_capability_forge_service, get_capability_gap_telemetry  # noqa: PLC0415

    snapshot = get_capability_gap_telemetry().snapshot(
        limit_recent=limit_recent,
        limit_hotspots=limit_hotspots,
        limit_candidates=limit_candidates,
        min_candidate_detected=min_candidate_detected,
        min_candidate_sessions=min_candidate_sessions,
        min_candidate_failure_rate=min_candidate_failure_rate,
        min_candidate_failed=min_candidate_failed,
    )
    try:
        forge_quality_snapshot = get_capability_forge_service().runtime_quality_stats(limit=8)
    except Exception:
        forge_quality_snapshot = {
            "timestamp": _now_ms(),
            "summary": {
                "tracked_proposals": 0,
                "unhealthy_proposals": 0,
                "critical_proposals": 0,
                "avg_health_score": 0.0,
                "avg_window_failure_rate": 0.0,
                "avg_window_p95_latency_ms": 0.0,
            },
            "items": [],
        }
    return CapabilityGapStatsResponse(
        timestamp=int(snapshot.get("timestamp") or _now_ms()),
        detected_total=int(snapshot.get("detected_total") or 0),
        executed_total=int(snapshot.get("executed_total") or 0),
        success_total=int(snapshot.get("success_total") or 0),
        failed_total=int(snapshot.get("failed_total") or 0),
        success_rate=float(snapshot.get("success_rate") or 0.0),
        active_runtime_capabilities=int(snapshot.get("active_runtime_capabilities") or 0),
        by_reason=[
            CapabilityGapStatItem(
                key=str(item.get("key") or "UNKNOWN"),
                count=int(item.get("count") or 0),
            )
            for item in list(snapshot.get("by_reason") or [])
            if isinstance(item, Mapping)
        ],
        by_family=[
            CapabilityGapStatItem(
                key=str(item.get("key") or "UNKNOWN"),
                count=int(item.get("count") or 0),
            )
            for item in list(snapshot.get("by_family") or [])
            if isinstance(item, Mapping)
        ],
        by_executor=[
            CapabilityGapStatItem(
                key=str(item.get("key") or "UNKNOWN"),
                count=int(item.get("count") or 0),
            )
            for item in list(snapshot.get("by_executor") or [])
            if isinstance(item, Mapping)
        ],
        hotspots=_build_capability_gap_hotspots(snapshot),
        forge_candidates=_build_capability_gap_forge_candidates(snapshot),
        forge_runtime_quality=_build_capability_gap_runtime_quality(forge_quality_snapshot),
        candidate_thresholds=_build_capability_gap_thresholds(snapshot),
        recent_events=[
            dict(item)
            for item in list(snapshot.get("recent_events") or [])
            if isinstance(item, Mapping)
        ],
    )


@router.get("/capability-gaps/forge-candidates", response_model=CapabilityGapForgeCandidatesResponse)
def get_capability_gap_forge_candidates(
    limit: int = Query(20, ge=1, le=200, description="Forge candidates to return"),
    min_candidate_detected: int = Query(3, ge=1, le=100, description="Min detections for forge candidate"),
    min_candidate_sessions: int = Query(2, ge=1, le=100, description="Min impacted sessions for forge candidate"),
    min_candidate_failure_rate: float = Query(0.35, ge=0.0, le=1.0, description="Min failure rate for forge candidate"),
    min_candidate_failed: int = Query(2, ge=0, le=100, description="Min failed executions for forge candidate"),
):
    from host_brain.app.dependencies import get_capability_gap_telemetry  # noqa: PLC0415

    snapshot = get_capability_gap_telemetry().snapshot(
        limit_recent=10,
        limit_hotspots=5,
        limit_candidates=limit,
        min_candidate_detected=min_candidate_detected,
        min_candidate_sessions=min_candidate_sessions,
        min_candidate_failure_rate=min_candidate_failure_rate,
        min_candidate_failed=min_candidate_failed,
    )
    candidates = _build_capability_gap_forge_candidates(snapshot)
    return CapabilityGapForgeCandidatesResponse(
        timestamp=int(snapshot.get("timestamp") or _now_ms()),
        candidate_thresholds=_build_capability_gap_thresholds(snapshot),
        total_candidates=len(candidates),
        candidates=candidates,
    )


class CapabilityForgePreviewRequest(BaseModel):
    candidate_key: Optional[str] = None
    canary_traffic_percent: int = Field(5, ge=1, le=100)


class CapabilityForgeCreateRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    proposer: str = Field(..., min_length=1, max_length=128)
    candidate_key: Optional[str] = None
    canary_traffic_percent: int = Field(5, ge=1, le=100)


class CapabilityForgeApproveRequest(BaseModel):
    approval_token_ref: str = Field(..., min_length=6, max_length=256)
    approver: str = Field(..., min_length=1, max_length=128)
    target_stage: str = Field("shadow", description="shadow/canary/full")


class CapabilityForgeRollbackRequest(BaseModel):
    operator: str = Field(..., min_length=1, max_length=128)
    reason: Optional[str] = Field(None, max_length=200)


class CapabilityForgeRuntimeMatchRequest(BaseModel):
    request_id: str = Field(..., min_length=1, max_length=128)
    suggested_family: str = Field(..., min_length=1, max_length=64)
    missing_capability_tags: List[str] = Field(default_factory=list)
    reason_code: Optional[str] = None


class CapabilityForgePreviewResponse(BaseModel):
    timestamp: int
    candidate: Dict[str, Any]
    recommended_release_plan: Dict[str, Any]
    recommendation: str


class CapabilityForgeProposalResponse(BaseModel):
    timestamp: int
    proposal: Dict[str, Any]


class CapabilityForgeProposalListResponse(BaseModel):
    timestamp: int
    total: int
    items: List[Dict[str, Any]]


class CapabilityForgeRuntimeMatchResponse(BaseModel):
    timestamp: int
    decision: Dict[str, Any]


class CapabilityForgeRuntimeStatsResponse(BaseModel):
    timestamp: int
    counters: Dict[str, int]
    apply_rate: float


class CapabilityForgeRuntimeTuningResponse(BaseModel):
    timestamp: int
    summary: Dict[str, int]
    items: List[Dict[str, Any]]


class CapabilityForgeRuntimeQualityResponse(BaseModel):
    timestamp: int
    summary: Dict[str, Any]
    items: List[Dict[str, Any]]


@router.post("/capability-gaps/forge-proposals/preview", response_model=CapabilityForgePreviewResponse)
def preview_capability_forge_proposal(body: CapabilityForgePreviewRequest) -> CapabilityForgePreviewResponse:
    from host_brain.app.dependencies import get_capability_forge_service  # noqa: PLC0415

    candidate = get_capability_forge_service().resolve_candidate(body.candidate_key)
    if candidate is None:
        raise HTTPException(
            status_code=404,
            detail={
                "reason_code": "CAPABILITY_FORGE_CANDIDATE_NOT_FOUND",
                "detail": "No forge candidate or hotspot is currently available",
            },
        )
    failure_rate = float(candidate.get("failure_rate") or 0.0)
    detected_count = int(candidate.get("detected_count") or 0)
    impact_sessions = int(candidate.get("impact_sessions") or 0)
    recommendation = "forge_with_canary"
    if failure_rate >= 0.5:
        recommendation = "forge_with_reliability_tests"
    elif detected_count >= 6 and impact_sessions >= 3:
        recommendation = "forge_to_full_candidate_after_short_canary"
    return CapabilityForgePreviewResponse(
        timestamp=_now_ms(),
        candidate=dict(candidate),
        recommended_release_plan={
            "shadow": {"enabled": True},
            "canary": {
                "enabled": True,
                "traffic_percent": int(body.canary_traffic_percent),
            },
            "full": {"enabled": True},
            "rollback": {"enabled": True, "observe_window_minutes": 30},
        },
        recommendation=recommendation,
    )


@router.post("/capability-gaps/forge-proposals", response_model=CapabilityForgeProposalResponse)
def create_capability_forge_proposal(body: CapabilityForgeCreateRequest) -> CapabilityForgeProposalResponse:
    from host_brain.app.dependencies import get_capability_forge_service  # noqa: PLC0415

    try:
        proposal = get_capability_forge_service().create_proposal(
            session_id=body.session_id,
            proposer=body.proposer,
            candidate_key=body.candidate_key,
            canary_traffic_percent=body.canary_traffic_percent,
        )
    except ValueError as exc:
        if str(exc) == "CAPABILITY_FORGE_CANDIDATE_NOT_FOUND":
            raise HTTPException(
                status_code=404,
                detail={"reason_code": str(exc), "detail": "candidate not found"},
            )
        raise HTTPException(status_code=422, detail={"reason_code": str(exc), "detail": str(exc)})
    return CapabilityForgeProposalResponse(timestamp=_now_ms(), proposal=proposal)


@router.get("/capability-gaps/forge-proposals", response_model=CapabilityForgeProposalListResponse)
def list_capability_forge_proposals(
    status: Optional[str] = Query(None, description="Optional status filter"),
    limit: int = Query(20, ge=1, le=200),
) -> CapabilityForgeProposalListResponse:
    from host_brain.app.dependencies import get_capability_forge_service  # noqa: PLC0415

    items = get_capability_forge_service().list_proposals(status=status, limit=limit)
    return CapabilityForgeProposalListResponse(
        timestamp=_now_ms(),
        total=len(items),
        items=items,
    )


@router.get("/capability-gaps/forge-proposals/{proposal_id}", response_model=CapabilityForgeProposalResponse)
def get_capability_forge_proposal(proposal_id: str) -> CapabilityForgeProposalResponse:
    from host_brain.app.dependencies import get_capability_forge_service  # noqa: PLC0415

    proposal = get_capability_forge_service().get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(
            status_code=404,
            detail={"reason_code": "CAPABILITY_FORGE_PROPOSAL_NOT_FOUND", "detail": f"proposal_id={proposal_id}"},
        )
    return CapabilityForgeProposalResponse(timestamp=_now_ms(), proposal=proposal)


@router.post("/capability-gaps/forge-proposals/{proposal_id}/approve", response_model=CapabilityForgeProposalResponse)
def approve_capability_forge_proposal(
    proposal_id: str,
    body: CapabilityForgeApproveRequest,
) -> CapabilityForgeProposalResponse:
    from host_brain.app.dependencies import get_capability_forge_service  # noqa: PLC0415

    stage = str(body.target_stage or "").strip().lower()
    if stage not in {"shadow", "canary", "full"}:
        raise HTTPException(
            status_code=422,
            detail={"reason_code": "CAPABILITY_FORGE_INVALID_STAGE", "detail": "target_stage must be shadow/canary/full"},
        )
    try:
        proposal = get_capability_forge_service().approve_and_promote(
            proposal_id=proposal_id,
            approval_token_ref=body.approval_token_ref,
            approver=body.approver,
            target_stage=stage,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"reason_code": str(exc), "detail": f"proposal_id={proposal_id}"},
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={"reason_code": str(exc), "detail": "approval token validation failed"},
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"reason_code": str(exc), "detail": str(exc)})
    return CapabilityForgeProposalResponse(timestamp=_now_ms(), proposal=proposal)


@router.post("/capability-gaps/forge-proposals/{proposal_id}/rollback", response_model=CapabilityForgeProposalResponse)
def rollback_capability_forge_proposal(
    proposal_id: str,
    body: CapabilityForgeRollbackRequest,
) -> CapabilityForgeProposalResponse:
    from host_brain.app.dependencies import get_capability_forge_service  # noqa: PLC0415

    try:
        proposal = get_capability_forge_service().rollback(
            proposal_id=proposal_id,
            operator=body.operator,
            reason=body.reason,
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail={"reason_code": "CAPABILITY_FORGE_PROPOSAL_NOT_FOUND", "detail": f"proposal_id={proposal_id}"},
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"reason_code": str(exc), "detail": str(exc)})
    return CapabilityForgeProposalResponse(timestamp=_now_ms(), proposal=proposal)


@router.post("/capability-gaps/forge-proposals/runtime-match", response_model=CapabilityForgeRuntimeMatchResponse)
def capability_forge_runtime_match(body: CapabilityForgeRuntimeMatchRequest) -> CapabilityForgeRuntimeMatchResponse:
    from host_brain.app.dependencies import get_capability_forge_service  # noqa: PLC0415

    decision = get_capability_forge_service().get_runtime_route_override(
        request_id=body.request_id,
        suggested_family=body.suggested_family,
        missing_capability_tags=body.missing_capability_tags,
        reason_code=body.reason_code,
    )
    return CapabilityForgeRuntimeMatchResponse(timestamp=_now_ms(), decision=dict(decision))


@router.get("/capability-gaps/forge-runtime/stats", response_model=CapabilityForgeRuntimeStatsResponse)
def capability_forge_runtime_stats() -> CapabilityForgeRuntimeStatsResponse:
    from host_brain.app.dependencies import get_capability_forge_service  # noqa: PLC0415

    snapshot = get_capability_forge_service().runtime_match_stats()
    return CapabilityForgeRuntimeStatsResponse(
        timestamp=int(snapshot.get("timestamp") or _now_ms()),
        counters={
            str(k): int(v or 0)
            for k, v in dict(snapshot.get("counters") or {}).items()
        },
        apply_rate=float(snapshot.get("apply_rate") or 0.0),
    )


@router.get("/capability-gaps/forge-runtime/tuning", response_model=CapabilityForgeRuntimeTuningResponse)
def capability_forge_runtime_tuning_stats(
    limit: int = Query(20, ge=1, le=200, description="Rows to return"),
) -> CapabilityForgeRuntimeTuningResponse:
    from host_brain.app.dependencies import get_capability_forge_service  # noqa: PLC0415

    snapshot = get_capability_forge_service().runtime_tuning_stats(limit=limit)
    return CapabilityForgeRuntimeTuningResponse(
        timestamp=int(snapshot.get("timestamp") or _now_ms()),
        summary={
            str(k): int(v or 0)
            for k, v in dict(snapshot.get("summary") or {}).items()
        },
        items=[
            dict(item)
            for item in list(snapshot.get("items") or [])
            if isinstance(item, Mapping)
        ],
    )


@router.get("/capability-gaps/forge-runtime/quality", response_model=CapabilityForgeRuntimeQualityResponse)
def capability_forge_runtime_quality_stats(
    limit: int = Query(20, ge=1, le=200, description="Rows to return"),
) -> CapabilityForgeRuntimeQualityResponse:
    from host_brain.app.dependencies import get_capability_forge_service  # noqa: PLC0415

    snapshot = get_capability_forge_service().runtime_quality_stats(limit=limit)
    return CapabilityForgeRuntimeQualityResponse(
        timestamp=int(snapshot.get("timestamp") or _now_ms()),
        summary=dict(snapshot.get("summary") or {}),
        items=[
            dict(item)
            for item in list(snapshot.get("items") or [])
            if isinstance(item, Mapping)
        ],
    )
