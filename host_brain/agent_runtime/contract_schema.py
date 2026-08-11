"""Deterministic schema snapshot for canonical Agent runtime contracts."""

from __future__ import annotations

from dataclasses import fields
from enum import StrEnum
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import (
    BUDGET_POLICY_SCHEMA_VERSION,
    BUDGET_RESERVATION_SCHEMA_VERSION,
    BudgetAmount,
    BudgetExceededAction,
    BudgetPolicy,
    BudgetReservation,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.acceptance_judge import (
    ACCEPTANCE_JUDGEMENT_SCHEMA_VERSION,
    AcceptanceCriterionStatus,
    AcceptanceCriterionVerdict,
    AcceptanceDecision,
    AcceptanceJudgement,
)
from host_brain.contracts.agent_runtime.contract_errors import (
    ACCEPTANCE_JUDGEMENT_INVALID,
    BUDGET_POLICY_INVALID,
    BUDGET_RESERVATION_INVALID,
    BUDGET_TRANSITION_INVALID,
    CONTRACT_FIELD_INVALID,
    CONTRACT_VERSION_REQUIRED,
    CONTRACT_VERSION_UNSUPPORTED,
    PLAN_CYCLE,
    REVIEW_ITEM_INVALID,
    REVIEW_TRANSITION_INVALID,
    SUB_AGENT_ADAPTER_INVALID,
    SUB_AGENT_LINEAGE_INVALID,
    TASK_GRAPH_INVALID,
    TASK_TRANSITION_INVALID,
    VERIFICATION_RESULT_INVALID,
)
from host_brain.contracts.agent_runtime.review_policy import (
    REVIEW_ITEM_SCHEMA_VERSION,
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.agent_runtime.sub_agent_contract import (
    SUB_AGENT_REQUEST_SCHEMA_VERSION,
    SUB_AGENT_SPEC_SCHEMA_VERSION,
    SubAgentRequestContract,
    SubAgentSpec,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TASK_EVENT_SCHEMA_VERSION,
    TASK_GRAPH_SCHEMA_VERSION,
    TASK_NODE_SCHEMA_VERSION,
    EventActorType,
    EventRedactionLevel,
    TaskEvent,
    TaskGraph,
    TaskGraphCompletionPolicy,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.verification_contract import (
    VERIFICATION_RESULT_SCHEMA_VERSION,
    VerificationResult,
    VerificationStatus,
)
from host_brain.contracts.agent_runtime.work_order import WORK_ORDER_V3_SCHEMA_VERSION, WorkOrderV3


CONTRACT_SCHEMA_SNAPSHOT_VERSION = "Natsume.agent.canonical_contracts.snapshot.v1"


def _field_names(contract_type: type[Any]) -> list[str]:
    return [item.name for item in fields(contract_type) if not item.name.startswith("_")]


def _enum_values(enum_type: type[StrEnum]) -> list[str]:
    return [item.value for item in enum_type]


def _entry(
    contract_type: type[Any],
    *,
    schema_version: str | None,
    required_fields: list[str],
    enum_fields: dict[str, type[StrEnum]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "fields": _field_names(contract_type),
        "required_fields": list(required_fields),
        "enum_fields": {
            field_name: _enum_values(enum_type)
            for field_name, enum_type in sorted((enum_fields or {}).items())
        },
    }


def build_contract_schema_snapshot() -> dict[str, Any]:
    """Return the frozen wire-surface snapshot used by T-NLE-04."""

    contracts = {
        "AcceptanceCriterionVerdict": _entry(
            AcceptanceCriterionVerdict,
            schema_version=None,
            required_fields=[
                "criterion_id",
                "criterion_text",
                "verdict",
                "evidence_refs",
                "deterministic",
            ],
            enum_fields={"verdict": AcceptanceCriterionStatus},
        ),
        "AcceptanceJudgement": _entry(
            AcceptanceJudgement,
            schema_version=ACCEPTANCE_JUDGEMENT_SCHEMA_VERSION,
            required_fields=[
                "schema_version",
                "judgement_id",
                "work_order_id",
                "decision",
                "criteria",
                "evidence_refs",
            ],
            enum_fields={"decision": AcceptanceDecision},
        ),
        "BudgetAmount": _entry(
            BudgetAmount,
            schema_version=None,
            required_fields=[],
        ),
        "BudgetPolicy": _entry(
            BudgetPolicy,
            schema_version=BUDGET_POLICY_SCHEMA_VERSION,
            required_fields=["schema_version", "scope", "limits", "on_exceeded"],
            enum_fields={"on_exceeded": BudgetExceededAction},
        ),
        "BudgetReservation": _entry(
            BudgetReservation,
            schema_version=BUDGET_RESERVATION_SCHEMA_VERSION,
            required_fields=[
                "schema_version",
                "budget_id",
                "scope",
                "limits",
                "reserved",
                "consumed",
                "status",
                "status_version",
            ],
            enum_fields={"status": BudgetReservationStatus},
        ),
        "ReviewItem": _entry(
            ReviewItem,
            schema_version=REVIEW_ITEM_SCHEMA_VERSION,
            required_fields=[
                "schema_version",
                "review_id",
                "review_type",
                "subject_ref",
                "subject_version",
                "requester",
                "reason",
                "risk_level",
                "side_effects",
                "evidence_refs",
                "status",
                "expires_at",
                "resume_phase",
                "resume_count",
            ],
            enum_fields={
                "review_type": ReviewType,
                "resume_phase": ReviewResumePhase,
                "status": ReviewStatus,
            },
        ),
        "SubAgentRequest": _entry(
            SubAgentRequestContract,
            schema_version=SUB_AGENT_REQUEST_SCHEMA_VERSION,
            required_fields=[
                "schema_version",
                "request_id",
                "session_id",
                "agent_id",
                "child_work_order",
                "context_refs",
                "timeout_ms",
            ],
        ),
        "SubAgentSpec": _entry(
            SubAgentSpec,
            schema_version=SUB_AGENT_SPEC_SCHEMA_VERSION,
            required_fields=[
                "schema_version",
                "agent_id",
                "capability_requirements",
                "allowed_actions",
                "timeout_ms",
                "max_attempts",
                "context_contract",
            ],
        ),
        "TaskEvent": _entry(
            TaskEvent,
            schema_version=TASK_EVENT_SCHEMA_VERSION,
            required_fields=[
                "schema_version",
                "event_id",
                "event_type",
                "ts_ms",
                "trace_id",
                "actor_type",
                "actor_id",
                "sequence_no",
                "cursor",
                "evidence_refs",
                "redaction_level",
            ],
            enum_fields={
                "actor_type": EventActorType,
                "redaction_level": EventRedactionLevel,
            },
        ),
        "TaskGraph": _entry(
            TaskGraph,
            schema_version=TASK_GRAPH_SCHEMA_VERSION,
            required_fields=[
                "schema_version",
                "graph_id",
                "root_work_order_id",
                "revision",
                "planner_ref",
                "status",
                "created_at",
                "nodes",
            ],
            enum_fields={
                "status": TaskGraphStatus,
                "completion_policy": TaskGraphCompletionPolicy,
            },
        ),
        "TaskNode": _entry(
            TaskNode,
            schema_version=TASK_NODE_SCHEMA_VERSION,
            required_fields=[
                "schema_version",
                "node_id",
                "dependencies",
                "route",
                "input_refs",
                "output_contract",
                "retry_policy",
                "risk_level",
                "acceptance_criteria",
                "status",
                "status_version",
            ],
            enum_fields={"status": TaskNodeStatus},
        ),
        "VerificationResult": _entry(
            VerificationResult,
            schema_version=VERIFICATION_RESULT_SCHEMA_VERSION,
            required_fields=[
                "schema_version",
                "verification_id",
                "work_order_id",
                "required",
                "status",
                "evidence_refs",
                "checks",
            ],
            enum_fields={"status": VerificationStatus},
        ),
        "WorkOrderV3": _entry(
            WorkOrderV3,
            schema_version=WORK_ORDER_V3_SCHEMA_VERSION,
            required_fields=_field_names(WorkOrderV3),
        ),
    }
    reason_codes = sorted(
        {
            ACCEPTANCE_JUDGEMENT_INVALID,
            BUDGET_POLICY_INVALID,
            BUDGET_RESERVATION_INVALID,
            BUDGET_TRANSITION_INVALID,
            CONTRACT_FIELD_INVALID,
            CONTRACT_VERSION_REQUIRED,
            CONTRACT_VERSION_UNSUPPORTED,
            PLAN_CYCLE,
            REVIEW_ITEM_INVALID,
            REVIEW_TRANSITION_INVALID,
            SUB_AGENT_ADAPTER_INVALID,
            SUB_AGENT_LINEAGE_INVALID,
            TASK_GRAPH_INVALID,
            TASK_TRANSITION_INVALID,
            VERIFICATION_RESULT_INVALID,
        }
    )
    return {
        "snapshot_version": CONTRACT_SCHEMA_SNAPSHOT_VERSION,
        "contracts": dict(sorted(contracts.items())),
        "reason_codes": reason_codes,
    }


__all__ = ["CONTRACT_SCHEMA_SNAPSHOT_VERSION", "build_contract_schema_snapshot"]
