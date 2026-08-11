"""Transactional authority checks for durable Lease takeover windows."""
from __future__ import annotations
import sqlite3
from typing import Any, Mapping
from host_brain.contracts.agent_runtime.budget_policy import BudgetReservationStatus
from host_brain.contracts.agent_runtime.task_graph import TaskGraphStatus
from host_brain.persistence.task_lease_replay_identity import (
    lease_acquisition_replay_binding_is_valid,
)
from host_brain.persistence.task_operation_reacquisition_authority import (
    assert_signed_reacquisition_lease_authority,
)
from host_brain.persistence.task_store_types import (
    IdempotencyStatus,
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED,
    TASK_STORE_INVALID,
    TaskStoreError,
)
class TaskLeaseTakeoverAuthorityMixin:
    """Validate the two explicit takeover modes under ``BEGIN IMMEDIATE``."""

    @staticmethod
    def _require_preoperation_recovery_graph_budget(
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
    ) -> sqlite3.Row:
        rows = conn.execute(
            """
            SELECT *
            FROM task_runtime_budget_reservation
            WHERE graph_id = ? AND graph_revision = ?
              AND node_id IS NULL AND attempt_id IS NULL
            ORDER BY budget_id
            """,
            (str(graph_id), int(graph_revision)),
        ).fetchall()
        if (
            len(rows) != 1
            or str(rows[0]["status"])
            not in {
                BudgetReservationStatus.ACTIVE.value,
                BudgetReservationStatus.EXCEEDED.value,
            }
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "pre-operation recovery requires exactly one ACTIVE or EXCEEDED "
                "graph-level budget",
            )
        return rows[0]

    def _assert_lease_takeover_operation_authority(
        self,
        conn: sqlite3.Connection,
        *,
        attempt_id: str,
        attempt: sqlite3.Row,
        absent_operation_reservation_id: str | None,
        existing_operation_reservation_id: str | None,
        operation_reacquisition_authorization: Mapping[str, Any] | None,
        owner_id: str,
        expected_attempt_version: int,
        expected_lease_id: str,
        now_ts_ms: int,
    ) -> None:
        if (
            absent_operation_reservation_id is None
            and existing_operation_reservation_id is None
        ):
            self._assert_graph_accepts_execution_authority(
                conn,
                graph_id=str(attempt["graph_id"]),
                graph_revision=int(attempt["graph_revision"]),
                action="Lease acquisition",
            )
            return

        if absent_operation_reservation_id is not None:
            reservation_ref = str(absent_operation_reservation_id or "").strip()
            if not reservation_ref:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "absent Operation reservation identity is required",
                )
            existing_operation = conn.execute(
                """
                SELECT reservation_id
                FROM task_runtime_idempotency_reservation
                WHERE reservation_id = ?
                   OR (business_scope = ? AND operation_fingerprint = ?)
                ORDER BY reservation_id
                LIMIT 1
                """,
                (
                    reservation_ref,
                    str(attempt["operation_scope"] or ""),
                    str(attempt["operation_fingerprint"] or ""),
                ),
            ).fetchone()
            if existing_operation is not None:
                raise TaskStoreError(
                    TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED,
                    "Operation reservation appeared before Lease takeover",
                )
        else:
            reservation_ref = str(existing_operation_reservation_id or "").strip()
            if not reservation_ref:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "existing Operation reservation identity is required",
                )
            existing_operation = conn.execute(
                "SELECT * FROM task_runtime_idempotency_reservation "
                "WHERE reservation_id = ?",
                (reservation_ref,),
            ).fetchone()
            if existing_operation is None:
                raise TaskStoreError(
                    TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED,
                    "Lease takeover requires its exact Operation",
                )
            if operation_reacquisition_authorization is not None:
                assert_signed_reacquisition_lease_authority(
                    conn,
                    operation=existing_operation,
                    attempt=attempt,
                    attempt_id=str(attempt_id),
                    owner_id=str(owner_id),
                    expected_attempt_version=int(expected_attempt_version),
                    expected_lease_id=str(expected_lease_id),
                    now_ts_ms=int(now_ts_ms),
                    authorization=operation_reacquisition_authorization,
                    proof_verifier=self._reconciliation_proof_verifier,
                )
            elif (
                str(existing_operation["attempt_id"]) != str(attempt_id)
                or str(existing_operation["status"])
                != IdempotencyStatus.COMMITTED.value
                or str(existing_operation["business_scope"])
                != str(attempt["operation_scope"] or "")
                or str(existing_operation["operation_fingerprint"])
                != str(attempt["operation_fingerprint"] or "")
            ):
                raise TaskStoreError(
                    TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED,
                    "checkpoint Lease takeover requires the exact COMMITTED Operation",
                )

        graph = self._require_graph(
            conn,
            str(attempt["graph_id"]),
            int(attempt["graph_revision"]),
        )
        if str(graph["status"]) != TaskGraphStatus.ACTIVE.value:
            mode = (
                "pre-operation"
                if absent_operation_reservation_id is not None
                else "checkpoint"
            )
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                f"{mode} Lease takeover requires an ACTIVE TaskGraph",
            )
        self._require_preoperation_recovery_graph_budget(
            conn,
            graph_id=str(attempt["graph_id"]),
            graph_revision=int(attempt["graph_revision"]),
        )

    def _replay_committed_lease_acquisition(
        self,
        conn: sqlite3.Connection,
        *,
        attempt: sqlite3.Row,
        attempt_id: str,
        owner_id: str,
        expected_attempt_version: int,
        expected_lease_id: str,
        expected_heartbeat_id: str,
        ttl_ms: int,
        now_ts_ms: int,
        outbox: OutboxMessage,
    ) -> dict[str, Any] | None:
        """Return an exact already-committed Lease acquisition response."""
        committed_version = int(expected_attempt_version) + 1
        if int(attempt["status_version"]) != committed_version:
            return None
        leases = conn.execute(
            """
            SELECT *
            FROM task_runtime_lease
            WHERE attempt_id = ? AND status = 'ACTIVE'
            ORDER BY fencing_token DESC, lease_id
            """,
            (str(attempt_id),),
        ).fetchall()
        if len(leases) != 1:
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                f"Lease acquisition replay authority drifted for Attempt {attempt_id}",
            )
        lease = leases[0]
        previous_fence = conn.execute(
            "SELECT COALESCE(MAX(fencing_token), 0) FROM task_runtime_lease "
            "WHERE attempt_id = ? AND lease_id <> ?",
            (str(attempt_id), str(expected_lease_id)),
        ).fetchone()
        heartbeats = conn.execute(
            "SELECT * FROM task_runtime_heartbeat WHERE lease_id = ? "
            "ORDER BY observed_ts_ms, heartbeat_id",
            (str(expected_lease_id),),
        ).fetchall()
        if (
            len(heartbeats) != 1
            or not lease_acquisition_replay_binding_is_valid(
                attempt=attempt,
                lease=lease,
                heartbeat=heartbeats[0] if heartbeats else {},
                attempt_id=str(attempt_id),
                owner_id=str(owner_id),
                expected_lease_id=str(expected_lease_id),
                expected_heartbeat_id=str(expected_heartbeat_id),
                ttl_ms=int(ttl_ms),
                now_ts_ms=int(now_ts_ms),
                previous_fencing_token=int(previous_fence[0]),
            )
        ):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                f"Lease acquisition replay binding drifted for Attempt {attempt_id}",
            )
        self._assert_mutation_outbox_replay(
            conn,
            outbox,
            expected_aggregate=(
                "TASK_ATTEMPT",
                str(attempt_id),
                committed_version,
            ),
            expected_event_type="TASK_ATTEMPT_LEASED",
            expected_attempt_id=str(attempt_id),
        )
        return dict(lease)
__all__ = ["TaskLeaseTakeoverAuthorityMixin"]
