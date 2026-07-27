"""Canonical contracts and stable identities for Internal Task admission."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import re
from typing import Any

from host_brain.agent_runtime.workspace_runtime import DurableWorkspaceRuntime
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetExceededAction,
    BudgetPolicy,
)
from host_brain.contracts.agent_runtime.control_route_execution_policy import (
    CONTROL_ROUTE_BUDGET_MODE,
    CONTROL_ROUTE_EXECUTION_POLICY_KEY,
    freeze_control_route_execution_policy,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphCompletionPolicy,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder, WorkOrderV3
from natsume_contracts.task import HostCommand, HostCommandSurface, build_host_command


INTERNAL_RUNTIME_INACTIVE = "INTERNAL_RUNTIME_INACTIVE"
INTERNAL_RUNTIME_AUTHORITY_INVALID = "INTERNAL_RUNTIME_AUTHORITY_INVALID"
INTERNAL_RUNTIME_GOVERNOR_GRANT_MISSING = "INTERNAL_RUNTIME_GOVERNOR_GRANT_MISSING"
INTERNAL_RUNTIME_DRAIN_TIMEOUT = "INTERNAL_RUNTIME_DRAIN_TIMEOUT"
INTERNAL_RUNTIME_GOVERNOR_REJECTED = "INTERNAL_RUNTIME_GOVERNOR_REJECTED"
INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED = (
    "INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED"
)
INTERNAL_RUNTIME_PRE_ADMITTED_WORK_ORDER_FORBIDDEN = (
    "INTERNAL_RUNTIME_PRE_ADMITTED_WORK_ORDER_FORBIDDEN"
)
INTERNAL_RUNTIME_STARTUP_CONTINUATION_DEFERRED = (
    "INTERNAL_RUNTIME_STARTUP_CONTINUATION_DEFERRED"
)
INTERNAL_AGENT_WORKER_ROUTE = "delegate"
INTERNAL_TASK_NODE_ROUTE = "delegate:agent_runtime"

_INTERNAL_AGENT_BUDGET_LIMITS = BudgetAmount(
    token_count=65_536,
    tool_call_count=64,
    time_ms=300_000,
    concurrency=1,
    retry_count=8,
    model_cost_micros=2_000_000,
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")


class GovernedInternalRuntimeError(RuntimeError):
    """Stable fail-closed error exposed by the Internal governed runtime."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or INTERNAL_RUNTIME_AUTHORITY_INVALID)
        self.detail = str(detail or "governed internal runtime failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


def build_internal_task_contracts(
    *,
    work_order: AgentWorkOrder,
    request_id: str,
    session_id: str,
    ts_ms: int,
    governor_grant_ref: str,
    command_authority: Mapping[str, Any],
    frozen_runtime_context: Mapping[str, Any],
) -> tuple[HostCommand, WorkOrderV3, TaskGraph]:
    """Build the sole HostCommand, WorkOrderV3, and TaskGraph admission set."""

    authority = dict(command_authority or {})
    principal_id = required_identifier(
        authority.get("principal_id"),
        field_name="principal_id",
    )
    claim_ref = _required_reference(
        authority.get("ingress_claim_ref") or authority.get("claim_ref"),
        field_name="ingress_claim_ref",
    )
    audit_ref = _required_reference(
        authority.get("ingress_audit_ref") or authority.get("audit_ref"),
        field_name="ingress_audit_ref",
    )
    grant_ref = _required_reference(
        governor_grant_ref,
        field_name="governor_grant_ref",
    )
    request_ref = required_identifier(request_id, field_name="request_id")
    session_ref = identifier_or_digest(session_id, prefix="internal-session")
    input_sha = str(
        authority.get("input_sha256") or authority.get("body_sha256") or ""
    ).strip().lower()
    if input_sha.startswith("sha256:"):
        input_sha = input_sha.split(":", 1)[1]
    if not re.fullmatch(r"[0-9a-f]{64}", input_sha):
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            "secure ingress body_sha256 is missing or invalid",
        )
    canonical_input_sha = f"sha256:{input_sha}"
    input_ref = f"internal-input://sha256/{input_sha}"
    suffix = hashlib.sha256(
        f"internal\n{request_ref}".encode("utf-8")
    ).hexdigest()[:40]
    graph_id = f"graph-internal-{suffix}"
    node_id = f"node-internal-{suffix}"
    risk_level = max(
        1,
        min(5, int(dict(work_order.metadata or {}).get("risk_level") or 1)),
    )
    business_scope = f"internal-agent:{_scope_token(work_order.domain)}"
    operation_fingerprint = _sha256_json(
        {
            "principal_id": principal_id,
            "input_sha256": canonical_input_sha,
            "work_order": work_order.to_dict(),
        }
    )
    output_contract = {
        "type": "internal_agent_result_proposal",
        "required": ["acceptance_effect_intents", "receipt_ref"],
    }
    review_policy, review_ref = _canonical_review_policy(
        work_order,
        suffix=suffix,
    )
    control_policy = freeze_control_route_execution_policy(
        surface=HostCommandSurface.INTERNAL,
        control_command_id="internal.agent.execute",
        target_id="agent_runtime",
        review_required=bool(review_policy.get("review_required")),
    )
    approval_policy = {
        **dict(work_order.approval_policy or {}),
        "required": True,
        "governor_grant_ref": grant_ref,
        "on_missing": "BLOCK",
    }
    budget_policy = BudgetPolicy(
        scope=f"work-order:{work_order.work_order_id}",
        limits=_INTERNAL_AGENT_BUDGET_LIMITS,
        on_exceeded=BudgetExceededAction.BLOCK,
        metadata={
            "policy": "governed_internal_agent_default",
            "policy_version": "governed_internal_agent_budget.v1",
            "route_type": "delegate",
            "fail_closed": True,
            "source": "internal_task_runtime_composition",
            "measurement_mode": CONTROL_ROUTE_BUDGET_MODE,
            "control_policy_ref": control_policy["policy_ref"],
        },
    )
    budget_policy.validate()
    context_payload = dict(frozen_runtime_context or {})
    overlap = sorted(set(work_order.input_pack).intersection(context_payload))
    if overlap:
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            "caller Internal input overlaps frozen Runtime Context: "
            + ",".join(overlap),
        )
    canonical_order = WorkOrderV3(
        work_order_id=work_order.work_order_id,
        domain=work_order.domain,
        run_mode=work_order.run_mode,
        objective=work_order.objective,
        workspace=(
            {
                "strategy": "durable_workspace",
                "workspace_id": DurableWorkspaceRuntime.workspace_id_for(
                    graph_id=graph_id,
                    graph_revision=1,
                ),
            }
            if work_order.requires_write
            else deepcopy(dict(work_order.workspace or {}))
        ),
        input_pack={
            **deepcopy(dict(work_order.input_pack or {})),
            **deepcopy(context_payload),
            CONTROL_ROUTE_EXECUTION_POLICY_KEY: deepcopy(control_policy),
        },
        allowed_actions=tuple(work_order.allowed_actions or ()),
        forbidden_actions=tuple(work_order.forbidden_actions or ()),
        acceptance_criteria=tuple(
            work_order.acceptance_criteria or ("result executed",)
        ),
        verification_plan=deepcopy(dict(work_order.verification_plan or {})),
        approval_policy=approval_policy,
        review_policy=review_policy,
        metadata={
            **deepcopy(dict(work_order.metadata or {})),
            "surface": HostCommandSurface.INTERNAL.value,
            "control_command_id": "internal.agent.execute",
            CONTROL_ROUTE_EXECUTION_POLICY_KEY: deepcopy(control_policy),
            "planning_lineage": {
                "graph_id": graph_id,
                "root_work_order_id": work_order.work_order_id,
                "revision": 1,
                "parent_revision": None,
                "approval_ref": grant_ref,
                "review_ref": review_ref,
                "node_bindings": [
                    {
                        "node_id": node_id,
                        "objective": work_order.objective,
                        "worker_route": "delegate",
                        "target_id": "agent_runtime",
                    }
                ],
            },
        },
        root_work_order_id=work_order.work_order_id,
        parent_work_order_id=None,
        task_graph_id=graph_id,
        task_node_id=node_id,
        graph_revision=1,
        attempt=0,
        capability_requirements=("agent_runtime",),
        risk_level=risk_level,
        input_refs=(input_ref, grant_ref, control_policy["policy_ref"]),
        output_contract=output_contract,
        budget_policy=budget_policy,
        business_idempotency_scope=business_scope,
        operation_fingerprint=operation_fingerprint,
        status_version=0,
        approval_ref=grant_ref,
        review_ref=review_ref,
    )
    canonical_order.validate()
    node = TaskNode(
        node_id=node_id,
        dependencies=(),
        route=INTERNAL_TASK_NODE_ROUTE,
        input_refs=(input_ref, grant_ref, control_policy["policy_ref"]),
        output_contract=output_contract,
        retry_policy={"max_attempts": 1, "retry_backoff_ms": 0},
        risk_level=risk_level,
        acceptance_criteria=canonical_order.acceptance_criteria,
        status=TaskNodeStatus.READY,
        status_version=0,
    )
    graph = TaskGraph(
        graph_id=graph_id,
        root_work_order_id=work_order.work_order_id,
        revision=1,
        planner_ref=f"planner://governed-internal/{suffix}",
        status=TaskGraphStatus.CREATED,
        created_at=int(ts_ms),
        result_node_id=node_id,
        completion_policy=TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1,
        nodes=(node,),
    )
    command = build_host_command(
        surface=HostCommandSurface.INTERNAL,
        request_id=request_ref,
        session_id=session_ref,
        principal_id=principal_id,
        ingress_claim_ref=claim_ref,
        ingress_audit_ref=audit_ref,
        governor_grant_ref=grant_ref,
        input_ref=input_ref,
        input_sha256=canonical_input_sha,
        root_work_order_id=work_order.work_order_id,
        graph_id=graph_id,
        graph_revision=1,
        route_type=INTERNAL_AGENT_WORKER_ROUTE,
        target_id="agent_runtime",
        business_scope=business_scope,
        operation_fingerprint=operation_fingerprint,
        created_at_ms=int(ts_ms),
    )
    return command, canonical_order, graph


