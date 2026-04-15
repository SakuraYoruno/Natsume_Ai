"""obs_routes_stats_capability.py — 可观测性统计端点（从 obs_routes_stats.py 拆分）。

子模块：召回包/UI断言/失败/权限/调度器/规划/记忆守卫/状态仲裁/上下文压缩/Prompt构建/情节存储/链路/语义召回/免疫门/检索/能力缺口统计
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
