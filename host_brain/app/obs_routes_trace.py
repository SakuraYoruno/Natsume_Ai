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
import time
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

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
# Pydantic 响应模型
# ---------------------------------------------------------------------------
class TraceNode(BaseModel):
    node_id: str
    node_type: str
    display_name: str
    side: Optional[str] = None
    provider: Optional[str] = None
    model_id: Optional[str] = None
    status: str
    reason_code: Optional[str] = None
    latency_ms: Optional[int] = None
    input_data: Optional[Any] = None
    output_data: Optional[Any] = None
    ts_ms: Optional[int] = None
    error: Optional[str] = None
    group_id: Optional[str] = None
    owner_node_id: Optional[str] = None
    subgraph_id: Optional[str] = None
    parent_node_id: Optional[str] = None


class TraceEdge(BaseModel):
    source: str
    target: str


class TraceResponse(BaseModel):
    request_id: str
    session_id: Optional[str] = None
    status: str
    total_latency_ms: Optional[int] = None
    node_count: int
    nodes: List[TraceNode]
    edges: List[TraceEdge]
    summary: Optional[Dict[str, Any]] = None
    subgraphs: List[Dict[str, Any]] = Field(default_factory=list)
    stream_events: List[Dict[str, Any]] = Field(default_factory=list)
    ts_ms: int
    timestamp: int


class TribeEventItem(BaseModel):
    event_id: str
    event_type: str
    source_agent: str
    target_agent: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    schema_version: str
    ts_ms: int


class TribeEventsResponse(BaseModel):
    total: int
    items: List[TribeEventItem]
    timestamp: int


class LiveTraceFeedItem(BaseModel):
    session: Any
    trace: Optional[TraceResponse] = None
    error: Optional[str] = None


class TraceLiveFeedSnapshot(BaseModel):
    type: Literal["snapshot"] = "snapshot"
    items: List[LiveTraceFeedItem] = Field(default_factory=list)
    count: int
    timestamp: int


class TraceLiveFeedNodeDelta(BaseModel):
    change_type: Literal["added", "updated"]
    node: TraceNode
    previous_status: Optional[str] = None


class TraceLiveFeedDeltaItem(BaseModel):
    request_id: str
    session_id: Optional[str] = None
    session: Optional[Any] = None
    trace_status: Optional[str] = None
    total_latency_ms: Optional[int] = None
    latest_node: Optional[TraceNode] = None
    changes: List[TraceLiveFeedNodeDelta] = Field(default_factory=list)
    change_count: int = 0


class TraceLiveFeedDelta(BaseModel):
    type: Literal["delta"] = "delta"
    items: List[TraceLiveFeedDeltaItem] = Field(default_factory=list)
    count: int
    timestamp: int


class StreamEventItem(BaseModel):
    request_id: str
    graph_id: str
    phase: str
    event_type: str
    node_id: Optional[str] = None
    chunk: Optional[str] = None
    ts_ms: int


class TraceStreamEventsResponse(BaseModel):
    request_id: str
    events: List[StreamEventItem]
    count: int
    timestamp: int


class GraphSchemaNode(BaseModel):
    node_id: str
    display_name: Optional[str] = None


class GraphSchemaEdge(BaseModel):
    source: str
    target: str


class GraphSchemaResponse(BaseModel):
    graph_id: str
    version: str
    entry_node: Optional[str] = None
    active_nodes: List[str] = Field(default_factory=list)
    nodes: List[GraphSchemaNode] = Field(default_factory=list)
    edges: List[GraphSchemaEdge] = Field(default_factory=list)
    graph_meta: Optional[Dict[str, Any]] = None
    timestamp: int


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


# ---------------------------------------------------------------------------
# GET /obs/trace/{request_id}
# ---------------------------------------------------------------------------
@router.get("/trace/{request_id}", response_model=TraceResponse)
async def get_trace(request_id: str):
    """获取单次会话的完整链路节点详情（含入参/出参/状态/延迟）。"""
    (
        _build_trace_response,
        _build_trace_response_lightweight,
        *_,
    ) = _get_trace_builders()
    ledger = _get_ledger()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_build_trace_response, ledger, request_id),
            timeout=_TRACE_BUILD_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "trace full build timeout: request_id=%s timeout_sec=%.2f, degrade to lightweight",
            request_id,
            _TRACE_BUILD_TIMEOUT_SEC,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("trace full build failed: request_id=%s err=%s, degrade to lightweight", request_id, exc)

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                _build_trace_response_lightweight,
                ledger,
                request_id,
                reason="TRACE_DETAIL_DEGRADED",
                message="trace detail built in lightweight mode due to timeout or downstream pressure",
            ),
            timeout=_TRACE_BUILD_FALLBACK_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail={
                "error": "trace_timeout",
                "request_id": request_id,
                "message": "trace detail build timed out in both full and lightweight modes",
            },
        ) from exc


