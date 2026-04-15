"""obs_routes_stats_input_dispatch.py — InputDispatch 生命周期统计端点。

拆分自 obs_routes_stats_input.py（P2-8）：
  GET /obs/input/dispatch-stats  — InputDispatch 生命周期统计
  GET /obs/recall-pack/stats     — RecallPack token 预算统计
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import Query
from pydantic import BaseModel

from host_brain.app.obs_routes_core import (
    router,
    _extract_input_dispatch_latency_ms,
    _get_ledger,
    _is_input_dispatch_event,
    _normalize_input_dispatch_source,
    _now_ms,
    _safe_json,
)

logger = logging.getLogger("Natsume.obs")


# ---------------------------------------------------------------------------
# Pydantic 响应模型
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


# ---------------------------------------------------------------------------
# GET /obs/input/dispatch-stats
# ---------------------------------------------------------------------------
@router.get("/input/dispatch-stats", response_model=InputDispatchStatsResponse)
def get_input_dispatch_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=60, description="时序聚合间隔（分钟）"),
):
    """Ref #48: InputDispatch 生命周期统计 — 按 source 分组的 ACCEPTED/COMPLETED/FAILED/NOT_WIRED 数量、成功率、平均延迟。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=now, limit=10_000)
        dispatch_rows = [r for r in rows if _is_input_dispatch_event(r)]
    except Exception:
        dispatch_rows = []

    source_stats: dict = defaultdict(lambda: {"accepted": 0, "completed": 0, "failed": 0, "not_wired": 0, "latencies": []})

    for r in dispatch_rows:
        evt = str(r.get("event_type") or r.get("status") or "").upper()
        payload = _safe_json(r.get("payload"))
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        src = _normalize_input_dispatch_source(
            payload_dict.get("source") or r.get("source") or r.get("channel"), evt,
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
            source=src, accepted=stats["accepted"], completed=stats["completed"],
            failed=stats["failed"], not_wired=stats["not_wired"],
            success_rate=sr, avg_latency_ms=avg_lat,
        ))

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

    return InputDispatchStatsResponse(
        window_minutes=window_minutes,
        total_accepted=total_accepted, total_completed=total_completed,
        total_failed=total_failed, total_not_wired=total_not_wired,
        overall_success_rate=overall_success_rate, sources=sources,
        timeseries_labels=labels, timeseries_accepted=ts_accepted,
        timeseries_completed=ts_completed, timestamp=now,
    )


# ---------------------------------------------------------------------------
# GET /obs/recall-pack/stats
# ---------------------------------------------------------------------------
@router.get("/recall-pack/stats", response_model=RecallPackStatsResponse)
def get_recall_pack_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #49: RecallPack token 预算统计 — 各槽位平均 token 消耗/截断率/冲突抑制率。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("error_log", start_ms=since_ms, end_ms=now)
        pack_rows = [
            r for r in rows
            if str(r.get("stage") or "").upper() in ("RECALL_PACK_TRACE", "RECALL_FUSION")
            and r.get("detail")
        ]
    except Exception:
        pack_rows = []

    if not pack_rows:
        try:
            pack_rows = ledger.fetch_by_time_range("recall_pack_log", start_ms=since_ms, end_ms=now)
        except Exception:
            pack_rows = []

    total_packs = len(pack_rows)
    if total_packs == 0:
        return RecallPackStatsResponse(
            window_minutes=window_minutes, total_packs=0,
            avg_total_tokens=None, truncation_rate=None, conflict_rate=None,
            slots=[], timestamp=now,
        )

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
        window_minutes=window_minutes, total_packs=total_packs,
        avg_total_tokens=avg_total_tokens, truncation_rate=truncation_rate,
        conflict_rate=conflict_rate, slots=slots, timestamp=now,
    )
