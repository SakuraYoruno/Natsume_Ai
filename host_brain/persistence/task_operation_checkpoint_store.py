"""Fail-closed durable Operation checkpoint reads for online continuation."""

from __future__ import annotations

import re
from typing import Any

from host_brain.persistence.task_record_codec import _outbox_record
from host_brain.persistence.task_store_types import (
    AttemptStatus,
    IdempotencyStatus,
    LeaseStatus,
    TASK_STORE_DURABLE_DATA_INVALID,
    TaskStoreError,
)


_EXECUTION_DIGEST_REFERENCE_PATTERN = re.compile(
    r"^sha256://execution/[0-9a-f]{64}$"
)


class TaskOperationCheckpointStoreMixin:
    """Persist/recover only checkpoints with one exact durable lineage."""

    def reconcile_operation_reservation_state(
        self,
        reservation_id: str,
    ) -> dict[str, Any]:
        """Refresh one reservation against the serialized durable lease state.

        This is deliberately a restrictive transition only.  It may move an
        expired or unowned ``RESERVED`` row to ``RECOVERY_REQUIRED`` in the
        same transaction as its recovery Outbox event, but it never revives or
        otherwise rewrites a terminal reservation.
        """

        with self._transaction() as conn:
            ts_ms = self._now()
            row = self._require_operation_reservation(conn, reservation_id)
            self._operation_record(row)
            recovery_reason = self._operation_recovery_reason(conn, row, ts_ms)
            if recovery_reason is not None:
                row = self._mark_operation_recovery_required(
                    conn,
                    row,
                    reason_code=recovery_reason,
                    ts_ms=ts_ms,
                )
            return self._operation_record(row)

    def get_committed_operation_checkpoint(
        self,
        reservation_id: str,
    ) -> dict[str, Any]:
        """Return one fully bound post-COMMIT checkpoint or fail closed.

        Recovery callers must not assemble authority from several permissive
        readers.  This read validates the committed reservation, its exact
        Attempt and historical Lease, and the sole COMMITTED Outbox evidence
        as one durable checkpoint before exposing it to the runtime.
        """

        conn = self._connect()
        try:
            # Keep Reservation, Attempt, historical Lease, and Outbox on one
            # SQLite snapshot.  Autocommit SELECTs could otherwise observe a
            # takeover transition between records and assemble mixed epochs.
            conn.execute("BEGIN")
            reservation = self._operation_record(
                self._require_operation_reservation(conn, reservation_id)
            )
            if reservation["status"] != IdempotencyStatus.COMMITTED.value:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "operation checkpoint requires a COMMITTED reservation",
                )

            attempt_id = str(reservation["attempt_id"])
            attempt_row = conn.execute(
                "SELECT * FROM task_runtime_attempt WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if attempt_row is None:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "committed operation checkpoint is missing its Attempt",
                )
            attempt = dict(attempt_row)
            try:
                attempt_status = AttemptStatus(str(attempt["status"]))
                attempt_no = attempt["attempt_no"]
                attempt_version = attempt["status_version"]
                graph_revision = attempt["graph_revision"]
            except (KeyError, TypeError, ValueError) as exc:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    f"committed operation checkpoint Attempt is malformed: {exc}",
                ) from exc
            if (
                not attempt_status
                or type(attempt_no) is not int
                or attempt_no < 1
                or type(attempt_version) is not int
                or attempt_version < 0
                or type(graph_revision) is not int
                or graph_revision < 1
                or not str(attempt.get("graph_id") or "").strip()
                or not str(attempt.get("node_id") or "").strip()
                or str(attempt.get("operation_scope") or "")
                != str(reservation["business_scope"])
                or str(attempt.get("operation_fingerprint") or "")
                != str(reservation["operation_fingerprint"])
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "committed operation checkpoint Attempt identity drifted",
                )

            lease_id = str(reservation["lease_id"])
            lease_row = conn.execute(
                "SELECT * FROM task_runtime_lease WHERE lease_id = ?",
                (lease_id,),
            ).fetchone()
            if lease_row is None:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "committed operation checkpoint is missing its historical Lease",
                )
            lease = dict(lease_row)
            try:
                lease_status = LeaseStatus(str(lease["status"]))
                lease_fence = lease["fencing_token"]
                lease_version = lease["status_version"]
                acquired_ts_ms = lease["acquired_ts_ms"]
                heartbeat_ts_ms = lease["heartbeat_ts_ms"]
                expires_ts_ms = lease["expires_ts_ms"]
            except (KeyError, TypeError, ValueError) as exc:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    f"committed operation checkpoint historical Lease is malformed: {exc}",
                ) from exc
            if (
                not lease_status
                or type(lease_fence) is not int
                or lease_fence < 1
                or type(lease_version) is not int
                or lease_version < 0
                or type(acquired_ts_ms) is not int
                or type(heartbeat_ts_ms) is not int
                or type(expires_ts_ms) is not int
                or acquired_ts_ms > heartbeat_ts_ms
                or heartbeat_ts_ms > expires_ts_ms
                or str(lease.get("attempt_id") or "") != attempt_id
                or str(lease.get("owner_id") or "")
                != str(reservation["owner_id"])
                or lease_fence != int(reservation["fencing_token"])
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "committed operation checkpoint historical Lease binding drifted",
                )

            outbox_rows = conn.execute(
                """
                SELECT * FROM task_runtime_outbox
                WHERE aggregate_type = 'OPERATION_RESERVATION'
                  AND aggregate_id = ?
                  AND event_type IN (
                      'OPERATION_COMMITTED',
                      'OPERATION_RECOVERY_COMMIT'
                  )
                ORDER BY outbox_id
                """,
                (str(reservation["reservation_id"]),),
            ).fetchall()
            if len(outbox_rows) != 1:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "committed operation checkpoint requires exactly one "
                    "normal or signed-recovery COMMIT Outbox",
                )
            outbox = _outbox_record(outbox_rows[0])
            if (
                int(outbox.get("aggregate_version") or -1)
                != int(reservation["status_version"])
                or str(outbox.get("attempt_id") or "") != attempt_id
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "committed operation checkpoint Outbox Attempt binding drifted",
                )
            evidence_refs = tuple(outbox.get("evidence_refs") or ())
            receipt_ref = str(reservation["receipt_ref"])
            receipt_tail = receipt_ref.rsplit("/", 1)[-1]
            checkpoint_digest_ref = (
                f"sha256://execution/{receipt_tail}"
                if len(receipt_tail) == 64
                and all(
                    char in "0123456789abcdef"
                    for char in receipt_tail.lower()
                )
                else None
            )
            normal_commit = (
                outbox.get("event_type") == "OPERATION_COMMITTED"
                and len(evidence_refs) == 2
                and evidence_refs[0] == receipt_ref
                and _EXECUTION_DIGEST_REFERENCE_PATTERN.fullmatch(
                    str(evidence_refs[1])
                )
                is not None
                and evidence_refs[1] == checkpoint_digest_ref
            )
            signed_checkpoint_commit = (
                outbox.get("event_type") == "OPERATION_RECOVERY_COMMIT"
                and str(reservation.get("reconciliation_action") or "")
                == "COMMIT"
                and str(reservation.get("reconciliation_outcome") or "")
                == "SIDE_EFFECT_CONFIRMED"
                and str(
                    reservation.get("reconciliation_proof_ref") or ""
                )
                != ""
                and evidence_refs
                == (
                    str(reservation["reconciliation_proof_ref"]),
                    receipt_ref,
                )
                and receipt_ref.startswith("worker-projection://")
                and checkpoint_digest_ref is not None
            )
            if not normal_commit and not signed_checkpoint_commit:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "committed operation checkpoint evidence binding is incomplete or drifted",
                )
            if (
                len(evidence_refs) != 2
                or checkpoint_digest_ref is None
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "committed operation checkpoint evidence binding is incomplete or drifted",
                )
            return {
                "reservation": reservation,
                "attempt": attempt,
                "lease": lease,
                "outbox": outbox,
                "checkpoint_digest_ref": checkpoint_digest_ref,
            }
        finally:
            conn.rollback()
            conn.close()


__all__ = ["TaskOperationCheckpointStoreMixin"]
