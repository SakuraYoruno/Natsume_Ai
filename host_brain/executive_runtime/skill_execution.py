"""Legacy low-level Skill execution helpers retained behind governed ports.

The historical direct Delegate/SubAgent branch is intentionally absent.  A
real delegate must use the independent governed route executor and its frozen
TaskGraph node authority.
"""

from ._shared import *  # noqa: F403
from host_brain.core.errors import MCP_TASK_AUTHORITY_REQUIRED
from host_brain.runtime.scene_consumer_projection import (
    SCENE_PROJECTION_BLOCK,
    SCENE_PROJECTION_REVIEW,
    SceneConsumerProjectionError,
    project_scene_decision_v2,
)


def _action_arbiter_execution_enabled() -> bool:
    """Read the legacy low-level guard without rebuilding an Executive graph.

    The remaining helper is reachable only through the frozen governed Skill
    port or diagnostic compatibility utilities.  It may enforce a legacy
    token/scene check for those utilities, but it cannot choose a route,
    replan, or obtain a runtime-capability override.
    """

    from host_brain.core.orchestration_settings import (  # noqa: PLC0415
        get_orchestration_settings,
    )

    return bool(get_orchestration_settings().action_arbiter_enabled)


def _extract_skill_args(intent_draft: dict[str, Any], request_body: Any, user_message: str) -> dict[str, Any]:
    if isinstance(intent_draft.get("args"), dict):
        return dict(intent_draft.get("args") or {})
    if isinstance(request_body, dict) and isinstance(request_body.get("args"), dict):
        return dict(request_body.get("args") or {})
    return {
        "query": user_message,
        "text": user_message,
        "user_message": user_message,
    }


def _governed_node_grant_error(
    value: Mapping[str, Any] | None,
    *,
    execution_context: Mapping[str, Any],
    request_id: str,
    session_id: str,
    skill_id: str,
    intent_id: str,
) -> str | None:
    grant = dict(value) if isinstance(value, Mapping) else {}
    context = dict(execution_context or {})
    try:
        revision = int(context.get("graph_revision") or 0)
        grant_revision = int(grant.get("graph_revision") or 0)
        expires_at_ms = int(grant.get("expires_at_ms") or 0)
        fencing_token = int(context.get("fencing_token") or 0)
    except (TypeError, ValueError):
        return "GOVERNED_NODE_GRANT_INVALID"
    expected = {
        "grant_ref": str(context.get("node_grant_ref") or "").strip(),
        "parent_grant_ref": str(context.get("parent_grant_ref") or "").strip(),
        "request_id": str(request_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "principal_id": str(context.get("principal_id") or "").strip(),
        "graph_id": str(context.get("graph_id") or "").strip(),
        "root_work_order_id": str(context.get("work_order_id") or "").strip(),
        "node_id": str(context.get("node_id") or "").strip(),
        "route": "skill",
        "target_id": str(skill_id or "").strip(),
        "intent_id": str(intent_id or "").strip(),
        "action_kind": str(skill_id or "").strip(),
        "token_subject": str(skill_id or "").strip(),
    }
    actual = {key: str(grant.get(key) or "").strip() for key in expected}
    required_runtime = (
        "command_id",
        "attempt_id",
        "lease_id",
        "lease_owner_id",
        "reservation_id",
        "business_scope",
        "operation_fingerprint",
    )
    if (
        actual != expected
        or revision < 1
        or grant_revision != revision
        or expires_at_ms <= int(time.time() * 1000)
        or any(not str(context.get(key) or "").strip() for key in required_runtime)
        or fencing_token < 1
    ):
        return "GOVERNED_NODE_GRANT_INVALID"
    return None


def _flatten_result_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, (int, float, bool)):
        return str(payload)
    if isinstance(payload, list):
        parts = [_flatten_result_text(item) for item in payload]
        return "\n".join(part for part in parts if part)
    if isinstance(payload, dict):
        for key in ("result_text", "result", "detail", "message", "summary", "output", "text"):
            text = _flatten_result_text(payload.get(key))
            if text:
                return text
        narrative = payload.get("narrative")
        if isinstance(narrative, dict):
            text = _flatten_result_text(narrative.get("text"))
            if text:
                return text
        return str(payload)
    return str(payload)


