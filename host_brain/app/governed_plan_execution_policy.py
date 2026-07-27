"""Production binder and replay gate for capability-planning graphs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import hashlib
import json
from typing import Any

from host_brain.contracts.agent_runtime.plan_execution_policy import (
    PLAN_CHAT_BUDGET_POLICY,
    PLAN_CHAT_EXECUTION_POLICY,
    PLAN_EXECUTION_POLICY_INVALID,
    PLAN_EXECUTION_POLICY_METADATA_KEY,
    PLAN_EXECUTION_POLICY_SCHEMA_VERSION,
    PLAN_NODE_WORKSPACE_EFFECT_MODE,
    PLAN_RESULT_EFFECT_MODE,
    PLAN_SPECULATIVE_EFFECT_MODE,
    PlanExecutionPolicyError,
    freeze_plan_execution_policy,
    validate_plan_execution_policy,
)
from host_brain.contracts.agent_runtime.review_policy import (
    NODE_REVIEW_POLICIES_KEY,
    pre_execution_review_required,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphCompletionPolicy,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.contracts.planning_search.capability_domain_models import (
    CAPABILITY_PLANNING_EXECUTION_STEPS,
    CAPABILITY_PLANNING_MODE,
    CAPABILITY_PLANNING_TARGET_ID,
)
from host_brain.contracts.skill_execution import SkillExecutionPolicy
from host_brain.orchestration.planning_graph_compiler import CompiledPlanningGraph
from host_brain.skills.execution_policy_validation import governed_skill_policy_error
from host_brain.task_runtime.worker_registry import GovernedWorkerRoute
from natsume_contracts.task import HostCommand


class GovernedPlanExecutionPolicyError(RuntimeError):
    """Reject a Plan before Task admission or any replayed worker call."""

    def __init__(self, detail: str) -> None:
        self.reason_code = PLAN_EXECUTION_POLICY_INVALID
        self.detail = str(detail or "governed Plan execution policy is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


def bind_governed_plan_execution_policy(
    compiled: CompiledPlanningGraph,
    *,
    skill_registry: Any,
) -> CompiledPlanningGraph:
    """Freeze current registry policy into a not-yet-admitted compiled graph."""

    if not isinstance(compiled, CompiledPlanningGraph):
        raise GovernedPlanExecutionPolicyError(
            "compiled capability Plan is not a CompiledPlanningGraph"
        )
    compiled.validate()
    order = compiled.work_order
    metadata = dict(order.metadata or {})
    if PLAN_EXECUTION_POLICY_METADATA_KEY in metadata:
        raise GovernedPlanExecutionPolicyError(
            "compiled capability Plan already carries an execution policy"
        )
    policy = _expected_policy(
        work_order=order,
        graph=compiled.task_graph,
        skill_registry=skill_registry,
    )
    metadata[PLAN_EXECUTION_POLICY_METADATA_KEY] = policy
    frozen_order = replace(order, metadata=metadata)
    frozen_order.validate()
    return CompiledPlanningGraph.freeze(
        work_order=frozen_order,
        task_graph=compiled.task_graph,
        trace_digest=compiled.trace_digest,
        selected_candidate_ids=compiled.selected_candidate_ids,
        virtual_state_schema=compiled.virtual_state_schema,
        virtual_state_digest=compiled.virtual_state_digest,
        previous_revision=compiled.previous_revision,
        failure_evidence_refs=compiled.failure_evidence_refs,
    )


def require_governed_plan_execution_policy(
    *,
    command: HostCommand,
    work_order: WorkOrderV3,
    graph: TaskGraph,
    skill_registry: Any,
) -> dict[str, Any]:
    """Reopen the frozen policy immediately before initial or resumed execution."""

    if (
        not isinstance(command, HostCommand)
        or command.route_type != "plan"
        or command.target_id != CAPABILITY_PLANNING_TARGET_ID
        or not isinstance(work_order, WorkOrderV3)
        or not isinstance(graph, TaskGraph)
    ):
        raise GovernedPlanExecutionPolicyError(
            "Plan execution requires a typed command, WorkOrderV3 and TaskGraph"
        )
    if (
        command.graph_id != graph.graph_id
        or command.graph_revision != graph.revision
        or command.root_work_order_id != graph.root_work_order_id
        or work_order.task_graph_id != graph.graph_id
        or work_order.graph_revision != graph.revision
        or work_order.root_work_order_id != graph.root_work_order_id
        or command.operation_fingerprint != work_order.operation_fingerprint
    ):
        raise GovernedPlanExecutionPolicyError(
            "Plan command, WorkOrder and TaskGraph lineage differs"
        )
    raw_policy = dict(work_order.metadata or {}).get(
        PLAN_EXECUTION_POLICY_METADATA_KEY
    )
    try:
        persisted = validate_plan_execution_policy(
            raw_policy if isinstance(raw_policy, Mapping) else {}
        )
    except PlanExecutionPolicyError as exc:
        raise GovernedPlanExecutionPolicyError(exc.detail) from exc
    expected = _expected_policy(
        work_order=work_order,
        graph=graph,
        skill_registry=skill_registry,
    )
    if persisted != expected:
        raise GovernedPlanExecutionPolicyError(
            "current Plan topology, budget or Skill policy differs from admission"
        )
    return persisted


def _expected_policy(
    *,
    work_order: WorkOrderV3,
    graph: TaskGraph,
    skill_registry: Any,
) -> dict[str, Any]:
    if not isinstance(work_order, WorkOrderV3) or not isinstance(graph, TaskGraph):
        raise GovernedPlanExecutionPolicyError(
            "Plan policy requires WorkOrderV3 and TaskGraph"
        )
    work_order.validate()
    graph.validate()
    if (
        graph.completion_policy is not TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1
        or graph.result_node_id is None
        or len(graph.nodes) != CAPABILITY_PLANNING_EXECUTION_STEPS + 1
        or work_order.domain != "skill"
        or work_order.budget_policy is None
    ):
        raise GovernedPlanExecutionPolicyError(
            "capability Plan graph shape or graph Budget policy is unsupported"
        )
    budget = work_order.budget_policy
    budget.validate()
    limits = budget.limits.to_dict()
    if (
        budget.on_exceeded.value != "BLOCK"
        or limits["token_count"] < 1
        or limits["tool_call_count"] < CAPABILITY_PLANNING_EXECUTION_STEPS
        or limits["time_ms"] < 1
        or limits["concurrency"] < 1
        or limits["model_cost_micros"] < 1
    ):
        raise GovernedPlanExecutionPolicyError(
            "capability Plan requires a finite fail-closed graph Budget"
        )
    input_pack = dict(work_order.input_pack or {})
    metadata = dict(work_order.metadata or {})
    request = _mapping(
        input_pack.get("capability_plan_request"),
        field_name="capability_plan_request",
    )
    planning_input = _mapping(
        input_pack.get("capability_planning"),
        field_name="capability_planning input",
    )
    planning_metadata = _mapping(
        metadata.get("capability_planning"),
        field_name="capability_planning metadata",
    )
    lineage = _mapping(
        metadata.get("planning_lineage"),
        field_name="planning_lineage",
    )
    approved_inputs = _mapping(
        input_pack.get("approved_candidate_inputs"),
        field_name="approved_candidate_inputs",
    )
    selected_candidate_ids = tuple(
        _text_sequence(
            planning_input.get("selected_candidate_ids"),
            field_name="selected_candidate_ids",
        )
    )
    if (
        request.get("planning_mode") != CAPABILITY_PLANNING_MODE
        or request.get("execution_steps") != CAPABILITY_PLANNING_EXECUTION_STEPS
        or planning_input.get("schema_version") != CAPABILITY_PLANNING_MODE
        or planning_metadata.get("mode") != CAPABILITY_PLANNING_MODE
        or planning_metadata.get("execution_steps")
        != CAPABILITY_PLANNING_EXECUTION_STEPS
        or len(selected_candidate_ids) != len(graph.nodes)
        or set(approved_inputs) != set(selected_candidate_ids)
        or lineage.get("graph_id") != graph.graph_id
        or lineage.get("root_work_order_id") != graph.root_work_order_id
        or lineage.get("revision") != graph.revision
        or lineage.get("result_node_id") != graph.result_node_id
        or lineage.get("completion_policy")
        != TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1.value
    ):
        raise GovernedPlanExecutionPolicyError(
            "capability Plan input, selection and graph lineage differ"
        )
    bindings = lineage.get("node_bindings")
    if not isinstance(bindings, list) or len(bindings) != len(graph.nodes):
        raise GovernedPlanExecutionPolicyError(
            "planning_lineage.node_bindings must cover the graph exactly"
        )
    node_review_policies = _mapping(
        dict(work_order.review_policy or {}).get(NODE_REVIEW_POLICIES_KEY),
        field_name=NODE_REVIEW_POLICIES_KEY,
    )
    if set(node_review_policies) != {node.node_id for node in graph.nodes}:
        raise GovernedPlanExecutionPolicyError(
            "node Review policy must cover the graph exactly"
        )
    if not callable(getattr(skill_registry, "require", None)):
        raise GovernedPlanExecutionPolicyError(
            "app-local SkillRegistry is unavailable"
        )
    node_policies: list[dict[str, Any]] = []
    workspace_effect_nodes = 0
    for index, (candidate_id, node, raw_binding) in enumerate(
        zip(selected_candidate_ids, graph.nodes, bindings, strict=True)
    ):
        binding = _mapping(
            raw_binding,
            field_name=f"node_bindings[{index}]",
        )
        try:
            route = GovernedWorkerRoute.parse(node.route)
        except Exception as exc:
            raise GovernedPlanExecutionPolicyError(
                f"TaskNode {node.node_id} has an invalid worker route"
            ) from exc
        target_id = str(route.target_id or "").strip()
        objective = str(binding.get("objective") or "").strip()
        if not objective:
            raise GovernedPlanExecutionPolicyError(
                f"TaskNode {node.node_id} binding has no objective"
            )
        approved = _mapping(
            approved_inputs.get(candidate_id),
            field_name=f"approved_candidate_inputs.{candidate_id}",
        )
        expected_binding = {
            "candidate_id": candidate_id,
            "node_id": node.node_id,
            "objective": objective,
            "worker_route": route.kind,
            "target_id": target_id,
            "depends_on_candidate_ids": [
                selected_candidate_ids[
                    tuple(item.node_id for item in graph.nodes).index(dependency)
                ]
                for dependency in node.dependencies
            ],
            "depends_on_node_ids": list(node.dependencies),
        }
        if binding != expected_binding:
            raise GovernedPlanExecutionPolicyError(
                f"TaskNode {node.node_id} differs from its selected binding"
            )
        if (
            approved.get("route") != route.kind
            or approved.get("target_id") != target_id
            or approved.get("objective") != objective
            or tuple(
                _text_sequence(
                    approved.get("depends_on_candidate_ids"),
                    field_name=(
                        f"approved_candidate_inputs.{candidate_id}."
                        "depends_on_candidate_ids"
                    ),
                )
            )
            != tuple(expected_binding["depends_on_candidate_ids"])
        ):
            raise GovernedPlanExecutionPolicyError(
                f"TaskNode {node.node_id} differs from its approved candidate input"
            )
        review_required = pre_execution_review_required(
            work_order,
            node_id=node.node_id,
        )
        if index < CAPABILITY_PLANNING_EXECUTION_STEPS:
            if route.kind != "skill" or not target_id:
                raise GovernedPlanExecutionPolicyError(
                    "pre-result capability Plan nodes must be Skills"
                )
            try:
                skill_meta = skill_registry.require(target_id)
            except Exception as exc:
                raise GovernedPlanExecutionPolicyError(
                    f"selected Skill is unavailable: {target_id}"
                ) from exc
            if governed_skill_policy_error(target_id, skill_meta) is not None:
                raise GovernedPlanExecutionPolicyError(
                    f"selected Skill policy is not executable: {target_id}"
                )
            execution_policy = getattr(skill_meta, "execution_policy", None)
            if execution_policy not in {
                SkillExecutionPolicy.INLINE_READ_ONLY,
                SkillExecutionPolicy.PRE_ACCEPTANCE_PROPOSAL_ONLY,
                SkillExecutionPolicy.POST_ACCEPTANCE_WORKSPACE,
            }:
                raise GovernedPlanExecutionPolicyError(
                    "intermediate Plan Skill execution policy is unsupported"
                )
            if (
                int(getattr(skill_meta, "risk_level", 0)) != node.risk_level
                or bool(getattr(skill_meta, "require_confirm", False))
                is not review_required
            ):
                raise GovernedPlanExecutionPolicyError(
                    f"selected Skill risk or Review policy drifted: {target_id}"
                )
            execution_policy_value = execution_policy.value
            budget_policy_value = getattr(
                getattr(skill_meta, "budget_policy", None),
                "value",
                "",
            )
            if (
                execution_policy
                is SkillExecutionPolicy.POST_ACCEPTANCE_WORKSPACE
            ):
                workspace_effect_nodes += 1
                effect_mode = PLAN_NODE_WORKSPACE_EFFECT_MODE
            else:
                effect_mode = PLAN_SPECULATIVE_EFFECT_MODE
        else:
            if (
                node.node_id != graph.result_node_id
                or route.kind != "chat"
                or target_id != "surface_reply"
                or review_required
            ):
                raise GovernedPlanExecutionPolicyError(
                    "final Plan node must be the unreviewed Chat result owner"
                )
            execution_policy_value = PLAN_CHAT_EXECUTION_POLICY
            budget_policy_value = PLAN_CHAT_BUDGET_POLICY
            effect_mode = PLAN_RESULT_EFFECT_MODE
        node_policies.append(
            {
                "node_id": node.node_id,
                "candidate_id": candidate_id,
                "route": route.kind,
                "target_id": target_id,
                "depends_on_node_ids": list(node.dependencies),
                "risk_level": node.risk_level,
                "review_required": review_required,
                "execution_policy": execution_policy_value,
                "budget_policy": budget_policy_value,
                "effect_mode": effect_mode,
            }
        )
    if workspace_effect_nodes > 1:
        raise GovernedPlanExecutionPolicyError(
            "one atomic Plan revision may select at most one write-capable "
            "Workspace Skill"
        )
    return freeze_plan_execution_policy(
        {
            "schema_version": (
                PLAN_EXECUTION_POLICY_SCHEMA_VERSION
            ),
            "graph_id": graph.graph_id,
            "graph_revision": graph.revision,
            "result_node_id": graph.result_node_id,
            "completion_policy": graph.completion_policy.value,
            "execution_steps": CAPABILITY_PLANNING_EXECUTION_STEPS,
            "work_order_budget_digest": _sha256_ref(budget.to_dict()),
            "nodes": node_policies,
        }
    )


def _mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GovernedPlanExecutionPolicyError(f"{field_name} must be an object")
    return dict(value)


def _text_sequence(value: Any, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise GovernedPlanExecutionPolicyError(f"{field_name} must be a list")
    normalized = tuple(str(item or "").strip() for item in value)
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        raise GovernedPlanExecutionPolicyError(
            f"{field_name} contains invalid or duplicate values"
        )
    return normalized


def _sha256_ref(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "GovernedPlanExecutionPolicyError",
    "bind_governed_plan_execution_policy",
    "require_governed_plan_execution_policy",
]