def _canonical_review_policy(
    work_order: AgentWorkOrder,
    *,
    suffix: str,
) -> tuple[dict[str, Any], str]:
    policy = deepcopy(dict(work_order.review_policy or {}))
    approval = dict(work_order.approval_policy or {})
    required = bool(
        work_order.review_required
        or policy.get("durable_review_required")
        or policy.get("require_durable_review")
        or approval.get("require_review_token")
        or approval.get("review_id")
    )
    if required:
        for key in ("waiver_authority", "waiver_ref"):
            policy.pop(key, None)
        policy.update(
            {
                "review_required": True,
                "durable_review_required": True,
                "mode": "durable_task_node_review",
                "reason": "internal_agent_review_policy",
            }
        )
        return policy, f"review-policy://internal-agent/{suffix}"
    review_ref = f"review-waiver://governor-grant/{suffix}"
    policy.update(
        {
            "review_required": False,
            "durable_review_required": False,
            "require_durable_review": False,
            "mode": "governor_grant_review_waiver",
            "reason": "approved_internal_agent_execution",
            "waiver_authority": "governor_grant",
            "waiver_ref": review_ref,
        }
    )
    return policy, review_ref


def required_identifier(value: Any, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            f"{field_name} is not a bounded identifier",
        )
    return normalized


