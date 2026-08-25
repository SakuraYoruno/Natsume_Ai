"""Cancellation-bound signed Operation reconciliation persistence adapter."""

from __future__ import annotations

from typing import Any

from host_brain.contracts.agent_runtime.operation_reconciliation import (
    OperationReconciliationStoreRejected,
)

from .task_store_types import (
    IdempotencyRecoveryAction,
    OutboxMessage,
    TASK_STORE_CANCELLATION_CONFLICT,
    TASK_STORE_INVALID,
    TaskStoreError,
)


class TaskOperationReconciliationStoreMixin:
    """Expose only COMMIT/RELEASE behind an exact pending cancellation fence."""

    def reconcile_graph_cancellation_operation(
        self,
        intent_ref: str,
        reservation_id: str,
        *,
        action: IdempotencyRecoveryAction | str,
        expected_version: int,
        outbox: OutboxMessage,
        reconciler_actor_id: str,
        downstream_receipt_ref: str | None = None,
        attestation_ref: str | None = None,
        attestation_key_id: str | None = None,
        attestation_signature: str | None = None,
    ) -> dict[str, Any]:
        """Settle one operation only while its exact cancellation is pending.

        The shared recovery mutation performs the exact intent/Attempt lineage
        check, proof verification, CAS, terminal mutation and Outbox insert in
        one SQLite transaction.  This boundary deliberately excludes
        ``REACQUIRE`` and translates only transactional Store rejections into
        a definite no-commit result.
        """

        cancellation_reference = str(intent_ref or "").strip()
        if not cancellation_reference:
            raise OperationReconciliationStoreRejected(
                TASK_STORE_INVALID,
                "cancellation intent_ref is required",
            )
        try:
            normalized_action = IdempotencyRecoveryAction(
                str(action or "").strip().upper()
            )
        except ValueError as exc:
            raise OperationReconciliationStoreRejected(
                TASK_STORE_INVALID,
                f"unsupported cancellation reconciliation action: {action}",
            ) from exc
        if normalized_action not in {
            IdempotencyRecoveryAction.COMMIT,
            IdempotencyRecoveryAction.RELEASE,
        }:
            raise OperationReconciliationStoreRejected(
                TASK_STORE_CANCELLATION_CONFLICT,
                "cancellation reconciliation permits only COMMIT or RELEASE",
            )
        try:
            return self.recover_operation_reservation(
                reservation_id,
                action=normalized_action,
                expected_version=expected_version,
                outbox=outbox,
                reconciler_actor_id=reconciler_actor_id,
                downstream_receipt_ref=downstream_receipt_ref,
                attestation_ref=attestation_ref,
                attestation_key_id=attestation_key_id,
                attestation_signature=attestation_signature,
                _required_cancellation_intent_ref=cancellation_reference,
            )
        except TaskStoreError as exc:
            raise OperationReconciliationStoreRejected(
                exc.reason_code,
                exc.detail,
            ) from exc


__all__ = ["TaskOperationReconciliationStoreMixin"]
