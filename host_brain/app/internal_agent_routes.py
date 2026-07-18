"""Internal Codex runtime v2 routes."""

from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any, Mapping

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from host_brain.contracts.agent_runtime.review_policy import ReviewItem
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
from host_brain.persistence.review_repository import ReviewStoreError
from host_brain.task_runtime.review_command_service import TaskReviewCommandService
from host_brain.agent_runtime.immune import build_immune_proposal_from_report
from host_brain.app.internal_task_runtime import (
    GovernedInternalRuntimeError,
    InternalAgentCommandService,
)
from host_brain.app.settings.core import get_core_settings
from host_brain.app.settings.internal_agent import get_internal_agent_settings
from host_brain.core.ids import now_ms
from host_brain.integrations.runtime_events import (
    RuntimeEventError,
    RuntimeEventStore,
)
from host_brain.observability.degradation_state import (
    DegradationStateError,
    require_degradation_capability,
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


def _review_command_service(request: Request) -> TaskReviewCommandService:
    service = getattr(request.state, "task_review_command_service", None)
    if service is None:
        service = getattr(request.app.state, "task_review_command_service", None)
    if not isinstance(service, TaskReviewCommandService):
        raise HTTPException(
            status_code=503,
            detail="TASK_REVIEW_COMMAND_SERVICE_UNAVAILABLE",
        )
    return service


def _get_runtime_event_store() -> RuntimeEventStore:
    return RuntimeEventStore(
        Path(get_internal_agent_settings().runtime_event_db_path).expanduser()
    )


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


def _require_degradation_http(
    *,
    capability_id: str,
    static_risk_level: int,
) -> dict[str, Any]:
    try:
        return require_degradation_capability(
            capability_id=capability_id,
            static_risk_level=static_risk_level,
        )
    except DegradationStateError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "reason_code": exc.reason_code,
                "detail": exc.detail,
                "capability_id": capability_id,
            },
        ) from exc


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


def _internal_command_service(request: Request) -> InternalAgentCommandService:
    service = getattr(request.state, "internal_agent_command_service", None)
    if not isinstance(service, InternalAgentCommandService):
        raise HTTPException(status_code=503, detail="INTERNAL_AGENT_RUNTIME_UNAVAILABLE")
    return service


def _internal_command_authority(request: Request) -> Any:
    authority = getattr(request.state, "internal_agent_authority", None)
    if authority is None or not bool(getattr(authority, "admissible", False)):
        raise HTTPException(status_code=403, detail="INTERNAL_AGENT_AUTHORITY_REQUIRED")
    if not all(
        str(getattr(authority, field_name, "") or "").strip()
        for field_name in (
            "request_id",
            "principal_id",
            "claim_ref",
            "audit_ref",
            "body_sha256",
        )
    ):
        raise HTTPException(status_code=403, detail="INTERNAL_AGENT_AUTHORITY_INCOMPLETE")
    return authority


def _review_actor(
    request: Request,
    payload: Mapping[str, Any] | None = None,
) -> str:
    """Bind Review audit identity to the authenticated operator principal."""

    authority = _internal_command_authority(request)
    principal_id = str(getattr(authority, "principal_id", "") or "").strip()
    body = dict(payload or {})
    asserted = _text(body.get("actor_id") or body.get("reviewer_id"))
    if asserted and asserted != principal_id:
        raise HTTPException(status_code=403, detail="REVIEW_ACTOR_IDENTITY_MISMATCH")
    return principal_id


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
        from host_brain.composition.process_services import get_life_memory_store  # noqa: PLC0415

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
            from host_brain.memory.life_memory.consolidator import LifeMemoryConsolidator  # noqa: PLC0415

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