# ---------------------------------------------------------------------------
# GET /obs/trace/{request_id}/stream_events
# ---------------------------------------------------------------------------
@router.get("/trace/{request_id}/stream_events", response_model=TraceStreamEventsResponse)
def get_trace_stream_events(request_id: str):
    """获取单次请求的 Chat Stream SSE 事件流，供链路详情展示。"""
    try:
        from host_brain.integrations.graph_evidence import get_evidence_plane
        ep = get_evidence_plane()
        rows = ep.query_stream_events(request_id=request_id)
    except Exception as exc:
        logger.debug("get_trace_stream_events: %s", exc)
        rows = []
    events = [
        StreamEventItem(
            request_id=str(r.get("request_id", "")),
            graph_id=str(r.get("graph_id", "")),
            phase=str(r.get("phase", "")),
            event_type=str(r.get("event_type", "")),
            node_id=r.get("node_id"),
            chunk=r.get("chunk"),
            ts_ms=int(r.get("ts_ms") or 0),
        )
        for r in rows
    ]
    return TraceStreamEventsResponse(
        request_id=request_id,
        events=events,
        count=len(events),
        timestamp=_now_ms(),
    )


# ---------------------------------------------------------------------------
# GET /obs/trace-feed/live
# ---------------------------------------------------------------------------
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
    (
        _,
        __,
        _build_trace_live_feed_snapshot,
        _build_trace_live_feed_delta,
        _trace_live_feed_signature,
    ) = _get_trace_builders()

    async def _event_stream():
        last_signature: Optional[str] = None
        last_snapshot: Optional[TraceLiveFeedSnapshot] = None
        first_event = True
        ledger = _get_ledger()
        while True:
            if await request.is_disconnected():
                break
            try:
                snapshot = await asyncio.to_thread(
                    _build_trace_live_feed_snapshot,
                    ledger,
                    limit=limit,
                    window_minutes=window_minutes,
                    keyword=keyword,
                    active_within_ms=active_within_ms,
                )
                signature = _trace_live_feed_signature(snapshot)
                if signature != last_signature:
                    delta = _build_trace_live_feed_delta(last_snapshot, snapshot)
                    yield _format_sse_event(
                        "snapshot",
                        _model_dump_compat(snapshot),
                        retry_ms=interval_ms if first_event else None,
                    )
                    if delta is not None:
                        yield _format_sse_event("delta", _model_dump_compat(delta))
                    last_signature = signature
                    last_snapshot = snapshot
                    first_event = False
                else:
                    yield _format_sse_event(
                        "heartbeat",
                        {"type": "heartbeat", "count": snapshot.count, "timestamp": _now_ms()},
                    )
            except Exception as exc:
                logger.warning("trace live feed push error: %s", exc)
                yield _format_sse_event(
                    "trace_error",
                    {"type": "error", "detail": str(exc), "timestamp": _now_ms()},
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
    """Tribe 事件观测窗口（Host 内存总线最近事件）。"""
    try:
        from host_brain.app.internal_tribe_routes import get_tribe_event_bus
        bus = get_tribe_event_bus()
        items = [
            TribeEventItem(**event.to_dict())
            for event in bus.recent(
                limit=limit,
                event_type=event_type,
                source_agent=source_agent,
                target_agent=target_agent,
                request_id=request_id,
                session_id=session_id,
                conversation_id=conversation_id,
            )
        ]
    except Exception as exc:
        logger.debug("get_tribe_events degraded: %s", exc)
        items = []

    timestamp = items[0].ts_ms if items else _now_ms()
    return TribeEventsResponse(total=len(items), items=items, timestamp=int(timestamp))


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
            model_id=_HOST_CHAT_14B_DEFAULT_MODEL_ID or "gemini-2.5-flash",
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
