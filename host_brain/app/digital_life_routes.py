from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query

from host_brain.app.dependencies import get_ledger
from host_brain.runtime.life_continuity import (
    advance_inner_life_to_now,
    attempt_life_proactive_push,
    get_current_environment,
    get_current_life_action,
    get_current_embodiment,
    get_life_state,
    get_life_runtime_health,
    get_recent_causality,
    get_recent_proactive_outbox,
    record_life_experience,
    record_work_environment_event,
    run_life_tick,
)
from host_brain.runtime.life_organs import (
    get_organ_runtime_state,
    get_recent_life_experience,
    get_recent_proactive_episodes,
)
from host_brain.runtime.autonomous_self import get_growth_state, get_learning_recent

router = APIRouter(prefix="/v1/digital-life", tags=["digital-life"])


@router.get("/state")
async def digital_life_state(person_id: str = Query("default")) -> dict[str, Any]:
    return get_life_state(get_ledger(), person_id=person_id)


@router.get("/runtime/health")
async def digital_life_runtime_health(person_id: str = Query("default")) -> dict[str, Any]:
    return get_life_runtime_health(ledger=get_ledger(), person_id=person_id)


@router.get("/environment/current")
async def digital_life_environment_current(person_id: str = Query("default")) -> dict[str, Any]:
    return get_current_environment(ledger=get_ledger(), person_id=person_id)


@router.get("/causality/recent")
async def digital_life_causality_recent(person_id: str = Query("default"), limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    return get_recent_causality(ledger=get_ledger(), person_id=person_id, limit=limit)


@router.get("/growth/state")
async def digital_life_growth_state(person_id: str = Query("default")) -> dict[str, Any]:
    return get_growth_state(ledger=get_ledger(), person_id=person_id)


@router.get("/learning/recent")
async def digital_life_learning_recent(person_id: str = Query("default"), limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    return get_learning_recent(ledger=get_ledger(), person_id=person_id, limit=limit)


@router.post("/work-event")
async def digital_life_work_event(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return await record_work_environment_event(
        ledger=get_ledger(),
        person_id=str(payload.get("person_id") or "default"),
        session_id=str(payload.get("session_id") or ""),
        request_id=str(payload.get("request_id") or ""),
        event_type=str(payload.get("event_type") or "task_progress"),
        work_id=str(payload.get("work_id") or ""),
        summary=str(payload.get("summary") or ""),
        urgency=str(payload.get("urgency") or "medium"),
        emotional_effect=payload.get("emotional_effect") if isinstance(payload.get("emotional_effect"), dict) else None,
        platform=str(payload.get("platform") or payload.get("reply_channel") or ""),
        conversation_id=str(payload.get("conversation_id") or payload.get("reply_target") or ""),
        proactive_content=str(payload.get("proactive_content") or payload.get("content") or ""),
    )


@router.post("/tick")
async def digital_life_tick(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return await run_life_tick(
        ledger=get_ledger(),
        person_id=str(payload.get("person_id") or "default"),
        reason=str(payload.get("reason") or "manual_tick"),
        force_dispatch=bool(payload.get("force_dispatch", True)),
    )


@router.post("/advance")
async def digital_life_advance(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    incoming = payload.get("incoming_event")
    return advance_inner_life_to_now(
        ledger=get_ledger(),
        person_id=str(payload.get("person_id") or "default"),
        reason=str(payload.get("reason") or "manual_advance"),
        incoming_event=incoming if isinstance(incoming, dict) else {},
        force=bool(payload.get("force", True)),
    )


@router.get("/inner-drive/current")
async def digital_life_inner_drive_current(person_id: str = Query("default"), force: bool = Query(False)) -> dict[str, Any]:
    return advance_inner_life_to_now(
        ledger=get_ledger(),
        person_id=person_id or "default",
        reason="debug_inner_drive_current",
        incoming_event={"source_kind": "debug_inner_drive_current"},
        force=force,
    )


@router.get("/embodiment/current")
async def digital_life_embodiment_current(person_id: str = Query("default")) -> dict[str, Any]:
    return get_current_embodiment(ledger=get_ledger(), person_id=person_id)


@router.get("/action/current")
async def digital_life_action_current(person_id: str = Query("default")) -> dict[str, Any]:
    return get_current_life_action(ledger=get_ledger(), person_id=person_id)


@router.post("/proactive/attempt")
async def digital_life_proactive_attempt(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return await attempt_life_proactive_push(
        ledger=get_ledger(),
        person_id=str(payload.get("person_id") or "default"),
        source_kind=str(payload.get("source_kind") or "manual_proactive_attempt"),
        platform=str(payload.get("platform") or payload.get("reply_channel") or ""),
        conversation_id=str(payload.get("conversation_id") or payload.get("reply_target") or ""),
        content=str(payload.get("content") or payload.get("proactive_content") or ""),
        force=bool(payload.get("force", True)),
        dry_run=bool(payload.get("dry_run", False)),
    )


@router.get("/proactive/recent")
async def digital_life_proactive_recent(person_id: str = Query("default"), limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    return get_recent_proactive_outbox(ledger=get_ledger(), person_id=person_id, limit=limit)


@router.get("/organs/state")
async def digital_life_organs_state(person_id: str = Query("default")) -> dict[str, Any]:
    return get_organ_runtime_state(ledger=get_ledger(), person_id=person_id)


@router.get("/experience/recent")
async def digital_life_experience_recent(person_id: str = Query("default"), limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return get_recent_life_experience(ledger=get_ledger(), person_id=person_id, limit=limit)


@router.post("/experience/ingest")
async def digital_life_experience_ingest(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    evidence = payload.get("evidence")
    return record_life_experience(
        ledger=get_ledger(),
        person_id=str(payload.get("person_id") or "default"),
        session_id=str(payload.get("session_id") or ""),
        source_kind=str(payload.get("source_kind") or "manual_ingest"),
        event_kind=str(payload.get("event_kind") or "inner_stream_tick"),
        content_summary=str(payload.get("content_summary") or payload.get("content") or ""),
        evidence=evidence if isinstance(evidence, dict) else {},
        salience=float(payload.get("salience") or 0.3),
        valence=float(payload.get("valence") or 0.0),
        uncertainty=float(payload.get("uncertainty") or 0.1),
        consume=bool(payload.get("consume", True)),
    )


@router.get("/proactive/episodes/recent")
async def digital_life_proactive_episodes_recent(person_id: str = Query("default"), limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    return get_recent_proactive_episodes(ledger=get_ledger(), person_id=person_id, limit=limit)


__all__ = ["router"]