def _is_effective_skill_success(
    *,
    raw_status: str,
    raw_code: str,
    raw_result: Any,
    result_text: str,
) -> bool:
    status_value = _normalize_text(raw_status, lower=True)
    if status_value in _SKILL_EXEC_FAIL_STATUS:
        return False

    code_value = _normalize_text(raw_code).upper()
    if code_value in _SKILL_EXEC_FAIL_CODES:
        return False

    digest = f"{str(raw_result or '')} {str(result_text or '')}".lower()
    if any(marker in digest for marker in _SKILL_EXEC_NO_EFFECT_MARKERS):
        return False
    return True


def _log_skill_exec_row(
    *,
    request_id: str,
    intent_id: str,
    skill_name: str,
    result_status: str,
    result_code: str | None,
    result_digest: str,
    approval_token_ref: str | None,
    ts_ms: int,
    ledger: Any,
    evidence_ref: str | None = None,
) -> None:
    if ledger is False:
        return
    try:
        ledger.log_skill_exec(
            {
                "request_id": request_id,
                "thread_id": None,
                "turn_id": None,
                "intent_id": intent_id,
                "event_id": f"evt::{request_id}::{skill_name}",
                "skill_name": skill_name,
                "approval_token_ref": approval_token_ref,
                "confirm_token_ref": None,
                "undo_token_ref": None,
                "result_status": result_status,
                "result_code": result_code,
                "result_digest": result_digest[:4000] if result_digest else None,
                "evidence_ref": evidence_ref or _skill_receipt_ref(request_id, skill_name),
                "manifest_ref": None,
                "ts_ms": ts_ms,
            }
        )
    except Exception as exc:
        _logger.debug("[%s] skill exec audit skipped skill=%s: %s", request_id, skill_name, exc)


def _skill_receipt_ref(request_id: str, skill_name: str) -> str:
    digest = hashlib.sha256(
        f"{str(request_id or '').strip()}\n{str(skill_name or '').strip()}".encode("utf-8")
    ).hexdigest()
    return f"skillreceipt://sha256/{digest}"


