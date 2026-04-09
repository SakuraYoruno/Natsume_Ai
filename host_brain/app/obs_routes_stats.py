"""obs_routes_stats.py — 子系统统计端点（从 obs_routes.py 拆分）。

包含 33 个统计端点（governor/stats ~ semantic-recall/stats）。
"""
from __future__ import annotations

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
    router,
    _extract_input_dispatch_latency_ms,
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
async def get_governor_stats(
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
async def get_errors(
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
async def get_subsystems_stats(
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
async def get_agent_loop_stats(
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
async def get_memory_stats(
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
async def get_memory_resident_context():
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
async def get_vts_stats(
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
async def get_distillation_stats(
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
async def get_wechat_stats(
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
async def reset_wechat_channel(
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
async def get_retention_stats(
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
async def get_qa_stats(
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
async def get_llm_call_stats(
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
async def get_checkpoints(
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
async def get_checkpoint_detail(checkpoint_id: str):
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
async def get_rate_limit_backend_stats(
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
async def get_vault_stats(
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
async def get_llm_route_stats(
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
async def get_input_dispatch_stats(
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
# Ref #49: GET /obs/recall-pack/stats  — RecallPack token 预算统计
# ---------------------------------------------------------------------------
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
async def get_recall_pack_stats(
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
async def get_ui_assertion_stats(
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
async def get_failure_stats(
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
async def get_permission_stats(
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
async def get_scheduler_history(
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
async def get_action_plan_stats(
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
async def get_memory_guard_stats(
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
async def get_state_arbiter_stats(
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
async def get_context_compressor_stats(
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
async def get_prompt_builder_stats(
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
async def get_episodic_store_stats(
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
async def get_chain_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #82: 请求处理阶段统计（Phase XIV v4 顶级 Agent 架构）。

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

    # Phase XIV v4 顶级 Agent 架构阶段名（预定义，保证前端有数据）
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
async def get_agent_loop_history(
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
async def get_error_heatmap(
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
async def get_semantic_recall_stats():
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
async def get_immune_gate_stats(
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
async def get_immune_gate_replay(
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


@router.get("/retrieval/runtime", response_model=RetrievalRuntimeResponse)
async def get_retrieval_runtime(
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

    episodic_write_model_id = None
    episodic_write_provider = None
    try:
        from host_brain.app.dependencies import get_episodic_store  # noqa: PLC0415
        episodic_store = get_episodic_store()
        episodic_write_model_id = getattr(episodic_store, "embedding_model_id_hint", None)
        episodic_write_provider = getattr(episodic_store, "embedding_provider_hint", None)
    except Exception:
        episodic_store = None

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
