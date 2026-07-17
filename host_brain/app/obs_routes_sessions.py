"""Observability — 会话列表端点。

拆分自 obs_routes_core.py（P2-7）：
  GET /obs/sessions         — 会话列表（分页 + 过滤）
  GET /obs/sessions/export  — 会话列表 CSV 导出
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import logging
import os
import time
from typing import List, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from host_brain.observability.api_models import SessionItem, SessionsResponse
from host_brain.app.obs_route_ports import (
    require_obs_query_projection_store,
    require_obs_route_ports,
)

logger = logging.getLogger("Natsume.obs")

router = APIRouter(prefix="/obs", tags=["observability"])
# OPT-4 首批迁移：从 settings.observability 读取，替代 os.getenv
from host_brain.app.settings.observability import get_observability_settings  # noqa: E402

_SESSIONS_CACHE_TTL_MS = max(int(get_observability_settings().obs_sessions_cache_ttl_ms), 0)

_DEBUG_SESSION_PREFIXES = ("smoke_", "sess-clarify-", "debug-", "chat-debug-")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _now_ms() -> int:
    return int(time.time() * 1000)


def _get_ledger():
    from host_brain.app.obs_routes_core import _get_ledger as _core_get_ledger
    return _core_get_ledger()


def _normalize_session_preview(value: Optional[str]) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _is_placeholder_session_preview(value: Optional[str]) -> bool:
    normalized = str(value or "").strip()
    if not normalized:
        return False
    return all(ch in {"?", "？"} for ch in normalized)


def _is_debug_session_id(session_id: Optional[str]) -> bool:
    normalized = str(session_id or "").strip().lower()
    if not normalized:
        return False
    return normalized.startswith(_DEBUG_SESSION_PREFIXES)


def _dedup_sessions_by_content(sessions: List[SessionItem]) -> List[SessionItem]:
    """同 session_id + 相同 input_preview 的多条请求只保留最新的一条。"""
    from typing import Dict
    best: Dict[str, SessionItem] = {}
    no_session: List[SessionItem] = []

    for session in sessions:
        sid = (session.session_id or "").strip()
        if not sid:
            no_session.append(session)
            continue
        preview = (session.input_preview or "").strip().casefold()
        key = f"{sid}::{preview}"
        existing = best.get(key)
        if existing is None or session.ts_ms > existing.ts_ms:
            best[key] = session

    deduped = sorted(best.values(), key=lambda s: s.ts_ms, reverse=True)
    return no_session + deduped


def _sanitize_session_items_for_export(
    sessions: List[SessionItem],
    *,
    keyword: Optional[str] = None,
) -> List[SessionItem]:
    from typing import Dict
    keyword_active = bool(str(keyword or "").strip())
    filtered: List[SessionItem] = []
    for session in sessions:
        preview_key = _normalize_session_preview(session.input_preview)
        if _is_placeholder_session_preview(session.input_preview):
            continue
        if not keyword_active and not preview_key:
            continue
        if not keyword_active and _is_debug_session_id(session.session_id):
            continue
        filtered.append(session)

    best: Dict[str, SessionItem] = {}
    passthrough: List[SessionItem] = []
    for session in filtered:
        session_key = str(session.session_id or "").strip().lower()
        preview_key = _normalize_session_preview(session.input_preview)
        if not session_key or not preview_key:
            passthrough.append(session)
            continue
        dedupe_key = f"{session_key}::{preview_key}"
        previous = best.get(dedupe_key)
        if previous is None or session.ts_ms > previous.ts_ms:
            best[dedupe_key] = session

    sanitized = passthrough + sorted(best.values(), key=lambda s: s.ts_ms, reverse=True)
    sanitized.sort(key=lambda s: s.ts_ms, reverse=True)
    return sanitized


def _build_sessions_response(
    ledger=None,
    *,
    projection_store=None,
    page: int,
    page_size: int,
    status: Optional[str],
    window_minutes: int,
    keyword: Optional[str],
) -> SessionsResponse:
    from host_brain.app.observability_runtime.session_query import SessionQueryService
    if ledger is None:
        if projection_store is None:
            raise RuntimeError("explicit query projection store is required")
        svc = SessionQueryService(None, projection_store=projection_store)
    else:
        # Background/source builders must never rediscover the process-owned
        # query store from a durable ledger path.  Their output is materialized
        # by the explicitly composed projection job instead.
        svc = SessionQueryService(ledger, source_only=True)
    since_ms = _now_ms() - window_minutes * 60 * 1000
    result = svc.query(
        page=page,
        page_size=page_size,
        status=status,
        keyword=keyword,
        start_time=since_ms,
        end_time=_now_ms(),
    )

    items = [
        SessionItem(
            request_id=r["request_id"],
            session_id=r.get("session_id"),
            ts_ms=r.get("ts_ms", 0),
            status=r.get("status", "error").upper(),
            decision=r.get("decision"),
            reason_code=r.get("reason_code"),
            total_latency_ms=r.get("duration_ms"),
            input_preview=r.get("input_preview"),
        )
        for r in result.get("items", [])
    ]

    return SessionsResponse(
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
        items=items,
        timestamp=_now_ms(),
        projection=result.get("projection"),
    )


def build_sessions_projection_response(
    projection_store,
    *,
    page: int,
    page_size: int,
    status: Optional[str],
    window_minutes: int,
    keyword: Optional[str],
) -> SessionsResponse:
    """Query sessions through one explicit application-local projection port."""

    if projection_store is None or not callable(getattr(projection_store, "get", None)):
        raise RuntimeError("explicit query projection store is required")
    return _build_sessions_response(
        None,
        projection_store=projection_store,
        page=page,
        page_size=page_size,
        status=status,
        window_minutes=window_minutes,
        keyword=keyword,
    )


# ---------------------------------------------------------------------------
# GET /obs/sessions
# ---------------------------------------------------------------------------
@router.get("/sessions", response_model=SessionsResponse)
def get_sessions(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="状态过滤: ok/rejected/error"),
    window_minutes: int = Query(60, ge=1, le=10080),
    keyword: Optional[str] = Query(None),
):
    """会话列表（分页 + 过滤）。"""
    from host_brain.app.obs_routes_core import _OBS_CACHE_WAIT_TIMEOUT_SEC

    route_ports = require_obs_route_ports(request)
    return route_ports.response_cache.get_or_build(
        cache_name="obs_sessions",
        cache_key={
            "page": int(page),
            "page_size": int(page_size),
            "status": status or "",
            "window_minutes": int(window_minutes),
            "keyword": keyword or "",
        },
        ttl_ms=_SESSIONS_CACHE_TTL_MS,
        wait_timeout_s=_OBS_CACHE_WAIT_TIMEOUT_SEC,
        builder=lambda: build_sessions_projection_response(
            route_ports.require_query_projection_store(),
            page=page,
            page_size=page_size,
            status=status,
            window_minutes=window_minutes,
            keyword=keyword,
        ),
    )


# ---------------------------------------------------------------------------
# GET /obs/sessions/export
# ---------------------------------------------------------------------------
@router.get("/sessions/export")
def export_sessions_csv(
    request: Request,
    status: Optional[str] = Query(None, description="状态过滤: ok/rejected/error"),
    window_minutes: int = Query(1440, ge=1, le=10080, description="导出时间窗口（分钟），默认 24h"),
    keyword: Optional[str] = Query(None),
    max_rows: int = Query(5000, ge=1, le=10000, description="最大导出行数"),
):
    """导出会话列表为 CSV 文件（支持时间范围 + 状态过滤）。"""
    sessions_response = build_sessions_projection_response(
        require_obs_query_projection_store(request),
        page=1,
        page_size=max_rows,
        status=status,
        window_minutes=window_minutes,
        keyword=keyword,
    )

    rows = _sanitize_session_items_for_export(
        list(sessions_response.items),
        keyword=keyword,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "request_id", "session_id", "ts_ms", "datetime",
        "status", "decision", "reason_code", "total_latency_ms",
    ])
    for r in rows:
        ts = r.ts_ms or 0
        dt_str = _dt.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        writer.writerow([
            r.request_id,
            r.session_id or "",
            ts,
            dt_str,
            r.status or "",
            r.decision or "",
            r.reason_code or "",
            r.total_latency_ms or "",
        ])

    output.seek(0)
    filename = f"sessions_{int(_now_ms() / 1000)}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