def _execute_skill(
    *,
    ports: Any,
    request_body: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    intent_id: str | None,
    skill_id: str,
    task_text: str,
    user_message: str,
    governor_result: dict[str, Any],
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    render_target_lang: str,
    skill_registry: Any | None = None,
    token_service_override: Any | None = None,
    ledger: Any | None = None,
    durable_authority_ledger: Any | None = None,
    capability_broker_override: Any | None = None,
    model_attempt_service_override: Any | None = None,
    production_authority_verifier: Any | None = None,
    governed_execution_context: Mapping[str, Any] | None = None,
    execution_args_override: Mapping[str, Any] | None = None,
    verified_node_grant: Mapping[str, Any] | None = None,
    frozen_executor_payload: Mapping[str, Any] | None = None,
    degradation_capability_port: Any | None = None,
) -> _ExecutionOutcome:
    started = time.time()
    if skill_registry is None:
        skill_registry = ports.get_skill_registry()
    if ledger is None:
        ledger = ports.get_ledger()
    registry = skill_registry
    if str(skill_id or "").strip().startswith("mcp::"):
        # Older durable graphs may still contain the retired bridge target.
        # Reject with an exact stable reason before registry lookup so restart
        # recovery never degrades into a generic missing-Skill failure and no
        # stale provider callback can be invoked.
        skill_receipt_ref = _skill_receipt_ref(request_id, skill_id)
        execution_intent_id = _normalize_text(intent_id) or f"intent::{request_id}::{skill_id}"
        latency_ms = int((time.time() - started) * 1000)
        _log_skill_exec_row(
            request_id=request_id,
            intent_id=execution_intent_id,
            skill_name=skill_id,
            result_status="REJECTED",
            result_code=MCP_TASK_AUTHORITY_REQUIRED,
            result_digest=MCP_TASK_AUTHORITY_REQUIRED,
            approval_token_ref=None,
            ts_ms=int(time.time() * 1000),
            ledger=ledger,
            evidence_ref=skill_receipt_ref,
        )
        return _ExecutionOutcome(
            route_type="skill",
            target_id=skill_id,
            skill_family="mcp",
            success=False,
            result_text="",
            error_text=MCP_TASK_AUTHORITY_REQUIRED,
            latency_ms=latency_ms,
            payload={
                "approval_token_ref": None,
                "fallback_policy": "strict_fail",
                "receipt_ref": skill_receipt_ref,
            },
        )
    skill_meta = registry.require(skill_id)
    skill_receipt_ref = _skill_receipt_ref(request_id, skill_id)
    approval_token_ref = _normalize_text(dict(governor_result or {}).get("tokens", {}).get("approval_token_ref"))
    confirm_token_ref = _normalize_text(dict(governor_result or {}).get("tokens", {}).get("confirm_token_ref"))
    execution_intent_id = _normalize_text(intent_id) or f"intent::{request_id}::{skill_id}"
    governed_node_verified = False
    if governed_execution_context is not None:
        grant_error = _governed_node_grant_error(
            verified_node_grant,
            execution_context=governed_execution_context,
            request_id=request_id,
            session_id=session_id,
            skill_id=skill_id,
            intent_id=execution_intent_id,
        )
        if grant_error:
            raise PermissionError(grant_error)
        governed_node_verified = True
        if bool(skill_meta.require_confirm):
            raise PermissionError("GOVERNED_DURABLE_CONFIRM_REVIEW_REQUIRED")

    if _normalize_text(dict(governor_result or {}).get("decision"), lower=True) not in {"", "approve"}:
        error_text = _normalize_text(governor_result.get("reason_code")) or "GOVERNOR_REJECTED"
        latency_ms = int((time.time() - started) * 1000)
        _log_skill_exec_row(
            request_id=request_id,
            intent_id=execution_intent_id,
            skill_name=skill_id,
            result_status="REJECTED",
            result_code=error_text,
            result_digest=error_text,
            approval_token_ref=approval_token_ref or None,
            ts_ms=int(time.time() * 1000),
            ledger=ledger,
            evidence_ref=skill_receipt_ref,
        )
        return _ExecutionOutcome(
            route_type="skill",
            target_id=skill_id,
            skill_family=str(skill_meta.family or "general"),
            success=False,
            result_text="",
            error_text=error_text,
            latency_ms=latency_ms,
            payload={
                "approval_token_ref": approval_token_ref or None,
                "receipt_ref": skill_receipt_ref,
            },
        )

    action_arbiter_decision: dict[str, Any] | None = None
    token_service = None
    if _action_arbiter_execution_enabled() and not governed_node_verified:
        envelope = dict(intent_draft or {})
        bound_intent_id = _normalize_text(envelope.get("id") or envelope.get("intent_id"))
        bound_session_id = _normalize_text(envelope.get("session_id"))
        try:
            bound_seq = int(envelope.get("seq"))
        except (TypeError, ValueError):
            bound_seq = -1
        authority_error = ""
        if not bound_intent_id or bound_intent_id != execution_intent_id:
            authority_error = "ARBITER_INTENT_ID_MISMATCH"
        elif not bound_session_id or bound_session_id != session_id:
            authority_error = "ARBITER_SESSION_ID_MISMATCH"
        elif bound_seq < 0:
            authority_error = "ARBITER_INTENT_SEQ_INVALID"

        if not authority_error and not governed_node_verified:
            from host_brain.governor.tokens import TOKEN_TYPE_APPROVAL  # noqa: PLC0415

            if token_service_override is None:
                token_service_override = ports.get_token_service()
            token_service = token_service_override
            token_check = token_service.validate_bound_token(
                approval_token_ref or None,
                expected_type=TOKEN_TYPE_APPROVAL,
                intent_id=bound_intent_id,
                skill_name=skill_id,
                session_id=bound_session_id,
                seq=bound_seq,
                now_ts_ms=int(time.time() * 1000),
                consume=False,
            )
            if not token_check.valid:
                authority_error = str(token_check.reason_code or "TOKEN_INVALID")

        if not authority_error:
            from host_brain.governor.action_arbiter import arbitrate_action  # noqa: PLC0415

            try:
                scene_projection = project_scene_decision_v2(
                    envelope.get("scene_decision_v2"),
                    expected_identity=envelope.get("scene_identity_v2"),
                )
            except SceneConsumerProjectionError as exc:
                authority_error = exc.reason_code
            else:
                if scene_projection.disposition == SCENE_PROJECTION_BLOCK:
                    authority_error = "SCENE_DECISION_V2_BLOCKED"
                elif scene_projection.disposition == SCENE_PROJECTION_REVIEW:
                    authority_error = "SCENE_DECISION_V2_REVIEW_REQUIRED"

            if not authority_error:
                action_arbiter_decision = arbitrate_action(
                    governor_decision=governor_result,
                    proposed_action={
                        "action_kind": skill_id,
                        "scope": str(skill_meta.scope or ""),
                        "risk_level": int(skill_meta.risk_level),
                        "requires_token": True,
                        "is_proactive": False,
                    },
                    intent_envelope=envelope,
                    scene_decision=scene_projection.action_projection(
                        user_goal_ref=f"intent://{bound_intent_id}"
                    ),
                    user_intent={
                        "explicit": True,
                        "intent_id": bound_intent_id,
                        "action_kind": str(envelope.get("action") or ""),
                        "goal_ref": f"intent://{bound_intent_id}",
                    },
                    now_ms=int(time.time() * 1000),
                    degradation_capability_port=degradation_capability_port,
                )
                try:
                    from host_brain.persona.life_evolution_observer import (  # noqa: PLC0415
                        observe_action_arbiter_decision,
                    )

                    observe_action_arbiter_decision(
                        action_arbiter_decision,
                        person_id=person_id,
                        source_kind="executive_runtime",
                        source_ref=f"request://{request_id}",
                    )
                except Exception:
                    pass
                if action_arbiter_decision.get("decision") == "BLOCK":
                    authority_error = str(
                        (
                            action_arbiter_decision.get("reason_codes")
                            or ["ACTION_ARBITER_BLOCKED"]
                        )[0]
                    )

        if authority_error:
            latency_ms = int((time.time() - started) * 1000)
            _log_skill_exec_row(
                request_id=request_id,
                intent_id=execution_intent_id,
                skill_name=skill_id,
                result_status="REJECTED",
                result_code=authority_error,
                result_digest=authority_error,
                approval_token_ref=approval_token_ref or None,
                ts_ms=int(time.time() * 1000),
                ledger=ledger,
                evidence_ref=skill_receipt_ref,
            )
            return _ExecutionOutcome(
                route_type="skill",
                target_id=skill_id,
                skill_family=str(skill_meta.family or "general"),
                success=False,
                result_text="",
                error_text=authority_error,
                latency_ms=latency_ms,
                payload={
                    "approval_token_ref": approval_token_ref or None,
                    "action_arbiter_decision": action_arbiter_decision,
                    "receipt_ref": skill_receipt_ref,
                },
            )

    args = (
        dict(execution_args_override)
        if execution_args_override is not None
        else _extract_skill_args(intent_draft, request_body, task_text)
    )
    if is_agent_runtime_skill(skill_id):
        if governed_execution_context is not None:
            # The admitted Host command owns the root WorkOrder identity.  A
            # governed skill must not mint a second WorkOrder inside its
            # normalizer, otherwise the sealed Task authority cannot bind the
            # actual executor payload to the durable command/graph.
            args["work_order_id"] = str(
                governed_execution_context.get("work_order_id") or ""
            ).strip()
        args.setdefault("approval_confirmed", True)
        args.setdefault("confirmed", True)
        if approval_token_ref:
            args.setdefault("approval_token_ref", approval_token_ref)
    exec_context = {
        "request_id": request_id,
        "session_id": session_id,
        "person_id": person_id,
        "user_message": user_message,
        "task_text": task_text,
        "render_target_lang": render_target_lang,
        "mood_state": _normalize_text(dict(emotion_context or {}).get("mood_state")) or "NEUTRAL",
        "emotion_tag": _normalize_text(dict(emotion_context or {}).get("emotion_tag")).upper() or "NEUTRAL",
        "emotion_context": dict(emotion_context or {}),
        "skill_family": str(skill_meta.family or "general"),
    }
    if governed_execution_context is not None:
        exec_context["_governed_execution_context"] = {
            "execution_mode": "governed_task_runtime",
            **dict(governed_execution_context),
        }
        if capability_broker_override is not None:
            exec_context["capability_broker"] = capability_broker_override
        if model_attempt_service_override is not None:
            exec_context["model_attempt_service"] = model_attempt_service_override
        if callable(production_authority_verifier):
            exec_context["production_authority_verifier"] = production_authority_verifier
    if action_arbiter_decision is not None:
        exec_context["action_arbiter_decision"] = action_arbiter_decision
    if skill_id == "resource_manager_status":
        exec_context["resource_manager_provider"] = ports.get_resource_manager
    if skill_id.startswith("memory_"):
        exec_context.update(
            _build_memory_exec_context(
                ports=ports,
                request_id=request_id,
                session_id=session_id,
                intent_id=execution_intent_id,
                emotion_context=emotion_context,
            )
        )

    try:
        if token_service is not None:
            token_result = token_service.validate_execution_tokens(
                intent_id=bound_intent_id,
                skill_name=skill_id,
                session_id=bound_session_id,
                seq=bound_seq,
                approval_token_ref=approval_token_ref or None,
                confirm_token_ref=confirm_token_ref or None,
                require_confirm=bool(skill_meta.require_confirm),
                now_ts_ms=int(time.time() * 1000),
            )
            if not token_result.valid:
                raise PermissionError(str(token_result.reason_code or "TOKEN_INVALID"))
        if frozen_executor_payload is not None:
            if governed_execution_context is None:
                raise PermissionError("FROZEN_EXECUTOR_PAYLOAD_REQUIRES_GOVERNED_CONTEXT")
            payload = dict(frozen_executor_payload)
        else:
            handler_payload = skill_meta.handler(args, exec_context)
            if isinstance(handler_payload, dict):
                payload = dict(handler_payload)
            else:
                payload = handler_payload
        if isinstance(payload, dict):
            if governed_execution_context is not None:
                payload["_context"] = exec_context
            else:
                payload.setdefault("_context", exec_context)
        if not isinstance(payload, dict):
            raise PermissionError("SKILL_EXECUTOR_PAYLOAD_INVALID")
        if not str(payload.get("tool") or "").strip():
            # Pure/read-only handlers still cross the canonical capability
            # Broker; the private projection adapter only returns their
            # normalized result after authority admission and durable claim.
            payload = {
                "tool": "skill_result",
                "result": payload,
                "_context": exec_context,
            }
        governed_authority = (
            {
                "execution_mode": "governed_task_runtime",
                **dict(governed_execution_context),
            }
            if governed_execution_context is not None
            else None
        )
        try:
            authority_seq = int(intent_draft.get("seq") or 0)
        except (TypeError, ValueError):
            authority_seq = 0
        dispatch_authority = bind_validated_skill_authority(
            payload=payload,
            request_id=request_id,
            skill_id=skill_id,
            principal_id=person_id,
            # A Task-governed dispatch is bound to the admitted HostCommand
            # target.  Token-governed dispatches are instead bound to the
            # Governor scope (normally the SkillMeta scope, e.g. ``host``).
            target_id=(
                _normalize_text(
                    dict(governed_execution_context or {}).get("target_id"),
                    lower=True,
                )
                if governed_execution_context is not None
                else (
                    _normalize_text(intent_draft.get("target"), lower=True)
                    or _normalize_text(skill_meta.scope, lower=True)
                )
            ),
            intent_id=execution_intent_id,
            session_id=session_id,
            seq=authority_seq,
            approval_token_ref=approval_token_ref or None,
            governed_execution_context=governed_authority,
            # ``False`` is the worker-thread audit suppression sentinel, not a
            # ledger candidate.  Authority verification must still bind to
            # the process canonical ledger used by the governed coordinator.
            durable_ledger=(
                durable_authority_ledger
                if durable_authority_ledger is not None
                else (None if ledger is False else ledger)
            ),
            validated_by=(
                "governed_host_adapter.durable_grant"
                if governed_execution_context is not None
                else (
                    "executive_runtime.token_service"
                    if token_service is not None
                    else "executive_runtime.governor_output"
                )
            ),
        )
        raw_result = dispatch_executor_payload(
            payload,
            authority=dispatch_authority,
            capability_broker=(
                capability_broker_override
                if capability_broker_override is not None
                else ports.get_capability_broker()
            ),
        )
        result_text = _flatten_result_text(raw_result)
        result_mapping = _coerce_mapping(raw_result)
        raw_status = _normalize_text(result_mapping.get("status"), lower=True)
        raw_code = _normalize_text(result_mapping.get("reason_code") or result_mapping.get("result_code"))
        success = _is_effective_skill_success(
            raw_status=raw_status,
            raw_code=raw_code,
            raw_result=raw_result,
            result_text=result_text,
        )
        latency_ms = int((time.time() - started) * 1000)
        _log_skill_exec_row(
            request_id=request_id,
            intent_id=execution_intent_id,
            skill_name=skill_id,
            result_status="SUCCESS" if success else "FAILED",
            result_code=raw_code or None,
            result_digest=(result_text or str(raw_result or "")),
            approval_token_ref=approval_token_ref or None,
            ts_ms=int(time.time() * 1000),
            ledger=ledger,
            evidence_ref=skill_receipt_ref,
        )
        return _ExecutionOutcome(
            route_type="skill",
            target_id=skill_id,
            skill_family=str(skill_meta.family or "general"),
            success=success,
            result_text=result_text,
            error_text="" if success else (raw_code or "SKILL_EXECUTION_NO_EFFECT"),
            latency_ms=latency_ms,
            payload={
                "result": raw_result,
                "approval_token_ref": approval_token_ref or None,
                "fallback_policy": str(skill_meta.fallback_policy or "chat_fallback"),
                "receipt_ref": skill_receipt_ref,
            },
        )
    except Exception as exc:
        latency_ms = int((time.time() - started) * 1000)
        _log_skill_exec_row(
            request_id=request_id,
            intent_id=execution_intent_id,
            skill_name=skill_id,
            result_status="FAILED",
            result_code="SKILL_EXECUTION_ERROR",
            result_digest=str(exc),
            approval_token_ref=approval_token_ref or None,
            ts_ms=int(time.time() * 1000),
            ledger=ledger,
            evidence_ref=skill_receipt_ref,
        )
        return _ExecutionOutcome(
            route_type="skill",
            target_id=skill_id,
            skill_family=str(skill_meta.family or "general"),
            success=False,
            result_text="",
            error_text=str(exc),
            latency_ms=latency_ms,
            payload={
                "approval_token_ref": approval_token_ref or None,
                "fallback_policy": str(getattr(skill_meta, "fallback_policy", "chat_fallback") or "chat_fallback"),
                "receipt_ref": skill_receipt_ref,
            },
        )