@router.post("/work-orders/preview")
async def preview_work_order(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    order = AgentWorkOrder.from_mapping(payload.get("work_order") if isinstance(payload.get("work_order"), dict) else payload)
    order.validate()
    return {"ok": True, "work_order": order.to_dict()}


@router.post("/work-orders/execute")
async def execute_work_order(
    request: Request,
    payload: dict[str, Any] = Body(...),
    background: bool = Query(False),
) -> dict[str, Any]:
    order = AgentWorkOrder.from_mapping(payload.get("work_order") if isinstance(payload.get("work_order"), dict) else payload)
    order.validate()
    try:
        service = _internal_command_service(request)
        authority = _internal_command_authority(request)
        terminal = await service.execute(
            work_order=order,
            request_id=str(authority.request_id or ""),
            session_id=_text(payload.get("session_id")) or "internal-codex",
            ts_ms=int(getattr(request.state, "request_started_ms", 0) or now_ms()),
            command_authority={
                "principal_id": authority.principal_id,
                "claim_ref": authority.claim_ref,
                "audit_ref": authority.audit_ref,
                "body_sha256": authority.body_sha256,
            },
        )
    except GovernedInternalRuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail={"reason_code": exc.reason_code, "detail": exc.detail},
        ) from exc
    receipt = dict(terminal.get("execution_receipt") or {})
    result = dict(terminal.get("result") or {})
    return {
        "accepted": str(terminal.get("decision") or "").upper() == "APPROVE",
        "background": False,
        "requested_background": bool(background),
        "job_id": None,
        "result": result,
        "execution_receipt": receipt,
        "replayed": bool(terminal.get("replayed")),
    }


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
async def create_review(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    review_payload = _mapping(payload.get("review")) or _mapping(payload)
    try:
        review = ReviewItem.from_mapping(review_payload)
        principal_id = _review_actor(request)
        if review.requester != principal_id:
            raise HTTPException(
                status_code=403,
                detail="REVIEW_REQUESTER_IDENTITY_MISMATCH",
            )
        if (
            review.subject_ref.startswith("task-node://")
            or _text(payload.get("task_graph_id"))
            or _text(payload.get("task_node_id"))
        ):
            raise HTTPException(
                status_code=403,
                detail="TASK_BOUND_REVIEW_CREATE_INTERNAL_ONLY",
            )
        record = _review_command_service(request).create_review(
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
async def approve_review(
    review_id: str,
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    body = _mapping(payload)
    actor_id = _review_actor(request, body)
    try:
        approved = _review_command_service(request).approve_review(
            review_id,
            actor_id=actor_id,
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
async def reject_review(
    review_id: str,
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    body = _mapping(payload)
    actor_id = _review_actor(request, body)
    try:
        record = _review_command_service(request).reject_review(
            review_id,
            actor_id=actor_id,
        )
    except ReviewStoreError as exc:
        _raise_review_store_error(exc)
    return {"accepted": True, "review": record}


@router.post("/reviews/{review_id}/resume")
async def resume_review(
    review_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    _require_degradation_http(
        capability_id="remote_review_resume",
        static_risk_level=4,
    )
    body = _mapping(payload)
    _review_actor(request, body)
    try:
        resume = _review_command_service(request).resume_review(
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
    del job_id
    raise HTTPException(status_code=410, detail="LEGACY_AGENT_JOB_REGISTRY_RETIRED")


@router.get("/runtime/drain-report")
async def get_runtime_drain_report() -> dict[str, Any]:
    return {
        "accepted": True,
        "drain": {
            "status": "DRAINED",
            "legacy_registry_retired": True,
            "active_legacy_jobs": 0,
            "authority": "durable_task_runtime",
        },
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
    _require_degradation_http(
        capability_id="runtime_event_stream",
        static_risk_level=3,
    )
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
    request: Request,
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
        service = _internal_command_service(request)
        authority = _internal_command_authority(request)
        terminal = await service.execute(
            work_order=AgentWorkOrder.from_mapping(work_order),
            request_id=str(authority.request_id or ""),
            session_id="internal-immune",
            ts_ms=int(getattr(request.state, "request_started_ms", 0) or now_ms()),
            command_authority={
                "principal_id": authority.principal_id,
                "claim_ref": authority.claim_ref,
                "audit_ref": authority.audit_ref,
                "body_sha256": authority.body_sha256,
            },
        )
    except GovernedInternalRuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail={"reason_code": exc.reason_code, "detail": exc.detail},
        ) from exc
    result = dict(terminal.get("result") or {})
    receipt = dict(terminal.get("execution_receipt") or {})
    with _immune_auto_state_lock:
        _immune_auto_state.update(
            {
                "source": source,
                "last_cycle_at_ms": now_ms(),
                "last_proposal": proposal,
                "last_job_id": receipt.get("command_id"),
                "last_result_status": result.get("status"),
            }
        )
    return {
        "accepted": str(terminal.get("decision") or "").upper() == "APPROVE",
        "background": False,
        "requested_background": bool(background),
        "job_id": None,
        "proposal": proposal,
        "dispatch": result,
        "execution_receipt": receipt,
        "replayed": bool(terminal.get("replayed")),
    }


@router.get("/immune/auto-state")
async def get_immune_auto_state() -> dict[str, Any]:
    with _immune_auto_state_lock:
        return dict(_immune_auto_state)


__all__ = [
    "router",
]
