"""Life-body aggregate observability routes for monitor-side digital life views."""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from host_brain.app.obs_route_ports import require_obs_query_projection_store
from host_brain.app.obs_routes_core import (
    _as_dict,
    _as_list,
    _now_ms,
)
from host_brain.app.observability_runtime.process_state_projection import (
    LIFE_BODY_KIND,
    LIFE_BODY_MAX_AGE_MS,
    read_process_projection,
    window_key,
)
from host_brain.core.runtime_helpers import _clean_text as _as_text
from host_brain.observability.projection_store import QueryProjectionStore

router = APIRouter(prefix="/obs", tags=["observability"])



class LifeBodyResponse(BaseModel):
    window_minutes: int
    timestamp: int
    runtimeSnapshot: Optional[Dict[str, Any]] = None
    promptStats: Optional[Dict[str, Any]] = None
    vtsData: Optional[Dict[str, Any]] = None
    memoryData: Optional[Dict[str, Any]] = None
    servicesData: Optional[Dict[str, Any]] = None
    subsystemsData: Optional[Dict[str, Any]] = None
    organTribeData: Optional[Dict[str, Any]] = None
    capabilityGapData: Optional[Dict[str, Any]] = None
    capabilityGapProjection: Dict[str, Any] = Field(default_factory=dict)
    humanoidLoopData: Optional[Dict[str, Any]] = None
    humanoidLoopTrendData: Optional[Dict[str, Any]] = None
    humanoidLoopAlertsData: Optional[Dict[str, Any]] = None
    humanoidLoopRemediationData: Optional[Dict[str, Any]] = None
    humanoidLoopDiagnosticsData: Optional[Dict[str, Any]] = None
    humanoidLoopHotspotsData: Optional[Dict[str, Any]] = None
    humanoidLoopHotspotTrendData: Optional[Dict[str, Any]] = None
    humanoidLoopSourceCorrelationData: Optional[Dict[str, Any]] = None
    runtimeAlerts: List[Dict[str, Any]] = Field(default_factory=list)
    nonLiveSlots: List[List[str]] = Field(default_factory=list)
    growthSignals: Dict[str, Any] = Field(default_factory=dict)
    derived: Dict[str, Any] = Field(default_factory=dict)
    model: Dict[str, Any] = Field(default_factory=dict)
    meta: Dict[str, Any] = Field(default_factory=dict)


class LifeTimelineResponse(BaseModel):
    window_minutes: int
    timestamp: int
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    filtered_total: int = 0
    primaryPathology: Optional[Dict[str, Any]] = None
    primaryOrgan: Optional[Dict[str, Any]] = None
    filters: Dict[str, Any] = Field(default_factory=dict)
    meta: Dict[str, Any] = Field(default_factory=dict)


class PathologiesResponse(BaseModel):
    window_minutes: int
    timestamp: int
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    filtered_total: int = 0
    primaryPathology: Optional[Dict[str, Any]] = None
    organSystems: List[Dict[str, Any]] = Field(default_factory=list)
    focusedItem: Optional[Dict[str, Any]] = None
    focusedOrgan: Optional[Dict[str, Any]] = None
    relatedEvents: List[Dict[str, Any]] = Field(default_factory=list)
    filters: Dict[str, Any] = Field(default_factory=dict)
    meta: Dict[str, Any] = Field(default_factory=dict)


def _clone_payload(payload: Any) -> Any:
    return copy.deepcopy(payload)


def _extract_representative_ref_id(value: Any, ref_type: str) -> str:
    candidates = value if isinstance(value, (list, tuple, set)) else [value]
    for item in candidates:
        item_dict = _as_dict(item)
        if _as_text(item_dict.get("type")) == _as_text(ref_type):
            return _as_text(item_dict.get("id"))
    return ""


def _extract_request_id_from_event(item: dict[str, Any]) -> str:
    return (
        _as_text(item.get("requestId"))
        or _as_text(item.get("sourceRequestId"))
        or _extract_representative_ref_id(item.get("representativeRefs"), "request")
    )


def _extract_session_id_from_event(item: dict[str, Any]) -> str:
    return (
        _as_text(item.get("sessionId"))
        or _as_text(item.get("sourceSessionId"))
        or _extract_representative_ref_id(item.get("representativeRefs"), "session")
    )


def _extract_tick_id_from_event(item: dict[str, Any]) -> str:
    return (
        _as_text(item.get("tickId"))
        or _as_text(item.get("sourceTickId"))
        or _extract_representative_ref_id(item.get("representativeRefs"), "tick")
    )






def _get_life_body_snapshot(
    store: QueryProjectionStore,
    window_minutes: int,
) -> dict[str, Any]:
    payload, projection = read_process_projection(
        store,
        LIFE_BODY_KIND,
        window_key(window_minutes),
        max_age_ms=LIFE_BODY_MAX_AGE_MS,
    )
    response = _clone_payload(payload) if payload is not None else {
        "window_minutes": int(window_minutes),
        "timestamp": _now_ms(),
    }
    meta = _as_dict(response.get("meta"))
    meta["projection"] = projection
    response["meta"] = meta
    return response


@router.get("/life-body", response_model=LifeBodyResponse)
def get_life_body(
    request: Request,
    window_minutes: int = Query(360, ge=1, le=10080, description="聚合时间窗口（分钟）"),
):
    store = require_obs_query_projection_store(request)
    return LifeBodyResponse(**_get_life_body_snapshot(store, window_minutes))


