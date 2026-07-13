from __future__ import annotations

import asyncio
from collections.abc import Mapping
import time
from typing import Any

from fastapi import APIRouter, Body, Query

from host_brain.app.dependencies import get_ledger
from host_brain.app.life_application_boundary import (
    project_inner_life_snapshot,
    read_life_events,
    read_life_snapshot,
    submit_life_command,
)
from host_brain.digital_life.contracts import LifeDeliveryAuthorization
from host_brain.orchestration.governor_grant import (
    DurableGovernorGrantResolver,
    canonical_proactive_intent_args,
    durable_governor_binding,
)
from host_brain.observability.remaining_query_projection import (
    WORLD_SNAPSHOT_KIND,
    read as read_query_projection,
    response_meta,
)


router = APIRouter(prefix="/v1/digital-life", tags=["digital-life"])

_UNTRUSTED_INTENT_FIELDS = frozenset(
    {
        "state_patch",
        "projection_intents",
        "organ_intents",
        "deliveries",
        "created_at_ms",
    }
)


def _life_projection_enabled() -> bool:
    from host_brain.persona.scene_policy import life_projection_enabled

    return life_projection_enabled()


def _state(person_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = read_life_snapshot(str(person_id or "default"))
    return snapshot, dict(snapshot.get("state") or {})


def _recent_events(person_id: str, *, limit: int) -> list[dict[str, Any]]:
    # Reader order is canonical ASC.  Fetching up to the bounded journal maximum
    # and slicing here preserves the historical "most recent first" HTTP contract.
    rows = read_life_events(person_id, after_sequence=0, limit=1000)
    return list(reversed(rows[-max(1, int(limit or 1)) :]))


def _command_payload(command_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = {
        str(key): value
        for key, value in dict(payload or {}).items()
        if str(key) not in _UNTRUSTED_INTENT_FIELDS
    }
    projection_intents: list[dict[str, Any]] = []
    organ_intents: list[dict[str, Any]] = []
    deliveries: list[dict[str, Any]] = []
    if command_type == "work_event":
        projection_intents.append(
            {
                "target": "work.environment",
                "payload": {
                    key: normalized.get(key)
                    for key in (
                        "event_type",
                        "work_id",
                        "summary",
                        "urgency",
                        "request_id",
                        "session_id",
                    )
                },
            }
        )
        organ_intents.append(
            {
                "target": "work_event",
                "payload": {
                    "event_type": str(normalized.get("event_type") or "task_progress"),
                    "emotional_effect": dict(normalized.get("emotional_effect") or {}),
                    "urgency": str(normalized.get("urgency") or "medium"),
                },
            }
        )
    elif command_type == "experience_ingest":
        projection_intents.append(
            {
                "target": "life.experience",
                "payload": {
                    key: normalized.get(key)
                    for key in (
                        "session_id",
                        "source_kind",
                        "event_kind",
                        "content_summary",
                        "evidence",
                        "salience",
                        "valence",
                        "uncertainty",
                        "consume",
                    )
                },
            }
        )
        if bool(normalized.get("consume", True)):
            organ_intents.append(
                {
                    "target": "experience",
                    "payload": {
                        "event_kind": str(normalized.get("event_kind") or "inner_stream_tick"),
                        "evidence": dict(normalized.get("evidence") or {}),
                    },
                }
            )
    elif command_type == "proactive_attempt":
        person_id = str(normalized.get("person_id") or "").strip()
        platform = str(normalized.get("platform") or normalized.get("reply_channel") or "").strip().lower()
        conversation_id = str(
            normalized.get("conversation_id") or normalized.get("reply_target") or ""
        ).strip()
        content = str(normalized.get("content") or normalized.get("proactive_content") or "").strip()
        source_kind = str(normalized.get("source_kind") or "manual_proactive_attempt").strip()
        delivery_payload = {
            "platform": platform,
            "conversation_id": conversation_id,
            "content": content,
            "source_kind": source_kind,
        }
        authorization = _validated_http_delivery_authorization(
            normalized.get("delivery_authorization")
            or normalized.get("authorization"),
            expected_request_id=str(normalized.get("request_id") or ""),
            expected_person_id=person_id,
            expected_action_kind="proactive_attempt",
            expected_target="proactive.gateway",
            expected_intent_args=canonical_proactive_intent_args(
                person_id=person_id,
                platform=platform,
                conversation_id=conversation_id,
                content=content,
                source_kind=source_kind,
            ),
        )
        if (
            platform
            and conversation_id
            and content
            and not bool(normalized.get("dry_run"))
            and authorization is not None
        ):
            deliveries.append(
                {
                    "destination": "proactive.gateway",
                    "authorization": authorization.as_dict(),
                    "payload": delivery_payload,
                }
            )
    normalized["projection_intents"] = projection_intents
    normalized["organ_intents"] = organ_intents
    normalized["deliveries"] = deliveries
    return normalized


def _validated_http_delivery_authorization(
    value: Any,
    *,
    expected_request_id: str,
    expected_person_id: str,
    expected_action_kind: str,
    expected_target: str,
    expected_intent_args: Mapping[str, Any],
) -> LifeDeliveryAuthorization | None:
    if not isinstance(value, Mapping):
        return None
    request_ref = str(value.get("request_id") or "").strip()
    grant_ref = str(value.get("governor_grant_ref") or "").strip()
    request_id = str(expected_request_id or "").strip()
    if not request_id or request_ref != request_id or not grant_ref:
        return None
    try:
        decision = DurableGovernorGrantResolver(get_ledger()).require_bound(
            request_id=request_ref,
            expected_ref=grant_ref,
            expected_person_id=expected_person_id,
            expected_action_kind=expected_action_kind,
            expected_target=expected_target,
            expected_intent_args=expected_intent_args,
        )
    except Exception:
        return None
    principal = str(durable_governor_binding(decision).get("principal_id") or "").strip()
    if not principal:
        return None
    return LifeDeliveryAuthorization(
        request_id=request_ref,
        governor_grant_ref=grant_ref,
        authority_kind="governed_life_http",
        authority_principal_id=principal,
    )


async def _submit_route_command(
    command_type: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    body = dict(payload or {})
    person_id = str(body.get("person_id") or "default")
    body["person_id"] = person_id
    raw_command_id = str(body.get("command_id") or body.get("request_id") or "").strip()
    command_id = f"life::{command_type}::{raw_command_id}" if raw_command_id else ""
    expected_version_raw = body.get("expected_version")
    expected_version = (
        max(0, int(expected_version_raw))
        if expected_version_raw not in (None, "")
        else None
    )
    command_payload = await asyncio.to_thread(
        _command_payload,
        command_type,
        body,
    )
    receipt = await asyncio.to_thread(
        submit_life_command,
        command_type=command_type,
        person_id=person_id,
        payload=command_payload,
        command_id=command_id,
        correlation_id=str(body.get("request_id") or "").strip() or None,
        causation_id=str(body.get("causation_id") or "").strip() or None,
        expected_version=expected_version,
        created_at_ms=int(time.time() * 1000),
        metadata={
            "source": "digital_life_http",
            "route_command_type": command_type,
        },
    )
    return {
        "status": str(receipt.get("status") or "UNKNOWN"),
        "reason_code": receipt.get("reason_code"),
        "command_id": receipt.get("command_id"),
        "event_id": receipt.get("event_id"),
        "person_id": receipt.get("person_id") or person_id,
        "state_version": receipt.get("state_version"),
        "state_version_after": receipt.get("state_version"),
        "state_digest": receipt.get("state_digest"),
        "outbox_count": int(receipt.get("outbox_count") or 0),
        "replayed": bool(receipt.get("replayed")),
        "receipt": receipt,
        "compatibility": {
            "mode": "governed_life_command",
            "legacy_direct_runtime": False,
            "command_type": command_type,
        },
    }


@router.get("/state")
async def digital_life_state(person_id: str = Query("default")) -> dict[str, Any]:
    _snapshot, state = _state(person_id)
    return state


@router.get("/runtime/health")
async def digital_life_runtime_health(person_id: str = Query("default")) -> dict[str, Any]:
    snapshot, state = _state(person_id)
    runtime = dict(state.get("runtime") or {})
    return {
        "person_id": snapshot["person_id"],
        "status": "OK" if snapshot["version"] > 0 else "EMPTY",
        "reason_code": None if snapshot["version"] > 0 else "LIFE_SNAPSHOT_NOT_FOUND",
        "state_version": snapshot["version"],
        "state_digest": snapshot["state_digest"],
        "last_event_id": snapshot["last_event_id"],
        "updated_at_ms": snapshot["updated_at_ms"],
        "runtime": runtime,
        "source": "life_snapshot_reader",
    }


@router.get("/environment/current")
async def digital_life_environment_current(person_id: str = Query("default")) -> dict[str, Any]:
    snapshot, state = _state(person_id)
    return {
        "person_id": snapshot["person_id"],
        "environment": dict(state.get("environment") or {}),
        "state_version": snapshot["version"],
        "source": "life_snapshot_reader",
    }


@router.get("/causality/recent")
async def digital_life_causality_recent(
    person_id: str = Query("default"),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    snapshot, _state_payload = _state(person_id)
    events = _recent_events(person_id, limit=limit)
    return {
        "person_id": snapshot["person_id"],
        "items": events,
        "state_events": events,
        "latest_state_version": snapshot["version"],
        "source": "life_snapshot_reader",
    }


@router.get("/world/snapshot")
async def digital_life_world_snapshot(
    person_id: str = Query("default"),
    session_id: str = Query(""),
    fact_key: list[str] | None = Query(None),
) -> dict[str, Any]:
    """Read the background world projection without opening canonical stores."""

    resolved_person = str(person_id or "default")
    payload, meta = read_query_projection(WORLD_SNAPSHOT_KIND, resolved_person)
    requested_keys = {str(item) for item in list(fact_key or []) if str(item).strip()}
    facts = [
        dict(item)
        for item in list((payload or {}).get("facts") or [])
        if isinstance(item, Mapping)
        and (not requested_keys or str(item.get("key") or "") in requested_keys)
    ]
    return {
        "schema_version": str(
            (payload or {}).get("schema_version") or "Natsume.world_projection.v1"
        ),
        "person_id": resolved_person,
        "generated_at_ms": int((payload or {}).get("generated_at_ms") or 0),
        "read_only": True,
        "write_api_available": False,
        "write_methods": [],
        "status": str((payload or {}).get("status") or "DEGRADED"),
        "reason_code": None if payload is not None else str(meta.get("reason_code") or "WORLD_PROJECTION_NOT_READY"),
        "facts": facts,
        "source_summary": dict((payload or {}).get("source_summary") or {}),
        "conflict_count": int((payload or {}).get("conflict_count") or 0),
        "expired_count": int((payload or {}).get("expired_count") or 0),
        "inferred_count": int((payload or {}).get("inferred_count") or 0),
        "session_filter_applied": bool(session_id),
        **response_meta(meta),
    }


@router.get("/growth/state")
async def digital_life_growth_state(person_id: str = Query("default")) -> dict[str, Any]:
    snapshot, state = _state(person_id)
    growth = dict(state.get("self_growth") or {})
    return {
        "person_id": snapshot["person_id"],
        "adaptive_traits": dict(growth.get("adaptive_traits") or {}),
        "habits": list(growth.get("habits") or []),
        "interests": list(growth.get("interests") or []),
        "competence_model": dict(growth.get("competence_model") or {}),
        "self_projects": list(state.get("self_projects") or growth.get("active_projects") or []),
        "growth_milestones": list(growth.get("applied_deltas") or [])[:20],
        "pending_growth": list(growth.get("suppressed_deltas") or [])[:20],
        "state_version": snapshot["version"],
        "source": "life_snapshot_reader",
    }


@router.get("/learning/recent")
async def digital_life_learning_recent(
    person_id: str = Query("default"),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    snapshot, state = _state(person_id)
    learning = dict(state.get("learning_state") or {})
    events = [
        row
        for row in _recent_events(person_id, limit=1000)
        if str(row.get("event_type") or "")
        in {"experience_ingest", "tick", "foreground_completed"}
    ][:limit]
    growth = dict(state.get("self_growth") or {})
    return {
        "person_id": snapshot["person_id"],
        "recent_clusters": list(learning.get("recent_clusters") or []),
        "learning_signals": list(learning.get("learning_signals") or []),
        "applied_deltas": list(growth.get("applied_deltas") or []),
        "suppressed_deltas": list(growth.get("suppressed_deltas") or []),
        "recent_events": events,
        "state_version": snapshot["version"],
        "source": "life_snapshot_reader",
    }


@router.post("/work-event")
async def digital_life_work_event(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    return await _submit_route_command("work_event", payload)


@router.post("/tick")
async def digital_life_tick(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    return await _submit_route_command("tick", payload)


@router.post("/advance")
async def digital_life_advance(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    return await _submit_route_command("advance", payload)


@router.get("/inner-drive/current")
async def digital_life_inner_drive_current(
    person_id: str = Query("default"),
    force: bool = Query(False),
) -> dict[str, Any]:
    snapshot, state = _state(person_id)
    return {
        "status": "OK" if snapshot["version"] > 0 else "EMPTY",
        "reason_code": None if snapshot["version"] > 0 else "LIFE_SNAPSHOT_NOT_FOUND",
        "person_id": snapshot["person_id"],
        "state": state,
        "inner_life_snapshot": project_inner_life_snapshot(snapshot),
        "state_version_before": snapshot["version"],
        "state_version_after": snapshot["version"],
        "source": "life_snapshot_reader",
        "compatibility": {
            "force_ignored": bool(force),
            "query_advances_state": False,
        },
    }


@router.get("/embodiment/current")
async def digital_life_embodiment_current(person_id: str = Query("default")) -> dict[str, Any]:
    snapshot, state = _state(person_id)
    return {
        "person_id": snapshot["person_id"],
        "embodiment": dict(state.get("embodiment") or {}),
        "latest_dispatch": {},
        "state_version": snapshot["version"],
        "source": "life_snapshot_reader",
    }


@router.get("/action/current")
async def digital_life_action_current(person_id: str = Query("default")) -> dict[str, Any]:
    snapshot, state = _state(person_id)
    return {
        "person_id": snapshot["person_id"],
        "life_action": dict(state.get("life_action") or {}),
        "state_version": snapshot["version"],
        "source": "life_snapshot_reader",
    }


@router.post("/proactive/attempt")
async def digital_life_proactive_attempt(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    return await _submit_route_command("proactive_attempt", payload)


@router.get("/proactive/recent")
async def digital_life_proactive_recent(
    person_id: str = Query("default"),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    events = [
        row
        for row in _recent_events(person_id, limit=1000)
        if str(row.get("event_type") or "") == "proactive_attempt"
    ][:limit]
    return {"person_id": str(person_id or "default"), "items": events, "source": "life_snapshot_reader"}


@router.get("/organs/state")
async def digital_life_organs_state(person_id: str = Query("default")) -> dict[str, Any]:
    snapshot, state = _state(person_id)
    organ = dict(state.get("organ_runtime") or {})
    return {
        "person_id": snapshot["person_id"],
        "organ_count": int(organ.get("organ_count") or 0),
        "organs": dict(organ.get("organs") or {}),
        "top_organs": list(organ.get("top_organs") or []),
        "updated_at_ms": int(organ.get("updated_at_ms") or snapshot["updated_at_ms"] or 0),
        "state_version": snapshot["version"],
        "source": "life_snapshot_reader",
    }


@router.get("/experience/recent")
async def digital_life_experience_recent(
    person_id: str = Query("default"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    events = [
        row
        for row in _recent_events(person_id, limit=1000)
        if str(row.get("event_type") or "") == "experience_ingest"
    ][:limit]
    return {"person_id": str(person_id or "default"), "items": events, "source": "life_snapshot_reader"}


@router.post("/experience/ingest")
async def digital_life_experience_ingest(
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    return await _submit_route_command("experience_ingest", payload)


@router.get("/proactive/episodes/recent")
async def digital_life_proactive_episodes_recent(
    person_id: str = Query("default"),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    events = [
        row
        for row in _recent_events(person_id, limit=1000)
        if str(row.get("event_type") or "") in {"proactive_attempt", "foreground_completed"}
    ][:limit]
    return {"person_id": str(person_id or "default"), "items": events, "source": "life_snapshot_reader"}


__all__ = ["router"]
