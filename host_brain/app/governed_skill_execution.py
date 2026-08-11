"""Execute one Skill only under a live governed TaskGraph node authority.

The retired Executive runtime and token-only ``SkillLoader`` are not valid
callers.  This module owns no route selection, planning, service lookup, or
delivery authority; every collaborator and every node identity is supplied by
the app-local frozen Skill worker.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import time
from typing import Any

from host_brain.agent_runtime.skill_ids import is_agent_runtime_skill
from host_brain.core.errors import (
    GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED,
    MCP_TASK_AUTHORITY_REQUIRED,
)
from host_brain.skills.executor_registry import (
    bind_validated_skill_authority,
    dispatch_executor_payload,
)
from host_brain.skills.governed_execution_support import (
    GovernedSkillExecutionOutcome,
    build_memory_exec_context,
    coerce_mapping,
    effective_skill_success,
    flatten_result_text,
    normalize_text,
)
from host_brain.skills.execution_policy_validation import (
    governed_skill_policy_error,
)


GOVERNED_NODE_GRANT_INVALID = "GOVERNED_NODE_GRANT_INVALID"
GOVERNED_MEMORY_ACCEPTANCE_EFFECT_REQUIRED = (
    "GOVERNED_MEMORY_ACCEPTANCE_EFFECT_REQUIRED"
)


def execute_governed_skill(
    *,
    ports: Any,
    durable_authority_ledger: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    intent_id: str,
    skill_id: str,
    task_text: str,
    user_message: str,
    governor_result: Mapping[str, Any],
    intent_draft: Mapping[str, Any],
    emotion_context: Mapping[str, Any],
    render_target_lang: str,
    governed_execution_context: Mapping[str, Any],
    execution_args: Mapping[str, Any],
    verified_node_grant: Mapping[str, Any],
    frozen_executor_payload: Mapping[str, Any] | None = None,
) -> GovernedSkillExecutionOutcome:
    """Run one admitted Skill node without a compatibility execution path."""

    started = time.time()
    skill_name = str(skill_id or "").strip()
    if skill_name.startswith("mcp::"):
        raise PermissionError(MCP_TASK_AUTHORITY_REQUIRED)
    if (
        durable_authority_ledger is None
        or not isinstance(governed_execution_context, Mapping)
        or not governed_execution_context
        or not isinstance(verified_node_grant, Mapping)
        or not verified_node_grant
    ):
        raise PermissionError(GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED)

    request_ref = str(request_id or "").strip()
    session_ref = str(session_id or "").strip()
    person_ref = str(person_id or "").strip()
    execution_intent_id = str(intent_id or "").strip()
    if not all(
        (
            request_ref,
            session_ref,
            person_ref,
            execution_intent_id,
            skill_name,
        )
    ):
        raise PermissionError(GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED)

    grant_error = _governed_node_grant_error(
        verified_node_grant,
        execution_context=governed_execution_context,
        request_id=request_ref,
        session_id=session_ref,
        skill_id=skill_name,
        intent_id=execution_intent_id,
    )
    if grant_error:
        raise PermissionError(grant_error)

    # ``memory_store`` is intentionally not a low-level execution target.
    # The frozen worker emits an effect-free proposal and the Task Runtime
    # performs the actual write only after graph Acceptance.  Keeping this
    # guard here prevents compatibility callers from restoring an eager write.
    if skill_name == "memory_store":
        raise PermissionError(GOVERNED_MEMORY_ACCEPTANCE_EFFECT_REQUIRED)

    registry = ports.skill_registry
    capability_broker = ports.capability_broker
    model_attempt_service = ports.model_attempt_service
    production_authority_verifier = ports.production_authority_verifier
    if (
        registry is None
        or capability_broker is None
        or model_attempt_service is None
        or not callable(production_authority_verifier)
    ):
        raise PermissionError("GOVERNED_SKILL_EXECUTION_PORTS_INCOMPLETE")

    skill_meta = registry.require(skill_name)
    _require_governed_skill_policy(skill_name, skill_meta)
    version_binding = _serialize_skill_version_binding(skill_meta)
    receipt_ref = _skill_receipt_ref(request_ref, skill_name)
    tokens = governor_result.get("tokens")
    token_projection = dict(tokens) if isinstance(tokens, Mapping) else {}
    approval_token_ref = normalize_text(
        token_projection.get("approval_token_ref")
    )
    decision = normalize_text(governor_result.get("decision"), lower=True)
    if decision != "approve":
        error_text = (
            normalize_text(governor_result.get("reason_code"))
            or "GOVERNOR_REJECTED"
        )
        return _outcome(
            started=started,
            skill_name=skill_name,
            skill_family=str(skill_meta.family or "general"),
            success=False,
            error_text=error_text,
            approval_token_ref=approval_token_ref,
            receipt_ref=receipt_ref,
            fallback_policy=str(skill_meta.fallback_policy or "strict_fail"),
        )
    if bool(skill_meta.require_confirm) and not str(
        governed_execution_context.get("pre_execution_review_ref") or ""
    ).startswith("review://"):
        raise PermissionError("GOVERNED_DURABLE_CONFIRM_REVIEW_REQUIRED")

    args = dict(execution_args)
    if is_agent_runtime_skill(skill_name):
        # The admitted Host command owns this identity; a normalizer cannot
        # mint a second WorkOrder after the TaskGraph was sealed.
        args["work_order_id"] = str(
            governed_execution_context.get("work_order_id") or ""
        ).strip()
        args.setdefault("approval_confirmed", True)
        args.setdefault("confirmed", True)
        if approval_token_ref:
            args.setdefault("approval_token_ref", approval_token_ref)

    exec_context = _build_execution_context(
        ports=ports,
        request_id=request_ref,
        session_id=session_ref,
        person_id=person_ref,
        intent_id=execution_intent_id,
        skill_name=skill_name,
        skill_meta=skill_meta,
        task_text=task_text,
        user_message=user_message,
        emotion_context=emotion_context,
        render_target_lang=render_target_lang,
        governed_execution_context=governed_execution_context,
    )
    model_budget_runtime = _compose_model_budget_runtime(
        skill_meta=skill_meta,
        model_attempt_service=model_attempt_service,
        governed_execution_context=governed_execution_context,
        request_id=request_ref,
    )
    if model_budget_runtime is not None:
        exec_context["_governed_skill_model_budget_runtime"] = (
            model_budget_runtime
        )

    try:
        if frozen_executor_payload is not None:
            payload: Any = dict(frozen_executor_payload)
        else:
            payload = skill_meta.handler(args, exec_context)
            if isinstance(payload, Mapping):
                payload = dict(payload)
        if not isinstance(payload, dict):
            raise PermissionError("SKILL_EXECUTOR_PAYLOAD_INVALID")
        payload["_context"] = exec_context
        if not str(payload.get("tool") or "").strip():
            # Even a pure projection crosses the sealed Broker boundary.
            payload = {
                "tool": "skill_result",
                "result": payload,
                "_context": exec_context,
            }

        governed_authority = {
            "execution_mode": "governed_task_runtime",
            **dict(governed_execution_context),
        }
        try:
            authority_seq = int(intent_draft.get("seq") or 0)
        except (TypeError, ValueError):
            authority_seq = 0
        dispatch_authority = bind_validated_skill_authority(
            payload=payload,
            request_id=request_ref,
            skill_id=skill_name,
            principal_id=person_ref,
            target_id=skill_name,
            intent_id=execution_intent_id,
            session_id=session_ref,
            seq=authority_seq,
            approval_token_ref=approval_token_ref or None,
            governed_execution_context=governed_authority,
            durable_ledger=durable_authority_ledger,
            validated_by="governed_host_adapter.durable_grant",
        )
        raw_result = dispatch_executor_payload(
            payload,
            authority=dispatch_authority,
            capability_broker=capability_broker,
        )
        result_text = flatten_result_text(raw_result)
        result_mapping = coerce_mapping(raw_result)
        raw_status = normalize_text(
            result_mapping.get("status"),
            lower=True,
        )
        raw_code = normalize_text(
            result_mapping.get("reason_code")
            or result_mapping.get("result_code")
        )
        success = effective_skill_success(
            raw_status=raw_status,
            raw_code=raw_code,
            raw_result=raw_result,
            result_text=result_text,
        )
        model_budget_attestation = (
            model_budget_runtime.attestation()
            if model_budget_runtime is not None
            else None
        )
        if (
            success
            and model_budget_runtime is not None
            and model_budget_attestation is None
        ):
            raise PermissionError(
                "GOVERNED_SKILL_MODEL_BUDGET_MEASUREMENT_REQUIRED"
            )
        return GovernedSkillExecutionOutcome(
            route_type="skill",
            target_id=skill_name,
            skill_family=str(skill_meta.family or "general"),
            success=success,
            result_text=result_text,
            error_text=(
                "" if success else (raw_code or "SKILL_EXECUTION_NO_EFFECT")
            ),
            latency_ms=int((time.time() - started) * 1000),
            payload={
                "result": raw_result,
                "approval_token_ref": approval_token_ref or None,
                "fallback_policy": str(
                    skill_meta.fallback_policy or "strict_fail"
                ),
                "receipt_ref": receipt_ref,
                **(
                    {"skill_version_binding": version_binding}
                    if version_binding is not None
                    else {}
                ),
                **(
                    {
                        "governed_skill_model_budget_attestation": (
                            model_budget_attestation
                        )
                    }
                    if model_budget_attestation is not None
                    else {}
                ),
            },
        )
    except Exception as exc:
        return _outcome(
            started=started,
            skill_name=skill_name,
            skill_family=str(skill_meta.family or "general"),
            success=False,
            error_text=str(exc),
            approval_token_ref=approval_token_ref,
            receipt_ref=receipt_ref,
            fallback_policy=str(skill_meta.fallback_policy or "strict_fail"),
        )


def record_governed_skill_execution(
    *,
    ledger: Any,
    request_id: str,
    intent_id: str,
    skill_name: str,
    result_status: str,
    result_code: str | None,
    result_digest: str,
    approval_token_ref: str | None,
    ts_ms: int,
    evidence_ref: str | None,
    manifest_ref: str | None = None,
) -> None:
    """Persist the canonical Skill worker receipt and fail on write loss."""

    if ledger is None or not callable(getattr(ledger, "log_skill_exec", None)):
        raise TypeError("governed Skill receipt ledger is required")
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
            "result_digest": (
                result_digest[:4000] if result_digest else None
            ),
            "evidence_ref": (
                evidence_ref or _skill_receipt_ref(request_id, skill_name)
            ),
            "manifest_ref": manifest_ref,
            "ts_ms": int(ts_ms),
        }
    )


def _build_execution_context(
    *,
    ports: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    intent_id: str,
    skill_name: str,
    skill_meta: Any,
    task_text: str,
    user_message: str,
    emotion_context: Mapping[str, Any],
    render_target_lang: str,
    governed_execution_context: Mapping[str, Any],
) -> dict[str, Any]:
    context = {
        "request_id": request_id,
        "session_id": session_id,
        "person_id": person_id,
        "user_message": user_message,
        "task_text": task_text,
        "render_target_lang": render_target_lang,
        "mood_state": (
            normalize_text(emotion_context.get("mood_state")) or "NEUTRAL"
        ),
        "emotion_tag": (
            normalize_text(emotion_context.get("emotion_tag")).upper()
            or "NEUTRAL"
        ),
        "emotion_context": dict(emotion_context),
        "skill_family": str(skill_meta.family or "general"),
        "_governed_execution_context": {
            "execution_mode": "governed_task_runtime",
            **dict(governed_execution_context),
        },
        "capability_broker": ports.capability_broker,
    }
    version_binding = _serialize_skill_version_binding(skill_meta)
    if version_binding is not None:
        context["skill_version_binding"] = version_binding
    if is_agent_runtime_skill(skill_name):
        context["model_attempt_service"] = ports.model_attempt_service
    if (ports.agent_artifact_store is None) != (
        ports.agent_workspace_runtime is None
    ):
        raise PermissionError("AGENT_RUNTIME_WORKSPACE_PORTS_INCOMPLETE")
    if ports.agent_artifact_store is not None:
        context["agent_artifact_store"] = ports.agent_artifact_store
        context["agent_workspace_runtime"] = ports.agent_workspace_runtime
    context["production_authority_verifier"] = (
        ports.production_authority_verifier
    )
    if skill_name == "resource_manager_status":
        context["resource_manager_provider"] = ports.get_resource_manager
    if skill_name.startswith("memory_"):
        context.update(
            build_memory_exec_context(
                ports=ports,
                request_id=request_id,
                session_id=session_id,
                intent_id=intent_id,
                emotion_context=dict(emotion_context),
            )
        )
    return context


def _serialize_skill_version_binding(skill_meta: Any) -> dict[str, Any] | None:
    version_binding = getattr(skill_meta, "version_binding", None)
    if version_binding is None:
        return None
    serialize_binding = getattr(version_binding, "to_dict", None)
    if not callable(serialize_binding):
        raise PermissionError("SKILL_RUNTIME_VERSION_BINDING_INVALID")
    serialized = dict(serialize_binding())
    if not str(serialized.get("bundle_id") or "").strip():
        raise PermissionError("SKILL_RUNTIME_VERSION_BINDING_INVALID")
    return serialized


def _compose_model_budget_runtime(
    *,
    skill_meta: Any,
    model_attempt_service: Any,
    governed_execution_context: Mapping[str, Any],
    request_id: str,
) -> Any | None:
    if not bool(getattr(skill_meta, "model_budget_required", False)):
        return None
    if model_attempt_service is None:
        raise PermissionError("GOVERNED_SKILL_MODEL_ATTEMPT_PORT_REQUIRED")

    from host_brain.model_runtime.governed_budget import (  # noqa: PLC0415
        build_governed_model_budget_guard,
    )
    from host_brain.model_runtime.governed_skill_budget import (  # noqa: PLC0415
        GovernedSkillModelBudgetRuntime,
    )

    guard = build_governed_model_budget_guard(
        budget=governed_execution_context.get("budget"),
        graph_id=str(governed_execution_context.get("graph_id") or ""),
        graph_revision=int(
            governed_execution_context.get("graph_revision") or 0
        ),
        node_id=str(governed_execution_context.get("node_id") or ""),
        attempt_id=str(
            governed_execution_context.get("attempt_id") or ""
        ),
    )
    return GovernedSkillModelBudgetRuntime(
        attempt_service=model_attempt_service,
        model_budget_guard=guard,
        task_request_id=request_id,
    )


def _require_governed_skill_policy(skill_name: str, skill_meta: Any) -> None:
    """Reject catalog-only or internally inconsistent Skill metadata."""

    error = governed_skill_policy_error(skill_name, skill_meta)
    if error is not None:
        raise PermissionError(error)


def _governed_node_grant_error(
    value: Mapping[str, Any],
    *,
    execution_context: Mapping[str, Any],
    request_id: str,
    session_id: str,
    skill_id: str,
    intent_id: str,
) -> str | None:
    grant = dict(value)
    context = dict(execution_context)
    try:
        revision = int(context.get("graph_revision") or 0)
        grant_revision = int(grant.get("graph_revision") or 0)
        expires_at_ms = int(grant.get("expires_at_ms") or 0)
        fencing_token = int(context.get("fencing_token") or 0)
    except (TypeError, ValueError):
        return GOVERNED_NODE_GRANT_INVALID
    expected = {
        "grant_ref": str(context.get("node_grant_ref") or "").strip(),
        "parent_grant_ref": str(
            context.get("parent_grant_ref") or ""
        ).strip(),
        "request_id": request_id,
        "session_id": session_id,
        "principal_id": str(context.get("principal_id") or "").strip(),
        "graph_id": str(context.get("graph_id") or "").strip(),
        "root_work_order_id": str(
            context.get("work_order_id") or ""
        ).strip(),
        "node_id": str(context.get("node_id") or "").strip(),
        "route": "skill",
        "target_id": skill_id,
        "intent_id": intent_id,
        "action_kind": skill_id,
        "token_subject": skill_id,
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
        return GOVERNED_NODE_GRANT_INVALID
    return None


def _outcome(
    *,
    started: float,
    skill_name: str,
    skill_family: str,
    success: bool,
    error_text: str,
    approval_token_ref: str,
    receipt_ref: str,
    fallback_policy: str,
) -> GovernedSkillExecutionOutcome:
    return GovernedSkillExecutionOutcome(
        route_type="skill",
        target_id=skill_name,
        skill_family=skill_family,
        success=success,
        result_text="",
        error_text=error_text,
        latency_ms=int((time.time() - started) * 1000),
        payload={
            "approval_token_ref": approval_token_ref or None,
            "fallback_policy": fallback_policy,
            "receipt_ref": receipt_ref,
        },
    )


def _skill_receipt_ref(request_id: str, skill_name: str) -> str:
    digest = hashlib.sha256(
        (
            f"{str(request_id or '').strip()}\n"
            f"{str(skill_name or '').strip()}"
        ).encode("utf-8")
    ).hexdigest()
    return f"skillreceipt://sha256/{digest}"


__all__ = [
    "GOVERNED_NODE_GRANT_INVALID",
    "GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED",
    "execute_governed_skill",
    "record_governed_skill_execution",
]