@router.get("/life-timeline", response_model=LifeTimelineResponse)
def get_life_timeline(
    request: Request,
    window_minutes: int = Query(360, ge=1, le=10080, description="聚合时间窗口（分钟）"),
    event_type: str = Query("", alias="type", description="事件类型过滤"),
    organ: str = Query("", description="器官系统过滤"),
    request_id: str = Query("", description="request 过滤（第一版为 representative ref 级 best-effort 过滤）"),
    session_id: str = Query("", description="session 过滤"),
    tick_id: str = Query("", description="tick / scheduler 事件过滤"),
):
    store = require_obs_query_projection_store(request)
    payload = _get_life_body_snapshot(store, window_minutes)
    model = _as_dict(payload.get("model"))
    items = _clone_payload(_as_list(model.get("timelineEventPool") or model.get("timelineEvents")))
    normalized_type = event_type.strip() if isinstance(event_type, str) else ""
    normalized_organ = organ.strip() if isinstance(organ, str) else ""
    normalized_request_id = request_id.strip() if isinstance(request_id, str) else ""
    normalized_session_id = session_id.strip() if isinstance(session_id, str) else ""
    normalized_tick_id = tick_id.strip() if isinstance(tick_id, str) else ""
    filtered = []
    for item in items:
        item_dict = _as_dict(item)
        if normalized_type and _as_text(item_dict.get("type")) != normalized_type:
            continue
        if normalized_organ and _as_text(item_dict.get("organId")) != normalized_organ:
            continue
        if normalized_request_id and _extract_request_id_from_event(item_dict) != normalized_request_id:
            continue
        if normalized_session_id and _extract_session_id_from_event(item_dict) != normalized_session_id:
            continue
        if normalized_tick_id and _extract_tick_id_from_event(item_dict) != normalized_tick_id:
            continue
        filtered.append(item_dict)
    return LifeTimelineResponse(window_minutes=window_minutes, timestamp=_now_ms(), items=filtered, total=len(items), filtered_total=len(filtered), primaryPathology=_as_dict(model.get("primaryPathology")) or None, primaryOrgan=_as_dict(model.get("primaryOrgan")) or None, filters={"type": normalized_type or None, "organ": normalized_organ or None, "request_id": normalized_request_id or None, "session_id": normalized_session_id or None, "tick_id": normalized_tick_id or None}, meta={"aggregateSource": "obs-sidecar-life-timeline", "coverage": "derived request / bond / memory / tick / immune projection from life-body aggregate; memory / bond prefer bounded ledger lineage, immune prefers bounded voice_event_log lineage, tick prefers scheduler_event_log when available", "lifeBodyMeta": _as_dict(payload.get("meta"))})


@router.get("/pathologies", response_model=PathologiesResponse)
def get_pathologies(
    request: Request,
    window_minutes: int = Query(360, ge=1, le=10080, description="聚合时间窗口（分钟）"),
    organ: str = Query("", description="器官系统过滤"),
    severity: str = Query("", description="病灶级别过滤"),
    pathology_id: str = Query("", description="聚焦单个病灶"),
):
    store = require_obs_query_projection_store(request)
    payload = _get_life_body_snapshot(store, window_minutes)
    model = _as_dict(payload.get("model"))
    items = [_as_dict(item) for item in _as_list(model.get("pathologies"))]
    related_events = [_as_dict(item) for item in _as_list(model.get("timelineEventPool") or model.get("timelineEvents"))]
    normalized_organ = _as_text(organ)
    normalized_severity = _as_text(severity).lower()
    normalized_pathology_id = _as_text(pathology_id)
    filtered = []
    for item in items:
        if normalized_organ and _as_text(item.get("organId")) != normalized_organ:
            continue
        if normalized_severity and _as_text(item.get("severity")).lower() != normalized_severity:
            continue
        if normalized_pathology_id and _as_text(item.get("id")) != normalized_pathology_id:
            continue
        filtered.append(item)
    focused_item = next((item for item in filtered if _as_text(item.get("id")) == normalized_pathology_id), None) if normalized_pathology_id else None
    if focused_item:
        focused_related_events = []
        for event in related_events:
            if _as_text(event.get("pathologyId")) == _as_text(focused_item.get("id")) or _as_text(event.get("organId")) == _as_text(focused_item.get("organId")):
                focused_related_events.append(event)
        related_events = focused_related_events
    else:
        related_events = []
    organ_systems = [_as_dict(item) for item in _as_list(model.get("organSystems"))]
    focused_organ = None
    if focused_item:
        focused_organ = next((item for item in organ_systems if _as_text(item.get("id")) == _as_text(focused_item.get("organId"))), None)
    return PathologiesResponse(window_minutes=window_minutes, timestamp=_now_ms(), items=filtered, total=len(items), filtered_total=len(filtered), primaryPathology=_as_dict(model.get("primaryPathology")) or None, organSystems=organ_systems, focusedItem=focused_item, focusedOrgan=focused_organ, relatedEvents=related_events, filters={"organ": normalized_organ or None, "severity": normalized_severity or None, "pathology_id": normalized_pathology_id or None}, meta={"aggregateSource": "obs-sidecar-pathologies", "coverage": "derived pathology projection from runtime alerts, gateway hotspots, and forge runtime quality", "lifeBodyMeta": _as_dict(payload.get("meta"))})
