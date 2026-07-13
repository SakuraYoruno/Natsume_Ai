"""obs_routes_stats_agent.py — 可观测性统计端点（从 obs_routes_stats.py 拆分）。

子模块：Agent Loop统计、Memory子系统统计
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.observability_runtime.remaining_query_projection import (
    AGENT_LOOP_INDEX_KEY,
    AGENT_LOOP_KIND,
    RESIDENT_CONTEXT_INDEX_KEY,
    RESIDENT_CONTEXT_KIND,
    read,
    response_meta,
)
from host_brain.app.observability_runtime.projection_ledger_view import get_projection_ledger_view

logger = logging.getLogger("Natsume.obs")


def _resolve_fetch_table(ledger, *candidates: str) -> str | None:
    resolver = getattr(ledger, "resolve_fetch_table", None)
    if callable(resolver):
        return resolver(*candidates)
    for candidate in candidates:
        normalized = str(candidate or "").strip()
        if normalized:
            return normalized
    return None


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
# P3: GET /obs/agent_loop/stats  — Agent Loop 详细统计
# ---------------------------------------------------------------------------
class AgentLoopRoundItem(BaseModel):
    request_id: str
    session_id: Optional[str]
    goal: Optional[str]
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
    schema_version: str = "Natsume.operator.query_projection.v1"
    projection_version: int = 0
    cursor: int = 0
    freshness_ts_ms: int = 0
    degraded_state: str = "REBUILDING"

@router.get("/agent_loop/stats", response_model=AgentLoopStatsResponse)
def get_agent_loop_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
    limit: int = Query(10, ge=1, le=50),
):
    """P3: Agent Loop 详细统计 — 目标达成率、平均轮次、最近执行列表。"""
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    payload, meta = read(AGENT_LOOP_KIND, AGENT_LOOP_INDEX_KEY)
    loops = [
        dict(item)
        for item in list((payload or {}).get("loops") or [])
        if isinstance(item, dict) and int(item.get("ts_ms") or 0) >= since_ms
    ]

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
        **response_meta(meta),
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
    ledger = get_projection_ledger_view()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # ── EpisodicStore 写入统计 ────────────────────────────────────────────
    episodic_rows = []
    episodic_table = _resolve_fetch_table(ledger, "episodic_store_log")
    if episodic_table:
        try:
            episodic_rows = ledger.fetch_by_time_range(episodic_table, start_ms=since_ms, end_ms=now)
        except Exception:
            episodic_rows = []

    ep_total = len(episodic_rows)
    ep_success = sum(1 for r in episodic_rows if str(r.get("status", "")).upper() in ("OK", "WRITTEN", "SUCCESS"))
    ep_failed = ep_total - ep_success
    ep_rate = round(ep_success / ep_total * 100, 1) if ep_total > 0 else None
    ep_last = max((r.get("ts_ms") or 0 for r in episodic_rows), default=None)

    # ── MemoryGuard 拒绝统计 ──────────────────────────────────────────────
    guard_rows = []
    guard_table = _resolve_fetch_table(ledger, "memory_guard_log")
    if guard_table:
        try:
            guard_rows = ledger.fetch_by_time_range(guard_table, start_ms=since_ms, end_ms=now)
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
    compress_rows = []
    compress_table = _resolve_fetch_table(ledger, "context_compress_log")
    if compress_table:
        try:
            compress_rows = ledger.fetch_by_time_range(compress_table, start_ms=since_ms, end_ms=now)
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
    distill_rows = []
    distill_table = _resolve_fetch_table(ledger, "distill_log")
    if distill_table:
        try:
            distill_rows = ledger.fetch_by_time_range(distill_table, start_ms=since_ms, end_ms=now)
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
# Ref #126: GET /obs/memory/resident-context — 常驻设定 / 风格绑定上下文
# ---------------------------------------------------------------------------
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


class ResidentMemoryContextResponse(BaseModel):
    world_canon_documents: List[ResidentCanonFileItem]
    persona_resident_documents: List[ResidentCanonFileItem]
    timestamp: int
    schema_version: str = "Natsume.operator.query_projection.v1"
    projection_version: int = 0
    cursor: int = 0
    freshness_ts_ms: int = 0
    degraded_state: str = "REBUILDING"


@router.get("/memory/resident-context", response_model=ResidentMemoryContextResponse)
def get_memory_resident_context():
    """Ref #126: 只读常驻上下文元数据投影，不扫描 canon 文件。"""
    now = _now_ms()
    payload, meta = read(RESIDENT_CONTEXT_KIND, RESIDENT_CONTEXT_INDEX_KEY)

    def item(value: Mapping[str, Any]) -> ResidentCanonFileItem:
        return ResidentCanonFileItem(
            kind=str(value.get("kind") or "unknown"),
            doc_id=str(value.get("doc_id") or ""),
            title=value.get("title"),
            source_path=value.get("source_ref"),
            version=value.get("version"),
            status=value.get("status"),
            updated_by=value.get("updated_by"),
            ts_ms=value.get("ts_ms"),
            preview=None,
        )

    world_docs = [
        item(value)
        for value in list((payload or {}).get("world_canon_documents") or [])
        if isinstance(value, Mapping)
    ]
    persona_docs = [
        item(value)
        for value in list((payload or {}).get("persona_resident_documents") or [])
        if isinstance(value, Mapping)
    ]

    return ResidentMemoryContextResponse(
        world_canon_documents=world_docs,
        persona_resident_documents=persona_docs,
        timestamp=now,
        **response_meta(meta),
    )
