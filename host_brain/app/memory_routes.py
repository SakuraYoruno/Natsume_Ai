from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request

from host_brain.app.context.memory_context_orchestrator import (
    run_memory_sanitize,
)
from host_brain.app.dependencies import get_ledger
from host_brain.app.memory_event_store import rebuild_memory_system
from host_brain.conversation.memory_spine import backfill_conversation_turn_memory_spine
from host_brain.observability.operator_query_gateway import OperatorQueryGateway
from host_brain.observability.remaining_query_projection import (
    MEMORY_CONTEXT_KIND,
    MEMORY_EVENTS_KIND,
    MEMORY_HEALTH_KIND,
    read as read_query_projection,
    response_meta,
)


router = APIRouter(prefix="/v1/memory", tags=["memory"])


async def _require_memory_debug_scope(request: Request) -> None:
    """Protect structural memory diagnostics with the operator authority."""

    gateway = getattr(request.app.state, "operator_query_gateway", None)
    if not isinstance(gateway, OperatorQueryGateway):
        gateway = OperatorQueryGateway.from_environment()
    context = gateway.open_asgi_context(request.scope)
    context.require_scope("obs:memory:read")


@router.get("/context/debug")
async def memory_context_debug(
    session_id: str = Query(""),
    person_id: str = Query("default"),
    user_message: str = Query(""),
    current_user_message: str = Query(""),
    limit: int = Query(8, ge=1, le=30),
    _authority: None = Depends(_require_memory_debug_scope),
) -> dict[str, Any]:
    payload, meta = read_query_projection(MEMORY_CONTEXT_KIND, str(person_id or "default"))
    return {
        "person_id": str(person_id or "default"),
        "session_id": str(session_id or ""),
        "layer_order": list((payload or {}).get("layer_order") or []),
        "reason_codes": list((payload or {}).get("reason_codes") or []),
        "binding": dict((payload or {}).get("binding") or {}),
        "counts": dict((payload or {}).get("counts") or {}),
        "projection_status": dict((payload or {}).get("projection_status") or {}),
        "content_withheld": True,
        "query_content_accepted": bool(user_message or current_user_message),
        "limit": int(limit),
        **response_meta(meta),
    }


@router.get("/health")
async def memory_health_route(person_id: str = Query("default")) -> dict[str, Any]:
    payload, meta = read_query_projection(MEMORY_HEALTH_KIND, str(person_id or "default"))
    return {
        **dict(payload or {}),
        "person_id": str(person_id or "default"),
        **response_meta(meta),
    }


@router.get("/events/recent")
async def memory_events_recent(
    person_id: str = Query("default"),
    session_id: str = Query(""),
    visibility: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    payload, meta = read_query_projection(MEMORY_EVENTS_KIND, str(person_id or "default"))
    items = []
    for raw in list((payload or {}).get("items") or []):
        if not isinstance(raw, dict):
            continue
        if session_id and str(raw.get("session_id") or "") != str(session_id):
            continue
        if visibility and str(raw.get("visibility") or "") != str(visibility):
            continue
        items.append(dict(raw))
        if len(items) >= int(limit):
            break
    return {
        "person_id": str(person_id or "default"),
        "items": items,
        "content_withheld": True,
        **response_meta(meta),
    }


@router.post("/rebuild/run")
async def memory_rebuild_run(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return rebuild_memory_system(
        ledger=get_ledger(),
        person_id=str(payload.get("person_id") or "default"),
    )


@router.post("/sanitize/run")
async def memory_sanitize_run(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return run_memory_sanitize(
        ledger=get_ledger(),
        person_id=str(payload.get("person_id") or "default"),
        dry_run=bool(payload.get("dry_run", False)),
    )


@router.post("/spine/backfill")
async def memory_spine_backfill(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return backfill_conversation_turn_memory_spine(
        person_id=str(payload.get("person_id") or "default"),
        session_id=str(payload.get("session_id") or ""),
        limit=int(payload.get("limit") or 500),
        dry_run=bool(payload.get("dry_run", True)),
        include_asr_sessions=bool(payload.get("include_asr_sessions", False)),
    )


__all__ = ["router"]
