"""Canonical one-node Host contracts for direct chat and delegate routes.

Complex plans are intentionally absent: they must enter through
``PlanningSearchService`` and ``PlanningGraphCompiler``.  This module only
freezes an already-committed direct route into the same WorkOrderV3/TaskGraph
contracts consumed by the governed runtime.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any
from urllib.parse import quote

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetExceededAction,
    BudgetPolicy,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
    validate_task_graph_admission_state,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.orchestration.governor_grant import governor_grant_ref
from natsume_contracts.task import HostCommand, HostCommandSurface, build_host_command


HOST_ROUTE_CONTRACT_INVALID = "HOST_ROUTE_CONTRACT_INVALID"
HOST_ROUTE_AUTHORITY_INVALID = "HOST_ROUTE_AUTHORITY_INVALID"
HOST_ROUTE_UNSUPPORTED = "HOST_ROUTE_UNSUPPORTED"

_SUPPORTED = frozenset({"chat", "delegate"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:api_?key|authorization|bearer|cookie|credential|password|"
    r"private_?key|raw_?auth|raw_?body|secret|signature|token)(?:$|_)",
    re.IGNORECASE,
)
_MAX_TEXT = 32_000
_BUDGETS = {
    "chat": BudgetAmount(
        token_count=16_384,
        tool_call_count=8,
        time_ms=120_000,
        concurrency=1,
        retry_count=1,
        model_cost_micros=500_000,
    ),
    "delegate": BudgetAmount(
        token_count=32_768,
        tool_call_count=32,
        time_ms=300_000,
        concurrency=1,
        retry_count=1,
        model_cost_micros=1_000_000,
    ),
}


class HostRouteContractError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or HOST_ROUTE_CONTRACT_INVALID)
        self.detail = str(detail or "Host route contract compilation failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class HostRouteContracts:
    command: HostCommand
    work_order: WorkOrderV3
    graph: TaskGraph

    def validate(self) -> None:
        self.work_order.validate()
        validate_task_graph_admission_state(self.graph)
        node = self.graph.nodes[0]
        if (
            self.command.root_work_order_id != self.work_order.root_work_order_id
            or self.command.root_work_order_id != self.graph.root_work_order_id
            or self.command.graph_id != self.graph.graph_id
            or self.command.graph_revision != self.graph.revision
            or self.work_order.task_graph_id != self.graph.graph_id
            or self.work_order.task_node_id != node.node_id
            or self.command.governor_grant_ref != self.work_order.approval_ref
        ):
            raise HostRouteContractError(
                HOST_ROUTE_CONTRACT_INVALID,
                "Host command/work-order/graph lineage differs",
            )


def build_direct_host_route_contracts(
    *,
    surface: HostCommandSurface,
    request_id: str,
    session_id: str,
    principal_id: str,
    ts_ms: int,
    intent_draft: Mapping[str, Any],
    governor_result: Mapping[str, Any],
    sanitized_message: str,
    person_id: str,
    emotion_context: Mapping[str, Any] | None = None,
    render_target_lang: str = "zh",
    input_sha256: str | None = None,
    ingress_claim_ref: str | None = None,
    ingress_audit_ref: str | None = None,
    frozen_context: Mapping[str, Any] | None = None,
) -> HostRouteContracts:
    if not isinstance(surface, HostCommandSurface):
        raise HostRouteContractError(HOST_ROUTE_CONTRACT_INVALID, "surface must be HostCommandSurface")
    request = _identifier(request_id, "request_id")
    session = _identifier_or_hash(session_id, "session")
    principal = _identifier_or_hash(principal_id, "principal")
    if bool(_text(ingress_claim_ref)) != bool(_text(ingress_audit_ref)):
        raise HostRouteContractError(
            HOST_ROUTE_AUTHORITY_INVALID,
            "ingress claim and audit references must be supplied together",
        )
    governor = dict(governor_result or {})
    if _text(governor.get("decision")).upper() != "APPROVE":
        raise HostRouteContractError(
            HOST_ROUTE_AUTHORITY_INVALID,
            "an approved Governor result is required",
        )
    route = _text(intent_draft.get("route")).lower()
    if route not in _SUPPORTED:
        raise HostRouteContractError(
            HOST_ROUTE_UNSUPPORTED,
            "direct contract builder accepts only chat or delegate",
        )
    target_id = (
        "surface_reply"
        if route == "chat"
        else _text(intent_draft.get("target_id") or intent_draft.get("agent_id"))
    )
    target = _identifier(target_id, "target_id")
    message = _text(sanitized_message)[:_MAX_TEXT]
    if not message:
        raise HostRouteContractError(HOST_ROUTE_CONTRACT_INVALID, "sanitized_message is required")
    grant_ref = governor_grant_ref(request, governor)
    if not grant_ref:
        raise HostRouteContractError(HOST_ROUTE_AUTHORITY_INVALID, "Governor grant ref is empty")

    digest = _sha256_json(
        {
            "surface": surface.value,
            "request_id": request,
            "route": route,
            "target_id": target,
            "principal_id": principal,
        }
    )
    suffix = digest[:40]
    root_id = f"wo-host-{suffix}"
    graph_id = f"graph-host-{suffix}"
    node_id = f"node-host-{suffix}"
    raw_input_digest = _text(input_sha256).lower()
    if raw_input_digest.startswith("sha256:"):
        raw_input_digest = raw_input_digest.split(":", 1)[1]
    if not re.fullmatch(r"[0-9a-f]{64}", raw_input_digest):
        raw_input_digest = hashlib.sha256(message.encode("utf-8")).hexdigest()
    input_digest = f"sha256:{raw_input_digest}"
    input_ref = f"input://sha256/{raw_input_digest}"
    business_scope = f"host-{route}:{target}"
    operation_fingerprint = "sha256:" + _sha256_json(
        {
            # Idempotency belongs to one canonical Host request.  Omitting the
            # request identity incorrectly deduplicates two independent chat
            # turns (or delegate commands) that happen to carry equal text.
            "request_id": request,
            "input_sha256": input_digest,
            "principal_id": principal,
            "route": route,
            "target_id": target,
            "safe_args": _safe_json(dict(intent_draft.get("args") or {}), path="intent.args"),
        }
    )
    review_ref = f"review-waiver://governor-grant/{suffix}"
    objective = _text(intent_draft.get("task") or message)[:_MAX_TEXT]
    node_binding = {
        "candidate_id": f"direct-{route}-{suffix}",
        "node_id": node_id,
        "objective": objective,
        "worker_route": route,
        "target_id": target,
    }
    budget = BudgetPolicy(
        scope=f"work-order:{root_id}",
        limits=_BUDGETS[route],
        on_exceeded=BudgetExceededAction.BLOCK,
        metadata={
            "policy": f"governed_direct_{route}_default",
            "policy_version": f"governed_direct_{route}_budget.v1",
            "fail_closed": True,
        },
    )
    budget.validate()
    output_contract = {
        "type": f"host_{route}_commit",
        "required": ["decision", "narrative", "receipt_ref"],
    }
    intent_projection = {
        "route": route,
        "target_id": target,
        "task": objective,
        "action": _identifier(
            intent_draft.get("action") or intent_draft.get("action_kind"),
            "intent.action",
        ),
        "target": _identifier(
            intent_draft.get("target") or intent_draft.get("scope"),
            "intent.target",
        ).lower(),
        "args": _safe_json(dict(intent_draft.get("args") or {}), path="intent.args"),
    }
    frozen = _safe_json(dict(frozen_context or {}), path="frozen_context")
    if not isinstance(frozen, Mapping):
        raise HostRouteContractError(
            HOST_ROUTE_CONTRACT_INVALID,
            "frozen_context must be a mapping",
        )
    reserved_frozen_keys = {
        "input_ref",
        "user_message",
        "task_text",
        "person_id",
        "intent_id",
        "intent_draft",
        "emotion_context",
        "render_target_lang",
    }
    overlap = sorted(reserved_frozen_keys.intersection(frozen))
    if overlap:
        raise HostRouteContractError(
            HOST_ROUTE_AUTHORITY_INVALID,
            "frozen_context cannot override canonical fields: " + ",".join(overlap),
        )
    work_order = WorkOrderV3(
        work_order_id=root_id,
        domain="chat" if route == "chat" else _delegate_domain(intent_draft, target),
        run_mode="worker",
        objective=objective,
        input_pack={
            **dict(frozen),
            "input_ref": input_ref,
            "user_message": message,
            "task_text": objective,
            "person_id": _text(person_id or principal)[:255],
            "intent_id": _text(governor.get("intent_id") or intent_draft.get("id"))[:255],
            "intent_draft": intent_projection,
            "emotion_context": _safe_json(dict(emotion_context or {}), path="emotion_context"),
            "render_target_lang": _text(render_target_lang or "zh")[:16],
        },
        allowed_actions=(("render_reply",) if route == "chat" else (f"delegate:{target}",)),
        forbidden_actions=("reroute", "replan", "cross_executor_resume"),
        acceptance_criteria=("frozen worker returned a durable accepted projection",),
        verification_plan={"required": True, "policy": "durable_worker_projection"},
        approval_policy={
            "required": True,
            "governor_grant_ref": grant_ref,
            "on_missing": "BLOCK",
        },
        review_policy={
            "review_required": False,
            "durable_review_required": False,
            "mode": "governor_grant_review_waiver",
            "reason": "explicit_low_risk_direct_route_waiver",
            "waiver_authority": "governor_grant",
            "waiver_ref": review_ref,
        },
        metadata={
            "surface": surface.value,
            "direct_route": True,
            "planning_lineage": {
                "graph_id": graph_id,
                "root_work_order_id": root_id,
                "revision": 1,
                "parent_revision": None,
                "approval_ref": grant_ref,
                "review_ref": review_ref,
                "node_bindings": [node_binding],
            },
        },
        root_work_order_id=root_id,
        task_graph_id=graph_id,
        task_node_id=node_id,
        graph_revision=1,
        capability_requirements=(("surface_reply",) if route == "chat" else (target,)),
        risk_level=max(1, min(5, int(governor.get("risk_level") or 1))),
        input_refs=(input_ref, grant_ref),
        output_contract=output_contract,
        budget_policy=budget,
        business_idempotency_scope=business_scope,
        operation_fingerprint=operation_fingerprint,
        approval_ref=grant_ref,
        review_ref=review_ref,
    )
    node = TaskNode(
        node_id=node_id,
        dependencies=(),
        route=f"{route}:{quote(target, safe='')}",
        input_refs=(input_ref, grant_ref),
        output_contract=output_contract,
        retry_policy={"max_attempts": 2, "retry_backoff_ms": 0},
        risk_level=work_order.risk_level,
        acceptance_criteria=work_order.acceptance_criteria,
        status=TaskNodeStatus.READY,
    )
    graph = TaskGraph(
        graph_id=graph_id,
        root_work_order_id=root_id,
        revision=1,
        planner_ref=f"planner://direct-host-route/{route}/{suffix}",
        status=TaskGraphStatus.CREATED,
        created_at=int(ts_ms),
        result_node_id=node_id,
        nodes=(node,),
    )
    command = build_host_command(
        surface=surface,
        request_id=request,
        session_id=session,
        principal_id=principal,
        ingress_claim_ref=_text(ingress_claim_ref) or None,
        ingress_audit_ref=_text(ingress_audit_ref) or None,
        governor_grant_ref=grant_ref,
        input_ref=input_ref,
        input_sha256=input_digest,
        root_work_order_id=root_id,
        graph_id=graph_id,
        graph_revision=1,
        route_type=route,
        target_id=target,
        business_scope=business_scope,
        operation_fingerprint=operation_fingerprint,
        created_at_ms=int(ts_ms),
    )
    contracts = HostRouteContracts(command=command, work_order=work_order, graph=graph)
    contracts.validate()
    return contracts


def _delegate_domain(intent: Mapping[str, Any], target: str) -> str:
    candidate = _text(intent.get("domain")).lower()
    supported = {
        "code",
        "finance",
        "translation",
        "creative",
        "research",
        "memory",
        "bio_ops",
        "immune",
        "evolution",
        "organ_ops",
        "embodiment",
    }
    if candidate in supported:
        return candidate
    target_lower = target.lower()
    if "code" in target_lower:
        return "code"
    if "research" in target_lower or "intent" in target_lower:
        return "research"
    return "research"


def _safe_json(value: Any, *, path: str) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            if _SENSITIVE_KEY.search(key):
                raise HostRouteContractError(
                    HOST_ROUTE_AUTHORITY_INVALID,
                    f"sensitive key is forbidden in durable input: {path}.{key}",
                )
            result[key] = _safe_json(child, path=f"{path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_json(child, path=f"{path}[]") for child in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise HostRouteContractError(HOST_ROUTE_CONTRACT_INVALID, f"{path} is not JSON-safe") from exc
        return value
    raise HostRouteContractError(HOST_ROUTE_CONTRACT_INVALID, f"{path} is not JSON-safe")


def _identifier(value: Any, field_name: str) -> str:
    text = _text(value)
    if not _IDENTIFIER.fullmatch(text):
        raise HostRouteContractError(HOST_ROUTE_CONTRACT_INVALID, f"{field_name} is invalid")
    return text


def _identifier_or_hash(value: Any, prefix: str) -> str:
    text = _text(value)
    return text if _IDENTIFIER.fullmatch(text) else f"{prefix}-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:40]}"


def _sha256_json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


__all__ = [
    "HOST_ROUTE_AUTHORITY_INVALID",
    "HOST_ROUTE_CONTRACT_INVALID",
    "HOST_ROUTE_UNSUPPORTED",
    "HostRouteContractError",
    "HostRouteContracts",
    "build_direct_host_route_contracts",
]
