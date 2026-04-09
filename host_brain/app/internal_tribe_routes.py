from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from host_brain.tribe.event_bus import InMemoryTribeEventBus, new_tribe_event

router = APIRouter(prefix="/internal", tags=["internal-tribe"])

_event_bus: Optional[InMemoryTribeEventBus] = None


def get_tribe_event_bus() -> InMemoryTribeEventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = InMemoryTribeEventBus(max_events=2048)
    return _event_bus


def set_tribe_event_bus(bus: InMemoryTribeEventBus | None) -> None:
    global _event_bus
    _event_bus = bus


def _validate_internal_token(x_internal_token: str | None) -> None:
    expected = str(os.environ.get("HOST_INTERNAL_TRIBE_TOKEN") or "").strip()
    if not expected:
        return
    if str(x_internal_token or "").strip() != expected:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED_INTERNAL_TRIBE_EVENT")


class TribeEventIn(BaseModel):
    event_type: str = Field(..., min_length=1, max_length=80)
    source_agent: str = Field(..., min_length=1, max_length=120)
    target_agent: Optional[str] = Field(default=None, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = Field(default="tribe.event.v1", max_length=40)


class TribeEventOut(BaseModel):
    accepted: bool
    event_id: str
    ts_ms: int
    event_type: str
    source_agent: str
    target_agent: Optional[str] = None


class TribeRecentResponse(BaseModel):
    total: int
    items: list[dict[str, Any]]
    timestamp: int


@router.post("/tribe_event", response_model=TribeEventOut, status_code=202)
async def ingest_tribe_event(
    body: TribeEventIn,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    _validate_internal_token(x_internal_token)
    bus = get_tribe_event_bus()
    event = new_tribe_event(
        event_type=body.event_type,
        source_agent=body.source_agent,
        target_agent=body.target_agent,
        payload=body.payload,
        schema_version=body.schema_version,
    )
    bus.publish(event)
    return TribeEventOut(
        accepted=True,
        event_id=event.event_id,
        ts_ms=event.ts_ms,
        event_type=event.event_type,
        source_agent=event.source_agent,
        target_agent=event.target_agent,
    )


@router.get("/tribe_event/recent", response_model=TribeRecentResponse)
async def list_tribe_events(
    limit: int = Query(20, ge=1, le=200),
    event_type: str | None = Query(default=None, max_length=80),
    source_agent: str | None = Query(default=None, max_length=120),
    target_agent: str | None = Query(default=None, max_length=120),
    request_id: str | None = Query(default=None, max_length=120),
    session_id: str | None = Query(default=None, max_length=120),
    conversation_id: str | None = Query(default=None, max_length=120),
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    _validate_internal_token(x_internal_token)
    bus = get_tribe_event_bus()
    items = [
        event.to_dict()
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
    timestamp = items[0]["ts_ms"] if items else 0
    return TribeRecentResponse(total=len(items), items=items, timestamp=int(timestamp))


# ── T-39：TribeStateRegistry 全量快照诊断端点 ─────────────────────────────────

class TribeStateAllResponse(BaseModel):
    total: int
    namespaces: list[dict[str, Any]]
    ts_ms: int


@router.get("/tribe_state/all", response_model=TribeStateAllResponse)
async def list_tribe_state_all(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    """T-39：返回 TribeStateRegistry 中所有命名空间的完整快照。

    供开发调试和运维诊断使用，按 updated_at_ms 降序排列。
    """
    _validate_internal_token(x_internal_token)
    try:
        from host_brain.tribe.tribe_state import get_global_registry  # noqa: PLC0415
        from host_brain.core.ids import now_ms  # noqa: PLC0415

        registry = get_global_registry()
        namespaces = registry.get_all_namespaces()
        return TribeStateAllResponse(
            total=len(namespaces),
            namespaces=namespaces,
            ts_ms=now_ms(),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TRIBE_STATE_ALL_FAILED: {exc}") from exc


# ── T-55：TribeStateRegistry 按族快照诊断端点 ─────────────────────────────────

class TribeStateFamilyResponse(BaseModel):
    family: str
    total: int
    namespaces: list[dict[str, Any]]
    ts_ms: int


@router.get("/tribe_state/family/{family}", response_model=TribeStateFamilyResponse)
async def list_tribe_state_by_family(
    family: str,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    """T-55：返回指定族（family）的所有命名空间快照。

    利用 TribeStateRegistry.get_state_by_family() 按族批量查询，
    供开发调试和运维诊断使用，按 updated_at_ms 降序排列。

    示例：
        GET /internal/tribe_state/family/limbic
        GET /internal/tribe_state/family/social_brain
        GET /internal/tribe_state/family/immune
    """
    _validate_internal_token(x_internal_token)
    family_clean = str(family or "").strip().lower()
    if not family_clean:
        raise HTTPException(status_code=400, detail="FAMILY_REQUIRED")
    try:
        from host_brain.tribe.tribe_state import get_global_registry  # noqa: PLC0415
        from host_brain.core.ids import now_ms  # noqa: PLC0415

        registry = get_global_registry()
        namespaces = registry.get_state_by_family(family_clean)
        return TribeStateFamilyResponse(
            family=family_clean,
            total=len(namespaces),
            namespaces=namespaces,
            ts_ms=now_ms(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TRIBE_STATE_FAMILY_FAILED: {exc}") from exc


# ── T-67：TribeEventBus 订阅数量诊断端点 ─────────────────────────────────────

class TribeSubscribersResponse(BaseModel):
    total_event_types: int
    total_handlers: int
    subscribers: dict[str, int]
    ts_ms: int


@router.get("/tribe_event/subscribers", response_model=TribeSubscribersResponse)
async def list_tribe_event_subscribers(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    """T-67：返回 TribeEventBus 各事件类型的当前订阅数量。

    供开发调试和运维诊断使用，可快速确认各 Agent 的订阅是否正确注册。
    """
    _validate_internal_token(x_internal_token)
    try:
        from host_brain.core.ids import now_ms  # noqa: PLC0415

        bus = get_tribe_event_bus()
        subscribers = bus.get_subscriber_count()
        total_handlers = sum(subscribers.values())
        return TribeSubscribersResponse(
            total_event_types=len(subscribers),
            total_handlers=total_handlers,
            subscribers=subscribers,
            ts_ms=now_ms(),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TRIBE_SUBSCRIBERS_FAILED: {exc}") from exc
