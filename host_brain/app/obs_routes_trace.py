"""Observability — 链路追踪端点。

拆分自 obs_routes_core.py（P2-7）：
  GET /obs/trace/{request_id}              — 单次会话完整链路节点详情
  GET /obs/trace/{request_id}/stream_events — Chat Stream SSE 链路详情
  GET /obs/trace-feed/live                 — 流式推送活跃链路快照
  GET /obs/tribe/events                    — Tribe 事件观测窗口
  GET /obs/graph/schema                    — 图编排定义
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from host_brain.observability.api_models import (
    GraphSchemaEdge,
    GraphSchemaNode,
    GraphSchemaResponse,
    LiveTraceFeedItem,
    StreamEventItem,
    TraceEdge,
    TraceLiveFeedDelta,
    TraceLiveFeedDeltaItem,
    TraceLiveFeedNodeDelta,
    TraceLiveFeedSnapshot,
    TraceNode,
    TraceResponse,
    TraceStreamEventsResponse,
    TribeEventItem,
    TribeEventsResponse,
)

from host_brain.observability.projection_runtime import (
    TRACE_FEED_INDEX_KEY,
    TRACE_FEED_KIND,
    TRACE_KIND,
    TRACE_STREAM_KIND,
    TRIBE_EVENTS_INDEX_KEY,
    TRIBE_EVENTS_KIND,
    projection_meta,
    trace_source,
)
from host_brain.observability.projection_daemon import (
    register_projection_job,
    start_projection_jobs,
    stop_projection_jobs,
)
from natsume_contracts.operator.v1 import ProjectionDegradedState

logger = logging.getLogger("Natsume.obs")

router = APIRouter(prefix="/obs", tags=["observability"])

_CORTEX_ORCHESTRATOR_NODE_IDS = {"cortex_orchestrator"}
_EXECUTIVE_ROUTER_NODE_IDS = {"executive_router"}


def _env_float(name: str, default: float, *, minimum: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return max(default, minimum)
    try:
        return max(float(raw), minimum)
    except (TypeError, ValueError):
        return max(default, minimum)


_TRACE_BUILD_TIMEOUT_SEC = _env_float("OBS_TRACE_BUILD_TIMEOUT_SEC", 3.5, minimum=1.0)
_TRACE_BUILD_FALLBACK_TIMEOUT_SEC = _env_float("OBS_TRACE_BUILD_FALLBACK_TIMEOUT_SEC", 1.5, minimum=0.5)
_TRACE_OPTIONAL_STAGE_TIMEOUT_SEC = _env_float("OBS_TRACE_OPTIONAL_STAGE_TIMEOUT_SEC", 0.6, minimum=0.1)
_TRACE_BUILD_SLOW_LOG_MS = int(_env_float("OBS_TRACE_BUILD_SLOW_LOG_MS", 1200.0, minimum=200.0))
_TRACE_FEED_WARM_ENABLED = bool(
    # OPT-4 首批迁移：从 settings.observability 读取
    __import__("host_brain.app.settings.observability", fromlist=["get_observability_settings"])
    .get_observability_settings()
    .obs_trace_feed_warm_enabled
)
_TRACE_FEED_WARM_INTERVAL_MS = max(int(_env_float("HOST_OBS_TRACE_FEED_WARM_INTERVAL_MS", 30000.0, minimum=500.0)), 500)
_TRACE_FEED_WARM_STALE_MS = max(int(_env_float("HOST_OBS_TRACE_FEED_WARM_STALE_MS", 45000.0, minimum=1000.0)), 1000)
_TRACE_FEED_WARM_STALE_FALLBACK_MS = max(
    int(_env_float("HOST_OBS_TRACE_FEED_WARM_STALE_FALLBACK_MS", 300000.0, minimum=1000.0)),
    int(_TRACE_FEED_WARM_STALE_MS),
)
_TRACE_FEED_WARM_FULL_REFRESH_MS = max(
    int(_env_float("HOST_OBS_TRACE_FEED_WARM_FULL_REFRESH_MS", 120000.0, minimum=1000.0)),
    int(_TRACE_FEED_WARM_INTERVAL_MS),
)
_TRACE_FEED_WARM_LIMIT = max(int(_env_float("HOST_OBS_TRACE_FEED_WARM_LIMIT", 6.0, minimum=1.0)), 1)
_TRACE_FEED_WARM_WINDOW_MINUTES = max(int(_env_float("HOST_OBS_TRACE_FEED_WARM_WINDOW_MINUTES", 1440.0, minimum=1.0)), 1)
_TRACE_FEED_WARM_ACTIVE_WITHIN_MS = max(int(_env_float("HOST_OBS_TRACE_FEED_WARM_ACTIVE_WITHIN_MS", 60000.0, minimum=1000.0)), 1000)
_trace_feed_warm_thread: threading.Thread | None = None
_trace_feed_warm_stop = threading.Event()
_trace_feed_warm_lock = threading.Lock()
_trace_feed_warm_state: dict[str, Any] = {
    "value": None,
    "updated_ts_ms": 0,
    "last_duration_ms": 0,
    "last_error": None,
    "session_signature": None,
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _now_ms() -> int:
    return int(time.time() * 1000)


def _get_ledger():
    from host_brain.app.obs_routes_core import _get_ledger as _core_get_ledger
    return _core_get_ledger()


def _safe_json(text: Any) -> Any:
    if isinstance(text, str):
        try:
            return json.loads(text)
        except Exception:
            return text
    return text


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _normalize_trace_status(raw_status: Any, *, default: str = "UNKNOWN") -> str:
    normalized = str(raw_status or "").strip().upper()
    if not normalized:
        return default
    return {
        "SUCCESS": "OK",
        "HEALTHY": "OK",
        "ERROR": "FAIL",
        "FAILED": "FAIL",
        "REJECTED": "FAIL",
    }.get(normalized, normalized)


def _model_dump_compat(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


def _format_sse_event(event: str, payload: Dict[str, Any], *, retry_ms: Optional[int] = None) -> str:
    lines: List[str] = []
    if retry_ms is not None:
        lines.append(f"retry: {int(retry_ms)}")
    lines.append(f"event: {event}")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    for line in data.splitlines() or ["{}"]:
        lines.append(f"data: {line}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _payload_contains_recall_content(payload: Dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    for key in ("items", "topk", "selected_refs", "slots"):
        value = payload.get(key)
        if isinstance(value, (list, tuple)) and len(value) > 0:
            return True
        if isinstance(value, dict) and bool(value):
            return True
        if isinstance(value, str) and value.strip():
            return True
    evidence_refs = payload.get("evidence_refs")
    if isinstance(evidence_refs, (list, tuple)) and len(evidence_refs) > 0:
        return True
    if isinstance(evidence_refs, str) and evidence_refs.strip():
        return True
    return False


# ---------------------------------------------------------------------------
# 节点构建辅助函数（从 obs_routes_core 导入）
# ---------------------------------------------------------------------------
def _get_trace_builders():
    """延迟导入 obs_routes_core 中的 trace 构建函数，避免循环导入。"""
    from host_brain.app.obs_routes_core import (
        _build_trace_response,
        _build_trace_response_lightweight,
        _build_trace_live_feed_snapshot,
        _build_trace_live_feed_delta,
        _trace_live_feed_signature,
    )
    return (
        _build_trace_response,
        _build_trace_response_lightweight,
        _build_trace_live_feed_snapshot,
        _build_trace_live_feed_delta,
        _trace_live_feed_signature,
    )


def _clone_trace_live_feed_snapshot(snapshot: TraceLiveFeedSnapshot) -> TraceLiveFeedSnapshot:
    if hasattr(snapshot, "model_copy"):
        return snapshot.model_copy(deep=True)
    return snapshot.copy(deep=True)


def _build_trace_live_feed_snapshot_sync(
    *,
    limit: int,
    window_minutes: int,
    keyword: Optional[str],
    active_within_ms: int,
) -> TraceLiveFeedSnapshot:
    (_, __, _build_trace_live_feed_snapshot, *___) = _get_trace_builders()
    return _build_trace_live_feed_snapshot(
        _get_ledger(),
        limit=limit,
        window_minutes=window_minutes,
        keyword=keyword,
        active_within_ms=active_within_ms,
    )


def _trace_feed_session_terminal(status: Any) -> bool:
    return _normalize_trace_status(status, default="UNKNOWN") in {"OK", "FAIL", "REJECTED"}


def _trace_feed_session_signature(
    *,
    limit: int,
    window_minutes: int,
    keyword: Optional[str],
    active_within_ms: int,
) -> tuple[tuple[str, int, str, int], ...]:
    from host_brain.app.obs_routes_core import (  # noqa: PLC0415
        _build_sessions_response,
        _dedup_sessions_by_content,
    )

    fetch_size = min(int(limit) * 3, 36)
    sessions_response = _build_sessions_response(
        _get_ledger(),
        page=1,
        page_size=fetch_size,
        status=None,
        window_minutes=int(window_minutes),
        keyword=keyword,
    )
    sessions = _dedup_sessions_by_content(sessions_response.items)[: int(limit)]
    if not str(keyword or "").strip():
        active_cutoff_ms = _now_ms() - max(0, int(active_within_ms))
        sessions = [
            session
            for session in sessions
            if int(getattr(session, "ts_ms", 0) or 0) >= active_cutoff_ms
            or not _trace_feed_session_terminal(getattr(session, "status", None))
        ]
    signature: list[tuple[str, int, str, int]] = []
    for session in sessions:
        request_id = str(getattr(session, "request_id", "") or "").strip()
        if not request_id:
            continue
        try:
            latency_ms = int(getattr(session, "total_latency_ms", -1) or -1)
        except (TypeError, ValueError):
            latency_ms = -1
        signature.append(
            (
                request_id,
                int(getattr(session, "ts_ms", 0) or 0),
                _normalize_trace_status(getattr(session, "status", None), default="UNKNOWN"),
                latency_ms,
            )
        )
    return tuple(signature)


def _store_trace_feed_warm_snapshot(
    snapshot: TraceLiveFeedSnapshot,
    *,
    started_at: int,
    session_signature: tuple[tuple[str, int, str, int], ...] | None,
    last_duration_ms: int | None = None,
) -> None:
    with _trace_feed_warm_lock:
        _trace_feed_warm_state["value"] = _clone_trace_live_feed_snapshot(snapshot)
        _trace_feed_warm_state["updated_ts_ms"] = _now_ms()
        _trace_feed_warm_state["last_duration_ms"] = (
            max(0, int(last_duration_ms))
            if last_duration_ms is not None
            else max(0, _now_ms() - started_at)
        )
        _trace_feed_warm_state["last_error"] = None
        _trace_feed_warm_state["session_signature"] = session_signature


def _refresh_trace_feed_warm_once() -> None:
    started_at = _now_ms()
    release_slot = None
    try:
        from host_brain.app.obs_resource_guard import (
            release_obs_warm_slot,
            should_defer_obs_warm_refresh,
            try_acquire_obs_warm_slot,
        )

        deferred, reason = should_defer_obs_warm_refresh(task_name="trace_feed")
        if deferred:
            with _trace_feed_warm_lock:
                _trace_feed_warm_state["last_error"] = f"deferred:{reason}"
                _trace_feed_warm_state["last_duration_ms"] = 0
            return
        acquired, reason = try_acquire_obs_warm_slot(task_name="trace_feed")
        if not acquired:
            with _trace_feed_warm_lock:
                _trace_feed_warm_state["last_error"] = f"deferred:{reason}"
                _trace_feed_warm_state["last_duration_ms"] = 0
            return
        release_slot = release_obs_warm_slot
    except Exception:
        release_slot = None
    try:
        session_signature: tuple[tuple[str, int, str, int], ...] | None = None
        try:
            session_signature = _trace_feed_session_signature(
                limit=_TRACE_FEED_WARM_LIMIT,
                window_minutes=_TRACE_FEED_WARM_WINDOW_MINUTES,
                keyword=None,
                active_within_ms=_TRACE_FEED_WARM_ACTIVE_WITHIN_MS,
            )
        except Exception as exc:
            logger.debug("trace feed warm session preflight failed: %s", exc)
            session_signature = None

        if session_signature == ():
            _store_trace_feed_warm_snapshot(
                TraceLiveFeedSnapshot(items=[], count=0, timestamp=_now_ms()),
                started_at=started_at,
                session_signature=session_signature,
                last_duration_ms=0,
            )
            return

        if session_signature is not None:
            with _trace_feed_warm_lock:
                previous_signature = _trace_feed_warm_state.get("session_signature")
                previous_value = _trace_feed_warm_state.get("value")
                previous_updated_ts_ms = int(_trace_feed_warm_state.get("updated_ts_ms") or 0)
            if (
                previous_value is not None
                and previous_signature == session_signature
                and previous_updated_ts_ms > 0
                and _now_ms() - previous_updated_ts_ms < _TRACE_FEED_WARM_FULL_REFRESH_MS
            ):
                _store_trace_feed_warm_snapshot(
                    _clone_trace_live_feed_snapshot(previous_value),
                    started_at=started_at,
                    session_signature=session_signature,
                    last_duration_ms=0,
                )
                return

        snapshot = _build_trace_live_feed_snapshot_sync(
            limit=_TRACE_FEED_WARM_LIMIT,
            window_minutes=_TRACE_FEED_WARM_WINDOW_MINUTES,
            keyword=None,
            active_within_ms=_TRACE_FEED_WARM_ACTIVE_WITHIN_MS,
        )
    except Exception as exc:
        with _trace_feed_warm_lock:
            _trace_feed_warm_state["last_error"] = str(exc)[:200]
            _trace_feed_warm_state["last_duration_ms"] = max(0, _now_ms() - started_at)
        logger.warning("trace feed warm refresh degraded: %s", exc)
        return
    finally:
        if release_slot is not None:
            release_slot()

    _store_trace_feed_warm_snapshot(
        snapshot,
        started_at=started_at,
        session_signature=session_signature,
    )


def _trace_feed_warm_loop() -> None:
    while not _trace_feed_warm_stop.is_set():
        _refresh_trace_feed_warm_once()
        wait_seconds = max(float(_TRACE_FEED_WARM_INTERVAL_MS) / 1000.0, 0.25)
        _trace_feed_warm_stop.wait(wait_seconds)


def start_trace_feed_warm_cache() -> dict[str, Any]:
    """Start the single durable projection authority.

    The legacy trace warm-cache loop is intentionally not started.  Query
    handlers read the durable projection store, so a second process-local
    refresher would be both wasted work and a competing read authority.
    """
    start_projection_jobs(interval_ms=5000)
    try:
        from host_brain.observability.projection_runtime import configure_embodiment_receipt_projection

        configure_embodiment_receipt_projection()
    except Exception as exc:
        logger.warning("embodiment receipt projection startup failed: %s", exc)
    return {**get_trace_feed_warm_status(), "authority": "projection_daemon"}


def stop_trace_feed_warm_cache() -> dict[str, Any]:
    global _trace_feed_warm_thread
    # Defensive cleanup for processes upgraded from the legacy implementation;
    # new processes never create this thread.
    _trace_feed_warm_stop.set()
    thread = _trace_feed_warm_thread
    if thread is not None:
        thread.join(timeout=2.0)
    _trace_feed_warm_thread = None
    stop_projection_jobs()
    try:
        from host_brain.observability.projection_runtime import stop_embodiment_receipt_projection

        stop_embodiment_receipt_projection()
    except Exception as exc:
        logger.debug("embodiment receipt projection shutdown failed: %s", exc)
    return get_trace_feed_warm_status()


def get_trace_feed_warm_status() -> dict[str, Any]:
    with _trace_feed_warm_lock:
        updated_ts_ms = int(_trace_feed_warm_state.get("updated_ts_ms") or 0)
        age_ms = max(0, _now_ms() - updated_ts_ms) if updated_ts_ms > 0 else None
        has_value = _trace_feed_warm_state.get("value") is not None
        last_duration_ms = int(_trace_feed_warm_state.get("last_duration_ms") or 0)
        last_error = str(_trace_feed_warm_state.get("last_error") or "").strip() or None
    running = _trace_feed_warm_thread is not None and _trace_feed_warm_thread.is_alive()
    return {
        "enabled": bool(_TRACE_FEED_WARM_ENABLED),
        "running": bool(running),
        "has_value": bool(has_value),
        "updated_ts_ms": updated_ts_ms or None,
        "age_ms": age_ms,
        "interval_ms": int(_TRACE_FEED_WARM_INTERVAL_MS),
        "stale_after_ms": int(_TRACE_FEED_WARM_STALE_MS),
        "full_refresh_ms": int(_TRACE_FEED_WARM_FULL_REFRESH_MS),
        "last_duration_ms": last_duration_ms,
        "last_error": last_error,
        "limit": int(_TRACE_FEED_WARM_LIMIT),
        "window_minutes": int(_TRACE_FEED_WARM_WINDOW_MINUTES),
        "active_within_ms": int(_TRACE_FEED_WARM_ACTIVE_WITHIN_MS),
    }


def _get_warmed_trace_feed_snapshot(
    *,
    limit: int,
    window_minutes: int,
    keyword: Optional[str],
    active_within_ms: int,
    allow_stale_ms: int | None = None,
) -> TraceLiveFeedSnapshot | None:
    if not _TRACE_FEED_WARM_ENABLED:
        return None
    if str(keyword or "").strip():
        return None
    if int(limit) != int(_TRACE_FEED_WARM_LIMIT):
        return None
    if int(window_minutes) != int(_TRACE_FEED_WARM_WINDOW_MINUTES):
        return None
    if int(active_within_ms) != int(_TRACE_FEED_WARM_ACTIVE_WITHIN_MS):
        return None

    with _trace_feed_warm_lock:
        value = _trace_feed_warm_state.get("value")
        updated_ts_ms = int(_trace_feed_warm_state.get("updated_ts_ms") or 0)

    if value is None or updated_ts_ms <= 0:
        return None
    max_age_ms = int(allow_stale_ms) if allow_stale_ms is not None else int(_TRACE_FEED_WARM_STALE_MS)
    if max(0, _now_ms() - updated_ts_ms) > max_age_ms:
        return None
    return _clone_trace_live_feed_snapshot(value)


def _trace_feed_should_defer_build() -> tuple[bool, str]:
    try:
        from host_brain.app.obs_resource_guard import should_defer_obs_warm_refresh  # noqa: PLC0415

        return should_defer_obs_warm_refresh(task_name="trace_feed_live")
    except Exception:
        return False, ""


# ---------------------------------------------------------------------------
# GET /obs/trace/{request_id}
# ---------------------------------------------------------------------------
@router.get("/trace/{request_id}", response_model=TraceResponse)
async def get_trace(request_id: str):
    """Read one durable Trace projection; this path never invokes source builders."""

    from host_brain.app.dependencies import get_query_projection_store

    document = get_query_projection_store().get(TRACE_KIND, request_id)
    meta = projection_meta(TRACE_KIND, request_id, document, max_age_ms=15000)
    if document is None or not meta["available"]:
        return TraceResponse(
            request_id=request_id,
            status="DEGRADED",
            node_count=0,
            nodes=[],
            edges=[],
            summary={
                "reason_code": meta.get("reason_code"),
                "message": "trace projection is rebuilding or stale",
            },
            ts_ms=_now_ms(),
            timestamp=_now_ms(),
            projection=meta,
        )
    payload = dict(document.payload)
    payload["projection"] = meta
    return TraceResponse(**payload)


def refresh_trace_query_projections() -> dict[str, int]:
    """Background-only Trace journal scan and projection refresh."""

    from host_brain.app.dependencies import get_query_projection_store

    ledger = _get_ledger()
    store = get_query_projection_store()
    build_full, _, build_feed, *_ = _get_trace_builders()
    now = _now_ms()
    since = now - 7 * 24 * 60 * 60 * 1000
    request_cursors: dict[str, int] = {}
    for table in ("graph_run_trace", "governor_log", "voice_event_log"):
        try:
            rows = ledger.fetch_by_time_range(
                table,
                start_ms=since,
                end_ms=now,
                limit=500,
                offset=0,
            )
        except Exception:
            rows = []
        for row in rows:
            request_id = str(row.get("request_id") or "").strip()
            if request_id:
                request_cursors[request_id] = max(
                    request_cursors.get(request_id, 0),
                    int(row.get("ts_ms") or 0),
                )
    refreshed = 0
    failed = 0
    try:
        from host_brain.integrations.graph_evidence import get_evidence_plane

        evidence_plane = get_evidence_plane()
    except Exception:
        evidence_plane = None
    for request_id, source_cursor in sorted(request_cursors.items()):
        try:
            current = store.get(TRACE_KIND, request_id)
            if current is not None and current.source_cursor >= source_cursor:
                store.upsert(
                    TRACE_KIND,
                    request_id,
                    current.payload,
                    source_cursor=current.source_cursor,
                    source_digest=current.source_digest,
                    source_ref="trace-background-reconcile",
                )
            else:
                source = trace_source(request_id, build_full(ledger, request_id))
                store.upsert(
                    TRACE_KIND,
                    request_id,
                    source.payload,
                    source_cursor=source_cursor,
                    source_digest=source.source_digest(),
                    source_ref="trace-background-worker",
                )
                refreshed += 1
        except Exception as exc:
            failed += 1
            if store.get(TRACE_KIND, request_id) is not None:
                store.mark_degraded(
                    TRACE_KIND,
                    request_id,
                    state=ProjectionDegradedState.SOURCE_UNAVAILABLE,
                    detail=f"{type(exc).__name__}: {exc}",
                )
        if evidence_plane is not None:
            try:
                stream_rows = evidence_plane.query_stream_events(request_id=request_id)
            except Exception:
                stream_rows = []
            stream_events = [
                {
                    "request_id": str(row.get("request_id") or request_id),
                    "graph_id": str(row.get("graph_id") or ""),
                    "phase": str(row.get("phase") or ""),
                    "event_type": str(row.get("event_type") or ""),
                    "node_id": row.get("node_id"),
                    "chunk": row.get("chunk"),
                    "ts_ms": int(row.get("ts_ms") or 0),
                }
                for row in stream_rows
                if isinstance(row, dict)
            ]
            stream_cursor = max(
                (int(event.get("ts_ms") or 0) for event in stream_events),
                default=source_cursor,
            )
            store.upsert(
                TRACE_STREAM_KIND,
                request_id,
                {
                    "request_id": request_id,
                    "events": stream_events,
                    "count": len(stream_events),
                    "timestamp": stream_cursor,
                },
                source_cursor=stream_cursor,
                source_ref="trace-stream-background-worker",
            )

    try:
        feed = build_feed(
            ledger,
            limit=12,
            window_minutes=10080,
            keyword=None,
            active_within_ms=3600000,
        )
        feed_payload = _model_dump_compat(feed)
        feed_items = list(feed_payload.get("items") or []) if isinstance(feed_payload, dict) else []
        feed_cursor = max(
            (
                max(
                    int((item.get("session") or {}).get("ts_ms") or 0),
                    int((item.get("trace") or {}).get("ts_ms") or 0),
                )
                for item in feed_items
                if isinstance(item, dict)
            ),
            default=0,
        )
        if isinstance(feed_payload, dict):
            feed_payload["timestamp"] = feed_cursor
            store.upsert(
                TRACE_FEED_KIND,
                TRACE_FEED_INDEX_KEY,
                feed_payload,
                source_cursor=feed_cursor,
                source_ref="trace-feed-background-worker",
            )
    except Exception as exc:
        failed += 1
        if store.get(TRACE_FEED_KIND, TRACE_FEED_INDEX_KEY) is not None:
            store.mark_degraded(
                TRACE_FEED_KIND,
                TRACE_FEED_INDEX_KEY,
                state=ProjectionDegradedState.SOURCE_UNAVAILABLE,
                detail=f"{type(exc).__name__}: {exc}",
            )

    try:
        from host_brain.app.internal_tribe_routes import get_tribe_event_bus

        tribe_rows = [event.to_dict() for event in get_tribe_event_bus().recent(limit=200)]
        tribe_cursor = max((int(row.get("ts_ms") or 0) for row in tribe_rows), default=0)
        store.upsert(
            TRIBE_EVENTS_KIND,
            TRIBE_EVENTS_INDEX_KEY,
            {"items": tribe_rows, "total": len(tribe_rows), "timestamp": tribe_cursor},
            source_cursor=tribe_cursor,
            source_ref="tribe-events-background-worker",
        )
    except Exception as exc:
        failed += 1
        if store.get(TRIBE_EVENTS_KIND, TRIBE_EVENTS_INDEX_KEY) is not None:
            store.mark_degraded(
                TRIBE_EVENTS_KIND,
                TRIBE_EVENTS_INDEX_KEY,
                state=ProjectionDegradedState.SOURCE_UNAVAILABLE,
                detail=f"{type(exc).__name__}: {exc}",
            )
    return {"refreshed_count": refreshed, "failed_count": failed}


register_projection_job("trace", refresh_trace_query_projections)


# ---------------------------------------------------------------------------
# GET /obs/trace/{request_id}/stream_events
# ---------------------------------------------------------------------------
@router.get("/trace/{request_id}/stream_events", response_model=TraceStreamEventsResponse)
def get_trace_stream_events(request_id: str):
    """Read the durable stream-event projection without EvidencePlane access."""

    from host_brain.app.dependencies import get_query_projection_store

    document = get_query_projection_store().get(TRACE_STREAM_KIND, request_id)
    meta = projection_meta(TRACE_STREAM_KIND, request_id, document, max_age_ms=15000)
    rows = list(document.payload.get("events") or []) if document is not None and meta["available"] else []
    events = [StreamEventItem(**dict(row)) for row in rows if isinstance(row, dict)]
    return TraceStreamEventsResponse(
        request_id=request_id,
        events=events,
        count=len(events),
        timestamp=_now_ms(),
        projection=meta,
    )


# ---------------------------------------------------------------------------
# GET /obs/trace-feed/live
# ---------------------------------------------------------------------------
@router.get("/trace-feed/snapshot", response_model=TraceLiveFeedSnapshot)
def get_trace_live_feed_snapshot(
    limit: int = Query(_TRACE_FEED_WARM_LIMIT, ge=1, le=12),
    window_minutes: int = Query(_TRACE_FEED_WARM_WINDOW_MINUTES, ge=1, le=10080),
    active_within_ms: int = Query(_TRACE_FEED_WARM_ACTIVE_WITHIN_MS, ge=1000, le=3600000),
    keyword: Optional[str] = Query(None),
):
    from host_brain.app.dependencies import get_query_projection_store

    document = get_query_projection_store().get(TRACE_FEED_KIND, TRACE_FEED_INDEX_KEY)
    meta = projection_meta(TRACE_FEED_KIND, TRACE_FEED_INDEX_KEY, document, max_age_ms=15000)
    if document is None or not meta["available"]:
        return TraceLiveFeedSnapshot(items=[], count=0, timestamp=_now_ms(), projection=meta)
    now = _now_ms()
    window_cutoff = now - int(window_minutes) * 60 * 1000
    active_cutoff = now - int(active_within_ms)
    keyword_text = str(keyword or "").strip().lower()
    filtered: list[LiveTraceFeedItem] = []
    for raw in list(document.payload.get("items") or []):
        if not isinstance(raw, dict):
            continue
        item = LiveTraceFeedItem(**raw)
        serialized = _model_dump_compat(item)
        session = serialized.get("session") if isinstance(serialized, dict) else {}
        trace = serialized.get("trace") if isinstance(serialized, dict) else {}
        event_ts = max(
            int(session.get("ts_ms") or 0) if isinstance(session, dict) else 0,
            int(trace.get("ts_ms") or 0) if isinstance(trace, dict) else 0,
        )
        if event_ts and (event_ts < window_cutoff or event_ts < active_cutoff):
            continue
        if keyword_text and keyword_text not in json.dumps(serialized, ensure_ascii=False).lower():
            continue
        filtered.append(item)
        if len(filtered) >= int(limit):
            break
    return TraceLiveFeedSnapshot(
        items=filtered,
        count=len(filtered),
        timestamp=now,
        projection=meta,
    )


@router.get("/trace-feed/live")
async def stream_trace_live_feed(
    request: Request,
    limit: int = Query(6, ge=1, le=12),
    window_minutes: int = Query(1440, ge=1, le=10080),
    active_within_ms: int = Query(60000, ge=1000, le=3600000),
    interval_ms: int = Query(1000, ge=250, le=10000),
    keyword: Optional[str] = Query(None),
    once: bool = Query(False),
):
    """流式推送 Trace 首页活跃链路快照，供 WebUI 实时渲染活跃 feed。"""
    async def _event_stream():
        last_signature: Optional[str] = None
        first_event = True
        while True:
            if await request.is_disconnected():
                break
            try:
                snapshot = get_trace_live_feed_snapshot(
                    limit=limit,
                    window_minutes=window_minutes,
                    keyword=keyword,
                    active_within_ms=active_within_ms,
                )
                snapshot_dict = _model_dump_compat(snapshot)
                signature = json.dumps(
                    snapshot_dict.get("items") if isinstance(snapshot_dict, dict) else [],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if signature != last_signature:
                    yield _format_sse_event(
                        "snapshot",
                        _model_dump_compat(snapshot),
                        retry_ms=interval_ms if first_event else None,
                    )
                    last_signature = signature
                    first_event = False
                else:
                    yield _format_sse_event(
                        "heartbeat",
                        {"type": "heartbeat", "count": snapshot.count, "timestamp": _now_ms()},
                    )
            except Exception as exc:
                logger.warning("trace live feed push error: %s", type(exc).__name__)
                yield _format_sse_event(
                    "trace_error",
                    {
                        "type": "error",
                        "detail": "OBS_TRACE_STREAM_UNAVAILABLE",
                        "timestamp": _now_ms(),
                    },
                )
            if once:
                break
            await asyncio.sleep(interval_ms / 1000)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /obs/tribe/events
# ---------------------------------------------------------------------------
@router.get("/tribe/events", response_model=TribeEventsResponse)
def get_tribe_events(
    limit: int = Query(20, ge=1, le=200, description="返回最近事件条数"),
    event_type: Optional[str] = Query(None, description="按事件类型过滤"),
    source_agent: Optional[str] = Query(None, description="按 source_agent 过滤"),
    target_agent: Optional[str] = Query(None, description="按 target_agent 过滤"),
    request_id: Optional[str] = Query(None, description="按 request_id 过滤"),
    session_id: Optional[str] = Query(None, description="按 session_id 过滤"),
    conversation_id: Optional[str] = Query(None, description="按 conversation_id 过滤"),
):
    """Read and filter the durable Tribe-event projection."""

    from host_brain.app.dependencies import get_query_projection_store

    document = get_query_projection_store().get(TRIBE_EVENTS_KIND, TRIBE_EVENTS_INDEX_KEY)
    meta = projection_meta(TRIBE_EVENTS_KIND, TRIBE_EVENTS_INDEX_KEY, document, max_age_ms=15000)
    raw_items = list(document.payload.get("items") or []) if document is not None and meta["available"] else []
    filters = {
        "event_type": event_type,
        "source_agent": source_agent,
        "target_agent": target_agent,
        "request_id": request_id,
        "session_id": session_id,
        "conversation_id": conversation_id,
    }
    items: list[TribeEventItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        if any(
            str(raw.get(field) or "") != str(expected)
            for field, expected in filters.items()
            if expected is not None
        ):
            continue
        items.append(TribeEventItem(**raw))
        if len(items) >= int(limit):
            break

    timestamp = items[0].ts_ms if items else _now_ms()
    return TribeEventsResponse(total=len(items), items=items, timestamp=int(timestamp), projection=meta)


# ---------------------------------------------------------------------------
# GET /obs/graph/schema
# ---------------------------------------------------------------------------
@router.get("/graph/schema", response_model=GraphSchemaResponse)
def get_graph_schema():
    """获取当前 Host 图编排定义（节点、边、graph_meta），供 WebUI 展示图拓扑。"""
    try:
        from host_brain.app.graph_config_builder import (
            CHAT_GRAPH_NODE_ORDER,
            _build_graph_config_by_profile,
            _HOST_CHAT_14B_DEFAULT_PROVIDER,
            _HOST_CHAT_14B_DEFAULT_MODEL_ID,
        )
        from host_brain.app.execution_profile import PROFILE_CHAT_FAST

        graph_config = _build_graph_config_by_profile(
            execution_profile=PROFILE_CHAT_FAST,
            provider_name=_HOST_CHAT_14B_DEFAULT_PROVIDER,
            model_id=_HOST_CHAT_14B_DEFAULT_MODEL_ID or "gemini-2.5-flash-lite",
            timeout_ms=60000,
            available=False,
            api_key=None,
            provider_fn=lambda _: None,
        )
        edges_raw = graph_config.get("edges") or []
        graph_meta = graph_config.get("graph_meta") or {}
        active = list(graph_meta.get("active_nodes") or CHAT_GRAPH_NODE_ORDER)
        nodes = [GraphSchemaNode(node_id=n) for n in active]
        edges = [
            GraphSchemaEdge(source=e.get("source", ""), target=e.get("target", ""))
            for e in edges_raw if isinstance(e, dict)
        ]
        return GraphSchemaResponse(
            graph_id=graph_config.get("graph_id", "host.chat.realtime"),
            version=graph_config.get("version", "v1"),
            entry_node=graph_config.get("entry_node"),
            active_nodes=active,
            nodes=nodes,
            edges=edges,
            graph_meta=graph_meta,
            timestamp=_now_ms(),
        )
    except Exception as exc:
        logger.warning("get_graph_schema failed: %s", exc)
        from host_brain.app.graph_config_builder import CHAT_GRAPH_NODE_ORDER
        return GraphSchemaResponse(
            graph_id="host.chat.realtime",
            version="v1",
            active_nodes=CHAT_GRAPH_NODE_ORDER,
            nodes=[GraphSchemaNode(node_id=n) for n in CHAT_GRAPH_NODE_ORDER],
            edges=[],
            timestamp=_now_ms(),
        )
