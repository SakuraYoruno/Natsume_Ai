"""Atomic persistence adapter for signed worker-checkpoint confirmation."""

from __future__ import annotations

import sqlite3
from typing import Any

from host_brain.contracts.agent_runtime.operation_reconciliation import (
    OperationReconciliationStoreRejected,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphStatus,
    TaskNodeStatus,
)
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_record,
)

from .task_store_types import (
    AttemptStatus,
    IdempotencyRecoveryAction,
    OutboxMessage,
    TASK_STORE_CANCELLATION_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_LEASE_CONFLICT,
    TASK_STORE_NOT_FOUND,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TaskStoreError,
)


class TaskOperationCheckpointReconciliationStoreMixin:
    """Expose only COMMIT with an exact immutable worker projection."""

    def reconcile_operation_checkpoint(
        self,
        reservation_id: str,
        *,
        action: IdempotencyRecoveryAction | str,
        attempt_id: str,
        expected_version: int,
        outbox: OutboxMessage,
        reconciler_actor_id: str,
        downstream_receipt_ref: str,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
    ) -> dict[str, Any]:
        try:
            normalized_action = IdempotencyRecoveryAction(
                str(action or "").strip().upper()
            )
        except ValueError as exc:
            raise OperationReconciliationStoreRejected(
                TASK_STORE_INVALID,
                f"unsupported checkpoint reconciliation action: {action}",
            ) from exc
        if normalized_action is not IdempotencyRecoveryAction.COMMIT:
            raise OperationReconciliationStoreRejected(
                TASK_STORE_INVALID,
                "worker checkpoint reconciliation permits only COMMIT",
            )
        try:
            return self.recover_operation_reservation(
                reservation_id,
                action=normalized_action,
                attempt_id=attempt_id,
                expected_version=expected_version,
                outbox=outbox,
                reconciler_actor_id=reconciler_actor_id,
                downstream_receipt_ref=downstream_receipt_ref,
                attestation_ref=attestation_ref,
                attestation_key_id=attestation_key_id,
                attestation_signature=attestation_signature,
                _required_worker_projection_ref=downstream_receipt_ref,
                _require_expired_worker_lease=True,
            )
        except TaskStoreError as exc:
            raise OperationReconciliationStoreRejected(
                exc.reason_code,
                exc.detail,
            ) from exc


def assert_worker_checkpoint_reconciliation_in_transaction(
    store: Any,
    conn: sqlite3.Connection,
    *,
    operation_row: sqlite3.Row,
    attempt_id: str | None,
    projection_ref: str,
    observed_ts_ms: int,
    require_expired_lease: bool,
) -> None:
    """Validate the signed projection and execution fence in the mutation UoW."""

    if not isinstance(conn, sqlite3.Connection) or not conn.in_transaction:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "worker checkpoint validation requires an active transaction",
        )
    attempt_ref = str(attempt_id or "").strip()
    if not attempt_ref:
        raise TaskStoreError(
            TASK_STORE_OWNERSHIP_CONFLICT,
            "worker checkpoint reconciliation requires an Attempt-bound Operation",
        )
    attempt = store._require_attempt(conn, attempt_ref)
    graph_id = str(attempt["graph_id"])
    graph_revision = int(attempt["graph_revision"])
    node_id = str(attempt["node_id"])
    try:
        store._assert_graph_not_cancelling(
            conn,
            graph_id=graph_id,
            graph_revision=graph_revision,
        )
    except TaskStoreError as exc:
        raise TaskStoreError(
            TASK_STORE_CANCELLATION_CONFLICT,
            exc.detail,
        ) from exc
    graph = store._require_graph(conn, graph_id, graph_revision)
    node = store._require_node(
        conn,
        graph_id,
        graph_revision,
        node_id,
    )
    if (
        str(graph["status"]) != TaskGraphStatus.ACTIVE.value
        or str(node["status"])
        not in {
            TaskNodeStatus.LEASED.value,
            TaskNodeStatus.RUNNING.value,
            TaskNodeStatus.EXECUTED.value,
            TaskNodeStatus.VERIFYING.value,
            TaskNodeStatus.VERIFIED.value,
        }
        or str(attempt["status"])
        not in {
            AttemptStatus.LEASED.value,
            AttemptStatus.RUNNING.value,
            AttemptStatus.RECOVERABLE.value,
        }
    ):
        raise TaskStoreError(
            TASK_STORE_OWNERSHIP_CONFLICT,
            "worker checkpoint target is not durably continuable",
        )
    projection_row = conn.execute(
        """
        SELECT * FROM task_runtime_worker_projection
        WHERE projection_id = ?
        """,
        (projection_ref,),
    ).fetchone()
    if projection_row is None:
        raise TaskStoreError(
            TASK_STORE_NOT_FOUND,
            "signed worker checkpoint projection was not found",
        )
    try:
        projection = validate_worker_projection_record(projection_row)
    except WorkerProjectionStoreError as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "signed worker checkpoint projection is invalid",
        ) from exc
    projection_tail = projection_ref.rsplit("/", 1)[-1]
    if (
        str(projection.get("projection_id")) != projection_ref
        or str(projection.get("payload_digest"))
        != f"sha256:{projection_tail}"
        or str(projection.get("graph_id")) != graph_id
        or int(projection.get("graph_revision") or 0) != graph_revision
        or str(projection.get("node_id")) != node_id
        or str(projection.get("attempt_id")) != attempt_ref
        or str(attempt["operation_scope"])
        != str(operation_row["business_scope"])
        or str(attempt["operation_fingerprint"])
        != str(operation_row["operation_fingerprint"])
    ):
        raise TaskStoreError(
            TASK_STORE_OWNERSHIP_CONFLICT,
            "signed worker checkpoint lineage or digest drifted",
        )
    if not require_expired_lease:
        return
    active_lease = conn.execute(
        """
        SELECT * FROM task_runtime_lease
        WHERE attempt_id = ? AND status = 'ACTIVE'
        """,
        (attempt_ref,),
    ).fetchone()
    if (
        active_lease is not None
        and int(active_lease["expires_ts_ms"]) > int(observed_ts_ms)
    ):
        raise TaskStoreError(
            TASK_STORE_LEASE_CONFLICT,
            "former worker Lease has not expired",
        )


__all__ = [
    "TaskOperationCheckpointReconciliationStoreMixin",
    "assert_worker_checkpoint_reconciliation_in_transaction",
]
