"""obs_routes_stats_capability_skill.py — Skill/工具/调度/规划类统计端点。

拆分自 obs_routes_stats_capability.py（P2-9）：
  GET /obs/ui-assertion/stats
  GET /obs/failure/stats
  GET /obs/permission/stats
  GET /obs/scheduler/history
  GET /obs/action-plan/stats
  GET /obs/memory-guard/stats
  GET /obs/state-arbiter/stats
  GET /obs/context-compressor/stats
  GET /obs/prompt-builder/stats
  GET /obs/episodic-store/stats
  GET /obs/chain/stats
  GET /obs/agent-loop/history
  GET /obs/error-heatmap
"""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from typing import Annotated, Any, Dict, List, Optional

from fastapi import Depends, Query
from pydantic import BaseModel, Field

from host_brain.app.obs_routes_core import (
    router,
    _env_int,
    _now_ms,
    _safe_json,
)
from host_brain.app.obs_route_ports import (
    ObsRoutePorts,
    require_obs_projection_ledger_view,
    require_obs_query_projection_store,
    require_obs_route_ports,
)
from host_brain.app.observability_runtime.projection_ledger_view import (
    fail_closed_projection_route,
)
from host_brain.app.observability_runtime.remaining_query_projection import (
    AGENT_LOOP_INDEX_KEY,
    AGENT_LOOP_KIND,
    read_from_store,
    response_meta,
)

logger = logging.getLogger("Natsume.obs")
_PROMPT_BUILDER_STATS_CACHE_TTL_MS = _env_int("OBS_PROMPT_BUILDER_STATS_CACHE_TTL_MS", 5000, minimum=0)


def _fetch_first_available_rows(
    ledger,
    *table_candidates: str,
    start_ms: int,
    end_ms: int,
) -> list[dict[str, Any]]:
    resolver = getattr(ledger, "resolve_fetch_table", None)
    for table in table_candidates:
        resolved = resolver(table) if callable(resolver) else table
        if not resolved:
            continue
        try:
            fetched = ledger.fetch_by_time_range(resolved, start_ms=start_ms, end_ms=end_ms)
        except Exception:
            continue
        if fetched:
            return fetched
    return []


def _fetch_rows_if_available(
    ledger,
    table: str,
    *,
    start_ms: int,
    end_ms: int,
) -> list[dict[str, Any]]:
    resolver = getattr(ledger, "resolve_fetch_table", None)
    resolved = resolver(table) if callable(resolver) else table
    if not resolved:
        return []
    try:
        return ledger.fetch_by_time_range(resolved, start_ms=start_ms, end_ms=end_ms)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Ref #50: GET /obs/ui-assertion/stats
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
@fail_closed_projection_route
def get_ui_assertion_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #50: UIAssertionGuard 统计。"""
    ledger = projection_view
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    try:
        rows = ledger.fetch_by_time_range("error_log", start_ms=since_ms, end_ms=now)
        ui_rows = [r for r in rows if str(r.get("reason_code") or r.get("stage") or "").upper() in ("SENSITIVE_UI_BLOCKED", "UI_PRECONDITION_FAILED", "UI_POSTCONDITION_FAILED")]
    except Exception:
        ui_rows = []
    if not ui_rows:
        ui_rows = _fetch_rows_if_available(ledger, "ui_assertion_log", start_ms=since_ms, end_ms=now)
    total = len(ui_rows)
    sensitive_blocked = sum(1 for r in ui_rows if str(r.get("reason_code") or r.get("audit_event") or "").upper() == "SENSITIVE_UI_BLOCKED")
    precondition_failed = sum(1 for r in ui_rows if str(r.get("reason_code") or r.get("audit_event") or "").upper() == "UI_PRECONDITION_FAILED")
    postcondition_failed = sum(1 for r in ui_rows if str(r.get("reason_code") or r.get("audit_event") or "").upper() == "UI_POSTCONDITION_FAILED")
    block_rate = round(sensitive_blocked / total * 100, 1) if total > 0 else None
    event_counter: Counter = Counter(str(r.get("reason_code") or r.get("audit_event") or "UNKNOWN").upper() for r in ui_rows)
    top_events = [UIAssertionEventItem(audit_event=evt, count=cnt, pct=round(cnt / total * 100, 1) if total > 0 else 0.0) for evt, cnt in event_counter.most_common(10)]
    failed_reason_counter: Counter = Counter()
    for r in ui_rows:
        if str(r.get("reason_code") or "").upper() != "UI_PRECONDITION_FAILED":
            continue
        try:
            detail = json.loads(r.get("detail") or "{}") if isinstance(r.get("detail"), str) else {}
            for fa in (detail.get("failed_assertions") or []):
                failed_reason_counter[str(fa)] += 1
        except Exception:
            pass
    return UIAssertionStatsResponse(window_minutes=window_minutes, total_assertions=total, sensitive_blocked=sensitive_blocked, precondition_failed=precondition_failed, postcondition_failed=postcondition_failed, block_rate=block_rate, top_events=top_events, top_failed_reasons=[{"reason": r, "count": c} for r, c in failed_reason_counter.most_common(5)], timestamp=now)


