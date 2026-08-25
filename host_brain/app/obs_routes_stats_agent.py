"""obs_routes_stats_agent.py — Agent/Memory 可观测性统计端点。

子模块：Agent Loop统计、Memory子系统统计
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, List, Mapping, Optional

from fastapi import Depends, Query
from pydantic import BaseModel

from host_brain.app.obs_route_ports import (
    require_obs_projection_ledger_view,
    require_obs_query_projection_store,
)
from host_brain.app.observability_runtime.projection_ledger_view import (
    fail_closed_projection_route,
)
from host_brain.app.observability_runtime.remaining_query_projection import (
    AGENT_LOOP_INDEX_KEY,
    AGENT_LOOP_KIND,
    RESIDENT_CONTEXT_INDEX_KEY,
    RESIDENT_CONTEXT_KIND,
    read_from_store,
    response_meta,
)

from .obs_routes_core import (
    router,
    _now_ms,
    _resolve_fetch_table,
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
    *,
    projection_store: Annotated[Any, Depends(require_obs_query_projection_store)],
):
    """P3: Agent Loop 详细统计 — 目标达成率、平均轮次、最近执行列表。"""
    return build_agent_loop_stats_from_store(
        projection_store,
        window_minutes=window_minutes,
        limit=limit,
    )


def build_agent_loop_stats_from_store(
    projection_store: Any,
    *,
    window_minutes: int,
    limit: int,
) -> AgentLoopStatsResponse:
    """Build Agent Loop statistics from an explicit query projection store."""

    payload, meta = read_from_store(
        projection_store,
        AGENT_LOOP_KIND,
        AGENT_LOOP_INDEX_KEY,
    )
    return _build_agent_loop_stats_response(
        payload,
        meta,
        window_minutes=window_minutes,
        limit=limit,
    )


def _build_agent_loop_stats_response(
    payload: Mapping[str, Any] | None,
    meta: Mapping[str, Any],
    *,
    window_minutes: int,
    limit: int,
) -> AgentLoopStatsResponse:
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
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
    total: Optional[int]
    success: Optional[int]
    failed: Optional[int]
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
    distill_count: Optional[int]
    observed_distillation_terminal_events: int
    timestamp: int

@router.get("/memory/stats", response_model=MemoryStatsResponse)
@fail_closed_projection_route
def get_memory_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #26: Memory 子系统详细统计。

    只消费后台物化的 durable authority：Episodic 操作日志、LifeMemory
    admission receipt、scheduler event 和 distillation audit。当前 durable
    事件没有蒸馏失败终态，因此蒸馏周期与失败率保持 ``None``，不能由
    私有 detail 或进程内计数推断。
    """
    ledger = projection_view
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    def _read_rows(table: str) -> list[dict[str, Any]]:
        resolved = _resolve_fetch_table(ledger, table)
        if not resolved:
            return []
        try:
            return ledger.fetch_by_time_range(
                resolved,
                start_ms=since_ms,
                end_ms=now,
            )
        except Exception:
            return []

    def _event_ts(row: Mapping[str, Any]) -> int:
        return int(
            row.get("ts_ms")
            or row.get("created_at_ms")
            or row.get("created_ts_ms")
            or 0
        )

    # ── EpisodicStore 写入统计（memory_ops_log）───────────────────────────
    episodic_rows = [
        row
        for row in _read_rows("memory_ops_log")
        if str(row.get("op_kind") or "").upper() == "WRITE"
    ]

    ep_total = len(episodic_rows)
    ep_actual_writes = sum(
        1
        for row in episodic_rows
        if str(row.get("status") or "").upper() == "SUCCESS"
    )
    ep_idempotent_skips = sum(
        1
        for row in episodic_rows
        if str(row.get("status") or "").upper() == "SKIPPED"
    )
    ep_success = ep_actual_writes + ep_idempotent_skips
    ep_failed = ep_total - ep_success
    ep_rate = round(ep_success / ep_total * 100, 1) if ep_total > 0 else None
    ep_last = max((_event_ts(row) for row in episodic_rows), default=None)

    # ── MemoryGuard 拒绝统计（LifeMemory admission receipt）───────────────
    # Worth=SKIP/WRITE_LIGHT 的请求没有执行 Guard，不进入分母。Guard 拒绝会
    # 以 WRITE_LIGHT + MEMORY_GUARD_LIGHT_ONLY:* 的 durable receipt 落盘。
    admission_rows = _read_rows("life_memory_admission_receipt")
    guard_rows = [
        row
        for row in admission_rows
        if str(row.get("reason_code") or "").upper()
        == "MEMORY_ADMISSION_ACCEPTED"
        or str(row.get("reason_code") or "").upper().startswith(
            "MEMORY_GUARD_LIGHT_ONLY"
        )
    ]
    guard_total = len(guard_rows)
    guard_rejected = sum(
        1
        for row in guard_rows
        if str(row.get("reason_code") or "").upper().startswith(
            "MEMORY_GUARD_LIGHT_ONLY"
        )
    )
    guard_reject_rate = round(guard_rejected / guard_total * 100, 1) if guard_total > 0 else None
    guard_last = max((_event_ts(row) for row in guard_rows), default=None)

    # ── DistillationEngine 终态审计事件（error_log）─────────────────────
    distill_audit_rows = [
        row
        for row in _read_rows("error_log")
        if str(row.get("stage") or "").upper()
        in {"DISTILL_DONE", "DISTILL_SKIPPED"}
    ]
    distill_success = sum(
        1
        for row in distill_audit_rows
        if str(row.get("stage") or "").upper() == "DISTILL_DONE"
    )
    distill_skipped = sum(
        1
        for row in distill_audit_rows
        if str(row.get("stage") or "").upper() == "DISTILL_SKIPPED"
    )
    observed_distill_terminal_events = len(distill_audit_rows)
    # 这些事件既可能来自 batch，也可能来自 single；batch 中还会同时出现
    # item-level 终态。它们不能证明完整 LongTermMemoryDistiller cycle 数量或
    # 成败，且当前 writer 没有 DISTILL_FAILED 终态。
    distill_rate = None
    distill_last = max(
        (_event_ts(row) for row in distill_audit_rows),
        default=None,
    )

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
            extra={
                "source_table": "memory_ops_log",
                "write_count": ep_actual_writes,
                "idempotent_skip_count": ep_idempotent_skips,
            },
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
            extra={
                "source_table": "life_memory_admission_receipt",
                "reject_rate": guard_reject_rate,
                "rejected_count": guard_rejected,
            },
        ),
        MemorySubsystemItem(
            subsystem="long_term_distiller",
            display_name="LongTermDistiller 蒸馏周期",
            total=None,
            success=None,
            failed=None,
            success_rate=distill_rate,
            p95_ms=None,
            last_ts=distill_last,
            extra={
                "source_table": "error_log",
                "observed_event_scope": "DISTILLATION_ENGINE_TERMINAL_AUDIT",
                "observed_terminal_events": observed_distill_terminal_events,
                "observed_done_events": distill_success,
                "observed_skipped_events": distill_skipped,
                "cycle_measurement_available": False,
                "failure_measurement_available": False,
            },
        ),
    ]

    return MemoryStatsResponse(
        window_minutes=window_minutes,
        subsystems=subsystems,
        total_writes=ep_actual_writes,
        guard_reject_rate=guard_reject_rate,
        distill_count=None,
        observed_distillation_terminal_events=observed_distill_terminal_events,
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
def get_memory_resident_context(
    projection_store: Annotated[Any, Depends(require_obs_query_projection_store)],
):
    """Ref #126: 只读常驻上下文元数据投影，不扫描 canon 文件。"""
    now = _now_ms()
    payload, meta = read_from_store(
        projection_store,
        RESIDENT_CONTEXT_KIND,
        RESIDENT_CONTEXT_INDEX_KEY,
    )

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
