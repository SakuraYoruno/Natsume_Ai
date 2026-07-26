"""Pure contract builders for the governed Host runtime.

This module freezes ingress data and constructs immutable HostCommand,
WorkOrderV3, and TaskGraph contracts.  It intentionally owns no worker,
repository, or application lifecycle state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import re
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetExceededAction,
    BudgetPolicy,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphCompletionPolicy,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.agent_runtime.production_studio import (
    AGENT_PRODUCTION_SKILL_IDS,
)
from host_brain.agent_runtime.governed_production_payload import (
    FROZEN_NODE_EXECUTOR_PAYLOADS_KEY as _FROZEN_NODE_EXECUTOR_PAYLOADS_KEY,
    GOVERNED_PRODUCTION_EXECUTOR_PAYLOAD_SCHEMA as _FROZEN_PRODUCTION_EXECUTOR_PAYLOAD_SCHEMA,
    build_governed_production_executor_payload,
)
from host_brain.orchestration import governor_grant_ref
from host_brain.app.governed_task_authority import (
    GOVERNED_HOST_AUTHORITY_INVALID,
    GovernedHostAdapterError,
    require_fail_closed_direct_skill_work_order as _require_fail_closed_direct_skill_work_order,
)
from natsume_contracts.task import HostCommand, HostCommandSurface, build_host_command


GOVERNED_HOST_WORKER_ROUTE = "skill"

_FORBIDDEN_DURABLE_KEYS = re.compile(
    r"(?:^|_)(?:api_?key|authorization|bearer|credential|password|raw_?body|"
    r"secret|signature|token)(?:$|_)",
    re.IGNORECASE,
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_MAX_DURABLE_TEXT = 32_000
_ACCEPTANCE_CRITERION = "governed worker returned a durable accepted projection"
_DIRECT_SKILL_BUDGET_LIMITS = BudgetAmount(
    token_count=16_384,
    tool_call_count=8,
    time_ms=120_000,
    concurrency=1,
    retry_count=1,
    model_cost_micros=500_000,
)
def _build_frozen_context(
    *,
    request_body: Any,
    surface_user_message: str,
    emotion_context: Mapping[str, Any] | None,
    emotion_snapshot: Mapping[str, Any] | None,
    bond_summary: Mapping[str, Any] | None,
    retrieval_evidence_items: Sequence[Mapping[str, Any]],
    retrieval_summary: Mapping[str, Any] | None,
    llm_runtime: Mapping[str, Any] | None,
    pii_ingress_stats: Mapping[str, Any] | None,
    motive_state: Mapping[str, Any] | None,
    render_target_lang: str,
) -> dict[str, Any]:
    attachments = [
        dict(item)
        for item in tuple(getattr(request_body, "attachments", ()) or ())
        if isinstance(item, Mapping)
    ]
    frozen = {
        "surface_user_message": str(surface_user_message or "")[:_MAX_DURABLE_TEXT],
        "prepared_emotion_context": dict(emotion_context or {}),
        "emotion_snapshot": dict(emotion_snapshot or {}),
        "bond_summary": dict(bond_summary or {}),
        "retrieval_evidence_items": [
            dict(item) for item in tuple(retrieval_evidence_items or ())
        ],
        "retrieval_summary": dict(retrieval_summary or {}),
        "llm_runtime": dict(llm_runtime or {}),
        "pii_ingress_stats": dict(pii_ingress_stats or {}),
        "motive_state": dict(motive_state or {}),
        "prepared_render_target_lang": str(render_target_lang or "zh")[:16],
        "request_body": {
            "input_source": str(getattr(request_body, "input_source", "") or "").strip(),
            "response_form": str(getattr(request_body, "response_form", "") or "").strip(),
            "render_policy": dict(getattr(request_body, "render_policy", {}) or {}),
            "attachments": attachments,
            "multimodal_observations": dict(
                getattr(request_body, "multimodal_observations", {}) or {}
            ),
            "attachment_summary": str(
                getattr(request_body, "attachment_summary", "") or ""
            ).strip(),
        },
        "delivery_context": {
            "reply_channel": str(getattr(request_body, "reply_channel", "") or "").strip(),
            "reply_target": str(getattr(request_body, "reply_target", "") or "").strip(),
        },
    }
    safe = _safe_durable_value(frozen, path="frozen_context")
    if not isinstance(safe, dict):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "frozen Host context is not a durable mapping",
        )
    return safe


def _ingress_references(
    authority: Mapping[str, Any] | None,
) -> tuple[str | None, str | None]:
    source = dict(authority or {})
    claim_ref = str(
        source.get("ingress_claim_ref") or source.get("claim_ref") or ""
    ).strip() or None
    audit_ref = str(
        source.get("ingress_audit_ref") or source.get("audit_ref") or ""
    ).strip() or None
    if bool(claim_ref) != bool(audit_ref):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "Secure Ingress claim and audit references must be supplied together",
        )
    return claim_ref, audit_ref


def _resolve_host_command_surface(
    *,
    request_body: Any,
    authority: Mapping[str, Any] | None,
) -> HostCommandSurface:
    claim_ref, _audit_ref = _ingress_references(authority)
    if not claim_ref:
        return HostCommandSurface.CHAT
    if isinstance(request_body, Mapping):
        raw_source = request_body.get("input_source") or request_body.get("source")
    else:
        raw_source = getattr(request_body, "input_source", None)
    source = str(raw_source or "").strip().upper()
    if source == "TOOL":
        return HostCommandSurface.TOOL
    if source == "OPERATOR":
        return HostCommandSurface.OPERATOR
    if source == "SCHEDULER":
        return HostCommandSurface.SCHEDULER
    return (
        HostCommandSurface.GATEWAY
        if source == "GATEWAY"
        else HostCommandSurface.HOST_INPUT
    )


def _resolve_host_command_principal(
    *,
    person_id: str,
    authority: Mapping[str, Any] | None,
) -> str:
    claim_ref, _audit_ref = _ingress_references(authority)
    if claim_ref:
        return _required_identifier(
            dict(authority or {}).get("principal_id"),
            field_name="principal_id",
        )
    return _identifier_or_digest(person_id or "anonymous", prefix="principal")


def _resolve_host_input_sha256(
    *,
    sanitized_message: str,
    authority: Mapping[str, Any] | None,
) -> str:
    raw = str(
        dict(authority or {}).get("input_sha256")
        or dict(authority or {}).get("body_sha256")
        or ""
    ).strip().lower()
    if not raw:
        return _sha256_text(sanitized_message)
    digest = raw.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "Secure Ingress body digest must be a sha256 hex digest",
        )
    return f"sha256:{digest}"


def _require_existing_plan_replay(
    record: Mapping[str, Any],
    *,
    request_body: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    sanitized_message: str,
    governor_result: Mapping[str, Any],
    command_authority: Mapping[str, Any] | None,
    expected_revision: int,
) -> None:
    durable = dict(record or {})
    command = durable.get("command")
    work_order = durable.get("work_order")
    assignment = durable.get("assignment")
    graph = durable.get("graph")
    authority = dict(command_authority or {})
    expected_surface = _resolve_host_command_surface(
        request_body=request_body,
        authority=authority,
    )
    expected = {
        "surface": expected_surface,
        "request_id": str(request_id or "").strip(),
        "session_id": _identifier_or_digest(session_id, prefix="session"),
        "principal_id": _resolve_host_command_principal(
            person_id=person_id,
            authority=authority,
        ),
        "input_sha256": _resolve_host_input_sha256(
            sanitized_message=sanitized_message,
            authority=authority,
        ),
        "governor_grant_ref": governor_grant_ref(
            str(request_id or "").split(".graph-revision-", 1)[0],
            governor_result,
        ),
        "graph_revision": int(expected_revision),
        "route_type": "plan",
        "target_id": "planning_search",
    }
    if (
        not isinstance(command, HostCommand)
        or not isinstance(work_order, WorkOrderV3)
        or not isinstance(graph, TaskGraph)
        or assignment is None
        or any(getattr(command, key) != value for key, value in expected.items())
        or command.graph_id != graph.graph_id
        or command.graph_revision != graph.revision
        or work_order.task_graph_id != graph.graph_id
        or work_order.graph_revision != graph.revision
        or work_order.approval_ref != command.governor_grant_ref
    ):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "durable planning replay differs from current ingress or authority",
        )
    work_order.validate()
    graph.validate()


def _build_contracts(
    *,
    request_body: Any,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: Mapping[str, Any],
    governor_result: Mapping[str, Any],
    person_id: str,
    sanitized_message: str,
    emotion_context: Mapping[str, Any] | None,
    render_target_lang: str,
    command_authority: Mapping[str, Any] | None,
    frozen_context: Mapping[str, Any] | None = None,
    skill_risk_level: int = 1,
    skill_requires_durable_review: bool = False,
) -> tuple[HostCommand, WorkOrderV3, TaskGraph]:
    authority = dict(command_authority or {})
    ingress_claim_ref, ingress_audit_ref = _ingress_references(authority)
    surface = _resolve_host_command_surface(
        request_body=request_body,
        authority=authority,
    )
    request_ref = _required_identifier(request_id, field_name="request_id")
    session_ref = _identifier_or_digest(session_id, prefix="session")
    target = str(intent_draft.get("target_id") or intent_draft.get("skill_id") or "").strip()
    target_ref = _required_identifier(target, field_name="target_id")
    identity_digest = _sha256_text(f"{surface.value}\n{request_ref}")
    suffix = identity_digest.split(":", 1)[1][:40]
    work_order_id = f"wo-host-{suffix}"
    graph_id = f"graph-host-{suffix}"
    node_id = f"node-host-{suffix}"

    input_sha256 = _resolve_host_input_sha256(
        sanitized_message=str(sanitized_message or ""),
        authority=authority,
    )
    input_ref = f"input://sha256/{input_sha256.split(':', 1)[1]}"
    principal_id = _resolve_host_command_principal(
        person_id=person_id,
        authority=authority,
    )
    grant_ref = governor_grant_ref(request_ref, governor_result)
    safe_intent = _safe_durable_value(dict(intent_draft or {}), path="intent_draft")
    if not isinstance(safe_intent, dict):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "intent_draft is not a durable mapping",
        )
    safe_intent["route"] = "skill"
    safe_intent["target_id"] = target_ref
    objective = str(sanitized_message or intent_draft.get("task") or target_ref).strip()
    objective = objective[:_MAX_DURABLE_TEXT] or f"execute skill {target_ref}"
    operation_fingerprint = _sha256_json(
        {
            "request_id": request_ref,
            "input_sha256": input_sha256,
            "principal_id": principal_id,
            "route_type": "skill",
            "target_id": target_ref,
            "args": safe_intent.get("args") if isinstance(safe_intent.get("args"), dict) else {},
        }
    )
    business_scope = f"host-skill:{target_ref}"
    try:
        canonical_risk_level = int(skill_risk_level)
    except (TypeError, ValueError) as exc:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "Skill risk level is invalid",
        ) from exc
    if not 1 <= canonical_risk_level <= 5:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "Skill risk level must be between 1 and 5",
        )
    durable_review_required = bool(skill_requires_durable_review)
    review_ref = (
        f"review-policy://task-node/{suffix}"
        if durable_review_required
        else f"review-waiver://governor-grant/{suffix}"
    )
    output_contract = {
        "type": "host_skill_commit",
        "required": ["decision", "narrative", "receipt_ref"],
    }
    capability_requirements = (target_ref,)
    budget_policy = BudgetPolicy(
        scope=f"work-order:{work_order_id}",
        limits=_DIRECT_SKILL_BUDGET_LIMITS,
        on_exceeded=BudgetExceededAction.BLOCK,
        metadata={
            "policy": "governed_direct_skill_default",
            "policy_version": "governed_direct_skill_budget.v1",
            "route_type": "skill",
            "fail_closed": True,
            "enforcement_scope": "direct_skill_worker_preflight",
        },
    )
    budget_policy.validate()
    safe_frozen_context = _safe_durable_value(
        dict(frozen_context or {}),
        path="frozen_context",
    )
    if not isinstance(safe_frozen_context, dict):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "frozen_context is not a durable mapping",
        )
    input_pack: dict[str, Any] = {
        **safe_frozen_context,
        "input_ref": input_ref,
        "user_message": objective,
        "task_text": str(intent_draft.get("task") or objective)[:_MAX_DURABLE_TEXT],
        "person_id": str(person_id or principal_id)[:255],
        "intent_id": str(governor_result.get("intent_id") or intent_draft.get("id") or "")[:255],
        "intent_draft": safe_intent,
        "emotion_context": _safe_durable_value(dict(emotion_context or {}), path="emotion_context"),
        "render_target_lang": str(render_target_lang or "zh")[:16],
    }
    if target_ref in AGENT_PRODUCTION_SKILL_IDS:
        input_pack[_FROZEN_NODE_EXECUTOR_PAYLOADS_KEY] = {
            node_id: build_governed_production_executor_payload(
                target_id=target_ref,
                intent_args=(
                    dict(safe_intent.get("args") or {})
                    if isinstance(safe_intent.get("args"), Mapping)
                    else {}
                ),
                request_id=request_ref,
                session_id=session_ref,
                person_id=str(person_id or principal_id)[:255],
                user_message=objective,
                work_order_id=work_order_id,
                graph_id=graph_id,
                node_id=node_id,
                graph_revision=1,
                business_scope=business_scope,
                operation_fingerprint=operation_fingerprint,
                approval_ref=grant_ref,
                review_ref=review_ref,
            )
        }
    work_order = WorkOrderV3(
        work_order_id=work_order_id,
        domain="skill",
        run_mode="worker",
        objective=objective,
        input_pack=input_pack,
        allowed_actions=(target_ref,),
        forbidden_actions=("reroute", "replan", "cross_executor_resume"),
        acceptance_criteria=(_ACCEPTANCE_CRITERION,),
        verification_plan={"required": True, "policy": "durable_worker_projection"},
        approval_policy={
            "required": True,
            "governor_grant_ref": grant_ref,
            "on_missing": "BLOCK",
        },
        review_policy=(
            {
                "review_required": True,
                "durable_review_required": True,
                "mode": "task_node_pre_execution_review",
                "reason": "skill_requires_durable_confirmation",
                "review_ref": review_ref,
            }
            if durable_review_required
            else {
                "review_required": False,
                "mode": "governor_grant_review_waiver",
                "reason": "separate_review_waived_by_approved_governor_grant",
                "waiver_authority": "governor_grant",
                "review_ref": review_ref,
            }
        ),
        metadata={
            "surface": surface.value,
            "graph_id": graph_id,
            "graph_revision": 1,
            "operation_fingerprint": operation_fingerprint,
            "planning_lineage": {
                "graph_id": graph_id,
                "root_work_order_id": work_order_id,
                "revision": 1,
                "parent_revision": None,
                "approval_ref": grant_ref,
                "review_ref": review_ref,
                "node_bindings": [
                    {
                        "node_id": node_id,
                        "objective": objective,
                        "worker_route": "skill",
                        "target_id": target_ref,
                    }
                ],
            },
            "lineage": {
                "root_work_order_id": work_order_id,
                "task_graph_id": graph_id,
                "task_node_id": node_id,
                "graph_revision": 1,
                "attempt": 0,
                "capability_requirements": list(capability_requirements),
                "approval_ref": grant_ref,
                "review_ref": review_ref,
                "business_idempotency_scope": business_scope,
                "operation_fingerprint": operation_fingerprint,
            },
        },
        root_work_order_id=work_order_id,
        parent_work_order_id=None,
        task_graph_id=graph_id,
        task_node_id=node_id,
        graph_revision=1,
        attempt=0,
        capability_requirements=capability_requirements,
        risk_level=canonical_risk_level,
        input_refs=(input_ref, grant_ref),
        output_contract=output_contract,
        budget_policy=budget_policy,
        business_idempotency_scope=business_scope,
        operation_fingerprint=operation_fingerprint,
        status_version=0,
        approval_ref=grant_ref,
        review_ref=review_ref,
    )
    work_order.validate()
    _require_fail_closed_direct_skill_work_order(work_order)
    node = TaskNode(
        node_id=node_id,
        dependencies=(),
        route=f"{GOVERNED_HOST_WORKER_ROUTE}:{target_ref}",
        input_refs=(input_ref, grant_ref),
        output_contract=output_contract,
        retry_policy={"max_attempts": 1, "retry_backoff_ms": 0},
        risk_level=canonical_risk_level,
        acceptance_criteria=(_ACCEPTANCE_CRITERION,),
        status=TaskNodeStatus.READY,
        status_version=0,
    )
    graph = TaskGraph(
        graph_id=graph_id,
        root_work_order_id=work_order_id,
        revision=1,
        planner_ref=f"planner://governed-host/{suffix}",
        status=TaskGraphStatus.CREATED,
        created_at=int(ts_ms),
        result_node_id=node_id,
        completion_policy=TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1,
        nodes=(node,),
    )
    command = build_host_command(
        surface=surface,
        request_id=request_ref,
        session_id=session_ref,
        principal_id=principal_id,
        ingress_claim_ref=ingress_claim_ref,
        ingress_audit_ref=ingress_audit_ref,
        governor_grant_ref=grant_ref,
        input_ref=input_ref,
        input_sha256=input_sha256,
        root_work_order_id=work_order_id,
        graph_id=graph_id,
        graph_revision=1,
        route_type="skill",
        target_id=target_ref,
        business_scope=business_scope,
        operation_fingerprint=operation_fingerprint,
        created_at_ms=int(ts_ms),
    )
    return command, work_order, graph


def _safe_durable_value(value: Any, *, path: str) -> Any:
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key or "").strip()
            if not key or _FORBIDDEN_DURABLE_KEYS.search(key):
                continue
            safe[key] = _safe_durable_value(raw_value, path=f"{path}.{key}")
        return safe
    if isinstance(value, (list, tuple)):
        return [_safe_durable_value(item, path=f"{path}[]") for item in value[:256]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:_MAX_DURABLE_TEXT]
    return str(value)[:1_000]


def _required_identifier(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(text):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            f"{field_name} is not a bounded identifier",
        )
    return text


def _identifier_or_digest(value: Any, *, prefix: str) -> str:
    text = str(value or "").strip()
    if _IDENTIFIER.fullmatch(text):
        return text
    return f"{prefix}-{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:40]}"


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _sha256_json(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        deepcopy(dict(value)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return _sha256_text(payload)


__all__ = [
    "GOVERNED_HOST_WORKER_ROUTE",
]