# ---------------------------------------------------------------------------
# Ref #53: GET /obs/failure/stats
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
@fail_closed_projection_route
def get_failure_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #53: FailureMatrix 失败事件统计。"""
    ledger = projection_view
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    rows = _fetch_first_available_rows(ledger, "failure_log", "error_log", start_ms=since_ms, end_ms=now)
    _failure_events = {"GOVERNOR_BLOCK", "EVIDENCE_EMPTY", "MODEL_FALLBACK", "QA_REWRITE", "EDGE_UNREACHABLE", "VISION_LOW_CONFIDENCE", "MODULE_TIMEOUT"}
    failure_rows = [r for r in rows if str(r.get("reason_code") or r.get("event") or r.get("stage") or "").upper() in _failure_events]
    total = len(failure_rows)
    _abort_events = {"GOVERNOR_BLOCK", "VISION_LOW_CONFIDENCE", "MODULE_TIMEOUT"}
    _degraded_mode_map = {"EVIDENCE_EMPTY": "conservative_answer", "MODEL_FALLBACK": "fallback_model", "QA_REWRITE": "rewritten_output", "EDGE_UNREACHABLE": "text_only"}
    event_counter: Counter = Counter()
    degraded_mode_counter: Counter = Counter()
    abort_count = 0
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
    top_events = [FailureEventItem(event=evt, count=cnt, pct=round(cnt / total * 100, 1) if total > 0 else 0.0, degraded_mode=_degraded_mode_map.get(evt)) for evt, cnt in event_counter.most_common(10)]
    hourly_buckets: dict = defaultdict(int)
    for r in failure_rows:
        ts = int(r.get("ts_ms") or r.get("created_at_ms") or 0)
        if ts > 0:
            hourly_buckets[ts // (3600 * 1000)] += 1
    hourly_trend = [{"hour_ts_ms": h * 3600 * 1000, "count": c} for h, c in sorted(hourly_buckets.items())[-24:]]
    return FailureStatsResponse(window_minutes=window_minutes, total_failures=total, abort_count=abort_count, degraded_count=degraded_count, abort_rate=abort_rate, top_events=top_events, hourly_trend=hourly_trend, timestamp=now)


# ---------------------------------------------------------------------------
# Ref #54: GET /obs/permission/stats
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
@fail_closed_projection_route
def get_permission_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #54: PermissionGuard 权限拒绝统计。"""
    ledger = projection_view
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    rows = _fetch_first_available_rows(
        ledger,
        "permission_log",
        "audit_log",
        "error_log",
        start_ms=since_ms,
        end_ms=now,
    )
    denied_rows = [r for r in rows if "PERMISSION_DENIED" in str(r.get("audit_tag") or r.get("reason_code") or r.get("stage") or "").upper()]
    allowed_rows = [r for r in rows if "PERMISSION_ALLOWED" in str(r.get("audit_tag") or r.get("stage") or "").upper()]
    total_checks = len(denied_rows) + len(allowed_rows)
    denied_count = len(denied_rows)
    allowed_count = len(allowed_rows)
    deny_rate = round(denied_count / total_checks * 100, 1) if total_checks > 0 else None
    tool_counter: Counter = Counter()
    scheduler_denied = 0
    proposer_map: dict = {}
    for r in denied_rows:
        try:
            detail = json.loads(r.get("detail") or "{}") if isinstance(r.get("detail"), str) else {}
        except Exception:
            detail = {}
        tool_name = str(detail.get("tool") or r.get("tool_name") or "unknown")
        proposer = str(detail.get("proposer_class") or r.get("proposer_class") or "USER").upper()
        tool_counter[tool_name] += 1
        proposer_map[tool_name] = proposer
        if proposer == "SCHEDULER":
            scheduler_denied += 1
    top_denied_tools = [PermissionDeniedItem(tool_name=tool, deny_count=cnt, pct=round(cnt / denied_count * 100, 1) if denied_count > 0 else 0.0, proposer_class=proposer_map.get(tool)) for tool, cnt in tool_counter.most_common(10)]
    return PermissionStatsResponse(window_minutes=window_minutes, total_checks=total_checks, allowed_count=allowed_count, denied_count=denied_count, deny_rate=deny_rate, scheduler_denied_count=scheduler_denied, top_denied_tools=top_denied_tools, timestamp=now)


# ---------------------------------------------------------------------------
# Ref #55: GET /obs/scheduler/history
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
@fail_closed_projection_route
def get_scheduler_history(
    window_minutes: int = Query(60, ge=1, le=10080),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #55: TaskScheduler 执行历史趋势。"""
    ledger = projection_view
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    rows = _fetch_first_available_rows(
        ledger,
        "scheduler_log",
        "task_log",
        "audit_log",
        start_ms=since_ms,
        end_ms=now,
    )
    # Live scheduler state is exposed only by the injected SchedulerCommandService.
    # This observability route has no command-service authority, so it derives a
    # read-only history projection from durable audit rows instead of consulting
    # either retired process-global scheduler.
    scheduler_tasks: list[dict] = []
    if not scheduler_tasks:
        task_agg: dict = defaultdict(lambda: {"task_id": "", "name": None, "status": "UNKNOWN", "run_count": 0, "error_count": 0, "last_run_ts": None, "last_error": None})
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
        raw_codes = t.get("last_immune_reason_codes")
        if isinstance(raw_codes, (list, tuple, set)):
            immune_codes = [str(c).strip().upper()[:80] for c in raw_codes if str(c).strip()][:6]
        elif raw_codes:
            immune_codes = [str(raw_codes).strip().upper()[:80]]
        else:
            immune_codes = []
        tasks.append(SchedulerTaskItem(
            task_id=str(t.get("task_id") or ""), name=t.get("name"),
            status=str(t.get("status") or "UNKNOWN").upper(),
            run_count=int(t.get("run_count") or 0), error_count=int(t.get("error_count") or 0),
            error_rate=round(int(t.get("error_count") or 0) / int(t.get("run_count") or 1) * 100, 1) if int(t.get("run_count") or 0) > 0 else None,
            last_run_ts=float(t["last_run_ts"]) if t.get("last_run_ts") else None,
            last_error=str(t["last_error"])[:200] if t.get("last_error") else None,
            last_reason_code=str(t.get("last_reason_code") or "").strip()[:80] or None,
            last_gate_state=str(t.get("last_gate_state") or "").strip().lower()[:32] or None,
            last_mood_state=str(t.get("last_mood_state") or "").strip().lower()[:32] or None,
            last_endocrine_cortisol=float(t["last_endocrine_cortisol"]) if t.get("last_endocrine_cortisol") is not None else None,
            last_endocrine_energy=float(t["last_endocrine_energy"]) if t.get("last_endocrine_energy") is not None else None,
            last_immune_decision=str(t.get("last_immune_decision") or "").strip().upper()[:16] or None,
            last_immune_risk_level=str(t.get("last_immune_risk_level") or "").strip().lower()[:16] or None,
            last_immune_reason_codes=immune_codes,
        ))
    reason_counter: Counter[str] = Counter()
    gate_counter: Counter[str] = Counter()
    for task in tasks:
        if task.last_reason_code:
            reason_counter[task.last_reason_code] += 1
        if task.last_gate_state:
            gate_counter[task.last_gate_state] += 1
    blocked_reason_total = sum(cnt for code, cnt in reason_counter.items() if code.endswith("_BLOCKED") or code in {"SYSMODE_FORBIDDEN", "IMMUNE_PROACTIVE_SUPPRESSED"})
    gate_blocked_rate = round(blocked_reason_total / total_tasks * 100, 1) if total_tasks > 0 else None
    blocked_reason_codes = [SchedulerReasonItem(reason_code=code, count=cnt, pct=round(cnt / total_tasks * 100, 1) if total_tasks > 0 else 0.0) for code, cnt in reason_counter.most_common(10) if code.endswith("_BLOCKED") or code in {"SYSMODE_FORBIDDEN", "IMMUNE_PROACTIVE_SUPPRESSED"}]
    gate_states = [SchedulerGateItem(gate_state=gs, count=cnt, pct=round(cnt / total_tasks * 100, 1) if total_tasks > 0 else 0.0) for gs, cnt in gate_counter.most_common(10)]
    hourly_runs: dict = defaultdict(int)
    hourly_errors: dict = defaultdict(int)
    for r in rows:
        ts = int(r.get("ts_ms") or r.get("last_run_ts") or 0)
        if ts > 0:
            h = ts // (3600 * 1000)
            hourly_runs[h] += int(r.get("run_count") or 1)
            hourly_errors[h] += int(r.get("error_count") or 0)
    all_hours = sorted(set(list(hourly_runs.keys()) + list(hourly_errors.keys())))
    run_trend = [{"hour_ts_ms": h * 3600 * 1000, "run_count": hourly_runs.get(h, 0), "error_count": hourly_errors.get(h, 0)} for h in all_hours[-24:]]
    return SchedulerHistoryResponse(window_minutes=window_minutes, total_tasks=total_tasks, running_count=running_count, success_count=success_count, failed_count=failed_count, disabled_count=disabled_count, overall_error_rate=overall_error_rate, gate_blocked_total=blocked_reason_total, gate_blocked_rate=gate_blocked_rate, blocked_reason_codes=blocked_reason_codes, gate_states=gate_states, tasks=tasks, run_trend=run_trend, timestamp=now)


# ---------------------------------------------------------------------------
# Ref #56: GET /obs/action-plan/stats
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
@fail_closed_projection_route
def get_action_plan_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #56: ActionPlan 执行成功率/指令类型分布。"""
    ledger = projection_view
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    rows = _fetch_first_available_rows(
        ledger,
        "action_plan_log",
        "vision_log",
        "audit_log",
        start_ms=since_ms,
        end_ms=now,
    )
    total = len(rows)
    if total == 0:
        return ActionPlanStatsResponse(window_minutes=window_minutes, total_plans=0, success_count=0, fallback_count=0, success_rate=None, avg_confidence=None, avg_action_count=None, top_action_types=[], fallback_reasons=[], timestamp=now)
    action_type_counter: Counter = Counter()
    fallback_reason_counter: Counter = Counter()
    confidence_list: list = []
    action_count_list: list = []
    success_count = 0
    fallback_count = 0
    for r in rows:
        try:
            detail = json.loads(r.get("detail") or "{}") if isinstance(r.get("detail"), str) else {}
        except Exception:
            detail = {}
        extra = detail.get("extra") or {}
        is_fallback = bool(extra.get("fallback") or r.get("is_fallback"))
        if is_fallback:
            fallback_count += 1
            fallback_reason_counter[str(extra.get("reason") or r.get("fallback_reason") or "UNKNOWN")] += 1
        else:
            success_count += 1
        confidence = float(extra.get("confidence") or r.get("confidence") or 0.0)
        if confidence > 0:
            confidence_list.append(confidence)
        actions = detail.get("actions") or []
        action_count_list.append(len(actions))
        for action in actions:
            action_type_counter[str(action.get("type") or "unknown").lower()] += 1
    total_actions = sum(action_type_counter.values())
    return ActionPlanStatsResponse(
        window_minutes=window_minutes, total_plans=total, success_count=success_count, fallback_count=fallback_count,
        success_rate=round(success_count / total * 100, 1) if total > 0 else None,
        avg_confidence=round(sum(confidence_list) / len(confidence_list), 3) if confidence_list else None,
        avg_action_count=round(sum(action_count_list) / len(action_count_list), 1) if action_count_list else None,
        top_action_types=[ActionTypeItem(action_type=at, count=cnt, pct=round(cnt / total_actions * 100, 1) if total_actions > 0 else 0.0) for at, cnt in action_type_counter.most_common(10)],
        fallback_reasons=[{"reason": r, "count": c} for r, c in fallback_reason_counter.most_common(5)],
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #59: GET /obs/memory-guard/stats
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
@fail_closed_projection_route
def get_memory_guard_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #59: MemoryGuard 宪法校验统计。"""
    ledger = projection_view
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    rows = _fetch_first_available_rows(
        ledger,
        "memory_guard_log",
        "audit_log",
        "error_log",
        start_ms=since_ms,
        end_ms=now,
    )
    _mg_tags = {"MEMORY_GUARD_FILTERED", "MEMORY_GUARD_DREAM_BLOCKED", "MEMORY_GUARD_NARRATIVE_BLOCKED", "MEMORY_GUARD_DISTILL_ELIGIBLE", "EPISODIC_WRITTEN", "EPISODIC_SKIPPED"}
    mg_rows = [r for r in rows if any(tag in str(r.get("audit_tag") or r.get("stage") or r.get("reason_code") or "").upper() for tag in _mg_tags)]
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
                    mg_rows.append({**row, "audit_tag": "MEMORY_GUARD_FILTERED" if "MEMORY_GUARD" in reason.upper() else "EPISODIC_SKIPPED"})
    except Exception:
        pass
    total = len(mg_rows)
    passed_count = sum(1 for r in mg_rows if "EPISODIC_WRITTEN" in str(r.get("audit_tag") or "").upper())
    blocked_count = sum(1 for r in mg_rows if any(t in str(r.get("audit_tag") or "").upper() for t in ("MEMORY_GUARD_FILTERED", "MEMORY_GUARD_DREAM_BLOCKED", "MEMORY_GUARD_NARRATIVE_BLOCKED")))
    narrative_only_count = sum(1 for r in mg_rows if "NARRATIVE_BLOCKED" in str(r.get("audit_tag") or "").upper())
    dream_blocked_count = sum(1 for r in mg_rows if "DREAM_BLOCKED" in str(r.get("audit_tag") or "").upper())
    distill_eligible_count = sum(1 for r in mg_rows if "DISTILL_ELIGIBLE" in str(r.get("audit_tag") or "").upper())
    block_rate = round(blocked_count / total * 100, 1) if total > 0 else None
    reason_counter: Counter = Counter()
    for r in mg_rows:
        try:
            detail = json.loads(r.get("detail") or "{}") if isinstance(r.get("detail"), str) else {}
        except Exception:
            detail = {}
        reason = str(detail.get("reason") or r.get("reason_code") or "UNKNOWN").upper()
        if reason != "UNKNOWN":
            reason_counter[reason] += 1
    return MemoryGuardStatsResponse(window_minutes=window_minutes, total_checks=total, passed_count=passed_count, blocked_count=blocked_count, block_rate=block_rate, narrative_only_count=narrative_only_count, dream_blocked_count=dream_blocked_count, distill_eligible_count=distill_eligible_count, top_reject_reasons=[MemoryGuardRejectItem(reason=r, count=c, pct=round(c / blocked_count * 100, 1) if blocked_count > 0 else 0.0) for r, c in reason_counter.most_common(10)], timestamp=now)


# ---------------------------------------------------------------------------
# Ref #60: GET /obs/state-arbiter/stats
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
@fail_closed_projection_route
def get_state_arbiter_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #60: StateArbiter 事实仲裁统计。"""
    ledger = projection_view
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    rows = _fetch_first_available_rows(
        ledger,
        "state_arbiter_log",
        "audit_log",
        "error_log",
        start_ms=since_ms,
        end_ms=now,
    )
    _sa_tags = {"STATE_SOURCE_CONFLICT", "STATE_SOURCE_FALLBACK", "STATE_ARBITER_CONFLICT", "SESSION_OVERRIDE_WRITTEN"}
    sa_rows = [r for r in rows if any(tag in str(r.get("audit_tag") or r.get("stage") or r.get("reason_code") or "").upper() for tag in _sa_tags)]
    total = len(sa_rows)
    conflict_count = sum(1 for r in sa_rows if "CONFLICT" in str(r.get("audit_tag") or "").upper())
    fallback_count = sum(1 for r in sa_rows if "FALLBACK" in str(r.get("audit_tag") or "").upper())
    session_override_count = sum(1 for r in sa_rows if "SESSION_OVERRIDE" in str(r.get("audit_tag") or "").upper())
    source_counter: Counter = Counter()
    conflict_key_counter: Counter = Counter()
    for r in sa_rows:
        try:
            detail = json.loads(r.get("detail") or "{}") if isinstance(r.get("detail"), str) else {}
        except Exception:
            detail = {}
        winner_source = str(detail.get("winner_source") or r.get("source") or "UNKNOWN").upper()
        if winner_source != "UNKNOWN":
            source_counter[winner_source] += 1
        fact_key = str(detail.get("fact_key") or r.get("fact_key") or "")
        if fact_key and "CONFLICT" in str(r.get("audit_tag") or "").upper():
            conflict_key_counter[fact_key] += 1
    total_hits = sum(source_counter.values())
    return StateArbiterStatsResponse(window_minutes=window_minutes, total_resolutions=total, conflict_count=conflict_count, fallback_count=fallback_count, conflict_rate=round(conflict_count / total * 100, 1) if total > 0 else None, fallback_rate=round(fallback_count / total * 100, 1) if total > 0 else None, session_override_count=session_override_count, top_sources=[StateSourceItem(source=src, hit_count=cnt, pct=round(cnt / total_hits * 100, 1) if total_hits > 0 else 0.0) for src, cnt in source_counter.most_common(10)], top_conflict_keys=[{"fact_key": k, "conflict_count": c} for k, c in conflict_key_counter.most_common(5)], timestamp=now)


# ---------------------------------------------------------------------------
# Ref #61: GET /obs/context-compressor/stats
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
@fail_closed_projection_route
def get_context_compressor_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #61: ContextCompressor 压缩效率统计。"""
    ledger = projection_view
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    rows = _fetch_first_available_rows(
        ledger,
        "context_compressor_log",
        "audit_log",
        "error_log",
        start_ms=since_ms,
        end_ms=now,
    )
    cc_rows = [r for r in rows if "CTX_COMPRESSED" in str(r.get("audit_tag") or r.get("stage") or "").upper() or "CONTEXT_COMPRESSOR" in str(r.get("stage") or r.get("reason_code") or "").upper()]
    total = len(cc_rows)
    active_count = sum(1 for r in cc_rows if "CTX_COMPRESSED" in str(r.get("audit_tag") or "").upper())
    degraded_count = sum(1 for r in cc_rows if "DEGRADED" in str(r.get("audit_tag") or r.get("stage") or "").upper())
    compression_ratios: list = []
    token_savings: list = []
    total_tokens_saved = 0
    strategy_counter: Counter = Counter()
    summary_generated_count = 0
    for r in cc_rows:
        try:
            detail = json.loads(r.get("detail") or "{}") if isinstance(r.get("detail"), str) else {}
        except Exception:
            detail = {}
        input_turns = int(detail.get("input_turns") or r.get("input_turns") or 0)
        output_turns = int(detail.get("output_turns") or r.get("output_turns") or 0)
        strategy = str(detail.get("strategy") or r.get("strategy") or "unknown").lower()
        has_summary = bool(detail.get("has_summary") or r.get("has_summary"))
        if input_turns > 0 and output_turns > 0:
            compression_ratios.append(round(output_turns / input_turns, 3))
            saved = input_turns - output_turns
            token_savings.append(saved)
            total_tokens_saved += saved
        if strategy and strategy != "unknown":
            strategy_counter[strategy] += 1
        if has_summary:
            summary_generated_count += 1
    total_strategies = sum(strategy_counter.values())
    return ContextCompressorStatsResponse(window_minutes=window_minutes, total_compressions=total, active_count=active_count, degraded_count=degraded_count, avg_compression_ratio=round(sum(compression_ratios) / len(compression_ratios), 3) if compression_ratios else None, avg_token_saving=round(sum(token_savings) / len(token_savings), 1) if token_savings else None, total_tokens_saved=total_tokens_saved, strategy_distribution=[{"strategy": s, "count": c, "pct": round(c / total_strategies * 100, 1) if total_strategies > 0 else 0.0} for s, c in strategy_counter.most_common(5)], summary_generated_count=summary_generated_count, timestamp=now)


# ---------------------------------------------------------------------------
# Ref #65: GET /obs/prompt-builder/stats
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


def build_prompt_builder_stats_from_view(
    projection_view: Any,
    *,
    window_minutes: int,
) -> PromptBuilderStatsResponse:
    """Build prompt statistics from one explicitly supplied read view."""

    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    rows = _fetch_first_available_rows(
        projection_view,
        "prompt_builder_log",
        "audit_log",
        "error_log",
        start_ms=since_ms,
        end_ms=now,
    )
    pb_rows = [
        row
        for row in rows
        if any(
            tag
            in str(
                row.get("audit_tag")
                or row.get("stage")
                or row.get("reason_code")
                or ""
            ).upper()
            for tag in (
                "PROMPT_BUILT",
                "PROMPT_INJECTION",
                "PROMPT_BUILDER",
                "INJECTION_DETECTED",
            )
        )
    ]
    total = len(pb_rows)
    injection_detected_count = sum(
        1
        for row in pb_rows
        if "INJECTION_DETECTED" in str(row.get("audit_tag") or "").upper()
    )
    injection_blocked_count = sum(
        1
        for row in pb_rows
        if "INJECTION_BLOCKED" in str(row.get("audit_tag") or "").upper()
    )
    injection_detection_rate = (
        round(injection_detected_count / total * 100, 1) if total > 0 else None
    )
    degraded_count = sum(
        1
        for row in pb_rows
        if "DEGRADED"
        in str(row.get("audit_tag") or row.get("stage") or "").upper()
    )
    high_risk_count = 0
    critical_risk_count = 0
    slot_counter: Counter = Counter()
    message_lengths: list[int] = []
    for row in pb_rows:
        try:
            detail = (
                json.loads(row.get("detail") or "{}")
                if isinstance(row.get("detail"), str)
                else {}
            )
        except Exception:
            detail = {}
        risk_level = str(
            detail.get("risk_level") or row.get("risk_level") or ""
        ).upper()
        if risk_level == "HIGH":
            high_risk_count += 1
        elif risk_level == "CRITICAL":
            critical_risk_count += 1
        for slot in list(
            detail.get("slots_filled") or row.get("slots_filled") or []
        ):
            slot_counter[str(slot)] += 1
        message_length = int(
            detail.get("user_message_len") or row.get("user_message_len") or 0
        )
        if message_length > 0:
            message_lengths.append(message_length)
    total_slot_fills = sum(slot_counter.values())
    return PromptBuilderStatsResponse(
        window_minutes=window_minutes,
        total_builds=total,
        injection_detected_count=injection_detected_count,
        injection_blocked_count=injection_blocked_count,
        injection_detection_rate=injection_detection_rate,
        high_risk_count=high_risk_count,
        critical_risk_count=critical_risk_count,
        slot_fill_distribution=[
            {
                "slot": slot,
                "count": count,
                "pct": (
                    round(count / total_slot_fills * 100, 1)
                    if total_slot_fills > 0
                    else 0.0
                ),
            }
            for slot, count in slot_counter.most_common(10)
        ],
        avg_user_message_len=(
            round(sum(message_lengths) / len(message_lengths), 1)
            if message_lengths
            else None
        ),
        degraded_count=degraded_count,
        timestamp=now,
    )


@router.get("/prompt-builder/stats", response_model=PromptBuilderStatsResponse)
@fail_closed_projection_route
def get_prompt_builder_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
    route_ports: Annotated[ObsRoutePorts, Depends(require_obs_route_ports)],
):
    """Ref #65: PromptBuilder 构建统计。"""
    return route_ports.response_cache.get_or_build(
        cache_name="prompt_builder_stats",
        cache_key={"window_minutes": int(window_minutes)},
        ttl_ms=_PROMPT_BUILDER_STATS_CACHE_TTL_MS,
        wait_timeout_s=5.0,
        builder=lambda: build_prompt_builder_stats_from_view(
            projection_view,
            window_minutes=window_minutes,
        ),
    )


# ---------------------------------------------------------------------------
# Ref #66: GET /obs/episodic-store/stats
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
@fail_closed_projection_route
def get_episodic_store_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #66: EpisodicStore 写入统计。"""
    ledger = projection_view
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    rows = _fetch_first_available_rows(
        ledger,
        "episodic_store_log",
        "audit_log",
        "error_log",
        start_ms=since_ms,
        end_ms=now,
    )
    _ep_tags = {"EPISODIC_WRITTEN", "EPISODIC_SKIPPED", "MEMORY_GUARD_DREAM_BLOCKED", "MEMORY_GUARD_NARRATIVE_BLOCKED", "MEMORY_GUARD_DISTILL_ELIGIBLE", "SKIPPED_MEMORY_GUARD"}
    ep_rows = [r for r in rows if any(tag in str(r.get("audit_tag") or r.get("stage") or r.get("reason_code") or "").upper() for tag in _ep_tags)]
    try:
        mem_ops = ledger.fetch_by_time_range("memory_ops_log", start_ms=since_ms, end_ms=now)
        if mem_ops:
            for row in mem_ops:
                if str(row.get("op_kind") or "").upper() == "WRITE" and str(row.get("status") or "").upper() == "SUCCESS":
                    ep_rows.append({**row, "audit_tag": "EPISODIC_WRITTEN"})
                elif str(row.get("op_kind") or "").upper() == "WRITE" and str(row.get("status") or "").upper() == "SKIPPED":
                    ep_rows.append({**row, "audit_tag": "EPISODIC_SKIPPED"})
    except Exception:
        pass
    total = len(ep_rows)
    written_count = sum(1 for r in ep_rows if "EPISODIC_WRITTEN" in str(r.get("audit_tag") or "").upper())
    skipped_count = sum(1 for r in ep_rows if any(t in str(r.get("audit_tag") or r.get("stage") or "").upper() for t in ("EPISODIC_SKIPPED", "SKIPPED_MEMORY_GUARD")))
    memory_guard_blocked_count = sum(1 for r in ep_rows if any(t in str(r.get("audit_tag") or "").upper() for t in ("MEMORY_GUARD_DREAM_BLOCKED", "MEMORY_GUARD_NARRATIVE_BLOCKED", "MEMORY_GUARD_FILTERED")))
    dream_blocked_count = sum(1 for r in ep_rows if "DREAM_BLOCKED" in str(r.get("audit_tag") or "").upper())
    narrative_only_blocked_count = sum(1 for r in ep_rows if "NARRATIVE_BLOCKED" in str(r.get("audit_tag") or "").upper())
    distill_eligible_count = sum(1 for r in ep_rows if "DISTILL_ELIGIBLE" in str(r.get("audit_tag") or "").upper())
    occurrence_counts: list = []
    for r in ep_rows:
        try:
            detail = json.loads(r.get("detail") or "{}") if isinstance(r.get("detail"), str) else {}
        except Exception:
            detail = {}
        occ = int(detail.get("occurrence_count") or r.get("occurrence_count") or 0)
        if occ > 0:
            occurrence_counts.append(occ)
    return EpisodicStoreStatsResponse(window_minutes=window_minutes, total_attempts=total, written_count=written_count, skipped_count=skipped_count, write_rate=round(written_count / total * 100, 1) if total > 0 else None, memory_guard_blocked_count=memory_guard_blocked_count, dream_blocked_count=dream_blocked_count, narrative_only_blocked_count=narrative_only_blocked_count, distill_eligible_count=distill_eligible_count, avg_occurrence_count=round(sum(occurrence_counts) / len(occurrence_counts), 1) if occurrence_counts else None, timestamp=now)


# ---------------------------------------------------------------------------
# Ref #82: GET /obs/chain/stats
# ---------------------------------------------------------------------------
class ChainNodeStatItem(BaseModel):
    node_name: str
    total_calls: int
    degraded_count: int
    degraded_rate: float
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
@fail_closed_projection_route
def get_chain_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #82: 请求处理阶段统计（Phase XIV v4 cortex_orchestrator 架构）。"""
    ledger = projection_view
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    rows = _fetch_rows_if_available(ledger, "audit_log", start_ms=since_ms, end_ms=now)
    audit_rows = [r for r in rows if str(r.get("stage") or "").upper() in ("AUDIT_SNAPSHOT", "COMMIT")]
    total_requests = len(audit_rows)
    governor_block_count = recall_degraded_count = episodic_written_count = qa_rewrite_count = canon_proposal_count = 0
    _KNOWN_NODES = ["airlock", "governor", "retrieval", "prompt_builder", "llm_stream", "response_intelligence", "sub_agent", "airlock_output", "episodic_store"]
    node_latencies: dict[str, list[float]] = {n: [] for n in _KNOWN_NODES}
    node_degraded: dict[str, int] = {n: 0 for n in _KNOWN_NODES}
    node_timeout: dict[str, int] = {n: 0 for n in _KNOWN_NODES}
    node_calls: dict[str, int] = {n: 0 for n in _KNOWN_NODES}
    for row in audit_rows:
        try:
            detail = json.loads(row.get("detail") or "{}") if isinstance(row.get("detail"), str) else {}
        except Exception:
            detail = {}
        audit_tags = list(detail.get("audit_tags") or [])
        node_timings_ms = dict(detail.get("node_timings_ms") or {})
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
        for node_name, latency_ms in node_timings_ms.items():
            if node_name not in node_latencies:
                node_latencies[node_name] = []
                node_degraded[node_name] = 0
                node_timeout[node_name] = 0
                node_calls[node_name] = 0
            node_calls[node_name] += 1
            if isinstance(latency_ms, (int, float)) and latency_ms > 0:
                node_latencies[node_name].append(float(latency_ms))
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
            p95_lat = round(sorted_lat[min(int(len(sorted_lat) * 0.95), len(sorted_lat) - 1)], 1)
        node_stats.append(ChainNodeStatItem(node_name=node_name, total_calls=calls, degraded_count=degraded, degraded_rate=round(degraded / calls * 100, 1) if calls > 0 else 0.0, avg_latency_ms=avg_lat, p95_latency_ms=p95_lat, timeout_count=timeout))
    return ChainStatsResponse(window_minutes=window_minutes, total_requests=total_requests, governor_block_count=governor_block_count, governor_block_rate=round(governor_block_count / total_requests * 100, 1) if total_requests > 0 else 0.0, recall_degraded_count=recall_degraded_count, episodic_written_count=episodic_written_count, qa_rewrite_count=qa_rewrite_count, canon_proposal_count=canon_proposal_count, node_stats=node_stats, timestamp=now)


# ---------------------------------------------------------------------------
# Ref #86: GET /obs/agent-loop/history
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
    schema_version: str = "Natsume.operator.query_projection.v1"
    projection_version: int = 0
    cursor: int = 0
    freshness_ts_ms: int = 0
    degraded_state: str = "REBUILDING"


@router.get("/agent-loop/history", response_model=AgentLoopHistoryResponse)
def get_agent_loop_history(
    window_minutes: int = Query(1440, ge=1, le=10080),
    limit: int = Query(10, ge=1, le=50),
    *,
    projection_store: Annotated[Any, Depends(require_obs_query_projection_store)],
):
    """Ref #86: 只读 Agent Loop 历史投影。"""
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    payload, meta = read_from_store(
        projection_store,
        AGENT_LOOP_KIND,
        AGENT_LOOP_INDEX_KEY,
    )
    rows = [
        dict(row)
        for row in list((payload or {}).get("history") or [])
        if isinstance(row, dict) and int(row.get("ts_ms") or 0) >= since_ms
    ][:limit]
    items = [AgentLoopHistoryItem(**row) for row in rows]
    return AgentLoopHistoryResponse(
        items=items,
        total=len(items),
        window_minutes=window_minutes,
        timestamp=now,
        **response_meta(meta),
    )


# ---------------------------------------------------------------------------
# Ref #88: GET /obs/error-heatmap
# ---------------------------------------------------------------------------
class ErrorHeatmapCell(BaseModel):
    hour: int
    node_name: str
    error_count: int
    pct: float


class ErrorHeatmapResponse(BaseModel):
    window_hours: int
    total_errors: int
    hours: List[int]
    nodes: List[str]
    cells: List[ErrorHeatmapCell]
    hourly_totals: List[Dict[str, Any]]
    node_totals: List[Dict[str, Any]]
    timestamp: int


@router.get("/error-heatmap", response_model=ErrorHeatmapResponse)
@fail_closed_projection_route
def get_error_heatmap(
    window_hours: int = Query(24, ge=1, le=168),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #88: 错误热力图端点 — 按小时 × 节点维度统计错误分布。"""
    import datetime as _dt
    ledger = projection_view
    now = _now_ms()
    since_ms = now - window_hours * 3600 * 1000
    try:
        rows = ledger.fetch_by_time_range("error_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []
    cell_counts: dict[tuple[int, str], int] = defaultdict(int)
    hourly_counts: dict[int, int] = defaultdict(int)
    node_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        ts = row.get("ts_ms") or 0
        if ts <= 0:
            continue
        hour = _dt.datetime.fromtimestamp(ts / 1000).hour
        node_name = str(row.get("node_id") or row.get("stage") or row.get("source") or "unknown")
        cell_counts[(hour, node_name)] += 1
        hourly_counts[hour] += 1
        node_counts[node_name] += 1
    total_errors = sum(hourly_counts.values())
    hours_list = sorted(hourly_counts.keys())
    nodes_list = sorted(node_counts.keys(), key=lambda n: node_counts[n], reverse=True)[:20]
    cells = [ErrorHeatmapCell(hour=hour, node_name=node_name, error_count=cnt, pct=round(cnt / hourly_counts.get(hour, 1) * 100, 1)) for (hour, node_name), cnt in cell_counts.items() if node_name in nodes_list]
    return ErrorHeatmapResponse(window_hours=window_hours, total_errors=total_errors, hours=hours_list, nodes=nodes_list, cells=cells, hourly_totals=[{"hour": h, "total": hourly_counts[h]} for h in range(24)], node_totals=[{"node_name": n, "total": node_counts[n]} for n in nodes_list], timestamp=now)
