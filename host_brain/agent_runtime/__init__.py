"""Lazy public exports for the Agent Runtime execution plane.

Importing a concrete ``agent_runtime`` contract must not initialize the whole
execution plane.  In particular, persistence repositories depend on the
contract modules below, while compatibility callers historically imported the
repository facades from this package.  PEP 562 lazy attributes preserve that
API without creating a package-initialization cycle.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "AgentExecutor": ("host_brain.agent_runtime.agent_executor", "AgentExecutor"),
    "AgentRuntimeExecutor": ("host_brain.agent_runtime.executor", "AgentRuntimeExecutor"),
    "AgentResult": ("host_brain.agent_runtime.result_contract", "AgentResult"),
    "AgentWorkOrder": ("host_brain.agent_runtime.work_order", "AgentWorkOrder"),
    "AcceptanceCriterionStatus": ("host_brain.agent_runtime.acceptance_judge", "AcceptanceCriterionStatus"),
    "AcceptanceCriterionVerdict": ("host_brain.agent_runtime.acceptance_judge", "AcceptanceCriterionVerdict"),
    "AcceptanceDecision": ("host_brain.agent_runtime.acceptance_judge", "AcceptanceDecision"),
    "AcceptanceJudgement": ("host_brain.agent_runtime.acceptance_judge", "AcceptanceJudgement"),
    "AttemptStatus": ("host_brain.persistence.task_repository", "AttemptStatus"),
    "BudgetAmount": ("host_brain.agent_runtime.budget_policy", "BudgetAmount"),
    "BudgetExceededAction": ("host_brain.agent_runtime.budget_policy", "BudgetExceededAction"),
    "BudgetPolicy": ("host_brain.agent_runtime.budget_policy", "BudgetPolicy"),
    "BudgetReservation": ("host_brain.agent_runtime.budget_policy", "BudgetReservation"),
    "BudgetReservationStatus": ("host_brain.agent_runtime.budget_policy", "BudgetReservationStatus"),
    "CanonicalPlannerOutput": ("host_brain.agent_runtime.planner_adapter", "CanonicalPlannerOutput"),
    "CanonicalRuntimeRouter": ("host_brain.agent_runtime.runtime_router", "CanonicalRuntimeRouter"),
    "CanonicalShadowScheduler": ("host_brain.agent_runtime.task_scheduler", "CanonicalShadowScheduler"),
    "ContractViolation": ("host_brain.agent_runtime.contract_errors", "ContractViolation"),
    "DurableTaskStore": ("host_brain.persistence.task_repository", "DurableTaskStore"),
    "Ed25519ReconciliationProofVerifier": ("host_brain.persistence.task_repository", "Ed25519ReconciliationProofVerifier"),
    "IdempotencyRecoveryAction": ("host_brain.persistence.task_repository", "IdempotencyRecoveryAction"),
    "OutboxMessage": ("host_brain.persistence.task_repository", "OutboxMessage"),
    "GraphRevisionIdentity": ("host_brain.agent_runtime.planner_adapter", "GraphRevisionIdentity"),
    "PlannerNodeView": ("host_brain.agent_runtime.planner_adapter", "PlannerNodeView"),
    "PLANNER_NEEDS_INPUT": ("host_brain.agent_runtime.planner_adapter", "PLANNER_NEEDS_INPUT"),
    "ReviewItem": ("host_brain.agent_runtime.review_policy", "ReviewItem"),
    "ReviewResumePhase": ("host_brain.agent_runtime.review_policy", "ReviewResumePhase"),
    "ReviewStatus": ("host_brain.agent_runtime.review_policy", "ReviewStatus"),
    "ReviewStore": ("host_brain.persistence.review_repository", "ReviewStore"),
    "ReviewStoreError": ("host_brain.persistence.review_repository", "ReviewStoreError"),
    "ReviewType": ("host_brain.agent_runtime.review_policy", "ReviewType"),
    "RuntimeAssignment": ("host_brain.agent_runtime.runtime_router", "RuntimeAssignment"),
    "RuntimeExecutor": ("host_brain.agent_runtime.runtime_router", "RuntimeExecutor"),
    "RuntimeMode": ("host_brain.agent_runtime.runtime_router", "RuntimeMode"),
    "RuntimeRouterError": ("host_brain.agent_runtime.runtime_router", "RuntimeRouterError"),
    "SchedulerAction": ("host_brain.agent_runtime.task_scheduler", "SchedulerAction"),
    "SchedulerDecision": ("host_brain.agent_runtime.task_scheduler", "SchedulerDecision"),
    "SchedulerShadowReport": ("host_brain.agent_runtime.task_scheduler", "SchedulerShadowReport"),
    "ShadowSchedulerError": ("host_brain.agent_runtime.task_scheduler", "ShadowSchedulerError"),
    "SubAgentRequestAdapter": ("host_brain.agent_runtime.sub_agent_contract", "SubAgentRequestAdapter"),
    "SubAgentRequestContract": ("host_brain.agent_runtime.sub_agent_contract", "SubAgentRequestContract"),
    "SubAgentSpec": ("host_brain.agent_runtime.sub_agent_contract", "SubAgentSpec"),
    "TaskEvent": ("host_brain.agent_runtime.task_graph", "TaskEvent"),
    "TaskGraph": ("host_brain.agent_runtime.task_graph", "TaskGraph"),
    "TaskGraphStatus": ("host_brain.agent_runtime.task_graph", "TaskGraphStatus"),
    "TaskNode": ("host_brain.agent_runtime.task_graph", "TaskNode"),
    "TaskNodeStatus": ("host_brain.agent_runtime.task_graph", "TaskNodeStatus"),
    "TaskStoreError": ("host_brain.persistence.task_repository", "TaskStoreError"),
    "VerificationResult": ("host_brain.agent_runtime.verification_policy", "VerificationResult"),
    "VerificationStatus": ("host_brain.agent_runtime.verification_policy", "VerificationStatus"),
    "WorkOrder": ("host_brain.agent_runtime.work_order", "WorkOrder"),
    "WorkOrderV3": ("host_brain.agent_runtime.work_order", "WorkOrderV3"),
    "adapt_planning_agent_plan": ("host_brain.agent_runtime.planner_adapter", "adapt_planning_agent_plan"),
    "adapt_skill_dag_plan": ("host_brain.agent_runtime.planner_adapter", "adapt_skill_dag_plan"),
    "agent_enabled": ("host_brain.agent_runtime.agent_executor", "agent_enabled"),
    "build_reconciliation_attestation_binding": ("host_brain.persistence.task_repository", "build_reconciliation_attestation_binding"),
    "build_outbox_payload_ref": ("host_brain.persistence.task_repository", "build_outbox_payload_ref"),
    "build_operation_fingerprint": ("host_brain.persistence.task_repository", "build_operation_fingerprint"),
    "build_child_work_order_for_plan_node": ("host_brain.agent_runtime.planner_adapter", "build_child_work_order_for_plan_node"),
    "build_planner_target_binding": ("host_brain.agent_runtime.planner_adapter", "build_planner_target_binding"),
    "build_planner_shadow_comparison": ("host_brain.agent_runtime.planner_adapter", "build_planner_shadow_comparison"),
    "execute_agent_production_payload": ("host_brain.agent_runtime.production_executor", "execute_agent_production_payload"),
    "execute_agent_runtime_payload": ("host_brain.agent_runtime.executor", "execute_agent_runtime_payload"),
    "evaluate_configured_shadow": ("host_brain.agent_runtime.task_scheduler", "evaluate_configured_shadow"),
    "evaluate_task_runtime_shadow": ("host_brain.agent_runtime.executor", "evaluate_task_runtime_shadow"),
    "judge_acceptance_result": ("host_brain.agent_runtime.acceptance_judge", "judge_acceptance_result"),
    "validate_same_graph_revision": ("host_brain.agent_runtime.planner_adapter", "validate_same_graph_revision"),
    "validate_planner_target_binding": ("host_brain.agent_runtime.sub_agent_contract", "validate_planner_target_binding"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()).union(__all__))
