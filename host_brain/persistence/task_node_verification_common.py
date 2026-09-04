"""Shared validation for durable TaskNode verification decisions."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.contracts.agent_runtime.task_graph import TaskNodeStatus
from host_brain.contracts.agent_runtime.verification_contract import (
    VerificationGate,
    VerificationResult,
)
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    TASK_STORE_INVALID,
    TaskStoreError,
    _non_negative_task_version as non_negative_version,
    _required_task_text as required_text,
    _task_node_subject as verification_subject,
)


NODE_VERIFICATION_SCHEMA_VERSION = (
    "Natsume.task_runtime.node_verification_decision.v1"
)
NODE_VERIFICATION_REQUIRED_COLUMNS = frozenset(
    {
        "verification_ref",
        "schema_version",
        "verification_id",
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
        "created_ts_ms",
    }
)
NODE_VERIFICATION_REQUIRED_TRIGGERS = frozenset(
    {
        "trg_task_runtime_node_verification_no_update",
        "trg_task_runtime_node_verification_no_delete",
    }
)


def verification_target(value: TaskNodeStatus | str) -> TaskNodeStatus:
    try:
        target = TaskNodeStatus(str(getattr(value, "value", value)))
    except ValueError as exc:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"unsupported verification target: {value}",
        ) from exc
    if target not in {
        TaskNodeStatus.VERIFIED,
        TaskNodeStatus.VERIFICATION_FAILED,
    }:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "verification target must be VERIFIED or VERIFICATION_FAILED",
        )
    return target


def validated_verification(
    value: VerificationResult | Mapping[str, Any],
    *,
    target: TaskNodeStatus,
    graph_id: str,
    node_id: str,
    root_work_order_id: str,
    attempt_id: str,
    outbox: OutboxMessage,
) -> VerificationResult:
    try:
        verification = VerificationGate.validate_node_transition(
            value,
            target_status=target.value,
            graph_id=graph_id,
            node_id=node_id,
            root_work_order_id=root_work_order_id,
            outbox_evidence_refs=outbox.evidence_refs,
        )
    except ContractViolation as exc:
        raise TaskStoreError(exc.reason_code, exc.detail) from exc
    if verification is None or verification.attempt_id != attempt_id:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "VerificationResult must bind the exact Attempt",
        )
    if outbox.attempt_id != attempt_id:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "verification Node Outbox must bind the exact Attempt",
        )
    return verification


def verification_ref(
    *,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    node_version_after: int,
    result_sha256: str,
    node_outbox_id: str,
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
        )
    )
    return (
        "task-verification://sha256/"
        + hashlib.sha256(material.encode("utf-8")).hexdigest()
    )


__all__ = [
    "NODE_VERIFICATION_REQUIRED_COLUMNS",
    "NODE_VERIFICATION_REQUIRED_TRIGGERS",
    "NODE_VERIFICATION_SCHEMA_VERSION",
    "non_negative_version",
    "required_text",
    "validated_verification",
    "verification_ref",
    "verification_subject",
    "verification_target",
]
