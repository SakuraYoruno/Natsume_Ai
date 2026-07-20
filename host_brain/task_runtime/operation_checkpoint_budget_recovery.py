"""Budget recovery boundary for a committed Operation checkpoint."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .admission import AdmittedExecution
from .budget_runtime import BudgetCharge, BudgetRuntimeContext, DurableBudgetRuntime
from .coordinator_receipts import build_execution_receipt, terminal_event
from .coordinator_records import _exception_reason
from .errors import GOVERNED_OPERATION_RECOVERY_REQUIRED, GovernedRuntimeError
from .operation_checkpoint_validation import WorkerBudgetUsageCheckpointState
from .operation_recovery_runtime import CommittedOperationCheckpoint
from .runtime_outbox import RuntimeOutbox


async def recover_checkpoint_budget(
    *,
    budget_runtime: DurableBudgetRuntime,
    runtime_outbox: RuntimeOutbox,
    admitted: AdmittedExecution,
    budget: BudgetRuntimeContext,
    checkpoint: CommittedOperationCheckpoint,
    worker_commit: Mapping[str, Any],
    attempt_id: str,
    receipt_ref: str,
    payload_digest: str,
    evidence_refs: tuple[str, ...],
) -> tuple[BudgetCharge | None, dict[str, Any] | None]:
    """Reconcile budget evidence or return a non-mutating recovery hold.

    A governed reconciliation failure is evidence ambiguity, not worker
    failure.  This boundary deliberately owns no lifecycle authority and does
    not create a terminal Outbox event.
    """

    try:
        state = checkpoint.budget_usage_state
        if state in {
            WorkerBudgetUsageCheckpointState.EXACT_ACCEPTED,
            WorkerBudgetUsageCheckpointState.EXACT_REJECTED,
        }:
            if not isinstance(checkpoint.budget_usage, Mapping):
                _blocked("exact worker BudgetUsage checkpoint lost its durable row")
            charge = await budget_runtime.reconcile_worker_checkpoint(
                budget,
                node_id=admitted.node_id,
                attempt_id=attempt_id,
                worker_commit=worker_commit,
                durable_usage=checkpoint.budget_usage,
            )
        elif state is WorkerBudgetUsageCheckpointState.MISSING:
            durable_elapsed_ms = checkpoint.durable_elapsed_ms
            if (
                type(durable_elapsed_ms) is not int
                or durable_elapsed_ms <= 0
                or checkpoint.lease is None
            ):
                _blocked(
                    "missing worker BudgetUsage has no positive durable elapsed "
                    "time and takeover Lease"
                )
            charge = await budget_runtime.settle_worker_checkpoint(
                budget,
                node_id=admitted.node_id,
                attempt_id=attempt_id,
                worker_commit=worker_commit,
                durable_elapsed_ms=durable_elapsed_ms,
            )
        else:
            _blocked("worker BudgetUsage checkpoint is conflicting")
    except GovernedRuntimeError as exc:
        await runtime_outbox.publish_pending()
        receipt = build_execution_receipt(
            admitted,
            attempt_id=attempt_id,
            receipt_ref=receipt_ref,
            payload_digest=payload_digest,
            status="RECOVERY_REQUIRED",
            verification_status="NOT_RUN",
            accepted=False,
            reason_code=_exception_reason(
                exc,
                GOVERNED_OPERATION_RECOVERY_REQUIRED,
            ),
            evidence_refs=evidence_refs,
            projection={
                "worker_replayed": False,
                "budget_usage_reconstructed": (
                    checkpoint.budget_usage_state
                    is WorkerBudgetUsageCheckpointState.MISSING
                ),
                "budget": budget.execution_payload(),
                "recovery_state": "HOLD",
            },
        )
        return None, terminal_event(receipt)
    return charge, None


def _blocked(detail: str) -> None:
    raise GovernedRuntimeError(GOVERNED_OPERATION_RECOVERY_REQUIRED, detail)


__all__ = ["recover_checkpoint_budget"]