def _required_reference(value: Any, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if "://" not in normalized or len(normalized) > 512:
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            f"{field_name} is not a durable reference",
        )
    return normalized


def identifier_or_digest(value: Any, *, prefix: str) -> str:
    normalized = str(value or "").strip()
    if _IDENTIFIER.fullmatch(normalized):
        return normalized
    return f"{prefix}-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def _scope_token(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", str(value or "agent").strip().lower())
    return normalized.strip("-")[:64] or "agent"


def _sha256_json(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        deepcopy(dict(value)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "INTERNAL_AGENT_WORKER_ROUTE",
    "INTERNAL_RUNTIME_AUTHORITY_INVALID",
    "INTERNAL_RUNTIME_DRAIN_TIMEOUT",
    "INTERNAL_RUNTIME_GOVERNOR_GRANT_MISSING",
    "INTERNAL_RUNTIME_GOVERNOR_REJECTED",
    "INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED",
    "INTERNAL_RUNTIME_INACTIVE",
    "INTERNAL_RUNTIME_PRE_ADMITTED_WORK_ORDER_FORBIDDEN",
    "INTERNAL_RUNTIME_STARTUP_CONTINUATION_DEFERRED",
    "INTERNAL_TASK_NODE_ROUTE",
    "GovernedInternalRuntimeError",
    "build_internal_task_contracts",
    "identifier_or_digest",
    "required_identifier",
]
