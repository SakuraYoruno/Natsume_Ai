from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query

from host_brain.app.context.memory_context_orchestrator import (
    build_memory_context_debug,
    memory_health,
    run_memory_sanitize,
)
from host_brain.app.dependencies import get_ledger
from host_brain.app.memory_event_store import get_recent_memory_events, rebuild_memory_system
from host_brain.conversation.memory_spine import backfill_conversation_turn_memory_spine


router = APIRouter(prefix="/v1/memory", tags=["memory"])


@router.get("/context/debug")
async def memory_context_debug(
    session_id: str = Query(""),
    person_id: str = Query("default"),
    user_message: str = Query(""),
    current_user_message: str = Query(""),
    limit: int = Query(8, ge=1, le=30),
) -> dict[str, Any]:
    return build_memory_context_debug(
        ledger=get_ledger(),
        session_id=session_id,
        person_id=person_id,
        user_message=user_message or current_user_message,
        limit=limit,
    )


@router.get("/health")
async def memory_health_route(person_id: str = Query("default")) -> dict[str, Any]:
    return memory_health(ledger=get_ledger(), person_id=person_id)


@router.get("/events/recent")
async def memory_events_recent(
    person_id: str = Query("default"),
    session_id: str = Query(""),
    visibility: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    return get_recent_memory_events(
        ledger=get_ledger(),
        person_id=person_id,
        session_id=session_id,
        visibility=visibility,
        limit=limit,
    )


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
