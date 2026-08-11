"""Pure deterministic identity for one durable retry scheduling mutation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount
from host_brain.persistence.task_record_codec import _dumps
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    TASK_STORE_INVALID,
    TaskStoreError,
)


@dataclass(frozen=True, slots=True)
class RetryScheduleIdentity:
    failure_ref: str
    budget_ref: str
    retry_delta: BudgetAmount
    retry_delta_json: str
    retry_delta_digest: str
    retry_usage_digest: str
    retry_usage_id: str
    retry_evidence_refs: tuple[str, str]


def build_retry_schedule_identity(
    *,
    error_ref: str,
    budget_id: str,
    attempt_id: str,
    expected_graph_version: int | None,
    terminalize_graph_on_budget_rejection: bool,
    attempt_outbox: OutboxMessage,
) -> RetryScheduleIdentity:
    """Validate request shape and derive content-addressed retry evidence."""

    failure_ref = str(error_ref or "").strip()
    budget_ref = str(budget_id or "").strip()
    if not failure_ref or not budget_ref:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "retry scheduling requires error_ref and budget_id",
        )
    if not terminalize_graph_on_budget_rejection and (
        isinstance(expected_graph_version, bool)
        or not isinstance(expected_graph_version, int)
        or expected_graph_version < 0
    ):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "node-only retry scheduling requires a non-negative expected_graph_version",
        )
    retry_delta = BudgetAmount(retry_count=1)
    retry_delta_json = _dumps(retry_delta.to_dict())
    retry_delta_digest = "sha256:" + hashlib.sha256(
        retry_delta_json.encode("utf-8")
    ).hexdigest()
    usage_material = f"{budget_ref}\n{attempt_id}\nretry"
    if not terminalize_graph_on_budget_rejection:
        usage_material += "\nnode-only"
    retry_usage_digest = hashlib.sha256(
        usage_material.encode("utf-8")
    ).hexdigest()
    retry_usage_id = f"budget-usage://{retry_usage_digest}"
    evidence_refs = (failure_ref, retry_usage_id)
    if tuple(attempt_outbox.evidence_refs) != evidence_refs:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "retry attempt outbox must bind error_ref and retry usage",
        )
    return RetryScheduleIdentity(
        failure_ref=failure_ref,
        budget_ref=budget_ref,
        retry_delta=retry_delta,
        retry_delta_json=retry_delta_json,
        retry_delta_digest=retry_delta_digest,
        retry_usage_digest=retry_usage_digest,
        retry_usage_id=retry_usage_id,
        retry_evidence_refs=evidence_refs,
    )


__all__ = ["RetryScheduleIdentity", "build_retry_schedule_identity"]
