"""Internal Codex runtime v2 routes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from typing import Any, Mapping

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from host_brain.agent_runtime import (
    AgentRuntimeExecutor,
    AgentWorkOrder,
    ReviewItem,
    ReviewStore,
    ReviewStoreError,
)
from host_brain.agent_runtime.immune import build_immune_proposal_from_report
from host_brain.agent_runtime.job_registry import GLOBAL_AGENT_JOB_REGISTRY
from host_brain.agent_runtime.runtime_router import (
    CanonicalRuntimeRouter,
    RuntimeRouterError,
    build_legacy_drain_report,
)
from host_brain.app.settings.core import get_core_settings
from host_brain.core.ids import now_ms
from host_brain.core.trace_redaction import sanitize_public_payload
from host_brain.integrations.runtime_events import (
    RuntimeEventError,
    RuntimeEventStore,
)

router = APIRouter(prefix="/internal/codex", tags=["internal-codex"])

_immune_auto_state_lock = threading.Lock()
_immune_auto_state: dict[str, Any] = {
    "source": "health",
    "last_cycle_at_ms": None,
    "last_proposal": None,
    "last_job_id": None,
    "last_result_status": None,
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool_flag(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _get_review_store() -> ReviewStore:
    raw_path = _text(os.environ.get("HOST_AGENT_REVIEW_DB_PATH"))
    db_path = Path(raw_path).expanduser() if raw_path else Path(__file__).resolve().parents[1] / "data" / "audit.sqlite3"
    return ReviewStore(db_path)


def _get_runtime_event_store() -> RuntimeEventStore:
    raw_path = _text(os.environ.get("HOST_RUNTIME_EVENT_DB_PATH"))
    db_path = Path(raw_path).expanduser() if raw_path else Path(__file__).resolve().parents[1] / "data" / "audit.sqlite3"
    return RuntimeEventStore(db_path)


def _require_runtime_event_replay_enabled() -> None:
    if not get_core_settings().runtime_event_replay_enabled:
        raise HTTPException(
            status_code=409,
            detail={
                "reason_code": "RUNTIME_EVENT_REPLAY_DISABLED",
                "detail": "set HOST_RUNTIME_EVENT_REPLAY_ENABLED=true to expose durable runtime replay",
            },
        )


def _raise_review_store_error(exc: ReviewStoreError) -> None:
    status = 503 if exc.reason_code == "REVIEW_STORE_SCHEMA_NOT_READY" else 409
    if exc.reason_code in {"REVIEW_STORE_INVALID", "REVIEW_CHECKPOINT_REQUIRED"}:
        status = 400
    raise HTTPException(status_code=status, detail=exc.to_dict())


def _raise_runtime_event_error(exc: RuntimeEventError) -> None:
    status = 503 if exc.reason_code == "RUNTIME_EVENT_SCHEMA_NOT_READY" else 400
    raise HTTPException(status_code=status, detail=exc.to_dict())


def _runtime_event_next_cursor(events: list[dict[str, Any]], *, after: int) -> int:
    if not events:
        return int(after)
    return int(events[-1]["cursor"])


def _runtime_event_replay_after(*, after: int, last_event_id: str | None) -> int:
    explicit_after = max(0, int(after))
    if explicit_after > 0:
        return explicit_after
    raw_header = _text(last_event_id)
    if not raw_header:
        return 0
    try:
        return max(0, int(raw_header))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="RUNTIME_EVENT_LAST_EVENT_ID_INVALID") from exc


def _format_runtime_sse_event(event_name: str, data: Mapping[str, Any], *, event_id: int | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {int(event_id)}")
    lines.append(f"event: {event_name}")
    lines.append("data: " + json.dumps(dict(data), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    lines.append("")
    return "\n".join(lines) + "\n"


def _runtime_owner_confirmation(order: AgentWorkOrder) -> str:
    metadata = _mapping(order.metadata)
    return (
        _text(metadata.get("task_runtime_owner_confirmation"))
        or _text(metadata.get("runtime_owner_confirmation"))
        or _text(metadata.get("confirmed_owner"))
    )


def _attach_runtime_assignment(order: AgentWorkOrder) -> tuple[AgentWorkOrder, dict[str, Any]]:
    settings = get_core_settings()
    metadata = _mapping(order.metadata)
    router = CanonicalRuntimeRouter(
        mode=settings.task_runtime_mode,
        canary_percent=settings.task_runtime_canary_percent,
        kill_switch=settings.task_runtime_kill_switch,
        canonical_owner=settings.task_runtime_canonical_owner or settings.task_runtime_shadow_owner,
        confirmed_owner=_runtime_owner_confirmation(order),
    )
    existing = metadata.get("runtime_assignment")
    assignment = (
        router.preserve_existing_assignment(existing, work_order_id=order.work_order_id)
        if isinstance(existing, Mapping)
        else router.assign_new_task(order.work_order_id)
    )
    payload = order.to_dict()
    next_metadata = _mapping(payload.get("metadata"))
    next_metadata["runtime_assignment"] = assignment.to_dict()
    payload["metadata"] = next_metadata
    return AgentWorkOrder.from_mapping(payload), assignment.to_dict()


def _extract_owner_directive(
    payload: Mapping[str, Any] | None,
    *,
    order: AgentWorkOrder | None = None,
    explicit_route: bool = False,
) -> dict[str, Any] | None:
    data = dict(payload or {})
    work_order_payload = _mapping(data.get("work_order"))
    metadata = dict(order.metadata) if order is not None else _mapping(work_order_payload.get("metadata"))

    raw = data.get("owner_directive")
    if raw is None and "directive" in data:
        raw = data.get("directive")
    if raw is None and "directive_text" in data:
        raw = {"directive_text": data.get("directive_text")}
    if raw is None and metadata:
        raw = metadata.get("owner_directive")
        if raw is None and "owner_directive_text" in metadata:
            raw = {"directive_text": metadata.get("owner_directive_text")}

    if raw is None and not explicit_route:
        return None

    directive_fields = _mapping(raw)
    directive_text = (
        _text(directive_fields.get("directive_text"))
        or _text(directive_fields.get("text"))
        or _text(directive_fields.get("instruction"))
    )
    if not directive_text and isinstance(raw, str):
        directive_text = _text(raw)
    if not directive_text and explicit_route:
        directive_text = (
            _text(data.get("directive_text"))
            or _text(data.get("text"))
            or _text(data.get("instruction"))
            or _text(data.get("objective"))
        )
    if not directive_text and raw is True and order is not None:
        directive_text = _text(order.objective)

    request_id = (
        _text(directive_fields.get("request_id"))
        or _text(data.get("request_id"))
        or _text(metadata.get("owner_directive_request_id"))
        or _text(metadata.get("request_id"))
        or (f"owner-directive-{order.work_order_id}" if order is not None else f"owner-directive-{now_ms()}")
    )
    return {
        "request_id": request_id,
        "directive_text": directive_text,
        "person_id": _text(directive_fields.get("person_id") or data.get("person_id") or metadata.get("person_id")) or "owner",
        "session_id": _text(directive_fields.get("session_id") or data.get("session_id") or metadata.get("session_id")) or "codex_operator",
        "directive_kind": _text(
            directive_fields.get("directive_kind")
            or data.get("directive_kind")
            or metadata.get("directive_kind")
            or "owner_directive"
        ),
        "applied_result": _text(
            directive_fields.get("applied_result")
            or data.get("applied_result")
            or metadata.get("applied_result")
        ),
        "distill": _bool_flag(
            directive_fields.get("distill")
            if "distill" in directive_fields
            else data.get("distill")
            if "distill" in data
            else metadata.get("distill_owner_directive"),
            default=True,
        ),
    }


def _persist_owner_directive_from_payload(
    payload: Mapping[str, Any] | None,
    *,
    order: AgentWorkOrder | None = None,
    explicit_route: bool = False,
) -> dict[str, Any]:
    directive = _extract_owner_directive(payload, order=order, explicit_route=explicit_route)
    if directive is None:
        return {"attempted": False, "status": "SKIPPED", "reason_code": "OWNER_DIRECTIVE_NOT_DECLARED"}
    if not _text(directive.get("directive_text")):
        return {"attempted": True, "status": "SKIPPED", "reason_code": "OWNER_DIRECTIVE_EMPTY_TEXT"}

    store = None
    try:
        from host_brain.app.dependencies import get_life_memory_store  # noqa: PLC0415

        store = get_life_memory_store()
    except Exception:
        store = None

    from host_brain.app.observability_persist import persist_owner_directive_memory  # noqa: PLC0415

    memory = persist_owner_directive_memory(
        request_id=_text(directive.get("request_id")),
        directive_text=_text(directive.get("directive_text")),
        person_id=_text(directive.get("person_id")) or "owner",
        session_id=_text(directive.get("session_id")) or "codex_operator",
        directive_kind=_text(directive.get("directive_kind")) or "owner_directive",
        applied_result=_text(directive.get("applied_result")) or None,
        life_memory_store=store,
        metadata={
            "entrypoint": "internal_codex",
            "work_order_id": order.work_order_id if order is not None else None,
            "codex_domain": order.domain if order is not None else None,
            "codex_run_mode": order.run_mode if order is not None else None,
        },
    )
    result = {
        "attempted": True,
        "status": memory.get("status"),
        "reason_code": memory.get("reason_code"),
        "episode_id": memory.get("episode_id"),
        "episode_kind": memory.get("episode_kind"),
        "evidence_refs": memory.get("evidence_refs") or [],
        "distillation": {"status": "SKIPPED", "reason_code": "OWNER_DIRECTIVE_DISTILL_DISABLED"},
    }
    if directive.get("distill") and memory.get("status") == "OK" and store is not None:
        try:
            from host_brain.memory.life_memory import LifeMemoryConsolidator  # noqa: PLC0415

            result["distillation"] = LifeMemoryConsolidator(store=store).distill_semantic_memories(
                limit=80,
                max_records=12,
                query_text=_text(directive.get("directive_text")),
            )
        except Exception as exc:
            result["distillation"] = {
                "status": "DEGRADED",
                "reason_code": "OWNER_DIRECTIVE_DISTILL_FAILED",
                "detail": str(exc)[:160],
            }
    return result


def _execute_job(job_id: str, work_order: dict[str, Any]) -> None:
    GLOBAL_AGENT_JOB_REGISTRY.update(job_id, status="RUNNING")
    try:
        result = AgentRuntimeExecutor().execute(AgentWorkOrder.from_mapping(work_order))
        terminal_status = "COMPLETED" if result.get("status") == "EXECUTED" else "FAILED"
        GLOBAL_AGENT_JOB_REGISTRY.update(job_id, status=terminal_status, result=result)
    except Exception as exc:
        GLOBAL_AGENT_JOB_REGISTRY.update(
            job_id,
            status="FAILED",
            result={"status": "FAILED", "reason_code": "CODEX_JOB_CRASHED", "detail": str(exc)},
        )


def _start_background_job(*, kind: str, work_order: dict[str, Any]) -> dict[str, Any]:
    job = GLOBAL_AGENT_JOB_REGISTRY.create(kind=kind, work_order=work_order)
    thread = threading.Thread(
        target=_execute_job,
        args=(str(job["job_id"]), dict(work_order)),
        name=f"codex-runtime-{job['job_id']}",
        daemon=True,
    )
    thread.start()
    return job


@router.post("/work-orders/preview")
async def preview_work_order(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    order = AgentWorkOrder.from_mapping(payload.get("work_order") if isinstance(payload.get("work_order"), dict) else payload)
    order.validate()
    return {"ok": True, "work_order": order.to_dict()}


@router.post("/work-orders/execute")
async def execute_work_order(
    payload: dict[str, Any] = Body(...),
    background: bool = Query(False),
) -> dict[str, Any]:
    order = AgentWorkOrder.from_mapping(payload.get("work_order") if isinstance(payload.get("work_order"), dict) else payload)
    order.validate()
    try:
        order, runtime_assignment = _attach_runtime_assignment(order)
    except RuntimeRouterError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc
    owner_directive_memory = _persist_owner_directive_from_payload(payload, order=order)
    if background:
        job = _start_background_job(kind="codex_work_order", work_order=order.to_dict())
        response = {
            "accepted": True,
            "background": True,
            "job_id": job["job_id"],
            "work_order": order.to_dict(),
            "runtime_assignment": runtime_assignment,
        }
        if owner_directive_memory.get("attempted"):
            response["owner_directive_memory"] = owner_directive_memory
        return response
    result = AgentRuntimeExecutor().execute(order)
    response = {
        "accepted": True,
        "background": False,
        "job_id": None,
        "result": result,
        "runtime_assignment": runtime_assignment,
    }
    if owner_directive_memory.get("attempted"):
        response["owner_directive_memory"] = owner_directive_memory
    return response


@router.post("/owner-directives")
async def ingest_owner_directive(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    owner_directive_memory = _persist_owner_directive_from_payload(payload, explicit_route=True)
    status = str(owner_directive_memory.get("status") or "").upper()
    reason = str(owner_directive_memory.get("reason_code") or "").upper()
    if status == "SKIPPED" and reason == "OWNER_DIRECTIVE_EMPTY_TEXT":
        raise HTTPException(status_code=400, detail="OWNER_DIRECTIVE_EMPTY_TEXT")
    if status in {"FAILED", "SKIPPED"}:
        raise HTTPException(status_code=503, detail=reason or "OWNER_DIRECTIVE_MEMORY_UNAVAILABLE")
    return {"accepted": True, "owner_directive_memory": owner_directive_memory}


@router.post("/reviews")
async def create_review(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    review_payload = _mapping(payload.get("review")) or _mapping(payload)
    try:
        review = ReviewItem.from_mapping(review_payload)
        record = _get_review_store().create_review(
            review,
            work_order_id=_text(payload.get("work_order_id")) or None,
            task_graph_id=_text(payload.get("task_graph_id")) or None,
            task_node_id=_text(payload.get("task_node_id")) or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="REVIEW_PAYLOAD_INVALID") from exc
    except ReviewStoreError as exc:
        _raise_review_store_error(exc)
    return {"accepted": True, "review": record}


@router.post("/reviews/{review_id}/approve")
async def approve_review(review_id: str, payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    body = _mapping(payload)
    try:
        approved = _get_review_store().approve_review(
            review_id,
            actor_id=_text(body.get("actor_id") or body.get("reviewer_id")) or "reviewer",
            expected_subject_ref=_text(body.get("subject_ref")) or None,
            expected_subject_version=(
                int(body["subject_version"]) if body.get("subject_version") is not None else None
            ),
            expected_resume_phase=_text(body.get("resume_phase")) or None,
            expected_checkpoint_id=_text(body.get("checkpoint_id")) or None,
            ttl_ms=int(body["ttl_ms"]) if body.get("ttl_ms") is not None else None,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="REVIEW_APPROVAL_PAYLOAD_INVALID") from exc
    except ReviewStoreError as exc:
        _raise_review_store_error(exc)
    return {"accepted": True, **approved}


@router.post("/reviews/{review_id}/reject")
async def reject_review(review_id: str, payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    body = _mapping(payload)
    try:
        record = _get_review_store().reject_review(
            review_id,
            actor_id=_text(body.get("actor_id") or body.get("reviewer_id")) or "reviewer",
        )
    except ReviewStoreError as exc:
        _raise_review_store_error(exc)
    return {"accepted": True, "review": record}


@router.post("/reviews/{review_id}/resume")
async def resume_review(review_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    body = _mapping(payload)
    try:
        resume = _get_review_store().resume_review(
            review_id,
            resume_token_ref=_text(body.get("resume_token_ref")),
            subject_ref=_text(body.get("subject_ref")),
            subject_version=int(body.get("subject_version")),
            resume_phase=_text(body.get("resume_phase")),
            checkpoint_id=_text(body.get("checkpoint_id")),
            resume_result_ref=_text(body.get("resume_result_ref")) or None,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="REVIEW_RESUME_PAYLOAD_INVALID") from exc
    except ReviewStoreError as exc:
        _raise_review_store_error(exc)
    return {"accepted": True, "resume": resume}


@router.get("/jobs/{job_id}")
async def get_codex_job(job_id: str) -> dict[str, Any]:
    job = GLOBAL_AGENT_JOB_REGISTRY.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="CODEX_JOB_NOT_FOUND")
    # Jobs created before the redacted trace schema may still carry legacy
    # ``thought`` fields in memory.  Read them compatibly but never re-publish
    # those fields through the API adapter.
    return dict(sanitize_public_payload(job))


@router.get("/runtime/drain-report")
async def get_runtime_drain_report() -> dict[str, Any]:
    return {
        "accepted": True,
        "drain": build_legacy_drain_report(GLOBAL_AGENT_JOB_REGISTRY.list_jobs()),
    }


@router.get("/runtime/events")
async def list_runtime_events(
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    event_type: str | None = Query(None),
    source_kind: str | None = Query(None),
) -> dict[str, Any]:
    _require_runtime_event_replay_enabled()
    try:
        events = _get_runtime_event_store().list_events(
            after_cursor=after,
            limit=limit,
            event_type=event_type,
            source_kind=source_kind,
        )
    except RuntimeEventError as exc:
        _raise_runtime_event_error(exc)
    return {
        "accepted": True,
        "schema_version": "Natsume.runtime.event.replay.v1",
        "events": events,
        "event_count": len(events),
        "after": int(after),
        "next_cursor": _runtime_event_next_cursor(events, after=after),
        "client_dedupe": {
            "field": "dedupe_key",
            "rule": "ignore events whose dedupe_key has already been applied",
        },
    }


@router.get("/runtime/events/sse")
async def stream_runtime_events_sse(
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    event_type: str | None = Query(None),
    source_kind: str | None = Query(None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    _require_runtime_event_replay_enabled()
    replay_after = _runtime_event_replay_after(after=after, last_event_id=last_event_id)
    try:
        events = _get_runtime_event_store().list_events(
            after_cursor=replay_after,
            limit=limit,
            event_type=event_type,
            source_kind=source_kind,
        )
    except RuntimeEventError as exc:
        _raise_runtime_event_error(exc)
    next_cursor = _runtime_event_next_cursor(events, after=replay_after)

    async def _event_stream():
        for event in events:
            yield _format_runtime_sse_event("runtime_event", event, event_id=int(event["cursor"]))
        yield _format_runtime_sse_event(
            "runtime_event_cursor",
            {
                "schema_version": "Natsume.runtime.event.cursor.v1",
                "after": replay_after,
                "next_cursor": next_cursor,
                "event_count": len(events),
                "client_dedupe_field": "dedupe_key",
            },
        )

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _live_report(source: str) -> dict[str, Any]:
    source_key = "readiness" if str(source or "").strip().lower() in {"ready", "readiness"} else "health"
    if source_key == "readiness":
        from host_brain.app.health_routes import build_live_readiness_report  # noqa: PLC0415

        return await build_live_readiness_report()
    from host_brain.app.health_routes import build_live_health_report  # noqa: PLC0415

    return await build_live_health_report()


@router.post("/immune/proposal")
async def build_immune_proposal(
    payload: dict[str, Any] | None = Body(default=None),
    source: str = Query("health"),
) -> dict[str, Any]:
    report = dict((payload or {}).get("report") or {})
    if not report:
        report = await _live_report(source)
    return build_immune_proposal_from_report(report, source=source)


@router.post("/immune/dispatch")
async def dispatch_immune_work_order(
    payload: dict[str, Any] | None = Body(default=None),
    source: str = Query("health"),
    background: bool = Query(True),
    confirmed: bool = Query(True),
) -> dict[str, Any]:
    proposal = await build_immune_proposal(payload or {}, source=source)
    work_order = proposal.get("work_order")
    if not proposal.get("triggered") or not isinstance(work_order, dict):
        return {
            "accepted": False,
            "executed": False,
            "reason_code": "IMMUNE_WORKORDER_NOT_REQUIRED",
            "proposal": proposal,
            "dispatch": None,
        }
    work_order = dict(work_order)
    approval = dict(work_order.get("approval_policy") or {})
    approval["confirmed"] = bool(confirmed)
    work_order["approval_policy"] = approval
    try:
        assigned_order, runtime_assignment = _attach_runtime_assignment(AgentWorkOrder.from_mapping(work_order))
    except RuntimeRouterError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc
    work_order = assigned_order.to_dict()
    if background:
        job = _start_background_job(kind="codex_immune", work_order=work_order)
        with _immune_auto_state_lock:
            _immune_auto_state.update(
                {
                    "source": source,
                    "last_cycle_at_ms": now_ms(),
                    "last_proposal": proposal,
                    "last_job_id": job["job_id"],
                    "last_result_status": "QUEUED",
                }
            )
        return {
            "accepted": True,
            "background": True,
            "job_id": job["job_id"],
            "proposal": proposal,
            "runtime_assignment": runtime_assignment,
        }
    result = AgentRuntimeExecutor().execute(assigned_order)
    with _immune_auto_state_lock:
        _immune_auto_state.update(
            {
                "source": source,
                "last_cycle_at_ms": now_ms(),
                "last_proposal": proposal,
                "last_job_id": None,
                "last_result_status": result.get("status"),
            }
        )
    return {
        "accepted": True,
        "background": False,
        "job_id": None,
        "proposal": proposal,
        "dispatch": result,
        "runtime_assignment": runtime_assignment,
    }


@router.get("/immune/auto-state")
async def get_immune_auto_state() -> dict[str, Any]:
    with _immune_auto_state_lock:
        return dict(_immune_auto_state)


async def run_scheduled_immune_auto_dispatch_cycle() -> dict[str, Any]:
    result = await dispatch_immune_work_order({}, source="health", background=True, confirmed=True)
    with _immune_auto_state_lock:
        _immune_auto_state["last_cycle_at_ms"] = now_ms()
        _immune_auto_state["last_result_status"] = "DISPATCHED" if result.get("accepted") else "SKIPPED"
    return result


def start_immune_auto_dispatch_monitor(*, logger: Any | None = None) -> dict[str, Any]:
    state = {"enabled": False, "running": False, "scheduler_managed": True}
    if logger is not None:
        logger.info("Codex immune monitor is scheduler-managed in runtime v2.")
    return state


def stop_immune_auto_dispatch_monitor(*, logger: Any | None = None) -> dict[str, Any]:
    state = {"enabled": False, "running": False}
    if logger is not None:
        logger.info("Codex immune monitor stop requested; no standalone monitor is active.")
    return state


__all__ = [
    "router",
    "run_scheduled_immune_auto_dispatch_cycle",
    "start_immune_auto_dispatch_monitor",
    "stop_immune_auto_dispatch_monitor",
]
