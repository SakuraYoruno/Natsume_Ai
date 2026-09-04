"""Shared validation for durable TaskNode acceptance decisions."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from host_brain.contracts.agent_runtime.acceptance_judge import (
    AcceptanceDecision,
    AcceptanceGate,
    AcceptanceJudgement,
)
from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.contracts.agent_runtime.task_graph import TaskNodeStatus
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    TASK_STORE_INVALID,
    TaskStoreError,
    _non_negative_task_version as non_negative_version,
    _required_task_text as required_text,
    _task_node_subject as acceptance_subject,
)


NODE_ACCEPTANCE_SCHEMA_VERSION = (
    "Natsume.task_runtime.node_acceptance_decision.v1"
)
NODE_ACCEPTANCE_REQUIRED_COLUMNS = frozenset(
    {
        "acceptance_ref",
        "schema_version",
        "judgement_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "node_version_before",
        "node_version_after",
        "target_status",
        "result_json",
        "result_sha256",
        "evidence_refs_json",
        "node_outbox_id",
        "review_id",
        "graph_version_before",
        "graph_version_after",
        "graph_outbox_id",
        "created_ts_ms",
    }
)
NODE_ACCEPTANCE_REQUIRED_TRIGGERS = frozenset(
    {
        "trg_task_runtime_node_acceptance_no_update",
        "trg_task_runtime_node_acceptance_no_delete",
    }
)
NODE_ACCEPTANCE_TARGETS = frozenset(
    {
        TaskNodeStatus.ACCEPTED,
        TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW,
    }
)


def acceptance_target(value: TaskNodeStatus | str) -> TaskNodeStatus:
    try:
        target = TaskNodeStatus(str(getattr(value, "value", value)))
    except ValueError as exc:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"unsupported acceptance target: {value}",
        ) from exc
    if target not in NODE_ACCEPTANCE_TARGETS:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "acceptance target must be ACCEPTED or WAITING_ACCEPTANCE_REVIEW",
        )
    return target


def validated_acceptance(
    value: AcceptanceJudgement | Mapping[str, Any],
    *,
    target: TaskNodeStatus,
    graph_id: str,
    node_id: str,
    root_work_order_id: str,
    attempt_id: str,
    node_acceptance_criteria: tuple[str, ...],
    outbox: OutboxMessage,
) -> AcceptanceJudgement:
    try:
        judgement = (
            value
            if isinstance(value, AcceptanceJudgement)
            else AcceptanceJudgement.from_mapping(value)
        )
        if target is TaskNodeStatus.ACCEPTED:
            accepted = AcceptanceGate.validate_node_transition(
                judgement,
                target_status=target.value,
                graph_id=graph_id,
                node_id=node_id,
                root_work_order_id=root_work_order_id,
                node_acceptance_criteria=node_acceptance_criteria,
                outbox_evidence_refs=outbox.evidence_refs,
            )
            if accepted is None:
                raise ContractViolation(
                    "ACCEPTANCE_JUDGEMENT_INVALID",
                    "ACCEPTED transition lost its AcceptanceJudgement",
                )
            judgement = accepted
        else:
            _validate_review_judgement(
                judgement,
                graph_id=graph_id,
                node_id=node_id,
                root_work_order_id=root_work_order_id,
                node_acceptance_criteria=node_acceptance_criteria,
                outbox_evidence_refs=outbox.evidence_refs,
            )
    except ContractViolation as exc:
        raise TaskStoreError(exc.reason_code, exc.detail) from exc
    if judgement.attempt_id != attempt_id:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "AcceptanceJudgement must bind the exact Attempt",
        )
    if outbox.attempt_id != attempt_id:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "acceptance Node Outbox must bind the exact Attempt",
        )
    return judgement


def _validate_review_judgement(
    judgement: AcceptanceJudgement,
    *,
    graph_id: str,
    node_id: str,
    root_work_order_id: str,
    node_acceptance_criteria: tuple[str, ...],
    outbox_evidence_refs: tuple[str, ...],
) -> None:
    judgement.validate()
    if judgement.decision is AcceptanceDecision.ACCEPT:
        raise ContractViolation(
            "ACCEPTANCE_JUDGEMENT_INVALID",
            "WAITING_ACCEPTANCE_REVIEW requires a non-ACCEPT judgement",
        )
    if judgement.work_order_id != root_work_order_id:
        raise ContractViolation(
            "ACCEPTANCE_JUDGEMENT_INVALID",
            "AcceptanceJudgement.work_order_id must match TaskGraph.root_work_order_id",
        )
    if (
        judgement.task_graph_id != graph_id
        or judgement.task_node_id != node_id
    ):
        raise ContractViolation(
            "ACCEPTANCE_JUDGEMENT_INVALID",
            "AcceptanceJudgement task graph/node identity must match the transition",
        )
    expected = tuple(
        str(item or "").strip()
        for item in node_acceptance_criteria
        if str(item or "").strip()
    )
    actual = tuple(item.criterion_text for item in judgement.criteria)
    if expected and actual != expected:
        raise ContractViolation(
            "ACCEPTANCE_JUDGEMENT_INVALID",
            "AcceptanceJudgement criteria must match TaskNode.acceptance_criteria",
        )
    missing = sorted(set(judgement.evidence_refs) - set(outbox_evidence_refs))
    if missing:
        raise ContractViolation(
            "ACCEPTANCE_EVIDENCE_REQUIRED",
            "outbox evidence_refs must include AcceptanceJudgement evidence refs",
        )


def acceptance_ref(
    *,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    node_version_after: int,
    result_sha256: str,
    node_outbox_id: str,
    review_id: str | None,
    graph_version_before: int | None,
    graph_version_after: int | None,
    graph_outbox_id: str | None,
) -> str:
    material = "\n".join(
        (
            graph_id,
            str(graph_revision),
            node_id,
            attempt_id,
            str(node_version_after),
            result_sha256,
            node_outbox_id,
            str(review_id or ""),
            (
                str(graph_version_before)
                if graph_version_before is not None
                else ""
            ),
            (
                str(graph_version_after)
                if graph_version_after is not None
                else ""
            ),
            str(graph_outbox_id or ""),
        )
    )
    return (
        "task-acceptance://sha256/"
        + hashlib.sha256(material.encode("utf-8")).hexdigest()
    )


__all__ = [
    "NODE_ACCEPTANCE_REQUIRED_COLUMNS",
    "NODE_ACCEPTANCE_REQUIRED_TRIGGERS",
    "NODE_ACCEPTANCE_SCHEMA_VERSION",
    "acceptance_ref",
    "acceptance_subject",
    "acceptance_target",
    "non_negative_version",
    "required_text",
    "validated_acceptance",
]
