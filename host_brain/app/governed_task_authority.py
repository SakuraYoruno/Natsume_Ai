"""Fail-closed authority and replay validation for governed Host execution.

This module owns deterministic validation only.  It does not compose workers,
write durable state, dispatch effects, or execute a TaskGraph.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import BudgetExceededAction
from host_brain.contracts.agent_runtime.delegate_target_policy import (
    GOVERNED_DELEGATE_BUDGET_MODE,
    GOVERNED_DELEGATE_EFFECT_MODE,
    GOVERNED_DELEGATE_EXECUTION_MODE,
    GOVERNED_DELEGATE_REVIEW_MODE,
    GovernedDelegateTargetPolicyError,
    validate_governed_delegate_target_policy,
)
from host_brain.contracts.agent_runtime.control_route_execution_policy import (
    ControlRouteExecutionPolicyError,
    require_control_route_execution_policy,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.contracts.agent_runtime.work_order_replay import (
    normalized_work_order_replay_payload,
)
from natsume_contracts.task import HostCommand, HostCommandSurface


GOVERNED_HOST_AUTHORITY_INVALID = "GOVERNED_HOST_AUTHORITY_INVALID"

_STABLE_REPLAY_COMMAND_FIELDS = (
    "schema_version",
    "command_id",
    "surface",
    "request_id",
    "session_id",
    "principal_id",
    "ingress_claim_ref",
    "ingress_audit_ref",
    "input_ref",
    "input_sha256",
    "root_work_order_id",
    "graph_id",
    "graph_revision",
    "route_type",
    "target_id",
    "business_scope",
    "operation_fingerprint",
)


class GovernedHostAdapterError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GOVERNED_HOST_AUTHORITY_INVALID)
        self.detail = str(detail or "governed Host adapter failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


def assert_stable_command_replay(
    candidate: HostCommand,
    durable: HostCommand,
) -> None:
    candidate_payload = candidate.to_dict()
    durable_payload = durable.to_dict()
    drift = [
        field_name
        for field_name in _STABLE_REPLAY_COMMAND_FIELDS
        if candidate_payload.get(field_name) != durable_payload.get(field_name)
    ]
    if drift:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "replayed Host command changed stable semantics: " + ", ".join(drift),
        )


def assert_stable_work_order_replay(
    candidate: WorkOrderV3,
    durable: WorkOrderV3,
    *,
    candidate_command: HostCommand,
    durable_command: HostCommand,
) -> None:
    require_fail_closed_governed_work_order(
        candidate,
        command=candidate_command,
    )
    require_fail_closed_governed_work_order(
        durable,
        command=durable_command,
    )
    if str(candidate_command.route_type or "").strip().lower() == "skill":
        require_fail_closed_direct_skill_work_order(candidate)
    if str(durable_command.route_type or "").strip().lower() == "skill":
        require_fail_closed_direct_skill_work_order(durable)
    candidate_payload = normalized_work_order_replay_payload(candidate)
    durable_payload = normalized_work_order_replay_payload(durable)
    drift = [
        field_name
        for field_name in candidate_payload
        if candidate_payload.get(field_name) != durable_payload.get(field_name)
    ]
    if drift:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "replayed WorkOrderV3 changed stable semantics: " + ", ".join(drift),
        )


def assert_work_order_command_binding(
    work_order: WorkOrderV3,
    command: HostCommand,
) -> None:
    require_fail_closed_governed_work_order(work_order, command=command)
    route = str(command.route_type or "").strip().lower()
    if route == "skill":
        require_fail_closed_direct_skill_work_order(work_order)
    approval_policy = mapping(work_order.approval_policy)
    review_policy = mapping(work_order.review_policy)
    expected_grant_ref = str(command.governor_grant_ref or "").strip()
    expected_review_ref = str(work_order.review_ref or "").strip()
    review_policy_ref = str(
        review_policy.get("review_ref") or review_policy.get("waiver_ref") or ""
    ).strip()
    review_binding_valid = bool(
        (
            expected_review_ref.startswith("review-waiver://")
            and review_policy.get("review_required") is False
            and review_policy.get("mode")
            == "governor_grant_review_waiver"
        )
        or (
            expected_review_ref.startswith("review-policy://task-node/")
            and review_policy.get("review_required") is True
            and review_policy.get("durable_review_required") is True
            and review_policy.get("mode")
            == "task_node_pre_execution_review"
        )
    )
    node_binding = planning_node_binding(
        work_order,
        node_id=str(work_order.task_node_id or "").strip(),
    )
    if (
        work_order.root_work_order_id != command.root_work_order_id
        or work_order.task_graph_id != command.graph_id
        or work_order.graph_revision != command.graph_revision
        or work_order.business_idempotency_scope != command.business_scope
        or work_order.operation_fingerprint != command.operation_fingerprint
        or work_order.approval_ref != expected_grant_ref
        or not review_binding_valid
        or expected_review_ref == expected_grant_ref
        or approval_policy.get("governor_grant_ref") != expected_grant_ref
        or review_policy_ref != expected_review_ref
        or command.input_ref not in set(work_order.input_refs)
        or expected_grant_ref not in set(work_order.input_refs)
        or command.target_id not in set(work_order.capability_requirements)
        or str(node_binding.get("worker_route") or "").strip().lower() != route
        or str(node_binding.get("target_id") or "").strip() != command.target_id
    ):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "governed WorkOrderV3 differs from durable HostCommand lineage",
        )


def planning_node_binding(work_order: WorkOrderV3, *, node_id: str) -> dict[str, Any]:
    planning_lineage = mapping(work_order.metadata.get("planning_lineage"))
    raw_bindings = planning_lineage.get("node_bindings")
    bindings = list(raw_bindings) if isinstance(raw_bindings, list) else []
    matches = [
        mapping(binding)
        for binding in bindings
        if isinstance(binding, Mapping)
        and str(binding.get("node_id") or "").strip() == str(node_id or "").strip()
    ]
    if len(matches) != 1:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            f"WorkOrder planning lineage requires exactly one binding for node {node_id}",
        )
    binding = matches[0]
    if (
        not str(binding.get("worker_route") or "").strip()
        or not str(binding.get("target_id") or "").strip()
        or not str(binding.get("objective") or "").strip()
    ):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            f"WorkOrder planning binding for node {node_id} is incomplete",
        )
    return binding


def require_fail_closed_governed_work_order(
    work_order: WorkOrderV3,
    *,
    command: HostCommand,
) -> None:
    if not isinstance(work_order, WorkOrderV3):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "governed execution requires an explicit WorkOrderV3",
        )
    try:
        work_order.validate()
    except Exception as exc:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            f"governed WorkOrderV3 is invalid: {exc}",
        ) from exc
    if command.surface in {
        HostCommandSurface.INTERNAL,
        HostCommandSurface.OPERATOR,
        HostCommandSurface.SCHEDULER,
    }:
        try:
            require_control_route_execution_policy(
                command=command,
                work_order=work_order,
            )
        except ControlRouteExecutionPolicyError as exc:
            raise GovernedHostAdapterError(
                exc.reason_code,
                exc.detail,
            ) from exc
    policy = work_order.budget_policy
    limits = policy.limits.to_dict() if policy is not None else {}
    approval_policy = mapping(work_order.approval_policy)
    planning_lineage = mapping(work_order.metadata.get("planning_lineage"))
    approval_binding = str(
        approval_policy.get("governor_grant_ref")
        or approval_policy.get("grant_ref")
        or ""
    ).strip()
    is_child_agent_delegate = (
        command.route_type == "delegate"
        and command.surface
        not in {
            HostCommandSurface.INTERNAL,
            HostCommandSurface.OPERATOR,
            HostCommandSurface.SCHEDULER,
        }
    )
    is_control_transport = command.surface in {
        HostCommandSurface.INTERNAL,
        HostCommandSurface.OPERATOR,
        HostCommandSurface.SCHEDULER,
    }
    budget_invalid = (
        _port_free_delegate_budget_invalid(
            work_order,
            command=command,
            limits=limits,
        )
        if is_child_agent_delegate
        else (
            not limits
            or any(int(value) < 0 for value in limits.values())
            or int(limits.get("concurrency", 0)) < 1
        )
        if is_control_transport
        else any(int(value) <= 0 for value in limits.values())
    )
    if (
        policy is None
        or policy.on_exceeded != BudgetExceededAction.BLOCK
        or budget_invalid
        or work_order.root_work_order_id != work_order.work_order_id
        or work_order.parent_work_order_id is not None
        or work_order.root_work_order_id != command.root_work_order_id
        or work_order.task_graph_id != command.graph_id
        or work_order.graph_revision != command.graph_revision
        or work_order.business_idempotency_scope != command.business_scope
        or work_order.operation_fingerprint != command.operation_fingerprint
        or work_order.approval_ref != str(command.governor_grant_ref or "").strip()
        or approval_binding != work_order.approval_ref
        or command.input_ref not in set(work_order.input_refs)
        or work_order.approval_ref not in set(work_order.input_refs)
        or planning_lineage.get("graph_id") != work_order.task_graph_id
        or planning_lineage.get("root_work_order_id") != work_order.root_work_order_id
        or planning_lineage.get("revision") != work_order.graph_revision
        or planning_lineage.get("approval_ref") != work_order.approval_ref
        or planning_lineage.get("review_ref") != work_order.review_ref
        or not isinstance(planning_lineage.get("node_bindings"), list)
        or not planning_lineage.get("node_bindings")
    ):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "governed WorkOrderV3 differs from durable HostCommand/planning lineage",
        )


def _port_free_delegate_budget_invalid(
    work_order: WorkOrderV3,
    *,
    command: HostCommand,
    limits: Mapping[str, Any],
) -> bool:
    """Allow exact-zero model dimensions only for the frozen read-only target."""

    input_policy = mapping(
        work_order.input_pack.get("delegate_target_policy")
    )
    metadata_policy = mapping(
        work_order.metadata.get("delegate_target_policy")
    )
    budget_metadata = mapping(
        getattr(work_order.budget_policy, "metadata", {})
    )
    try:
        input_policy = validate_governed_delegate_target_policy(input_policy)
        metadata_policy = validate_governed_delegate_target_policy(
            metadata_policy
        )
    except GovernedDelegateTargetPolicyError:
        return True
    if (
        input_policy != metadata_policy
        or input_policy.get("target_id") != command.target_id
        or input_policy.get("execution_mode")
        != GOVERNED_DELEGATE_EXECUTION_MODE
        or input_policy.get("review_mode") != GOVERNED_DELEGATE_REVIEW_MODE
        or input_policy.get("budget_mode") != GOVERNED_DELEGATE_BUDGET_MODE
        or input_policy.get("effect_mode") != GOVERNED_DELEGATE_EFFECT_MODE
        or budget_metadata.get("measurement_mode")
        != GOVERNED_DELEGATE_BUDGET_MODE
        or budget_metadata.get("enabled_target") != command.target_id
    ):
        return True
    try:
        return any(
            (
                int(limits.get("token_count", -1)) != 0,
                int(limits.get("model_cost_micros", -1)) != 0,
                int(limits.get("tool_call_count", 0)) < 1,
                int(limits.get("time_ms", 0)) < 1,
                int(limits.get("concurrency", 0)) < 1,
                int(limits.get("retry_count", 0)) < 1,
            )
        )
    except (TypeError, ValueError):
        return True


def require_fail_closed_direct_skill_work_order(work_order: WorkOrderV3) -> None:
    if not isinstance(work_order, WorkOrderV3):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "direct skill execution requires an explicit WorkOrderV3",
        )
    try:
        work_order.validate()
    except Exception as exc:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            f"direct skill WorkOrderV3 is invalid: {exc}",
        ) from exc
    policy = work_order.budget_policy
    if policy is None:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "direct skill WorkOrderV3 has no BudgetPolicy",
        )
    if policy.on_exceeded != BudgetExceededAction.BLOCK:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "direct skill BudgetPolicy must fail closed with BLOCK",
        )
    limits = policy.limits.to_dict()
    if any(int(value) <= 0 for value in limits.values()):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "direct skill BudgetPolicy limits must all be finite and non-zero",
        )
    if (
        work_order.root_work_order_id != work_order.work_order_id
        or work_order.parent_work_order_id is not None
        or not work_order.task_graph_id
        or not work_order.task_node_id
        or work_order.graph_revision < 1
        or work_order.attempt != 0
        or not work_order.capability_requirements
        or not work_order.input_refs
        or not work_order.approval_ref
        or not work_order.review_ref
        or not work_order.business_idempotency_scope
        or not work_order.operation_fingerprint
    ):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "direct skill WorkOrderV3 lineage is incomplete",
        )
    lineage = mapping(work_order.metadata.get("lineage"))
    expected_lineage = {
        "root_work_order_id": work_order.root_work_order_id,
        "task_graph_id": work_order.task_graph_id,
        "task_node_id": work_order.task_node_id,
        "graph_revision": work_order.graph_revision,
        "attempt": work_order.attempt,
        "capability_requirements": list(work_order.capability_requirements),
        "approval_ref": work_order.approval_ref,
        "review_ref": work_order.review_ref,
        "business_idempotency_scope": work_order.business_idempotency_scope,
        "operation_fingerprint": work_order.operation_fingerprint,
    }
    if lineage != expected_lineage:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "direct skill WorkOrderV3 metadata lineage differs from canonical fields",
        )
    planning_lineage = mapping(work_order.metadata.get("planning_lineage"))
    expected_binding = {
        "node_id": work_order.task_node_id,
        "objective": work_order.objective,
        "worker_route": "skill",
        "target_id": work_order.capability_requirements[0],
    }
    if (
        planning_lineage.get("graph_id") != work_order.task_graph_id
        or planning_lineage.get("root_work_order_id") != work_order.root_work_order_id
        or planning_lineage.get("revision") != work_order.graph_revision
        or planning_lineage.get("parent_revision") is not None
        or planning_lineage.get("approval_ref") != work_order.approval_ref
        or planning_lineage.get("review_ref") != work_order.review_ref
        or planning_lineage.get("node_bindings") != [expected_binding]
    ):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "direct skill WorkOrderV3 planning lineage differs from canonical fields",
        )


def mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


__all__ = [
    "GOVERNED_HOST_AUTHORITY_INVALID",
    "GovernedHostAdapterError",
    "assert_stable_command_replay",
    "assert_stable_work_order_replay",
    "assert_work_order_command_binding",
    "mapping",
    "normalized_work_order_replay_payload",
    "planning_node_binding",
    "require_fail_closed_direct_skill_work_order",
    "require_fail_closed_governed_work_order",
]
