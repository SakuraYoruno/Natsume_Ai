"""Bind one write-capable Agent Skill payload to its admitted Task attempt.

The public Agent Skill handlers still emit the compatibility WorkOrder v2
shape.  That shape is user-influenced and therefore cannot carry graph,
Workspace, Review, grant, Budget, or execution authority.  This module copies
only semantic fields from the compatibility payload and rebuilds every
authority field from the already-verified governed execution context.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from host_brain.agent_runtime.workspace_runtime import DurableWorkspaceRuntime
from host_brain.contracts.agent_runtime.work_order import (
    AgentWorkOrder,
    WorkOrderV3,
)


GOVERNED_AGENT_WORK_ORDER_BINDING_INVALID = (
    "GOVERNED_AGENT_WORK_ORDER_BINDING_INVALID"
)
GOVERNED_AGENT_REVIEW_REQUIRED = "GOVERNED_AGENT_REVIEW_REQUIRED"

WRITE_AGENT_RUNTIME_SKILL_IDS = frozenset(
    {"agent_worker", "agent_self_heal", "agent_self_evolve"}
)

_PRE_ADMITTED_FIELDS = frozenset(
    {
        "root_work_order_id",
        "parent_work_order_id",
        "task_graph_id",
        "task_node_id",
        "graph_revision",
        "attempt",
        "capability_requirements",
        "risk_level",
        "input_refs",
        "output_contract",
        "budget_policy",
        "business_idempotency_scope",
        "operation_fingerprint",
        "status_version",
        "approval_ref",
        "review_ref",
    }
)
_RESERVED_METADATA_FIELDS = frozenset(
    {
        *_PRE_ADMITTED_FIELDS,
        "execution_authority",
        "fencing_token",
        "lease_id",
        "lease_owner_id",
        "node_grant_ref",
        "parent_grant_ref",
        "reservation_id",
        "workspace_id",
    }
)
_REQUIRED_CONTEXT_FIELDS = (
    "command_id",
    "request_id",
    "principal_id",
    "target_id",
    "work_order_id",
    "graph_id",
    "node_id",
    "attempt_id",
    "lease_id",
    "lease_owner_id",
    "reservation_id",
    "node_grant_ref",
    "parent_grant_ref",
    "business_scope",
    "operation_fingerprint",
    "pre_execution_review_ref",
)
_READ_REQUIRED_CONTEXT_FIELDS = tuple(
    field
    for field in _REQUIRED_CONTEXT_FIELDS
    if field != "pre_execution_review_ref"
)


class GovernedAgentWorkOrderBindingError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(
            reason_code or GOVERNED_AGENT_WORK_ORDER_BINDING_INVALID
        )
        self.detail = str(detail or "governed Agent WorkOrder binding failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


def bind_governed_agent_write_order(
    raw_order: Mapping[str, Any],
    *,
    governed_execution_context: Mapping[str, Any],
    workspace_runtime: DurableWorkspaceRuntime,
) -> WorkOrderV3:
    """Return one server-bound WorkOrderV3 for a reviewed Agent write node."""

    if not isinstance(raw_order, Mapping):
        _invalid("write-capable Agent payload has no WorkOrder mapping")
    data = deepcopy(dict(raw_order))
    explicit_schema = str(data.get("schema_version") or "").strip()
    if explicit_schema and explicit_schema.lower() not in {
        "2",
        "v2",
        "work_order.v2",
        "natsume.agent.work_order.v2",
    }:
        _invalid("Agent Skill payload may not submit a pre-admitted WorkOrderV3")
    forbidden = sorted(_PRE_ADMITTED_FIELDS.intersection(data))
    if forbidden:
        _invalid(
            "Agent Skill payload contains pre-admitted fields: "
            + ",".join(forbidden)
        )
    metadata = data.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    forbidden_metadata = sorted(_RESERVED_METADATA_FIELDS.intersection(metadata))
    if forbidden_metadata:
        _invalid(
            "Agent Skill metadata contains authority fields: "
            + ",".join(forbidden_metadata)
        )
    raw_workspace = data.get("workspace")
    if isinstance(raw_workspace, Mapping) and (
        str(raw_workspace.get("strategy") or "").strip().lower()
        == "durable_workspace"
        or str(raw_workspace.get("workspace_id") or "").strip()
    ):
        _invalid("Agent Skill payload may not select a durable Workspace owner")

    context = dict(governed_execution_context or {})
    if (
        str(context.get("execution_mode") or "").strip()
        != "governed_task_runtime"
    ):
        _invalid("Agent write has no governed Task execution mode")
    missing = [
        field
        for field in _REQUIRED_CONTEXT_FIELDS
        if not str(context.get(field) or "").strip()
    ]
    if missing:
        _invalid("governed Agent context is incomplete: " + ",".join(missing))
    try:
        graph_revision = int(context.get("graph_revision"))
        fencing_token = int(context.get("fencing_token"))
        node_risk_level = int(context.get("node_risk_level"))
    except (TypeError, ValueError) as exc:
        raise GovernedAgentWorkOrderBindingError(
            GOVERNED_AGENT_WORK_ORDER_BINDING_INVALID,
            "governed Agent integer authority fields are invalid",
        ) from exc
    if graph_revision < 1 or fencing_token < 1 or not 1 <= node_risk_level <= 5:
        _invalid("governed Agent graph, fence, or risk authority is invalid")

    target_id = str(context["target_id"]).strip().lower()
    if target_id not in WRITE_AGENT_RUNTIME_SKILL_IDS:
        _invalid(f"unsupported write-capable Agent Skill: {target_id}")
    if node_risk_level < 4:
        _invalid("write-capable Agent Skill is under-classified below risk level 4")
    work_order_id = str(context["work_order_id"]).strip()
    if str(data.get("work_order_id") or "").strip() != work_order_id:
        _invalid("Agent Skill WorkOrder id differs from the admitted Host command")
    if (
        str(workspace_runtime.runtime_owner_id or "").strip()
        != str(context["lease_owner_id"]).strip()
    ):
        _invalid("Workspace runtime owner differs from the Task lease owner")

    legacy = AgentWorkOrder.from_mapping(data)
    legacy.validate()
    if not legacy.requires_write:
        _invalid("Agent Skill write binding received a read-only WorkOrder")
    if str(metadata.get("skill_id") or "").strip().lower() != target_id:
        _invalid("Agent Skill WorkOrder metadata target drifted")
    request_id = str(context["request_id"]).strip()
    metadata_request = str(metadata.get("request_id") or "").strip()
    if metadata_request and metadata_request != request_id:
        _invalid("Agent Skill WorkOrder request id drifted")

    graph_id = str(context["graph_id"]).strip()
    node_id = str(context["node_id"]).strip()
    review_ref = str(context["pre_execution_review_ref"]).strip()
    if not review_ref.startswith("review://"):
        raise GovernedAgentWorkOrderBindingError(
            GOVERNED_AGENT_REVIEW_REQUIRED,
            "Agent write lacks a consumed durable pre-execution Review",
        )
    workspace_id = workspace_runtime.workspace_id_for(
        graph_id=graph_id,
        graph_revision=graph_revision,
    )
    canonical = WorkOrderV3(
        work_order_id=work_order_id,
        domain=legacy.domain,
        run_mode=legacy.run_mode,
        objective=legacy.objective,
        workspace={
            "strategy": "durable_workspace",
            "workspace_id": workspace_id,
        },
        input_pack=deepcopy(legacy.input_pack),
        allowed_actions=tuple(legacy.allowed_actions),
        forbidden_actions=tuple(
            dict.fromkeys(
                (
                    *legacy.forbidden_actions,
                    "git_push",
                    "modify_secret_files",
                    "cross_workspace_write",
                )
            )
        ),
        acceptance_criteria=tuple(legacy.acceptance_criteria),
        verification_plan=deepcopy(legacy.verification_plan),
        approval_policy={
            "required": True,
            "authority_kind": "node_bound_grant",
            "node_grant_ref": str(context["node_grant_ref"]).strip(),
            "parent_grant_ref": str(context["parent_grant_ref"]).strip(),
        },
        review_policy={
            "review_required": False,
            "mode": "consumed_task_node_pre_execution_review",
            "review_ref": review_ref,
        },
        metadata={
            key: deepcopy(value)
            for key, value in metadata.items()
            if key not in _RESERVED_METADATA_FIELDS
        }
        | {
            "request_id": request_id,
            "skill_id": target_id,
            "attempt_id": str(context["attempt_id"]).strip(),
            "reservation_id": str(context["reservation_id"]).strip(),
            "workspace_binding": "server_derived_graph_epoch",
        },
        root_work_order_id=work_order_id,
        parent_work_order_id=None,
        task_graph_id=graph_id,
        task_node_id=node_id,
        graph_revision=graph_revision,
        attempt=0,
        capability_requirements=(target_id,),
        risk_level=node_risk_level,
        input_refs=tuple(
            dict.fromkeys(
                (
                    str(context["parent_grant_ref"]).strip(),
                    str(context["node_grant_ref"]).strip(),
                    review_ref,
                )
            )
        ),
        output_contract={
            "type": "governed_agent_workspace_artifact",
            "required": [
                "protected_artifact",
                "verification",
                "result_digest",
            ],
        },
        budget_policy=None,
        business_idempotency_scope=str(context["business_scope"]).strip(),
        operation_fingerprint=str(context["operation_fingerprint"]).strip(),
        status_version=0,
        approval_ref=str(context["node_grant_ref"]).strip(),
        review_ref=review_ref,
    )
    canonical.validate()
    return canonical


def bind_governed_agent_read_order(
    raw_order: Mapping[str, Any],
    *,
    governed_execution_context: Mapping[str, Any],
) -> WorkOrderV3:
    """Rebuild one read-only Agent WorkOrder from trusted Task authority.

    Compatibility Agent handlers still produce a caller-shaped V2 mapping.
    A governed node must not execute that mapping directly because its graph,
    grant, Attempt, Budget and operation lineage would otherwise be absent
    from the canonical WorkOrder consumed by ``AgentRuntimeExecutor``.
    """

    if not isinstance(raw_order, Mapping):
        _invalid("read-only Agent payload has no WorkOrder mapping")
    data = deepcopy(dict(raw_order))
    explicit_schema = str(data.get("schema_version") or "").strip()
    if explicit_schema and explicit_schema.lower() not in {
        "2",
        "v2",
        "work_order.v2",
        "natsume.agent.work_order.v2",
    }:
        _invalid("Agent Skill payload may not submit a pre-admitted WorkOrderV3")
    forbidden = sorted(_PRE_ADMITTED_FIELDS.intersection(data))
    if forbidden:
        _invalid(
            "Agent Skill payload contains pre-admitted fields: "
            + ",".join(forbidden)
        )
    metadata = data.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    forbidden_metadata = sorted(
        _RESERVED_METADATA_FIELDS.intersection(metadata)
    )
    if forbidden_metadata:
        _invalid(
            "Agent Skill metadata contains authority fields: "
            + ",".join(forbidden_metadata)
        )

    context = dict(governed_execution_context or {})
    if (
        str(context.get("execution_mode") or "").strip()
        != "governed_task_runtime"
    ):
        _invalid("Agent read has no governed Task execution mode")
    missing = [
        field
        for field in _READ_REQUIRED_CONTEXT_FIELDS
        if not str(context.get(field) or "").strip()
    ]
    if missing:
        _invalid("governed Agent context is incomplete: " + ",".join(missing))
    try:
        graph_revision = int(context.get("graph_revision"))
        node_risk_level = int(context.get("node_risk_level"))
    except (TypeError, ValueError) as exc:
        raise GovernedAgentWorkOrderBindingError(
            GOVERNED_AGENT_WORK_ORDER_BINDING_INVALID,
            "governed Agent integer authority fields are invalid",
        ) from exc
    if graph_revision < 1 or not 1 <= node_risk_level <= 5:
        _invalid("governed Agent graph or risk authority is invalid")

    legacy = AgentWorkOrder.from_mapping(data)
    legacy.validate()
    if legacy.requires_write:
        _invalid("read-only Agent binding received a write-capable WorkOrder")
    target_id = str(context["target_id"]).strip().lower()
    work_order_id = str(context["work_order_id"]).strip()
    if str(data.get("work_order_id") or "").strip() != work_order_id:
        _invalid("Agent Skill WorkOrder id differs from the admitted Host command")
    if str(metadata.get("skill_id") or "").strip().lower() != target_id:
        _invalid("Agent Skill WorkOrder metadata target drifted")
    request_id = str(context["request_id"]).strip()
    metadata_request = str(metadata.get("request_id") or "").strip()
    if metadata_request and metadata_request != request_id:
        _invalid("Agent Skill WorkOrder request id drifted")

    graph_id = str(context["graph_id"]).strip()
    node_id = str(context["node_id"]).strip()
    review_ref = str(
        context.get("pre_execution_review_ref") or ""
    ).strip()
    input_refs = [
        str(context["parent_grant_ref"]).strip(),
        str(context["node_grant_ref"]).strip(),
    ]
    if review_ref:
        if not review_ref.startswith("review://"):
            raise GovernedAgentWorkOrderBindingError(
                GOVERNED_AGENT_REVIEW_REQUIRED,
                "Agent read review authority is not a consumed durable Review",
            )
        input_refs.append(review_ref)
    canonical = WorkOrderV3(
        work_order_id=work_order_id,
        domain=legacy.domain,
        run_mode=legacy.run_mode,
        objective=legacy.objective,
        workspace=deepcopy(legacy.workspace),
        input_pack=deepcopy(legacy.input_pack),
        allowed_actions=tuple(legacy.allowed_actions),
        forbidden_actions=tuple(
            dict.fromkeys(
                (
                    *legacy.forbidden_actions,
                    "write_files",
                    "git_push",
                    "modify_secret_files",
                    "cross_workspace_write",
                )
            )
        ),
        acceptance_criteria=tuple(legacy.acceptance_criteria),
        verification_plan=deepcopy(legacy.verification_plan),
        approval_policy={
            "required": True,
            "authority_kind": "node_bound_grant",
            "node_grant_ref": str(context["node_grant_ref"]).strip(),
            "parent_grant_ref": str(context["parent_grant_ref"]).strip(),
        },
        review_policy={
            "review_required": False,
            "mode": (
                "consumed_task_node_pre_execution_review"
                if review_ref
                else "task_node_governed_execution"
            ),
            **({"review_ref": review_ref} if review_ref else {}),
        },
        metadata={
            key: deepcopy(value)
            for key, value in metadata.items()
            if key not in _RESERVED_METADATA_FIELDS
            and key != "governed_lineage"
        }
        | {
            "request_id": request_id,
            "skill_id": target_id,
            "attempt_id": str(context["attempt_id"]).strip(),
            "reservation_id": str(context["reservation_id"]).strip(),
            "governed_binding": "server_derived_task_attempt",
        },
        root_work_order_id=work_order_id,
        parent_work_order_id=None,
        task_graph_id=graph_id,
        task_node_id=node_id,
        graph_revision=graph_revision,
        attempt=0,
        capability_requirements=(target_id,),
        risk_level=node_risk_level,
        input_refs=tuple(dict.fromkeys(input_refs)),
        output_contract={
            "type": "governed_agent_result_proposal",
            "required": [
                "result_text",
                "verification",
                "model_attempts",
            ],
        },
        budget_policy=None,
        business_idempotency_scope=str(context["business_scope"]).strip(),
        operation_fingerprint=str(
            context["operation_fingerprint"]
        ).strip(),
        status_version=0,
        approval_ref=str(context["node_grant_ref"]).strip(),
        review_ref=review_ref or None,
    )
    canonical.validate()
    return canonical


def _invalid(detail: str) -> None:
    raise GovernedAgentWorkOrderBindingError(
        GOVERNED_AGENT_WORK_ORDER_BINDING_INVALID,
        detail,
    )


__all__ = [
    "GOVERNED_AGENT_REVIEW_REQUIRED",
    "GOVERNED_AGENT_WORK_ORDER_BINDING_INVALID",
    "GovernedAgentWorkOrderBindingError",
    "WRITE_AGENT_RUNTIME_SKILL_IDS",
    "bind_governed_agent_read_order",
    "bind_governed_agent_write_order",
]
