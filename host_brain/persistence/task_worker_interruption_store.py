"""Immediate durable classification for known post-reservation interruptions."""

from __future__ import annotations

import re
from typing import Any

from host_brain.contracts.agent_runtime.operation_reconciliation import (
    OperationReconciliationStoreRejected,
)

from .task_store_types import (
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_INVALID,
    TaskStoreError,
    build_outbox_payload_ref,
)


class TaskWorkerInterruptionStoreMixin:
    """Classify interruption without deciding an external side effect."""

    def mark_worker_interruption_recovery_required(
        self,
        reservation_id: str,
        *,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        reason_code: str,
    ) -> dict[str, Any]:
        """Move one exact live Operation fence to ``RECOVERY_REQUIRED``.

        The mutation is atomic with its Outbox event. It never commits or
        releases the Operation and therefore cannot attribute an unknown
        external side effect. Exact response-loss replay is read-only.
        """

        reservation_ref = str(reservation_id or "").strip()
        attempt_ref = str(attempt_id or "").strip()
        lease_ref = str(lease_id or "").strip()
        owner_ref = str(owner_id or "").strip()
        reason = str(reason_code or "").strip()
        if (
            not reservation_ref
            or not attempt_ref
            or not lease_ref
            or not owner_ref
            or type(fencing_token) is not int
            or fencing_token < 1
            or type(expected_version) is not int
            or expected_version < 0
            or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,119}", reason)
        ):
            raise OperationReconciliationStoreRejected(
                TASK_STORE_INVALID,
                "worker interruption recovery identity or reason code is invalid",
            )
        try:
            with self._transaction() as conn:
                row = self._require_operation_reservation(conn, reservation_ref)
                status = str(row["status"])
                if status == "RECOVERY_REQUIRED":
                    if (
                        int(row["status_version"]) != expected_version + 1
                        or str(row["attempt_id"] or "") != attempt_ref
                        or str(row["lease_id"] or "") != lease_ref
                        or str(row["owner_id"] or "") != owner_ref
                        or int(row["fencing_token"] or 0) != fencing_token
                        or str(row["recovery_reason_code"] or "") != reason
                        or row["recovery_ts_ms"] is None
                    ):
                        raise OperationReconciliationStoreRejected(
                            TASK_STORE_CAS_CONFLICT,
                            "worker interruption recovery replay drifted",
                        )
                    next_version = expected_version + 1
                    idempotency_key = (
                        "operation-recovery-required:"
                        f"{reservation_ref}:{next_version}"
                    )
                    stored_outbox = conn.execute(
                        """
                        SELECT outbox_id FROM task_runtime_outbox
                        WHERE idempotency_key = ?
                        """,
                        (idempotency_key,),
                    ).fetchone()
                    replay_outbox = OutboxMessage.create(
                        aggregate_type="OPERATION_RESERVATION",
                        aggregate_id=reservation_ref,
                        aggregate_version=next_version,
                        event_type="OPERATION_RECOVERY_REQUIRED",
                        payload_ref=build_outbox_payload_ref(
                            aggregate_type="OPERATION_RESERVATION",
                            aggregate_id=reservation_ref,
                            aggregate_version=next_version,
                        ),
                        idempotency_key=idempotency_key,
                        attempt_id=attempt_ref,
                        outbox_id=(
                            str(stored_outbox["outbox_id"])
                            if stored_outbox is not None
                            else "outbox::missing-worker-interruption"
                        ),
                    )
                    self._assert_mutation_outbox_replay(
                        conn,
                        replay_outbox,
                        expected_aggregate=(
                            "OPERATION_RESERVATION",
                            reservation_ref,
                            next_version,
                        ),
                        expected_event_type="OPERATION_RECOVERY_REQUIRED",
                        expected_attempt_id=attempt_ref,
                    )
                    self._record_operation_recovery_hold_in_transaction(
                        conn,
                        reservation_id=reservation_ref,
                        reason_code=reason,
                    )
                    return self._operation_record(row)
                self._require_version(
                    row,
                    expected_version,
                    subject=f"OperationReservation {reservation_ref}",
                )
                ts_ms = self._now()
                self._assert_operation_binding(
                    conn,
                    row,
                    attempt_id=attempt_ref,
                    lease_id=lease_ref,
                    owner_id=owner_ref,
                    fencing_token=fencing_token,
                    now_ts_ms=ts_ms,
                )
                return self._operation_record(
                    self._mark_operation_recovery_required(
                        conn,
                        row,
                        reason_code=reason,
                        ts_ms=ts_ms,
                    )
                )
        except OperationReconciliationStoreRejected:
            raise
        except TaskStoreError as exc:
            raise OperationReconciliationStoreRejected(
                exc.reason_code,
                exc.detail,
            ) from exc


__all__ = ["TaskWorkerInterruptionStoreMixin"]
