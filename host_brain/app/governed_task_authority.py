"""Fail-closed authority and replay validation for governed Host execution.

This module owns deterministic validation only.  It does not compose workers,
write durable state, dispatch effects, or execute a TaskGraph.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import BudgetExceededAction
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from natsume_contracts.task import HostCommand


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


def normalized_work_order_replay_payload(work_order: WorkOrderV3) -> dict[str, Any]:
    payload = work_order.to_dict()
    authority_refs = {
        str(work_order.approval_ref or "").strip(),
        str(work_order.review_ref or "").strip(),
    } - {""}
    payload["approval_ref"] = "<durable-authority-ref>"
    payload["review_ref"] = "<durable-authority-ref>"
    payload["input_refs"] = [
        "<durable-authority-ref>" if str(ref) in authority_refs else str(ref)
        for ref in work_order.input_refs
    ]
    approval_policy = mapping(payload.get("approval_policy"))
    approval_policy["governor_grant_ref"] = "<durable-authority-ref>"
    payload["approval_policy"] = approval_policy
    review_policy = mapping(payload.get("review_policy"))
    review_policy["review_ref"] = "<durable-authority-ref>"
    payload["review_policy"] = review_policy
    metadata = mapping(payload.get("metadata"))
    lineage = mapping(metadata.get("lineage"))
    lineage["approval_ref"] = "<durable-authority-ref>"
    lineage["review_ref"] = "<durable-authority-ref>"
    metadata["lineage"] = lineage
    planning_lineage = mapping(metadata.get("planning_lineage"))
    planning_lineage["approval_ref"] = "<durable-authority-ref>"
    planning_lineage["review_ref"] = "<durable-authority-ref>"
    metadata["planning_lineage"] = planning_lineage
    payload["metadata"] = metadata
    _normalize_frozen_executor_payloads_for_replay(payload)
    return payload


def _normalize_frozen_executor_payloads_for_replay(payload: dict[str, Any]) -> None:
    """Mask rotating authority references nested in an admitted adapter payload."""

    input_pack = mapping(payload.get("input_pack"))
    raw_payloads = mapping(input_pack.get("frozen_node_executor_payloads"))
    if not raw_payloads:
        return
    normalized_payloads: dict[str, Any] = {}
    for node_id, raw_payload in raw_payloads.items():
        frozen = mapping(raw_payload)
        adapter_order = mapping(frozen.get("work_order"))
        metadata = mapping(adapter_order.get("metadata"))
        lineage = mapping(metadata.get("governed_lineage"))
        if lineage:
            # The durable WorkOrder stays authoritative on replay.  A renewed
            # governor/review reference is not a semantic change to the frozen
            # legacy adapter body, but changes its transport digest.
            if "approval_ref" in lineage:
                lineage["approval_ref"] = "<durable-authority-ref>"
            if "review_ref" in lineage:
                lineage["review_ref"] = "<durable-authority-ref>"
            metadata["governed_lineage"] = lineage
            adapter_order["metadata"] = metadata
            frozen["work_order"] = adapter_order
            if "payload_sha256" in frozen:
                frozen["payload_sha256"] = "<frozen-payload-digest>"
        normalized_payloads[str(node_id)] = frozen
    input_pack["frozen_node_executor_payloads"] = normalized_payloads
    payload["input_pack"] = input_pack


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
        or not expected_review_ref.startswith("review-waiver://")
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
    policy = work_order.budget_policy
    limits = policy.limits.to_dict() if policy is not None else {}
    approval_policy = mapping(work_order.approval_policy)
    planning_lineage = mapping(work_order.metadata.get("planning_lineage"))
    approval_binding = str(
        approval_policy.get("governor_grant_ref")
        or approval_policy.get("grant_ref")
        or ""
    ).strip()
    if (
        policy is None
        or policy.on_exceeded != BudgetExceededAction.BLOCK
        or any(int(value) <= 0 for value in limits.values())
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
