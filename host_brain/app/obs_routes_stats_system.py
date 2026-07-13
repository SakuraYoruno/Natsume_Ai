"""obs_routes_stats_system.py — 可观测性统计端点（从 obs_routes_stats.py 拆分）。

子模块：VTS/蒸馏/WeChat/保留策略/QA/LLM调用/Checkpoint/限流/Vault/LLM路由统计
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
from host_brain.app.observability_runtime.health_device_projection import (
    DEVICE_MAX_AGE_MS,
    DEVICE_STATUS_KIND,
    read_projection_payload,
)
from host_brain.app.observability_runtime.remaining_query_projection import (
    CHECKPOINT_INDEX_KEY,
    CHECKPOINT_INDEX_KIND,
    CHECKPOINT_KIND,
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
# Ref #30: GET /obs/vts/stats  — VTS 通道统计
# ---------------------------------------------------------------------------
@router.get("/avatar/state", response_model=Dict[str, Any])
async def get_avatar_state():
    """Read the durable avatar projection without issuing a device command."""
    now = _now_ms()
    try:
        from host_brain.composition.process_services import get_query_projection_store  # noqa: PLC0415

        payload, meta = read_projection_payload(
            get_query_projection_store(),
            kind=DEVICE_STATUS_KIND,
            key="avatar",
            max_age_ms=DEVICE_MAX_AGE_MS,
        )
        if payload is None:
            return {
                "status": "DEGRADED",
                "success": False,
                "reason_code": str(meta.get("reason_code") or "DEVICE_STATUS_PROJECTION_EMPTY"),
                "detail": "avatar status projection is empty or stale",
                "latency_ms": None,
                "event": {},
                "schema_version": str(meta.get("schema_version") or "Natsume.operator.query_projection.v1"),
                "projection_version": int(meta.get("projection_version") or 0),
                "cursor": int(meta.get("source_cursor") or 0),
                "freshness_ts_ms": int(meta.get("freshness_ts_ms") or 0),
                "degraded_state": str(meta.get("degraded_state") or "REBUILDING"),
                "timestamp": now,
            }
        return {
            "status": str(payload.get("status") or "UNKNOWN"),
            "success": bool(payload.get("success", False)),
            "reason_code": payload.get("reason_code"),
            "detail": str(payload.get("detail") or ""),
            "latency_ms": payload.get("latency_ms"),
            "event": dict(payload.get("event") or {}),
            "schema_version": str(meta.get("schema_version") or "Natsume.operator.query_projection.v1"),
            "projection_version": int(meta.get("projection_version") or 0),
            "cursor": int(meta.get("source_cursor") or 0),
            "freshness_ts_ms": int(meta.get("freshness_ts_ms") or 0),
            "degraded_state": str(meta.get("degraded_state") or "HEALTHY"),
            "timestamp": now,
        }
    except Exception as exc:
        logger.warning("get_avatar_state failed: %s", exc)
        return {
            "status": "DEGRADED",
            "success": False,
            "reason_code": "EDGE_AVATAR_STATE_UNAVAILABLE",
            "detail": "avatar status projection is unavailable",
            "latency_ms": None,
            "event": {},
            "schema_version": "Natsume.operator.query_projection.v1",
            "projection_version": 0,
            "cursor": 0,
            "freshness_ts_ms": 0,
            "degraded_state": "SOURCE_UNAVAILABLE",
            "timestamp": now,
        }


class AvatarBehaviorRequest(BaseModel):
    behavior_id: str = Field(..., description="idle/listening/thinking/interrupted")
    emotion_tag: Optional[str] = None
    body_action_id: Optional[str] = None
    emotion_intensity: Optional[float] = None
    duration_s: Optional[float] = None
    talk_state: Optional[str] = None
    source: Optional[str] = "monitor"


async def post_avatar_behavior_control(body: AvatarBehaviorRequest):
    """Apply a safe high-level avatar behavior from monitor controls."""
    now = _now_ms()
    try:
        from host_brain.channels.embodiment import submit_avatar_behavior  # noqa: PLC0415

        result = await submit_avatar_behavior(
            behavior_id=body.behavior_id,
            emotion_tag=body.emotion_tag,
            body_action_id=body.body_action_id,
            emotion_intensity=body.emotion_intensity,
            duration_s=body.duration_s,
            talk_state=body.talk_state,
            source=body.source,
            request_id=f"obs_avatar_behavior_{now}",
        )
        return {
            "status": "OK" if bool(result.success) else "DEGRADED",
            "success": bool(result.success),
            "reason_code": result.reason_code,
            "detail": result.detail,
            "latency_ms": int(result.latency_ms or 0),
            "event": dict(result.event or {}),
            "timestamp": now,
        }
    except Exception as exc:
        logger.warning("post_avatar_behavior_control failed: %s", exc)
        return {
            "status": "DEGRADED",
            "success": False,
            "reason_code": "EDGE_AVATAR_BEHAVIOR_UNAVAILABLE",
            "detail": str(exc)[:300],
            "latency_ms": None,
            "event": {},
            "timestamp": now,
        }


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
    ledger = get_projection_ledger_view()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # ── VTS 表情事件统计 ──────────────────────────────────────────────────
    vts_rows = []
    vts_table = _resolve_fetch_table(ledger, "vts_event_log")
    if vts_table:
        try:
            vts_rows = ledger.fetch_by_time_range(vts_table, start_ms=since_ms, end_ms=now)
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
    talk_gate_rows = []
    talk_gate_table = _resolve_fetch_table(ledger, "talk_gate_log")
    if talk_gate_table:
        try:
            talk_gate_rows = ledger.fetch_by_time_range(talk_gate_table, start_ms=since_ms, end_ms=now)
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
    ledger = get_projection_ledger_view()
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
    ledger = get_projection_ledger_view()
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
    ledger = get_projection_ledger_view()
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
    ledger = get_projection_ledger_view()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    qa_table = _resolve_fetch_table(ledger, "qa_violation_log", "qa_violation")
    if qa_table:
        try:
            rows = ledger.fetch_by_time_range(qa_table, start_ms=since_ms, end_ms=now)
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
    ledger = get_projection_ledger_view()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    rows = []
    llm_table = _resolve_fetch_table(ledger, "llm_call_log", "llm_node_call_log")
    if llm_table:
        try:
            rows = ledger.fetch_by_time_range(llm_table, start_ms=since_ms, end_ms=now)
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
    schema_version: str = "Natsume.operator.query_projection.v1"
    projection_version: int = 0
    cursor: int = 0
    freshness_ts_ms: int = 0
    degraded_state: str = "REBUILDING"

class CheckpointDetailResponse(BaseModel):
    checkpoint_id: str
    request_id: Optional[str]
    interrupt_reason: Optional[str]
    resume_deadline_at: Optional[int]
    created_at_ms: Optional[int]
    is_expired: bool
    payload: Optional[Any]
    timestamp: int
    schema_version: str = "Natsume.operator.query_projection.v1"
    projection_version: int = 0
    cursor: int = 0
    freshness_ts_ms: int = 0
    degraded_state: str = "REBUILDING"

@router.get("/checkpoints", response_model=CheckpointsResponse)
def get_checkpoints(
    limit: int = Query(20, ge=1, le=100, description="返回条数"),
    include_expired: bool = Query(False, description="是否包含已超时的 checkpoint"),
):
    """Ref #42: 从 durable checkpoint index projection 读取列表。"""
    now = _now_ms()
    payload, meta = read(CHECKPOINT_INDEX_KIND, CHECKPOINT_INDEX_KEY)
    items: list[CheckpointItem] = []
    for r in list((payload or {}).get("items") or []):
        if not isinstance(r, Mapping):
            continue
        deadline = r.get("resume_deadline_at")
        is_expired = bool(deadline and int(deadline) < now)
        if not include_expired and is_expired:
            continue
        items.append(CheckpointItem(
            checkpoint_id=str(r.get("checkpoint_id") or r.get("id") or ""),
            request_id=r.get("request_id"),
            interrupt_reason=r.get("interrupt_reason"),
            resume_deadline_at=int(deadline) if deadline else None,
            created_at_ms=int(r.get("created_at_ms") or r.get("ts_ms") or 0),
            is_expired=is_expired,
            payload_summary=None,
        ))
        if len(items) >= limit:
            break

    return CheckpointsResponse(
        total=len(items), items=items, timestamp=now, **response_meta(meta)
    )


@router.get("/checkpoints/{checkpoint_id}", response_model=CheckpointDetailResponse)
def get_checkpoint_detail(checkpoint_id: str):
    """Ref #42: 单个 checkpoint 公共元数据；正文 payload 永不投影。"""
    now = _now_ms()
    payload, meta = read(CHECKPOINT_KIND, checkpoint_id)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "checkpoint_projection_not_ready",
                "checkpoint_id": checkpoint_id,
                **response_meta(meta),
            },
        )
    deadline = payload.get("resume_deadline_at")
    is_expired = bool(deadline and int(deadline) < now)

    return CheckpointDetailResponse(
        checkpoint_id=checkpoint_id,
        request_id=payload.get("request_id"),
        interrupt_reason=payload.get("interrupt_reason"),
        resume_deadline_at=int(deadline) if deadline else None,
        created_at_ms=int(payload.get("created_at_ms") or 0),
        is_expired=is_expired,
        payload=None,
        timestamp=now,
        **response_meta(meta),
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
    ledger = get_projection_ledger_view()
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
    ledger = get_projection_ledger_view()
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
    ledger = get_projection_ledger_view()
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
