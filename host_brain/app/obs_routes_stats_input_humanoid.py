"""obs_routes_stats_input_humanoid.py — Gateway 人体闭环系列统计端点。

拆分自 obs_routes_stats_input.py（P2-8）：
  GET /obs/input/humanoid-loop-stats
  GET /obs/input/humanoid-loop-trend
  GET /obs/input/humanoid-loop-alerts
  GET /obs/input/humanoid-loop-remediation
  GET /obs/input/humanoid-loop-diagnostics
  GET /obs/input/humanoid-loop-hotspots
  GET /obs/input/humanoid-loop-hotspot-trend
  GET /obs/input/humanoid-loop-source-correlation
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Annotated, Any, Dict, List, Optional

from fastapi import Depends, Query
from pydantic import BaseModel
from host_brain.app.obs_route_ports import require_obs_projection_ledger_view
from host_brain.app.observability_runtime.projection_ledger_view import (
    fail_closed_projection_route,
)
from host_brain.observability.model_attempt_projection import (
    latest_terminal_model_attempts,
)

from host_brain.app.obs_routes_core import (
    router,
    _build_trace_summary,
    _extract_input_dispatch_latency_ms,
    _fetch_request_rows,
    _is_input_dispatch_event,
    _normalize_input_dispatch_source,
    _now_ms,
    _safe_json,
)

logger = logging.getLogger("Natsume.obs")


# ---------------------------------------------------------------------------
# 共享辅助函数
# ---------------------------------------------------------------------------
def _build_gateway_request_flags_from_voice_rows(voice_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    flags: Dict[str, Any] = {
        "accepted": False, "completed": False, "failed": False,
        "ack_pending": False, "acked": False,
        "progress_fastpath": False, "progress_pushed": False,
        "deviation_detected": False, "deviation_recovered": False,
        "reason_codes": [],
    }
    for row in sorted(
        [dict(item) for item in voice_rows if isinstance(item, dict)],
        key=lambda item: (int(item.get("ts_ms") or 0), int(item.get("seq") or 0), str(item.get("event_type") or "")),
    ):
        event_type = str(row.get("event_type") or row.get("status") or "").strip().upper()
        if not event_type:
            continue
        payload = _safe_json(row.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        source = _normalize_input_dispatch_source(
            payload_dict.get("source") or row.get("source") or row.get("channel"), event_type,
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


def _collect_gateway_request_voice_groups(
    rows: List[Dict[str, Any]],
) -> tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Dict[str, Optional[int]]]]:
    request_rows: Dict[str, List[Dict[str, Any]]] = {}
    request_meta: Dict[str, Dict[str, Optional[int]]] = {}
    for row in sorted(
        [dict(item) for item in rows if isinstance(item, dict)],
        key=lambda item: (int(item.get("ts_ms") or 0), int(item.get("seq") or 0), str(item.get("event_type") or "")),
    ):
        event_type = str(row.get("event_type") or row.get("status") or "").strip().upper()
        if not event_type:
            continue
        payload = _safe_json(row.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        source = _normalize_input_dispatch_source(
            payload_dict.get("source") or row.get("source") or row.get("channel"), event_type,
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


def _classify_humanoid_loop_root_cause_snapshot(
    *, flags: Dict[str, Any], governor_rows: List[Dict[str, Any]], error_rows: List[Dict[str, Any]], fallback_reason: str = "",
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
        suspected_breakpoint, root_cause_code = "governor_gate", "GOVERNOR_BLOCKED"
    elif flags.get("failed"):
        suspected_breakpoint, root_cause_code = "execution_stage", "EXECUTION_FAILED"
    elif flags.get("deviation_detected") and not flags.get("deviation_recovered"):
        suspected_breakpoint, root_cause_code = "deviation_recovery", "DEVIATION_NOT_RECOVERED"
    elif flags.get("completed") and not flags.get("ack_touched") and not flags.get("progress_touched"):
        suspected_breakpoint, root_cause_code = "humanoid_touch", "COMPLETED_WITHOUT_HUMANOID_TOUCH"
    elif not flags.get("ack_touched"):
        suspected_breakpoint, root_cause_code = "ack_touch_path", "ACK_PATH_MISSING"
    elif not flags.get("progress_touched") and not flags.get("completed"):
        suspected_breakpoint, root_cause_code = "progress_touch_path", "PROGRESS_PATH_MISSING"
    elif flags.get("completed") and not flags.get("closed_loop"):
        suspected_breakpoint, root_cause_code = "closed_loop_convergence", "CLOSED_LOOP_NOT_CONVERGED"
    else:
        suspected_breakpoint, root_cause_code = "trace_review", "TRACE_REVIEW_REQUIRED"

    return {"root_cause_code": root_cause_code, "suspected_breakpoint": suspected_breakpoint, "reason_hint": reason_hint, "evidence_codes": evidence_codes[:6]}


def _resolve_humanoid_loop_hotspot_profile(*, root_cause_code: str, suspected_breakpoint: str) -> Dict[str, str]:
    profiles: Dict[str, Dict[str, str]] = {
        "ACK_PATH_MISSING": {"organ_system": "language_feedback", "organ_label": "语言回执系统", "operator_focus": "优先检查即时 ACK 开关、超时配置和人格化确认回路是否被短路。", "organ_story": "生命体已经收到请求，但没有先开口回应，用户体感会像神经已接入、语言器官却失声。", "recommended_view_path": "/system", "recommended_view_label": "检查 ACK 配置"},
        "PROGRESS_PATH_MISSING": {"organ_system": "somatic_progress", "organ_label": "体感进度系统", "operator_focus": "优先检查 progress fastpath、主动进度推送和长任务回报节律。", "organ_story": "身体还在执行，但没有把过程反馈给外界，表现为'人还活着，却像完全没动静'。", "recommended_view_path": "/gateway", "recommended_view_label": "检查进度回路"},
        "COMPLETED_WITHOUT_HUMANOID_TOUCH": {"organ_system": "language_feedback", "organ_label": "语言回执系统", "operator_focus": "检查 ACK 与 Progress 触达链路是否同时缺席，避免'做完了但一句话不说'。", "organ_story": "动作已经完成，但生命体没有留下任何触达痕迹，容易给用户造成冰冷的后台感。", "recommended_view_path": "/gateway", "recommended_view_label": "复核触达闭环"},
        "EXECUTION_FAILED": {"organ_system": "motor_execution", "organ_label": "运动执行系统", "operator_focus": "优先检查失败 skill、工具调用、超时和依赖服务状态。", "organ_story": "意图已经下达到身体，但真正动手的执行器失步了，表现为'知道该做，却做不成'。", "recommended_view_path": "", "recommended_view_label": "打开失败链路"},
        "DEVIATION_NOT_RECOVERED": {"organ_system": "immune_realign", "organ_label": "免疫纠偏系统", "operator_focus": "检查 deviation detect / notice / retry 是否真的形成了 recover 闭环。", "organ_story": "系统已经感知自己偏航，但免疫纠偏没有把轨道拉回来，属于自愈机制失手。", "recommended_view_path": "", "recommended_view_label": "查看偏航修复"},
        "GOVERNOR_BLOCKED": {"organ_system": "prefrontal_governor", "organ_label": "前额叶门控系统", "operator_focus": "检查 Governor/Immune Gate 决策、审批令牌与风控策略是否过严。", "organ_story": "动作还没开始，前额叶就先把身体刹住了，通常是门控、审批或免疫规则造成的阻断。", "recommended_view_path": "/gateway", "recommended_view_label": "检查门控链路"},
        "CLOSED_LOOP_NOT_CONVERGED": {"organ_system": "closed_loop_integration", "organ_label": "闭环整合系统", "operator_focus": "复核 ACK、Progress、Deviation Recovery 是否全部在同一次请求上完成闭合。", "organ_story": "链路各器官都动了一部分，但没能真正收束成一次完整的人体闭环。", "recommended_view_path": "/gateway", "recommended_view_label": "查看闭环总览"},
        "TRACE_REVIEW_REQUIRED": {"organ_system": "closed_loop_integration", "organ_label": "闭环整合系统", "operator_focus": "直接打开 Trace 逐节点回放，定位异常到底落在理解、执行还是回执层。", "organ_story": "当前异常还没有形成稳定模式，需要先通过回放把真正病灶定位出来。", "recommended_view_path": "", "recommended_view_label": "打开链路回放"},
    }
    fallback = {"organ_system": "closed_loop_integration", "organ_label": "闭环整合系统", "operator_focus": "先从 Trace 回放确认断点，再决定是补触达、修执行还是调门控。", "organ_story": "当前病灶尚未命中稳定模板，需要通过链路回放继续收敛。", "recommended_view_path": "", "recommended_view_label": "打开链路回放"}
    profile = dict(profiles.get(str(root_cause_code or "").strip().upper(), fallback))
    if not profile.get("organ_system") and suspected_breakpoint == "execution_stage":
        profile["organ_system"] = "motor_execution"
        profile["organ_label"] = "运动执行系统"
    return profile


# ---------------------------------------------------------------------------
# Pydantic 响应模型
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


# ---------------------------------------------------------------------------
# GET /obs/input/humanoid-loop-stats
# ---------------------------------------------------------------------------
@router.get("/input/humanoid-loop-stats", response_model=InputHumanoidLoopStatsResponse)
@fail_closed_projection_route
def get_input_humanoid_loop_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #48B: Gateway 人体闭环统计（ACK / 进度回报 / 偏航恢复）。"""
    ledger = projection_view
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=now, limit=20_000)
        dispatch_rows = [r for r in rows if _is_input_dispatch_event(r)]
    except Exception:
        dispatch_rows = []

    request_flags: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"accepted": False, "completed": False, "failed": False, "ack_pending": False, "acked": False, "progress_fastpath": False, "progress_pushed": False, "deviation_detected": False, "deviation_recovered": False}
    )
    stage_counter: Counter[str] = Counter()

    for row in dispatch_rows:
        event_type = str(row.get("event_type") or row.get("status") or "").strip().upper()
        if not event_type:
            continue
        payload = _safe_json(row.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        source = _normalize_input_dispatch_source(payload_dict.get("source") or row.get("source") or row.get("channel"), event_type)
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
    progress_touched_requests = sum(1 for f in request_flags.values() if f.get("progress_fastpath") or f.get("progress_pushed"))
    deviation_detected_requests = sum(1 for f in request_flags.values() if f.get("deviation_detected"))
    deviation_recovered_requests = sum(1 for f in request_flags.values() if f.get("deviation_recovered"))
    closed_loop_requests = sum(
        1 for f in request_flags.values()
        if f.get("completed")
        and (f.get("ack_pending") or f.get("acked") or f.get("progress_fastpath") or f.get("progress_pushed") or f.get("deviation_detected"))
        and (not f.get("deviation_detected") or f.get("deviation_recovered"))
    )

    def _rate(num, den): return round(num / den * 100, 1) if den > 0 else None

    stage_counts = [
        InputHumanoidLoopStageItem(stage=stage, count=count, rate=_rate(count, total_gateway_requests))
        for stage, count in stage_counter.most_common(20)
    ]

    return InputHumanoidLoopStatsResponse(
        window_minutes=window_minutes,
        total_gateway_requests=total_gateway_requests,
        completed_requests=completed_requests, failed_requests=failed_requests,
        ack_touched_requests=ack_touched_requests, ack_confirmed_requests=ack_confirmed_requests,
        progress_touched_requests=progress_touched_requests,
        deviation_detected_requests=deviation_detected_requests,
        deviation_recovered_requests=deviation_recovered_requests,
        closed_loop_requests=closed_loop_requests,
        completion_rate=_rate(completed_requests, total_gateway_requests),
        ack_touch_rate=_rate(ack_touched_requests, total_gateway_requests),
        progress_touch_rate=_rate(progress_touched_requests, total_gateway_requests),
        deviation_recovery_rate=_rate(deviation_recovered_requests, deviation_detected_requests),
        closed_loop_rate=_rate(closed_loop_requests, total_gateway_requests),
        stage_counts=stage_counts, timestamp=now,
    )


# ---------------------------------------------------------------------------
# GET /obs/input/humanoid-loop-trend
# ---------------------------------------------------------------------------
@router.get("/input/humanoid-loop-trend", response_model=InputHumanoidLoopTrendResponse)
@fail_closed_projection_route
def get_input_humanoid_loop_trend(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=360, description="时序聚合间隔（分钟）"),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #48C: Gateway 人体闭环趋势统计（按请求归一化聚合）。"""
    ledger = projection_view
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=now, limit=30_000)
        dispatch_rows = [r for r in rows if _is_input_dispatch_event(r)]
    except Exception:
        dispatch_rows = []

    request_flags: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"first_ts": None, "accepted_ts": None, "completed": False, "failed": False, "ack_pending": False, "acked": False, "progress_fastpath": False, "progress_pushed": False, "deviation_detected": False, "deviation_recovered": False}
    )

    for row in dispatch_rows:
        event_type = str(row.get("event_type") or row.get("status") or "").strip().upper()
        if not event_type:
            continue
        payload = _safe_json(row.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        source = _normalize_input_dispatch_source(payload_dict.get("source") or row.get("source") or row.get("channel"), event_type)
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
    ts_total = [0] * bucket_count
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
        closed_loop = bool(completed and (ack_touched or progress_touched or deviation_detected) and (not deviation_detected or deviation_recovered))
        ts_total[idx] += 1
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

    def _rate_series(num_list, den_list):
        return [round(num_list[i] / den_list[i] * 100, 1) if den_list[i] > 0 else None for i in range(bucket_count)]

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

    return InputHumanoidLoopTrendResponse(
        window_minutes=window_minutes, interval_minutes=interval_minutes,
        timeseries_labels=labels,
        timeseries_total_gateway_requests=ts_total,
        timeseries_completed_requests=ts_completed,
        timeseries_failed_requests=ts_failed,
        timeseries_closed_loop_requests=ts_closed_loop,
        timeseries_ack_touched_requests=ts_ack_touched,
        timeseries_progress_touched_requests=ts_progress_touched,
        timeseries_deviation_detected_requests=ts_deviation_detected,
        timeseries_deviation_recovered_requests=ts_deviation_recovered,
        timeseries_completion_rate=_rate_series(ts_completed, ts_total),
        timeseries_closed_loop_rate=_rate_series(ts_closed_loop, ts_total),
        timeseries_ack_touch_rate=_rate_series(ts_ack_touched, ts_total),
        timeseries_progress_touch_rate=_rate_series(ts_progress_touched, ts_total),
        timeseries_deviation_recovery_rate=_rate_series(ts_deviation_recovered, ts_deviation_detected),
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# GET /obs/input/humanoid-loop-alerts
# ---------------------------------------------------------------------------
@router.get("/input/humanoid-loop-alerts", response_model=InputHumanoidLoopAlertsResponse)
@fail_closed_projection_route
def get_input_humanoid_loop_alerts(
    window_minutes: int = Query(360, ge=1, le=10080),
    interval_minutes: int = Query(30, ge=1, le=360),
    min_bucket_requests: int = Query(3, ge=1, le=100),
    completion_rate_warn: float = Query(85.0, ge=0.0, le=100.0),
    closed_loop_rate_warn: float = Query(70.0, ge=0.0, le=100.0),
    ack_touch_rate_warn: float = Query(75.0, ge=0.0, le=100.0),
    progress_touch_rate_warn: float = Query(50.0, ge=0.0, le=100.0),
    failed_rate_warn: float = Query(20.0, ge=0.0, le=100.0),
    deviation_recovery_rate_warn: float = Query(60.0, ge=0.0, le=100.0),
    closed_loop_drop_warn: float = Query(25.0, ge=0.0, le=100.0),
    baseline_buckets: int = Query(3, ge=1, le=24),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #48D: Gateway 人体闭环趋势告警评估。"""
    now = _now_ms()
    trend = get_input_humanoid_loop_trend(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
        projection_view=projection_view,
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
            window_minutes=window_minutes, interval_minutes=interval_minutes,
            evaluated_bucket_index=None, evaluated_label=None, sample_requests=0,
            status="no_data", severity="info",
            summary="最近窗口内暂无满足最小样本量的 Gateway 闭环数据。",
            latest_completion_rate=None, latest_closed_loop_rate=None,
            latest_ack_touch_rate=None, latest_progress_touch_rate=None,
            latest_failed_rate=None, latest_deviation_recovery_rate=None,
            alerts=[], timestamp=now,
        )

    sample_requests = int(total_series[idx] or 0)
    latest_completion_rate = completed_rate_series[idx]
    latest_closed_loop_rate = closed_loop_rate_series[idx]
    latest_ack_touch_rate = ack_touch_rate_series[idx]
    latest_progress_touch_rate = progress_touch_rate_series[idx]
    latest_deviation_recovery_rate = deviation_recovery_rate_series[idx]
    latest_failed_rate = round(int(failed_series[idx] or 0) / sample_requests * 100, 1) if sample_requests > 0 else None

    alerts: List[InputHumanoidLoopAlertItem] = []

    def _push_alert(*, code, severity, title, detail, metric, value, threshold, direction):
        alerts.append(InputHumanoidLoopAlertItem(code=code, severity=severity, title=title, detail=detail, metric=metric, value=value, threshold=threshold, direction=direction))

    if latest_completion_rate is not None and latest_completion_rate < completion_rate_warn:
        _push_alert(code="COMPLETION_LOW", severity="warning", title="完成率偏低", detail=f"当前桶完成率 {latest_completion_rate}% 低于阈值 {completion_rate_warn}%。", metric="completion_rate", value=latest_completion_rate, threshold=completion_rate_warn, direction="below")
    if latest_closed_loop_rate is not None and latest_closed_loop_rate < closed_loop_rate_warn:
        _push_alert(code="CLOSED_LOOP_LOW", severity="critical", title="闭环率偏低", detail=f"当前桶闭环率 {latest_closed_loop_rate}% 低于阈值 {closed_loop_rate_warn}%。", metric="closed_loop_rate", value=latest_closed_loop_rate, threshold=closed_loop_rate_warn, direction="below")
    if latest_ack_touch_rate is not None and latest_ack_touch_rate < ack_touch_rate_warn:
        _push_alert(code="ACK_TOUCH_LOW", severity="warning", title="ACK触达率偏低", detail=f"当前桶 ACK 触达率 {latest_ack_touch_rate}% 低于阈值 {ack_touch_rate_warn}%。", metric="ack_touch_rate", value=latest_ack_touch_rate, threshold=ack_touch_rate_warn, direction="below")
    if latest_progress_touch_rate is not None and latest_progress_touch_rate < progress_touch_rate_warn:
        _push_alert(code="PROGRESS_TOUCH_LOW", severity="warning", title="进度触达率偏低", detail=f"当前桶进度触达率 {latest_progress_touch_rate}% 低于阈值 {progress_touch_rate_warn}%。", metric="progress_touch_rate", value=latest_progress_touch_rate, threshold=progress_touch_rate_warn, direction="below")
    if latest_failed_rate is not None and latest_failed_rate > failed_rate_warn:
        _push_alert(code="FAILED_RATE_HIGH", severity="critical", title="失败率偏高", detail=f"当前桶失败率 {latest_failed_rate}% 高于阈值 {failed_rate_warn}%。", metric="failed_rate", value=latest_failed_rate, threshold=failed_rate_warn, direction="above")
    if latest_deviation_recovery_rate is not None and latest_deviation_recovery_rate < deviation_recovery_rate_warn:
        _push_alert(code="DEVIATION_RECOVERY_LOW", severity="warning", title="偏航恢复率偏低", detail=f"当前桶偏航恢复率 {latest_deviation_recovery_rate}% 低于阈值 {deviation_recovery_rate_warn}%。", metric="deviation_recovery_rate", value=latest_deviation_recovery_rate, threshold=deviation_recovery_rate_warn, direction="below")

    baseline_values = [float(closed_loop_rate_series[j]) for j in range(max(0, idx - 24), idx) if int(total_series[j] or 0) >= min_bucket_requests and closed_loop_rate_series[j] is not None]
    baseline_values = baseline_values[-baseline_buckets:]
    if baseline_values and latest_closed_loop_rate is not None:
        baseline_avg = round(sum(baseline_values) / len(baseline_values), 1)
        drop = round(baseline_avg - float(latest_closed_loop_rate), 1)
        if drop >= closed_loop_drop_warn:
            severity = "critical" if drop >= closed_loop_drop_warn * 1.5 else "warning"
            _push_alert(code="CLOSED_LOOP_DROP", severity=severity, title="闭环率突降", detail=f"当前桶较基线下降 {drop}%（基线 {baseline_avg}% -> 当前 {latest_closed_loop_rate}%）。", metric="closed_loop_rate_drop", value=drop, threshold=closed_loop_drop_warn, direction="above")

    if any(item.severity == "critical" for item in alerts):
        status, severity = "critical", "critical"
    elif alerts:
        status, severity = "degraded", "warning"
    else:
        status, severity = "healthy", "ok"

    summary = f"闭环趋势健康（样本 {sample_requests}，桶 {labels[idx]}）。" if status == "healthy" else (f"闭环趋势轻度异常（{len(alerts)} 项），建议关注触达链路。" if status == "degraded" else f"闭环趋势严重异常（{len(alerts)} 项），建议立即排查 Gateway 执行链。")

    return InputHumanoidLoopAlertsResponse(
        window_minutes=window_minutes, interval_minutes=interval_minutes,
        evaluated_bucket_index=idx, evaluated_label=labels[idx] if idx < len(labels) else None,
        sample_requests=sample_requests, status=status, severity=severity, summary=summary,
        latest_completion_rate=latest_completion_rate, latest_closed_loop_rate=latest_closed_loop_rate,
        latest_ack_touch_rate=latest_ack_touch_rate, latest_progress_touch_rate=latest_progress_touch_rate,
        latest_failed_rate=latest_failed_rate, latest_deviation_recovery_rate=latest_deviation_recovery_rate,
        alerts=alerts, timestamp=now,
    )


# ---------------------------------------------------------------------------
# GET /obs/input/humanoid-loop-remediation
# ---------------------------------------------------------------------------
@router.get("/input/humanoid-loop-remediation", response_model=InputHumanoidLoopRemediationResponse)
@fail_closed_projection_route
def get_input_humanoid_loop_remediation(
    window_minutes: int = Query(360, ge=1, le=10080),
    interval_minutes: int = Query(30, ge=1, le=360),
    min_bucket_requests: int = Query(3, ge=1, le=100),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #48E: Gateway 人体闭环告警驱动自愈建议。"""
    now = _now_ms()
    alerts_resp = get_input_humanoid_loop_alerts(
        window_minutes=window_minutes, interval_minutes=interval_minutes,
        min_bucket_requests=min_bucket_requests,
        completion_rate_warn=85.0, closed_loop_rate_warn=70.0, ack_touch_rate_warn=75.0,
        progress_touch_rate_warn=50.0, failed_rate_warn=20.0, deviation_recovery_rate_warn=60.0,
        closed_loop_drop_warn=25.0, baseline_buckets=3,
        projection_view=projection_view,
    )

    step_map: Dict[str, InputHumanoidLoopRemediationStepItem] = {}
    representative_requests: List[InputHumanoidLoopRemediationRequestItem] = []

    def _register_step(*, step_id, priority, title, detail, category, target_path, target_label, operator_hint, evidence_code=None):
        existing = step_map.get(step_id)
        if existing is None:
            step_map[step_id] = InputHumanoidLoopRemediationStepItem(
                step_id=step_id, priority=priority, title=title, detail=detail,
                category=category, target_path=target_path, target_label=target_label,
                operator_hint=operator_hint, evidence_codes=[evidence_code] if evidence_code else [],
            )
            return
        if priority < existing.priority:
            existing.priority = priority
        if evidence_code and evidence_code not in existing.evidence_codes:
            existing.evidence_codes.append(evidence_code)

    # 收集代表性请求
    if alerts_resp.evaluated_bucket_index is not None:
        ledger = projection_view
        since_ms = now - window_minutes * 60 * 1000
        interval_ms = interval_minutes * 60 * 1000
        bucket_start = since_ms + int(alerts_resp.evaluated_bucket_index) * interval_ms
        bucket_end = bucket_start + interval_ms
        try:
            rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=now, limit=30_000)
            dispatch_rows = [r for r in rows if _is_input_dispatch_event(r)]
        except Exception:
            dispatch_rows = []

        req_flags: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"first_ts": None, "accepted_ts": None, "completed": False, "failed": False, "ack_pending": False, "acked": False, "progress_fastpath": False, "progress_pushed": False, "deviation_detected": False, "deviation_recovered": False}
        )
        for row in dispatch_rows:
            event_type = str(row.get("event_type") or row.get("status") or "").strip().upper()
            if not event_type:
                continue
            payload = _safe_json(row.get("payload"))
            payload_dict = dict(payload) if isinstance(payload, dict) else {}
            source = _normalize_input_dispatch_source(payload_dict.get("source") or row.get("source") or row.get("channel"), event_type)
            if source != "GATEWAY":
                continue
            request_id = str(row.get("request_id") or payload_dict.get("request_id") or "").strip()
            if not request_id:
                continue
            f = req_flags[request_id]
            ts_val = int(row.get("ts_ms") or 0)
            if ts_val > 0:
                if f["first_ts"] is None or ts_val < f["first_ts"]:
                    f["first_ts"] = ts_val
                if "ACCEPTED" in event_type and (f["accepted_ts"] is None or ts_val < f["accepted_ts"]):
                    f["accepted_ts"] = ts_val
            status_upper = str(row.get("status") or "").strip().upper()
            if "COMPLETED" in event_type:
                f["completed"] = True
            if "FAILED" in event_type or "ERROR" in event_type:
                f["failed"] = True
            if event_type == "INPUT_ACK_PENDING":
                f["ack_pending"] = True
            if event_type == "INPUT_ACKED":
                f["acked"] = True
            if event_type == "INPUT_PROGRESS_FASTPATH" and status_upper == "OK":
                f["progress_fastpath"] = True
            if event_type == "INPUT_PROGRESS_PUSHED":
                f["progress_pushed"] = True
            if event_type == "INPUT_DEVIATION_DETECTED":
                f["deviation_detected"] = True
            if event_type == "INPUT_DEVIATION_RECOVERED":
                f["deviation_recovered"] = True

        ranked: list[tuple[int, int, str, str, str]] = []
        for request_id, f in req_flags.items():
            anchor_ts = f.get("accepted_ts") or f.get("first_ts")
            if not anchor_ts or anchor_ts < bucket_start or anchor_ts >= bucket_end:
                continue
            ack_touched = bool(f.get("ack_pending") or f.get("acked"))
            progress_touched = bool(f.get("progress_fastpath") or f.get("progress_pushed"))
            deviation_detected = bool(f.get("deviation_detected"))
            deviation_recovered = bool(f.get("deviation_recovered"))
            completed = bool(f.get("completed"))
            failed = bool(f.get("failed"))
            closed_loop = bool(completed and (ack_touched or progress_touched or deviation_detected) and (not deviation_detected or deviation_recovered))
            if failed:
                category, reason, rank = "failed", "请求执行失败", 0
            elif not closed_loop:
                category, reason, rank = "not_closed_loop", "请求完成但未形成闭环触达", 1
            elif not ack_touched and not progress_touched:
                category, reason, rank = "low_touch", "触达信号不足", 2
            else:
                continue
            ranked.append((rank, -int(anchor_ts), request_id, category, reason))
        ranked.sort()
        representative_requests.extend(
            InputHumanoidLoopRemediationRequestItem(request_id=rid, category=cat, reason=rsn, target_path=f"/trace/{rid}")
            for _, _, rid, cat, rsn in ranked[:3]
        )

    if alerts_resp.status == "no_data":
        _register_step(step_id="generate_gateway_samples", priority=1, title="先生成可评估样本", detail="最近窗口内满足样本量的 Gateway 请求不足，先通过 QQ/微信等通道产生一批真实操作型请求。", category="sampling", target_path="/gateway", target_label="打开 Gateway", operator_hint="建议连续发送 3~5 条需要执行的操作请求，再观察闭环指标。")
        return InputHumanoidLoopRemediationResponse(
            window_minutes=window_minutes, interval_minutes=interval_minutes,
            status=alerts_resp.status, severity=alerts_resp.severity,
            summary="当前暂无足够样本，先补齐真实 Gateway 请求后再做闭环排查。",
            primary_focus="补样本", next_review_hint="补齐样本后刷新数字生命体总览，观察闭环趋势与告警状态。",
            representative_requests=[], steps=sorted(step_map.values(), key=lambda item: (item.priority, item.step_id)),
            timestamp=now,
        )

    _register_step(step_id="inspect_gateway_recent_health", priority=1, title="先看 Gateway 最近窗口健康", detail="确认当前桶的请求量、失败量、闭环率和平台通道状态，先判断是整体退化还是局部触达失灵。", category="gateway", target_path="/gateway", target_label="打开 Gateway", operator_hint="重点对照闭环率、失败率与平台消息吞吐是否同步恶化。")
    _register_step(step_id="open_trace_full", priority=2, title="打开全链路 Trace 排查断点", detail="沿输入接收、意图识别、技能执行、人格渲染逐层查看，确认闭环究竟断在 ACK、进度、执行还是最终回执。", category="trace", target_path="/trace/full", target_label="打开 Trace 总览", operator_hint="优先抽取最近一个失败请求和一个未闭环请求做对照。")
    _register_step(step_id="inspect_alert_center", priority=3, title="回到告警中心按原因收敛问题", detail="把当前闭环异常与系统其它告警对齐，确认是否同时伴随服务降级、错误码聚集或 Forge 风险扩散。", category="alerts", target_path="/alerts", target_label="打开告警中心", operator_hint="若闭环异常与服务 DOWN 同时出现，优先处理底层通道问题。")

    if alerts_resp.status == "healthy":
        _register_step(step_id="keep_ack_and_progress_path_warm", priority=4, title="维持 ACK 与进度路径热身", detail="当前闭环健康，但仍建议定期验证 ACK、进度查询与偏航恢复路径未被新改动破坏。", category="preventive", target_path="/digital-life", target_label="留在当前页", operator_hint="每次改路由、输入链路或人格渲染后都应回归验证。")
    else:
        for alert in alerts_resp.alerts:
            code = str(alert.code or "").upper()
            if code in {"CLOSED_LOOP_LOW", "CLOSED_LOOP_DROP", "COMPLETION_LOW", "FAILED_RATE_HIGH"}:
                _register_step(step_id="inspect_gateway_recent_health", priority=1, title="先看 Gateway 最近窗口健康", detail="确认当前桶的请求量、失败量、闭环率和平台通道状态，先判断是整体退化还是局部触达失灵。", category="gateway", target_path="/gateway", target_label="打开 Gateway", operator_hint="重点对照闭环率、失败率与平台消息吞吐是否同步恶化。", evidence_code=code)
                _register_step(step_id="open_trace_full", priority=2, title="打开全链路 Trace 排查断点", detail="沿输入接收、意图识别、技能执行、人格渲染逐层查看，确认闭环究竟断在 ACK、进度、执行还是最终回执。", category="trace", target_path="/trace/full", target_label="打开 Trace 总览", operator_hint="优先抽取最近一个失败请求和一个未闭环请求做对照。", evidence_code=code)
            if code == "ACK_TOUCH_LOW":
                _register_step(step_id="verify_ack_settings", priority=2, title="检查即时 ACK 配置与送达链路", detail="确认 ACK 路径是否被关闭、超时过短或被误判为非动作意图，从而没有及时返回人格化确认。", category="ack", target_path="/system", target_label="打开系统配置", operator_hint="重点检查 HOST_GATEWAY_IMMEDIATE_ACK_ENABLED、HOST_GATEWAY_IMMEDIATE_ACK_WAIT_RESULT、HOST_GATEWAY_IMMEDIATE_ACK_TIMEOUT_MS。", evidence_code=code)
            if code == "PROGRESS_TOUCH_LOW":
                _register_step(step_id="verify_progress_fastpath", priority=3, title="检查进度查询快路径与主动推送", detail="确认进度询问有没有被识别出来，以及长任务是否启动了主动进度回报循环。", category="progress", target_path="/gateway", target_label="打开 Gateway", operator_hint="重点检查 HOST_GATEWAY_PROGRESS_FASTPATH_ENABLED、HOST_GATEWAY_PROGRESS_FASTPATH_WAIT_RESULT 与主动进度推送配置。", evidence_code=code)
            if code == "DEVIATION_RECOVERY_LOW":
                _register_step(step_id="verify_deviation_guard", priority=3, title="检查偏航检测与重试自愈链路", detail="确认偏航检测是否触发、提示是否送达、以及 realign 重试是否真正被执行并覆盖了原结果。", category="deviation", target_path="/trace/full", target_label="打开 Trace 总览", operator_hint="重点检查 HOST_GATEWAY_DEVIATION_GUARD_ENABLED、HOST_GATEWAY_DEVIATION_NOTICE_ENABLED、HOST_GATEWAY_DEVIATION_RETRY_ENABLED。", evidence_code=code)

    status_to_focus = {"healthy": "维持稳定", "degraded": "恢复触达", "critical": "抢修闭环", "no_data": "补齐样本"}
    primary_focus = status_to_focus.get(str(alerts_resp.status or "").lower(), "闭环排查")
    summary = alerts_resp.summary if alerts_resp.alerts else "当前未检测到闭环异常，建议继续保持 ACK、进度与偏航恢复链路的回归验证。"

    return InputHumanoidLoopRemediationResponse(
        window_minutes=window_minutes, interval_minutes=interval_minutes,
        status=alerts_resp.status, severity=alerts_resp.severity, summary=summary,
        primary_focus=primary_focus, next_review_hint="完成排查后重新发送一条操作型请求，并观察 ACK、进度触达与闭环率是否回升到正常区间。",
        representative_requests=representative_requests,
        steps=sorted(step_map.values(), key=lambda item: (item.priority, item.step_id)),
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# GET /obs/input/humanoid-loop-diagnostics
# ---------------------------------------------------------------------------
def _build_input_humanoid_loop_diagnostic_item(*, ledger, request_id: str, category: str, reason: str) -> InputHumanoidLoopDiagnosticItem:
    from host_brain.audit.sqlite_ledger import SQLiteLedger
    intent_rows = _fetch_request_rows(ledger, "intent_log", request_id)
    governor_rows = _fetch_request_rows(ledger, "governor_log", request_id)
    retrieval_rows = _fetch_request_rows(ledger, "retrieval_event_log", request_id)
    llm_rows = _fetch_request_rows(ledger, "model_attempt_log", request_id)
    airlock_rows = _fetch_request_rows(ledger, "airlock_log", request_id)
    bond_rows = _fetch_request_rows(ledger, "bond_update_log", request_id)
    memory_rows = _fetch_request_rows(ledger, "memory_ops_log", request_id)
    error_rows = _fetch_request_rows(ledger, "error_log", request_id)
    voice_rows = _fetch_request_rows(ledger, "voice_event_log", request_id)

    summary = _build_trace_summary(
        request_id=request_id, intent_rows=intent_rows, governor_rows=governor_rows,
        retrieval_rows=retrieval_rows, llm_rows=llm_rows, airlock_rows=airlock_rows,
        bond_rows=bond_rows, memory_rows=memory_rows, error_rows=error_rows, voice_rows=voice_rows,
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
    def _push_evidence(code):
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
        suspected_breakpoint, root_cause_code = "governor_gate", "GOVERNOR_BLOCKED"
        root_cause_summary = f"请求在执行前被 Governor/Immune Gate 拦截，当前门控原因是 {governor_reason or 'UNKNOWN'}，需要先处理审批、风控或门禁规则。"
        recommended_view_label = "打开门控链路"
    elif flags.get("failed"):
        suspected_breakpoint, root_cause_code = "execution_stage", "EXECUTION_FAILED"
        root_cause_summary = f"请求已经进入执行阶段，但最终失败收敛；当前错误指纹为 {reason_hint or 'UNKNOWN'}，优先检查失败节点与错误日志。"
        recommended_view_label = "打开失败链路"
    elif flags.get("deviation_detected") and not flags.get("deviation_recovered"):
        suspected_breakpoint, root_cause_code = "deviation_recovery", "DEVIATION_NOT_RECOVERED"
        root_cause_summary = "链路中已经检测到偏航，但没有看到 recovered 信号，自愈重试没有真正闭合，需要复核 realign 提示和重试执行。"
        recommended_view_label = "查看偏航修复"
    elif flags.get("completed") and not flags.get("ack_touched") and not flags.get("progress_touched"):
        suspected_breakpoint, root_cause_code = "humanoid_touch", "COMPLETED_WITHOUT_HUMANOID_TOUCH"
        root_cause_summary = "请求虽然完成了，但整条链路缺少 ACK / Progress 生命体触达信号，用户体感上会像'系统做了事，却没有任何回应'。"
        recommended_view_label = "检查触达链路"
    elif not flags.get("ack_touched"):
        suspected_breakpoint, root_cause_code = "ack_touch_path", "ACK_PATH_MISSING"
        root_cause_summary = "请求已经进入 Gateway 执行，但未记录到 ACK 触达，优先排查即时确认链路是否被跳过、超时或错误归类。"
        recommended_view_label = "检查 ACK 回路"
    elif not flags.get("progress_touched") and not flags.get("completed"):
        suspected_breakpoint, root_cause_code = "progress_touch_path", "PROGRESS_PATH_MISSING"
        root_cause_summary = "请求仍在处理中，但没有进度触达信号，用户无法感知系统仍在继续执行。"
        recommended_view_label = "检查进度回路"
    elif flags.get("completed") and not flags.get("closed_loop"):
        suspected_breakpoint, root_cause_code = "closed_loop_convergence", "CLOSED_LOOP_NOT_CONVERGED"
        root_cause_summary = "请求已经收敛，但闭环条件没有全部满足，建议复核 ACK、Progress 与偏航恢复信号是否都完成落账。"
        recommended_view_label = "复核闭环收敛"
    else:
        suspected_breakpoint, root_cause_code = "trace_review", "TRACE_REVIEW_REQUIRED"
        root_cause_summary = "当前请求存在异常样态，但还未命中稳定根因模板，建议直接打开链路回放逐节点复核。"
        recommended_view_label = "打开链路回放"

    return InputHumanoidLoopDiagnosticItem(
        request_id=request_id, category=category, status=str(flags.get("status") or "observed"),
        input_text=str(summary.get("input_text") or "").strip() or None,
        output_text=str(summary.get("output_text") or "").strip() or None,
        suspected_breakpoint=suspected_breakpoint, root_cause_code=root_cause_code,
        root_cause_summary=root_cause_summary, reason_hint=reason_hint,
        evidence_codes=evidence_codes[:6],
        recommended_view_path=f"/trace/{request_id}", recommended_view_label=recommended_view_label,
    )


@router.get("/input/humanoid-loop-diagnostics", response_model=InputHumanoidLoopDiagnosticsResponse)
@fail_closed_projection_route
def get_input_humanoid_loop_diagnostics(
    window_minutes: int = Query(360, ge=1, le=10080),
    interval_minutes: int = Query(30, ge=1, le=360),
    min_bucket_requests: int = Query(3, ge=1, le=100),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #48F: Gateway 人体闭环异常回放摘要与根因归纳。"""
    now = _now_ms()
    remediation = get_input_humanoid_loop_remediation(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
        min_bucket_requests=min_bucket_requests,
        projection_view=projection_view,
    )

    if not remediation.representative_requests:
        summary = ("当前窗口未发现需要回放归因的代表性异常请求。" if str(remediation.status or "").lower() in {"healthy", "no_data"} else "当前窗口存在闭环异常，但暂时没有提取到可归纳的代表性请求，请继续结合修复建议排查。")
        return InputHumanoidLoopDiagnosticsResponse(window_minutes=window_minutes, interval_minutes=interval_minutes, status=remediation.status, severity=remediation.severity, summary=summary, diagnosed_count=0, diagnostics=[], timestamp=now)

    ledger = projection_view
    diagnostics: List[InputHumanoidLoopDiagnosticItem] = []
    for item in remediation.representative_requests[:5]:
        request_id = str(item.request_id or "").strip()
        if not request_id:
            continue
        try:
            diagnostics.append(_build_input_humanoid_loop_diagnostic_item(ledger=ledger, request_id=request_id, category=str(item.category or "").strip() or "unknown", reason=str(item.reason or "").strip()))
        except Exception as exc:
            logger.warning("obs.humanoid_loop.diagnostics_build_failed request_id=%s error=%s", request_id, exc)
            diagnostics.append(InputHumanoidLoopDiagnosticItem(request_id=request_id, category=str(item.category or "").strip() or "unknown", status="diagnostic_unavailable", input_text=None, output_text=None, suspected_breakpoint="trace_review", root_cause_code="TRACE_SUMMARY_UNAVAILABLE", root_cause_summary="该请求的根因摘要暂时生成失败，请直接打开全链路 Trace 手动排查。", reason_hint=str(item.reason or "").strip() or None, evidence_codes=["TRACE_SUMMARY_UNAVAILABLE"], recommended_view_path=f"/trace/{request_id}", recommended_view_label="打开链路回放"))

    return InputHumanoidLoopDiagnosticsResponse(window_minutes=window_minutes, interval_minutes=interval_minutes, status=remediation.status, severity=remediation.severity, summary=f"已为最近 {len(diagnostics)} 条代表性异常请求生成断点与根因摘要，可直接按 request 回放定位。", diagnosed_count=len(diagnostics), diagnostics=diagnostics, timestamp=now)


# ---------------------------------------------------------------------------
# GET /obs/input/humanoid-loop-hotspots
# ---------------------------------------------------------------------------
def _classify_humanoid_loop_hotspot_severity(*, root_cause_code: str, request_count: int, overall_status: str) -> str:
    critical_codes = {"EXECUTION_FAILED", "DEVIATION_NOT_RECOVERED", "GOVERNOR_BLOCKED"}
    if str(root_cause_code or "").strip().upper() in critical_codes and request_count >= 1:
        return "critical"
    if str(overall_status or "").strip().lower() in {"critical", "degraded"} or request_count >= 2:
        return "warning"
    return "info"


@router.get("/input/humanoid-loop-hotspots", response_model=InputHumanoidLoopHotspotsResponse)
@fail_closed_projection_route
def get_input_humanoid_loop_hotspots(
    window_minutes: int = Query(360, ge=1, le=10080),
    interval_minutes: int = Query(30, ge=1, le=360),
    min_bucket_requests: int = Query(3, ge=1, le=100),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #48G: Gateway 人体闭环病灶热区聚类。"""
    now = _now_ms()
    diagnostics_resp = get_input_humanoid_loop_diagnostics(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
        min_bucket_requests=min_bucket_requests,
        projection_view=projection_view,
    )

    if not diagnostics_resp.diagnostics:
        return InputHumanoidLoopHotspotsResponse(window_minutes=window_minutes, interval_minutes=interval_minutes, status=diagnostics_resp.status, severity=diagnostics_resp.severity, summary="当前窗口尚未形成系统级病灶热区。", diagnosed_count=0, hotspot_count=0, hotspots=[], timestamp=now)

    grouped: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    diagnosed_count = len(diagnostics_resp.diagnostics)

    for item in diagnostics_resp.diagnostics:
        root_cause_code = str(item.root_cause_code or "").strip().upper() or "TRACE_REVIEW_REQUIRED"
        suspected_breakpoint = str(item.suspected_breakpoint or "").strip() or "trace_review"
        profile = _resolve_humanoid_loop_hotspot_profile(root_cause_code=root_cause_code, suspected_breakpoint=suspected_breakpoint)
        key = (str(profile.get("organ_system") or "closed_loop_integration"), root_cause_code, suspected_breakpoint)
        bucket = grouped.setdefault(key, {"profile": profile, "root_cause_code": root_cause_code, "suspected_breakpoint": suspected_breakpoint, "request_count": 0, "evidence_codes": [], "representative_requests": []})
        bucket["request_count"] += 1
        for code in list(item.evidence_codes or []):
            normalized = str(code or "").strip().upper()
            if normalized and normalized not in bucket["evidence_codes"]:
                bucket["evidence_codes"].append(normalized)
        bucket["representative_requests"].append(InputHumanoidLoopHotspotRequestItem(request_id=str(item.request_id or "").strip(), status=str(item.status or "").strip() or "observed", target_path=str(item.recommended_view_path or f"/trace/{item.request_id}")))

    hotspots: List[InputHumanoidLoopHotspotItem] = []
    for group in sorted(grouped.values(), key=lambda item: (-int(item.get("request_count") or 0), str(item.get("root_cause_code") or ""), str(item.get("suspected_breakpoint") or ""))):
        profile = dict(group.get("profile") or {})
        request_count = int(group.get("request_count") or 0)
        share_pct = round(request_count / diagnosed_count * 100, 1) if diagnosed_count > 0 else 0.0
        root_cause_code = str(group.get("root_cause_code") or "").strip().upper()
        suspected_breakpoint = str(group.get("suspected_breakpoint") or "").strip()
        representative_requests = list(group.get("representative_requests") or [])[:3]
        recommended_view_path = str(profile.get("recommended_view_path") or "").strip() or (representative_requests[0].target_path if representative_requests else "/gateway")
        severity = _classify_humanoid_loop_hotspot_severity(root_cause_code=root_cause_code, request_count=request_count, overall_status=diagnostics_resp.status)
        organ_label = str(profile.get("organ_label") or "闭环整合系统")
        narrative = f"最近 {request_count}/{diagnosed_count} 条代表性异常集中在{organ_label}，主病灶是 {root_cause_code}。{str(profile.get('organ_story') or '').strip()}"
        hotspots.append(InputHumanoidLoopHotspotItem(
            hotspot_id=f"{profile.get('organ_system') or 'closed_loop_integration'}:{root_cause_code}".lower(),
            organ_system=str(profile.get("organ_system") or "closed_loop_integration"),
            organ_label=organ_label, root_cause_code=root_cause_code, suspected_breakpoint=suspected_breakpoint,
            severity=severity, request_count=request_count, share_pct=share_pct, narrative=narrative,
            operator_focus=str(profile.get("operator_focus") or "优先打开 Trace 逐节点定位病灶。"),
            evidence_codes=list(group.get("evidence_codes") or [])[:6],
            representative_requests=representative_requests,
            recommended_view_path=recommended_view_path,
            recommended_view_label=str(profile.get("recommended_view_label") or "打开病灶回放"),
        ))

    top_hotspot = hotspots[0]
    return InputHumanoidLoopHotspotsResponse(
        window_minutes=window_minutes, interval_minutes=interval_minutes,
        status=diagnostics_resp.status, severity=diagnostics_resp.severity,
        summary=f"当前主病灶集中在{top_hotspot.organ_label}，最近 {top_hotspot.request_count}/{diagnosed_count} 条代表性异常都围绕 {top_hotspot.root_cause_code} 聚集。",
        diagnosed_count=diagnosed_count, hotspot_count=len(hotspots), hotspots=hotspots, timestamp=now,
    )


# ---------------------------------------------------------------------------
# GET /obs/input/humanoid-loop-hotspot-trend
# ---------------------------------------------------------------------------
@router.get("/input/humanoid-loop-hotspot-trend", response_model=InputHumanoidLoopHotspotTrendResponse)
@fail_closed_projection_route
def get_input_humanoid_loop_hotspot_trend(
    window_minutes: int = Query(360, ge=1, le=10080),
    interval_minutes: int = Query(30, ge=1, le=360),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #48H: Gateway 人体闭环病灶热区时间演化趋势。"""
    ledger = projection_view
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    interval_ms = interval_minutes * 60 * 1000
    bucket_count = max(1, (window_minutes * 60 * 1000) // interval_ms)

    try:
        voice_rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=now, limit=30_000)
    except Exception:
        voice_rows = []
    try:
        governor_rows = ledger.fetch_by_time_range("governor_log", start_ms=since_ms, end_ms=now, limit=30_000)
    except Exception:
        governor_rows = []
    try:
        error_rows = ledger.fetch_by_time_range("error_log", start_ms=since_ms, end_ms=now, limit=30_000)
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
        snapshot = _classify_humanoid_loop_root_cause_snapshot(flags=flags, governor_rows=governor_by_request.get(request_id, []), error_rows=error_by_request.get(request_id, []))
        root_cause_code = str(snapshot.get("root_cause_code") or "TRACE_REVIEW_REQUIRED")
        suspected_breakpoint = str(snapshot.get("suspected_breakpoint") or "trace_review")
        profile = _resolve_humanoid_loop_hotspot_profile(root_cause_code=root_cause_code, suspected_breakpoint=suspected_breakpoint)
        organ_system = str(profile.get("organ_system") or "closed_loop_integration")
        organ_label = str(profile.get("organ_label") or "闭环整合系统")
        organ_labels[organ_system] = organ_label
        bucket_abnormal[idx] += 1
        bucket_organ_counts[idx][organ_system] = int(bucket_organ_counts[idx].get(organ_system, 0)) + 1
        root_key = (organ_system, root_cause_code)
        bucket_root_counts[idx][root_key] = int(bucket_root_counts[idx].get(root_key, 0)) + 1

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

    total_organ_counts: Dict[str, int] = {}
    for organ_map in bucket_organ_counts:
        for organ_system, count in organ_map.items():
            total_organ_counts[organ_system] = int(total_organ_counts.get(organ_system, 0)) + int(count or 0)

    top_organ_systems = [organ_system for organ_system, _ in sorted(total_organ_counts.items(), key=lambda item: (-int(item[1] or 0), str(item[0])))[:3]]
    series = [InputHumanoidLoopHotspotTrendSeriesItem(organ_system=organ_system, organ_label=organ_labels.get(organ_system, organ_system), request_counts=[int(bucket_organ_counts[i].get(organ_system, 0)) for i in range(bucket_count)]) for organ_system in top_organ_systems]

    dominant_buckets: List[InputHumanoidLoopHotspotTrendBucketItem] = []
    for idx in range(bucket_count):
        dominant_organ_system = dominant_organ_label = dominant_root_cause_code = None
        dominant_request_count = 0
        dominant_share_pct = None
        organ_map = bucket_organ_counts[idx]
        if organ_map:
            dominant_organ_system = sorted(organ_map.items(), key=lambda item: (-int(item[1] or 0), str(item[0])))[0][0]
            dominant_request_count = int(organ_map.get(dominant_organ_system, 0))
            dominant_organ_label = organ_labels.get(dominant_organ_system, dominant_organ_system)
            root_items = [(root_code, count) for (organ_system, root_code), count in bucket_root_counts[idx].items() if organ_system == dominant_organ_system]
            if root_items:
                dominant_root_cause_code = sorted(root_items, key=lambda item: (-int(item[1] or 0), str(item[0])))[0][0]
            if bucket_abnormal[idx] > 0:
                dominant_share_pct = round(dominant_request_count / bucket_abnormal[idx] * 100, 1)
        dominant_buckets.append(InputHumanoidLoopHotspotTrendBucketItem(bucket_index=idx, label=labels[idx], sample_requests=int(bucket_samples[idx]), abnormal_requests=int(bucket_abnormal[idx]), dominant_organ_system=dominant_organ_system, dominant_organ_label=dominant_organ_label, dominant_root_cause_code=dominant_root_cause_code, dominant_request_count=dominant_request_count, dominant_share_pct=dominant_share_pct))

    latest_bucket = next((item for item in reversed(dominant_buckets) if item.abnormal_requests > 0), None)
    summary = "当前窗口尚未形成病灶时间演化趋势。" if latest_bucket is None else f"最近异常主病灶落在{latest_bucket.dominant_organ_label or latest_bucket.dominant_organ_system}，根因主轴是 {latest_bucket.dominant_root_cause_code or 'UNKNOWN'}，可沿时间线回看病灶升温过程。"

    return InputHumanoidLoopHotspotTrendResponse(
        window_minutes=window_minutes, interval_minutes=interval_minutes, summary=summary,
        latest_dominant_organ_system=(latest_bucket.dominant_organ_system if latest_bucket else None),
        latest_dominant_organ_label=(latest_bucket.dominant_organ_label if latest_bucket else None),
        latest_dominant_root_cause_code=(latest_bucket.dominant_root_cause_code if latest_bucket else None),
        timeseries_labels=labels,
        timeseries_total_requests=[int(item) for item in bucket_samples],
        timeseries_abnormal_requests=[int(item) for item in bucket_abnormal],
        dominant_buckets=dominant_buckets, series=series, timestamp=now,
    )


# ---------------------------------------------------------------------------
# GET /obs/input/humanoid-loop-source-correlation
# ---------------------------------------------------------------------------
@router.get("/input/humanoid-loop-source-correlation", response_model=InputHumanoidLoopSourceCorrelationResponse)
@fail_closed_projection_route
async def get_input_humanoid_loop_source_correlation(
    window_minutes: int = Query(360, ge=1, le=10080),
    interval_minutes: int = Query(30, ge=1, le=360),
    min_bucket_requests: int = Query(3, ge=1, le=100),
    *,
    projection_view: Annotated[Any, Depends(require_obs_projection_ledger_view)],
):
    """Ref #48I: Gateway 人体闭环病灶源头归因。"""
    from host_brain.app.obs_routes_stats_governor import get_governor_stats, get_errors, get_subsystems_stats
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000
    hotspots_resp = get_input_humanoid_loop_hotspots(
        window_minutes=window_minutes,
        interval_minutes=interval_minutes,
        min_bucket_requests=min_bucket_requests,
        projection_view=projection_view,
    )

    if not hotspots_resp.hotspots:
        return InputHumanoidLoopSourceCorrelationResponse(window_minutes=window_minutes, interval_minutes=interval_minutes, status=hotspots_resp.status, severity=hotspots_resp.severity, summary="当前窗口尚未形成可归因的人体闭环病灶源头。", primary_source_system=None, primary_source_label=None, source_count=0, items=[], timestamp=now)

    ledger = projection_view

    # 各源头快照（简化版，保持与原逻辑一致）
    def _gateway_snap():
        try:
            voice_rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=now, limit=30_000)
        except Exception:
            voice_rows = []
        request_voice_rows, _ = _collect_gateway_request_voice_groups(voice_rows)
        total = len(request_voice_rows)
        ack_missing = progress_missing = touchless_completed = failed = deviation_unrecovered = 0
        for rows in request_voice_rows.values():
            f = _build_gateway_request_flags_from_voice_rows(rows)
            if not f.get("ack_touched"):
                ack_missing += 1
            if not f.get("progress_touched"):
                progress_missing += 1
            if f.get("completed") and not f.get("ack_touched") and not f.get("progress_touched"):
                touchless_completed += 1
            if f.get("failed"):
                failed += 1
            if f.get("deviation_detected") and not f.get("deviation_recovered"):
                deviation_unrecovered += 1
        status = "DEGRADED" if (touchless_completed > 0 or deviation_unrecovered > 0) else ("UP" if total > 0 else "UNKNOWN")
        return {"status": status, "total_requests": total, "ack_missing": ack_missing, "progress_missing": progress_missing, "touchless_completed": touchless_completed, "failed": failed, "deviation_unrecovered": deviation_unrecovered, "channel_errors": 0}

    def _governor_snap():
        try:
            resp = get_governor_stats(
                window_minutes=window_minutes,
                interval_minutes=interval_minutes,
                projection_view=projection_view,
            )
            rejected = int(resp.rejected or 0)
            rejection_rate = float(resp.rejection_rate or 0.0)
            status = "DEGRADED" if rejected > 0 else ("UP" if int(resp.total or 0) > 0 else "UNKNOWN")
            return {"status": status, "rejected": rejected, "rejection_rate": rejection_rate, "top_reason_codes": [str(item.reason_code or "").strip().upper() for item in list(resp.reason_codes or [])[:3] if str(item.reason_code or "").strip()]}
        except Exception:
            return {"status": "UNKNOWN", "rejected": 0, "rejection_rate": 0.0, "top_reason_codes": []}

    def _error_snap():
        try:
            resp = get_errors(
                window_minutes=window_minutes,
                interval_minutes=interval_minutes,
                limit=10,
                projection_view=projection_view,
            )
            total_errors = int(resp.total_errors or 0)
            status = "DOWN" if total_errors >= 3 else ("DEGRADED" if total_errors > 0 else "UP")
            top_stage = str(resp.stages[0].stage or "").strip().upper() if list(resp.stages or []) else None
            return {"status": status, "total_errors": total_errors, "top_stage": top_stage, "top_codes": [str(item.reason_code or "").strip().upper() for item in list(resp.top_codes or [])[:4] if str(item.reason_code or "").strip()]}
        except Exception:
            return {"status": "UNKNOWN", "total_errors": 0, "top_stage": None, "top_codes": []}

    def _subsystem_snap():
        try:
            resp = get_subsystems_stats(
                window_minutes=window_minutes,
                projection_view=projection_view,
            )
            problematic = [{"subsystem": str(item.subsystem or ""), "status": str(item.status or "").upper()} for item in list(resp.subsystems or []) if int(item.failed or 0) > 0 or int(item.degraded or 0) > 0 or str(item.status or "").upper() in {"DOWN", "DEGRADED"}]
            status = "DEGRADED" if problematic else ("UP" if list(resp.subsystems or []) else "UNKNOWN")
            return {"status": status, "problem_count": len(problematic), "problem_subsystems": problematic[:4]}
        except Exception:
            return {"status": "UNKNOWN", "problem_count": 0, "problem_subsystems": []}

    def _services_snap():
        try:
            llm_rows = ledger.fetch_by_time_range("model_attempt_log", start_ms=since_ms, end_ms=now, limit=20_000)
        except Exception:
            llm_rows = []
        grouped: Dict[str, Dict[str, Any]] = {}
        for row in latest_terminal_model_attempts(
            dict(item) for item in llm_rows if isinstance(item, dict)
        ):
            attempt_status = str(row.get("status") or "").strip().upper()
            service_id = (
                str(row.get("adapter_id") or "").strip()
                or str(row.get("model_id") or "").strip()
                or str(row.get("prompt_id") or "").strip()
                or "unknown"
            )
            bucket = grouped.setdefault(service_id, {"total": 0, "failed": 0})
            bucket["total"] += 1
            if attempt_status != "SUCCEEDED":
                bucket["failed"] += 1
        degraded = sum(1 for item in grouped.values() if 0 < int(item.get("failed") or 0) < int(item.get("total") or 1))
        down = sum(1 for item in grouped.values() if int(item.get("failed") or 0) >= int(item.get("total") or 1) and int(item.get("total") or 0) > 0)
        status = "DOWN" if down > 0 else ("DEGRADED" if degraded > 0 else ("UP" if grouped else "UNKNOWN"))
        return {"status": status, "degraded": degraded, "down": down, "problem_services": []}

    gateway_snapshot = _gateway_snap()
    governor_snapshot = _governor_snap()
    error_snapshot = _error_snap()
    subsystem_snapshot = _subsystem_snap()
    services_snapshot = _services_snap()

    # 源头归因聚合（简化版，保持与原逻辑一致）
    grouped_sources: Dict[str, Dict[str, Any]] = {}
    diagnosed_count = max(1, int(hotspots_resp.diagnosed_count or 0))

    def _source_meta(source_id):
        meta = {
            "gateway": {"source_label": "Gateway 通道反馈层", "source_kind": "gateway", "operator_focus": "优先复核 ACK、进度触达与平台消息回执是否真正形成回路。", "recommended_view_path": "/gateway", "recommended_view_label": "打开感官网关"},
            "governor": {"source_label": "Governor / 免疫门控层", "source_kind": "governor", "operator_focus": "优先检查审批令牌、风险规则与门控阈值是否过严或误伤。", "recommended_view_path": "/system", "recommended_view_label": "打开系统底座"},
            "errors": {"source_label": "错误执行层", "source_kind": "errors", "operator_focus": "优先围绕最近错误 stage / reason_code 回放失败链路，确认是技能、工具还是依赖失败。", "recommended_view_path": "/alerts", "recommended_view_label": "打开免疫告警"},
            "subsystems": {"source_label": "器官子系统健康层", "source_kind": "subsystems", "operator_focus": "优先检查 Skill、Sandbox、Agent Loop 等器官系统是否出现退化或失败尖峰。", "recommended_view_path": "/system", "recommended_view_label": "打开系统底座"},
            "services": {"source_label": "依赖服务健康层", "source_kind": "services", "operator_focus": "优先检查模型、边缘服务与基础依赖的降级/不可用情况，确认是否拖垮执行闭环。", "recommended_view_path": "/system", "recommended_view_label": "打开服务健康"},
        }
        return dict(meta.get(source_id, meta["errors"]))

    def _add_source(source_id, hotspot, weight, source_status, evidence, narrative):
        meta = _source_meta(source_id)
        bucket = grouped_sources.setdefault(source_id, {"source_id": source_id, "source_label": meta["source_label"], "source_kind": meta["source_kind"], "source_status": source_status, "score": 0.0, "correlated_hotspots": [], "evidence": [], "narrative": "", "operator_focus": meta["operator_focus"], "recommended_view_path": meta["recommended_view_path"], "recommended_view_label": meta["recommended_view_label"]})
        bucket["score"] = float(bucket.get("score") or 0.0) + max(0.05, min(0.95, float(weight or 0.0))) * max(1, int(hotspot.request_count or 0))
        root_cause_code = str(hotspot.root_cause_code or "").strip().upper()
        if root_cause_code and root_cause_code not in bucket["correlated_hotspots"]:
            bucket["correlated_hotspots"].append(root_cause_code)
        for item in list(evidence or []):
            normalized = str(item or "").strip()
            if normalized and normalized not in bucket["evidence"] and len(bucket["evidence"]) < 8:
                bucket["evidence"].append(normalized)
        if not bucket.get("narrative"):
            bucket["narrative"] = narrative
        confidence_pct = min(99.0, round(float(bucket.get("score") or 0.0) / diagnosed_count * 100, 1))
        bucket["confidence_pct"] = confidence_pct
        critical_codes = {"EXECUTION_FAILED", "DEVIATION_NOT_RECOVERED", "GOVERNOR_BLOCKED"}
        hotspot_set = {str(c or "").strip().upper() for c in list(bucket.get("correlated_hotspots") or [])}
        status_upper = str(bucket.get("source_status") or "").strip().upper()
        if confidence_pct >= 75.0 or status_upper == "DOWN" or hotspot_set.intersection(critical_codes):
            bucket["impact_level"] = "critical"
        elif confidence_pct >= 45.0 or status_upper == "DEGRADED":
            bucket["impact_level"] = "warning"
        else:
            bucket["impact_level"] = "info"

    for hotspot in list(hotspots_resp.hotspots or []):
        root_cause_code = str(hotspot.root_cause_code or "").strip().upper()
        hotspot_evidence = [str(code or "").strip().upper() for code in list(hotspot.evidence_codes or [])[:4] if str(code or "").strip()]
        if root_cause_code == "EXECUTION_FAILED":
            _add_source("errors", hotspot, 0.72 + (0.1 if int(error_snapshot.get("total_errors") or 0) > 0 else 0), str(error_snapshot.get("status") or ""), [f"hotspot={root_cause_code}", f"errors={int(error_snapshot.get('total_errors') or 0)}"] + hotspot_evidence[:2], f"{hotspot.organ_label} 的失败病灶与错误执行层高度重合。")
            _add_source("subsystems", hotspot, 0.56 + (0.12 if list(subsystem_snapshot.get("problem_subsystems") or []) else 0), str(subsystem_snapshot.get("status") or ""), [f"hotspot={root_cause_code}"], "执行器官链路出现失败尖峰时，通常会同时在子系统上留下退化痕迹。")
        elif root_cause_code == "GOVERNOR_BLOCKED":
            _add_source("governor", hotspot, 0.84 + (0.06 if float(governor_snapshot.get("rejection_rate") or 0.0) > 0 else 0), str(governor_snapshot.get("status") or ""), [f"hotspot={root_cause_code}", f"rejected={int(governor_snapshot.get('rejected') or 0)}"] + hotspot_evidence[:2], "病灶集中在门控前额叶，说明请求尚未进入真正执行阶段就被审批或免疫规则拦下。")
        elif root_cause_code in {"ACK_PATH_MISSING", "PROGRESS_PATH_MISSING", "COMPLETED_WITHOUT_HUMANOID_TOUCH"}:
            _add_source("gateway", hotspot, 0.78 + (0.08 if int(gateway_snapshot.get("touchless_completed") or 0) > 0 else 0), str(gateway_snapshot.get("status") or ""), [f"hotspot={root_cause_code}", f"ack_missing={int(gateway_snapshot.get('ack_missing') or 0)}"] + hotspot_evidence[:2], "语言回执或进度触达缺口通常发生在 Gateway 通道反馈层。")
        elif root_cause_code == "DEVIATION_NOT_RECOVERED":
            _add_source("gateway", hotspot, 0.68 + (0.08 if int(gateway_snapshot.get("deviation_unrecovered") or 0) > 0 else 0), str(gateway_snapshot.get("status") or ""), [f"hotspot={root_cause_code}", f"deviation_unrecovered={int(gateway_snapshot.get('deviation_unrecovered') or 0)}"], "偏航已被感知但没有被拉回，优先怀疑反馈-重试-恢复链条在 Gateway 侧没有真正闭合。")
            _add_source("errors", hotspot, 0.5 + (0.08 if int(error_snapshot.get("total_errors") or 0) > 0 else 0), str(error_snapshot.get("status") or ""), [f"hotspot={root_cause_code}"] + hotspot_evidence[:2], "若恢复链路伴随错误日志升高，说明自愈动作本身在执行期也遭遇了失败或超时。")
        else:
            _add_source("errors", hotspot, 0.36 + (0.06 if int(error_snapshot.get("total_errors") or 0) > 0 else 0), str(error_snapshot.get("status") or ""), [f"hotspot={root_cause_code}"] + hotspot_evidence[:2], "当前病灶还没有稳定模板，优先从错误执行层与代表性链路回放收束真正源头。")
            _add_source("gateway", hotspot, 0.32 + (0.04 if int(gateway_snapshot.get("channel_errors") or 0) > 0 else 0), str(gateway_snapshot.get("status") or ""), [f"hotspot={root_cause_code}"], "在没有稳定模式前，也要同时排除 Gateway 反馈层是否存在吞回执、断进度或通道抖动。")

    items: List[InputHumanoidLoopSourceCorrelationItem] = [
        InputHumanoidLoopSourceCorrelationItem(
            source_id=str(item.get("source_id") or ""), source_label=str(item.get("source_label") or ""),
            source_kind=str(item.get("source_kind") or ""), confidence_pct=float(item.get("confidence_pct") or 0.0),
            impact_level=str(item.get("impact_level") or "info"), source_status=(str(item.get("source_status") or "").strip() or None),
            correlated_hotspots=list(item.get("correlated_hotspots") or [])[:4], evidence=list(item.get("evidence") or [])[:8],
            narrative=str(item.get("narrative") or ""), operator_focus=str(item.get("operator_focus") or ""),
            recommended_view_path=str(item.get("recommended_view_path") or "/alerts"), recommended_view_label=str(item.get("recommended_view_label") or "打开源头归因"),
        )
        for item in sorted(grouped_sources.values(), key=lambda value: (-float(value.get("confidence_pct") or 0.0), str(value.get("source_id") or "")))
    ]

    primary = items[0] if items else None
    summary = (f"当前主病灶最可能回落到 {primary.source_label}，它与 {', '.join(primary.correlated_hotspots[:2]) or '当前异常'} 的关联度最高，建议先沿 {primary.recommended_view_label} 收束根因。" if primary else "当前窗口已识别病灶，但还没有形成稳定的源头归因结果。")

    return InputHumanoidLoopSourceCorrelationResponse(
        window_minutes=window_minutes, interval_minutes=interval_minutes,
        status=hotspots_resp.status, severity=hotspots_resp.severity, summary=summary,
        primary_source_system=(primary.source_id if primary else None),
        primary_source_label=(primary.source_label if primary else None),
        source_count=len(items), items=items, timestamp=now,
    )
