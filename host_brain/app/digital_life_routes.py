"""Digital Life 的 Host HTTP 边界：读取快照、提交受治理命令，不直接改写生命状态。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from host_brain.app.host_life_route_authority import (
    HostLifeRouteAuthority,
    require_host_life_route_authority,
)
from host_brain.app.host_route_ports import (
    HostRoutePorts,
    bind_host_route_endpoint,
)
from host_brain.digital_life.application_boundary import (
    project_inner_life_snapshot,
    read_life_events,
    read_life_snapshot,
    submit_life_command,
)
from host_brain.digital_life.companion_permission_ingress import (
    COMPANION_PERMISSION_BODY_INVALID,
    COMPANION_PERMISSION_COMMAND_NOT_APPLIED,
    COMPANION_PERMISSION_EVIDENCE_UNAVAILABLE,
    COMPANION_PERMISSION_INGRESS_UNAVAILABLE,
    COMPANION_PERMISSION_PRINCIPAL_KIND_DENIED,
    COMPANION_PERMISSION_RESULT_CONFLICT,
    COMPANION_PERMISSION_RESULT_RECORD_UNAVAILABLE,
    COMPANION_PERMISSION_SCOPE_BINDING_DENIED,
    CompanionPermissionIngress,
    CompanionPermissionIngressError,
)
from host_brain.digital_life.companion_profile_ingress import (
    COMPANION_PROFILE_BODY_INVALID,
    COMPANION_PROFILE_COMMAND_NOT_APPLIED,
    COMPANION_PROFILE_EVIDENCE_UNAVAILABLE,
    COMPANION_PROFILE_INGRESS_UNAVAILABLE,
    COMPANION_PROFILE_PRINCIPAL_KIND_DENIED,
    COMPANION_PROFILE_RESULT_CONFLICT,
    COMPANION_PROFILE_RESULT_RECORD_UNAVAILABLE,
    COMPANION_PROFILE_SCOPE_BINDING_DENIED,
    CompanionProfileIngress,
    CompanionProfileIngressError,
)
from host_brain.digital_life.companion_feedback_ingress import (
    COMPANION_FEEDBACK_BODY_INVALID,
    COMPANION_FEEDBACK_COMMAND_NOT_APPLIED,
    COMPANION_FEEDBACK_EVIDENCE_UNAVAILABLE,
    COMPANION_FEEDBACK_INGRESS_UNAVAILABLE,
    COMPANION_FEEDBACK_PRINCIPAL_KIND_DENIED,
    COMPANION_FEEDBACK_RESULT_CONFLICT,
    COMPANION_FEEDBACK_RESULT_RECORD_UNAVAILABLE,
    COMPANION_FEEDBACK_SCOPE_BINDING_DENIED,
    CompanionFeedbackIngress,
    CompanionFeedbackIngressError,
)
from natsume_contracts.life import LifeDeliveryAuthorization
from host_brain.orchestration.governor_grant import (
    DurableGovernorGrantResolver,
    canonical_proactive_intent_args,
    durable_governor_binding,
)
from host_brain.app.observability_runtime.remaining_query_projection import (
    WORLD_SNAPSHOT_KIND,
    read_from_store,
    response_meta,
)
from host_brain.app.obs_route_ports import require_obs_query_projection_store


_UNTRUSTED_INTENT_FIELDS = frozenset(
    {
        "state_patch",
        "projection_intents",
        "organ_intents",
        "deliveries",
        "created_at_ms",
    }
)


def _companion_permission_http_status(reason_code: str) -> int:
    """Map redacted user-control failures without exposing auth material."""

    reason = str(reason_code or "").strip().upper()
    if reason in {
        COMPANION_PERMISSION_INGRESS_UNAVAILABLE,
        COMPANION_PERMISSION_EVIDENCE_UNAVAILABLE,
        COMPANION_PERMISSION_RESULT_RECORD_UNAVAILABLE,
        COMPANION_PERMISSION_COMMAND_NOT_APPLIED,
    } or "UNAVAILABLE" in reason or "ENFORCE_REQUIRED" in reason:
        return 503
    if reason == COMPANION_PERMISSION_BODY_INVALID:
        return 422
    if reason in {
        COMPANION_PERMISSION_PRINCIPAL_KIND_DENIED,
        COMPANION_PERMISSION_SCOPE_BINDING_DENIED,
    } or "SCOPE" in reason or "PRINCIPAL" in reason:
        return 403
    if reason == COMPANION_PERMISSION_RESULT_CONFLICT or any(
        marker in reason for marker in ("IDEMPOTENCY", "REPLAY", "CONFLICT")
    ):
        return 409
    return 401


def _companion_profile_http_status(reason_code: str) -> int:
    """Map profile-control failures without exposing auth or user data."""

    reason = str(reason_code or "").strip().upper()
    if reason in {
        COMPANION_PROFILE_INGRESS_UNAVAILABLE,
        COMPANION_PROFILE_EVIDENCE_UNAVAILABLE,
        COMPANION_PROFILE_RESULT_RECORD_UNAVAILABLE,
        COMPANION_PROFILE_COMMAND_NOT_APPLIED,
    } or "UNAVAILABLE" in reason or "ENFORCE_REQUIRED" in reason:
        return 503
    if reason == COMPANION_PROFILE_BODY_INVALID:
        return 422
    if reason in {
        COMPANION_PROFILE_PRINCIPAL_KIND_DENIED,
        COMPANION_PROFILE_SCOPE_BINDING_DENIED,
    } or "SCOPE" in reason or "PRINCIPAL" in reason:
        return 403
    if reason == COMPANION_PROFILE_RESULT_CONFLICT or any(
        marker in reason for marker in ("IDEMPOTENCY", "REPLAY", "CONFLICT")
    ):
        return 409
    if any(
        marker in reason
        for marker in ("THRESHOLD", "CANARY", "RELATIONSHIP", "ROLLBACK", "DRIFT")
    ):
        return 409
    return 401


def _companion_feedback_http_status(reason_code: str) -> int:
    """Map receipt-scoped feedback failures without disclosing private data."""

    reason = str(reason_code or "").strip().upper()
    if reason in {
        COMPANION_FEEDBACK_INGRESS_UNAVAILABLE,
        COMPANION_FEEDBACK_EVIDENCE_UNAVAILABLE,
        COMPANION_FEEDBACK_RESULT_RECORD_UNAVAILABLE,
        COMPANION_FEEDBACK_COMMAND_NOT_APPLIED,
    } or "UNAVAILABLE" in reason or "ENFORCE_REQUIRED" in reason:
        return 503
    if reason == COMPANION_FEEDBACK_BODY_INVALID:
        return 422
    if reason in {
        COMPANION_FEEDBACK_PRINCIPAL_KIND_DENIED,
        COMPANION_FEEDBACK_SCOPE_BINDING_DENIED,
    } or "SCOPE" in reason or "PRINCIPAL" in reason:
        return 403
    if reason == COMPANION_FEEDBACK_RESULT_CONFLICT or any(
        marker in reason
        for marker in ("IDEMPOTENCY", "REPLAY", "CONFLICT", "RELATIONSHIP")
    ):
        return 409
    return 401


def _life_projection_enabled() -> bool:
    """延迟读取 persona 策略，避免路由导入时冻结可配置的投影开关。"""
    from host_brain.persona.scene_policy import life_projection_enabled

    return life_projection_enabled()


def _state(
    person_id: str,
    *,
    life: HostLifeRouteAuthority,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """读取指定 person 的权威快照及其状态负载；路由不维护第二份状态。"""
    snapshot = read_life_snapshot(
        str(person_id or "default"),
        reader=life.snapshot_reader,
    )
    return snapshot, dict(snapshot.get("state") or {})


def _recent_events(
    person_id: str,
    *,
    limit: int,
    life: HostLifeRouteAuthority,
) -> list[dict[str, Any]]:
    """把权威 ASC 事件流转换为历史 HTTP 所需的“最新在前”有界视图。"""
    # Reader order is canonical ASC.  Fetching up to the bounded journal maximum
    # and slicing here preserves the historical "most recent first" HTTP contract.
    rows = read_life_events(
        person_id,
        after_sequence=0,
        limit=1000,
        reader=life.snapshot_reader,
    )
    return list(reversed(rows[-max(1, int(limit or 1)) :]))


def _command_payload(
    command_type: str,
    payload: Mapping[str, Any],
    *,
    ledger_provider: Callable[[], Any] | None,
) -> dict[str, Any]:
    """净化 HTTP 负载并生成允许的投影/器官/投递意图。

    客户端不得直接传入状态补丁、投影意图或投递清单；这些字段必须由受信任的路由
    映射生成。主动投递还会在此重验 Governor grant，缺失或不匹配时保持为空清单。
    """
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
            ledger_provider=ledger_provider,
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
    ledger_provider: Callable[[], Any] | None,
) -> LifeDeliveryAuthorization | None:
    """把 HTTP 中的投递引用收窄为与请求、人物、动作和参数绑定的授权。

    所有解析或账本读取异常都返回 ``None``，使调用方不创建 delivery；这避免网络
    抖动或伪造 grant 被解释为可恢复的默认放行。
    """
    if not isinstance(value, Mapping):
        return None
    request_ref = str(value.get("request_id") or "").strip()
    grant_ref = str(value.get("governor_grant_ref") or "").strip()
    request_id = str(expected_request_id or "").strip()
    if not request_id or request_ref != request_id or not grant_ref:
        return None
    if not callable(ledger_provider):
        return None
    try:
        decision = DurableGovernorGrantResolver(ledger_provider()).require_bound(
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
    *,
    route_ports: HostRoutePorts,
) -> dict[str, Any]:
    """经 Host lifecycle authority 把路由请求写成可幂等的 Life 命令。

    command_id 由命令类型与调用方 request/command ID 组成，避免不同主链共享重放键；
    同步状态机工作移至线程，既不阻塞 ASGI 事件循环，也不绕过版本前置条件。
    """
    life = require_host_life_route_authority(route_ports)
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
        ledger_provider=route_ports.ledger_provider,
    )
    receipt = await asyncio.to_thread(
        submit_life_command,
        service=life.command_service,
        reader=life.snapshot_reader,
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


async def digital_life_state(
    person_id: str = Query("default"),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    """返回只读状态投影；读取本身绝不会推进 Digital Life。"""
    _snapshot, state = _state(
        person_id,
        life=require_host_life_route_authority(_route_ports),
    )
    return state


async def digital_life_runtime_health(
    person_id: str = Query("default"),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    snapshot, state = _state(
        person_id,
        life=require_host_life_route_authority(_route_ports),
    )
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


async def digital_life_environment_current(
    person_id: str = Query("default"),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    snapshot, state = _state(
        person_id,
        life=require_host_life_route_authority(_route_ports),
    )
    return {
        "person_id": snapshot["person_id"],
        "environment": dict(state.get("environment") or {}),
        "state_version": snapshot["version"],
        "source": "life_snapshot_reader",
    }


async def digital_life_causality_recent(
    person_id: str = Query("default"),
    limit: int = Query(20, ge=1, le=100),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    life = require_host_life_route_authority(_route_ports)
    snapshot, _state_payload = _state(person_id, life=life)
    events = _recent_events(person_id, limit=limit, life=life)
    return {
        "person_id": snapshot["person_id"],
        "items": events,
        "state_events": events,
        "latest_state_version": snapshot["version"],
        "source": "life_snapshot_reader",
    }


async def digital_life_world_snapshot(
    person_id: str = Query("default"),
    session_id: str = Query(""),
    fact_key: list[str] | None = Query(None),
    projection_store: Any = Depends(require_obs_query_projection_store),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    """读取后台世界投影，不打开 Canonical store，也不提供写入通道。

    可选 fact key 只是本地响应过滤；结果中的 ``read_only`` 明确提醒调用方，这个
    可能滞后的投影不能被当作写入、授权或冲突解决依据。
    """

    del _route_ports
    resolved_person = str(person_id or "default")
    payload, meta = read_from_store(
        projection_store,
        WORLD_SNAPSHOT_KIND,
        resolved_person,
    )
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


async def digital_life_growth_state(
    person_id: str = Query("default"),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    snapshot, state = _state(
        person_id,
        life=require_host_life_route_authority(_route_ports),
    )
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


async def digital_life_learning_recent(
    person_id: str = Query("default"),
    limit: int = Query(20, ge=1, le=100),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    life = require_host_life_route_authority(_route_ports)
    snapshot, state = _state(person_id, life=life)
    learning = dict(state.get("learning_state") or {})
    events = [
        row
        for row in _recent_events(person_id, limit=1000, life=life)
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


async def digital_life_work_event(
    payload: dict[str, Any] = Body(default_factory=dict),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    """把工作进展提交为 Life 命令，而非由 HTTP 端点直接更新环境状态。"""
    return await _submit_route_command(
        "work_event",
        payload,
        route_ports=_route_ports,
    )


async def digital_life_tick(
    payload: dict[str, Any] = Body(default_factory=dict),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    return await _submit_route_command(
        "tick",
        payload,
        route_ports=_route_ports,
    )


async def digital_life_advance(
    payload: dict[str, Any] = Body(default_factory=dict),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    return await _submit_route_command(
        "advance",
        payload,
        route_ports=_route_ports,
    )


async def digital_life_inner_drive_current(
    person_id: str = Query("default"),
    force: bool = Query(False),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    snapshot, state = _state(
        person_id,
        life=require_host_life_route_authority(_route_ports),
    )
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


async def digital_life_embodiment_current(
    person_id: str = Query("default"),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    snapshot, state = _state(
        person_id,
        life=require_host_life_route_authority(_route_ports),
    )
    return {
        "person_id": snapshot["person_id"],
        "embodiment": dict(state.get("embodiment") or {}),
        "latest_dispatch": {},
        "state_version": snapshot["version"],
        "source": "life_snapshot_reader",
    }


async def digital_life_action_current(
    person_id: str = Query("default"),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    snapshot, state = _state(
        person_id,
        life=require_host_life_route_authority(_route_ports),
    )
    return {
        "person_id": snapshot["person_id"],
        "life_action": dict(state.get("life_action") or {}),
        "state_version": snapshot["version"],
        "source": "life_snapshot_reader",
    }


async def digital_life_proactive_attempt(
    payload: dict[str, Any] = Body(default_factory=dict),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    """提交主动触达尝试；实际投递仍须通过 payload 中重新验证的 Governor 授权。"""
    return await _submit_route_command(
        "proactive_attempt",
        payload,
        route_ports=_route_ports,
    )


async def digital_life_proactive_permission(
    request: Request,
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    """Record only a signed, explicitly structured contact decision.

    This intentionally does not accept natural-language consent, inferred
    warmth, Bond, emotion tags or an arbitrary Life command payload.
    """

    provider = _route_ports.companion_permission_ingress_provider
    if not callable(provider):
        raise HTTPException(
            status_code=503,
            detail=COMPANION_PERMISSION_INGRESS_UNAVAILABLE,
        )
    try:
        ingress = provider()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=COMPANION_PERMISSION_INGRESS_UNAVAILABLE,
        ) from exc
    if not isinstance(ingress, CompanionPermissionIngress):
        raise HTTPException(
            status_code=503,
            detail=COMPANION_PERMISSION_INGRESS_UNAVAILABLE,
        )
    raw_body = await request.body()
    try:
        result = await asyncio.to_thread(
            ingress.submit,
            request=request,
            raw_body=raw_body,
            received_at_ms=int(time.time() * 1000),
        )
    except CompanionPermissionIngressError as exc:
        raise HTTPException(
            status_code=_companion_permission_http_status(exc.reason_code),
            detail=exc.reason_code,
        ) from exc
    return result.as_dict()


async def digital_life_companion_profile(
    request: Request,
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    """Start only a signed, bounded companion-expression canary.

    The body is strict JSON with the complete low-risk preference surface. It
    cannot carry natural-language rationale, Soul patches, emotion state,
    consent, raw media or arbitrary Life commands.
    """

    provider = _route_ports.companion_profile_ingress_provider
    if not callable(provider):
        raise HTTPException(
            status_code=503,
            detail=COMPANION_PROFILE_INGRESS_UNAVAILABLE,
        )
    try:
        ingress = provider()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=COMPANION_PROFILE_INGRESS_UNAVAILABLE,
        ) from exc
    if not isinstance(ingress, CompanionProfileIngress):
        raise HTTPException(
            status_code=503,
            detail=COMPANION_PROFILE_INGRESS_UNAVAILABLE,
        )
    raw_body = await request.body()
    try:
        result = await asyncio.to_thread(
            ingress.submit,
            request=request,
            raw_body=raw_body,
            received_at_ms=int(time.time() * 1000),
        )
    except CompanionProfileIngressError as exc:
        raise HTTPException(
            status_code=_companion_profile_http_status(exc.reason_code),
            detail=exc.reason_code,
        ) from exc
    return result.as_dict()


async def digital_life_companion_profile_rollback(
    request: Request,
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    """Restore only the exact predecessor of a signed user's active profile.

    The body names the profile currently visible to the relationship; it never
    names a rollback target.  The canonical lifecycle rejects a stale active
    profile and reopens the same durable rollback receipt after claim loss.
    """

    provider = _route_ports.companion_profile_ingress_provider
    if not callable(provider):
        raise HTTPException(
            status_code=503,
            detail=COMPANION_PROFILE_INGRESS_UNAVAILABLE,
        )
    try:
        ingress = provider()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=COMPANION_PROFILE_INGRESS_UNAVAILABLE,
        ) from exc
    if not isinstance(ingress, CompanionProfileIngress):
        raise HTTPException(
            status_code=503,
            detail=COMPANION_PROFILE_INGRESS_UNAVAILABLE,
        )
    raw_body = await request.body()
    try:
        result = await asyncio.to_thread(
            ingress.submit_rollback,
            request=request,
            raw_body=raw_body,
            received_at_ms=int(time.time() * 1000),
        )
    except CompanionProfileIngressError as exc:
        raise HTTPException(
            status_code=_companion_profile_http_status(exc.reason_code),
            detail=exc.reason_code,
        ) from exc
    return result.as_dict()


async def digital_life_companion_feedback(
    request: Request,
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    """Record one signed, bounded evaluation of an accepted response.

    The body refers to a canonical response receipt and a fixed feedback kind;
    it cannot contain raw dialogue, ratings, Soul changes or arbitrary Life
    state.  The relationship aggregate still verifies the receipt owns an
    active provisional episode before writing an aftereffect.
    """

    provider = _route_ports.companion_feedback_ingress_provider
    if not callable(provider):
        raise HTTPException(
            status_code=503,
            detail=COMPANION_FEEDBACK_INGRESS_UNAVAILABLE,
        )
    try:
        ingress = provider()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=COMPANION_FEEDBACK_INGRESS_UNAVAILABLE,
        ) from exc
    if not isinstance(ingress, CompanionFeedbackIngress):
        raise HTTPException(
            status_code=503,
            detail=COMPANION_FEEDBACK_INGRESS_UNAVAILABLE,
        )
    raw_body = await request.body()
    try:
        result = await asyncio.to_thread(
            ingress.submit,
            request=request,
            raw_body=raw_body,
            received_at_ms=int(time.time() * 1000),
        )
    except CompanionFeedbackIngressError as exc:
        raise HTTPException(
            status_code=_companion_feedback_http_status(exc.reason_code),
            detail=exc.reason_code,
        ) from exc
    return result.as_dict()


async def digital_life_proactive_recent(
    person_id: str = Query("default"),
    limit: int = Query(20, ge=1, le=100),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    life = require_host_life_route_authority(_route_ports)
    events = [
        row
        for row in _recent_events(
            person_id,
            limit=1000,
            life=life,
        )
        if str(row.get("event_type") or "") == "proactive_attempt"
    ][:limit]
    return {"person_id": str(person_id or "default"), "items": events, "source": "life_snapshot_reader"}


async def digital_life_organs_state(
    person_id: str = Query("default"),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    snapshot, state = _state(
        person_id,
        life=require_host_life_route_authority(_route_ports),
    )
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


async def digital_life_experience_recent(
    person_id: str = Query("default"),
    limit: int = Query(50, ge=1, le=200),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    life = require_host_life_route_authority(_route_ports)
    events = [
        row
        for row in _recent_events(
            person_id,
            limit=1000,
            life=life,
        )
        if str(row.get("event_type") or "") == "experience_ingest"
    ][:limit]
    return {"person_id": str(person_id or "default"), "items": events, "source": "life_snapshot_reader"}


async def digital_life_experience_ingest(
    payload: dict[str, Any] = Body(default_factory=dict),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    return await _submit_route_command(
        "experience_ingest",
        payload,
        route_ports=_route_ports,
    )


async def digital_life_proactive_episodes_recent(
    person_id: str = Query("default"),
    limit: int = Query(20, ge=1, le=100),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    life = require_host_life_route_authority(_route_ports)
    events = [
        row
        for row in _recent_events(
            person_id,
            limit=1000,
            life=life,
        )
        if str(row.get("event_type") or "") in {"proactive_attempt", "foreground_completed"}
    ][:limit]
    return {"person_id": str(person_id or "default"), "items": events, "source": "life_snapshot_reader"}


def create_digital_life_router(route_ports: HostRoutePorts) -> APIRouter:
    """创建绑定一个 Host lifespan authority 的 Digital Life 路由。

    兼容模块级 router 会以空端口构造，因此读写均会失败关闭；嵌入消费者必须显式
    提供应用局部端口，不能依赖隐藏的全局生命周期服务。
    """

    if not isinstance(route_ports, HostRoutePorts):
        raise TypeError("route_ports must be HostRoutePorts")
    router = APIRouter(tags=["digital-life"])

    def add(path: str, endpoint: Any, *, methods: list[str]) -> None:
        router.add_api_route(
            path,
            bind_host_route_endpoint(endpoint, route_ports),
            methods=methods,
        )

    add("/v1/digital-life/state", digital_life_state, methods=["GET"])
    add(
        "/v1/digital-life/runtime/health",
        digital_life_runtime_health,
        methods=["GET"],
    )
    add(
        "/v1/digital-life/environment/current",
        digital_life_environment_current,
        methods=["GET"],
    )
    add(
        "/v1/digital-life/causality/recent",
        digital_life_causality_recent,
        methods=["GET"],
    )
    add(
        "/v1/digital-life/world/snapshot",
        digital_life_world_snapshot,
        methods=["GET"],
    )
    add(
        "/v1/digital-life/growth/state",
        digital_life_growth_state,
        methods=["GET"],
    )
    add(
        "/v1/digital-life/learning/recent",
        digital_life_learning_recent,
        methods=["GET"],
    )
    add(
        "/v1/digital-life/work-event",
        digital_life_work_event,
        methods=["POST"],
    )
    add("/v1/digital-life/tick", digital_life_tick, methods=["POST"])
    add("/v1/digital-life/advance", digital_life_advance, methods=["POST"])
    add(
        "/v1/digital-life/inner-drive/current",
        digital_life_inner_drive_current,
        methods=["GET"],
    )
    add(
        "/v1/digital-life/embodiment/current",
        digital_life_embodiment_current,
        methods=["GET"],
    )
    add(
        "/v1/digital-life/action/current",
        digital_life_action_current,
        methods=["GET"],
    )
    add(
        "/v1/digital-life/proactive/attempt",
        digital_life_proactive_attempt,
        methods=["POST"],
    )
    add(
        "/v1/digital-life/relationship/proactive-permission",
        digital_life_proactive_permission,
        methods=["POST"],
    )
    add(
        "/v1/digital-life/relationship/companion-profile",
        digital_life_companion_profile,
        methods=["POST"],
    )
    add(
        "/v1/digital-life/relationship/companion-profile/rollback",
        digital_life_companion_profile_rollback,
        methods=["POST"],
    )
    add(
        "/v1/digital-life/relationship/response-feedback",
        digital_life_companion_feedback,
        methods=["POST"],
    )
    add(
        "/v1/digital-life/proactive/recent",
        digital_life_proactive_recent,
        methods=["GET"],
    )
    add(
        "/v1/digital-life/organs/state",
        digital_life_organs_state,
        methods=["GET"],
    )
    add(
        "/v1/digital-life/experience/recent",
        digital_life_experience_recent,
        methods=["GET"],
    )
    add(
        "/v1/digital-life/experience/ingest",
        digital_life_experience_ingest,
        methods=["POST"],
    )
    add(
        "/v1/digital-life/proactive/episodes/recent",
        digital_life_proactive_episodes_recent,
        methods=["GET"],
    )
    return router


# Compatibility-only router for embedded consumers. Mutation and Life reads
# fail closed until those consumers provide an explicit HostRoutePorts bundle.
router = create_digital_life_router(HostRoutePorts())


__all__ = ["create_digital_life_router", "router"]
