"""Atomic READY-node claim and exact response-loss replay.

The mixin borrows the concrete store's single-writer transaction, clock, and
shared validation helpers.  Replay stays on the caller's connection and never
consumes another execution slot.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping
import uuid

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphStatus,
    TaskNodeStatus,
    validate_task_node_transition,
)
from host_brain.persistence.task_record_codec import (
    _amount_from_json,
    _dumps,
    _node_record,
    _validated_node_payload,
)
from host_brain.persistence.task_store_types import (
    AttemptStatus,
    LeaseStatus,
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_LEASE_CONFLICT,
    TASK_STORE_SCHEDULER_CAPACITY,
    TaskStoreError,
    build_outbox_payload_ref,
)
from host_brain.persistence.review_continuation_store import (
    ReviewContinuationStoreError,
    validate_review_continuation_attempt_claim_in_transaction,
)


class TaskReadyClaimStoreMixin:
    """Claim READY nodes atomically without owning concrete store state."""

    def claim_ready_node(
        self,
        *,
        attempt_id: str,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        expected_node_version: int,
        expected_attempt_count: int,
        owner_id: str,
        lease_ttl_ms: int,
        budget_id: str,
        operation_scope: str | None,
        operation_fingerprint: str | None,
        attempt_created_outbox: OutboxMessage,
        attempt_leased_outbox: OutboxMessage,
        review_continuation_authority: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically create the next attempt and lease one bounded READY node.

        This closes the historical crash gap between ``create_attempt`` and
        ``acquire_lease``.  The caller may preview capacity with the pure B0
        scheduler, but this transaction rechecks dependency and concurrency
        invariants under ``BEGIN IMMEDIATE``.
        """

        self.assert_dag_scheduling_ready()
        attempt_ref = str(attempt_id or "").strip()
        owner = str(owner_id or "").strip()
        budget_ref = str(budget_id or "").strip()
        scope = str(operation_scope or "").strip() or None
        fingerprint = str(operation_fingerprint or "").strip() or None
        if (
            not attempt_ref
            or not owner
            or not budget_ref
            or int(lease_ttl_ms) <= 0
            or int(expected_attempt_count) < 0
            or ((scope is None) != (fingerprint is None))
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "claim requires attempt/owner/budget, positive lease, "
                "non-negative attempt count, and paired operation identity",
            )
        lease_id = f"lease::{uuid.uuid4().hex}"
        heartbeat_id = f"heartbeat::{uuid.uuid4().hex}"
        with self._transaction() as conn:
            ts_ms = self._now()
            expires_ts_ms = ts_ms + int(lease_ttl_ms)
            self._assert_graph_not_cancelling(
                conn,
                graph_id=graph_id,
                graph_revision=graph_revision,
            )
            graph = self._require_graph(conn, graph_id, graph_revision)
            if str(graph["status"]) != TaskGraphStatus.ACTIVE.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"TaskGraph {graph_id}@{graph_revision} must be ACTIVE before claim",
                )
            budget_row = conn.execute(
                """
                SELECT * FROM task_runtime_budget_reservation
                WHERE budget_id = ?
                """,
                (budget_ref,),
            ).fetchone()
            if (
                budget_row is None
                or str(budget_row["status"]) != BudgetReservationStatus.ACTIVE.value
                or str(budget_row["graph_id"]) != str(graph_id)
                or int(budget_row["graph_revision"] or 0) != int(graph_revision)
                or budget_row["node_id"] is not None
                or budget_row["attempt_id"] is not None
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "READY claim requires the ACTIVE graph-level budget reservation",
                )
            concurrency_limit = _amount_from_json(
                budget_row["limits_json"],
                field_name="task_runtime_budget_reservation.limits_json",
            ).concurrency
            if concurrency_limit < 1:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "READY claim budget has no positive concurrency limit",
                )
            node = self._require_node(conn, graph_id, graph_revision, node_id)
            try:
                validate_review_continuation_attempt_claim_in_transaction(
                    conn,
                    authority=review_continuation_authority,
                    graph_id=str(graph_id),
                    graph_revision=int(graph_revision),
                    node_id=str(node_id),
                    now_ts_ms=ts_ms,
                )
            except ReviewContinuationStoreError as exc:
                raise TaskStoreError(exc.reason_code, exc.detail) from exc
            existing_attempt = conn.execute(
                "SELECT * FROM task_runtime_attempt WHERE attempt_id = ?",
                (attempt_ref,),
            ).fetchone()
            if existing_attempt is not None:
                return self._replay_ready_node_claim(
                    conn,
                    attempt=existing_attempt,
                    node=node,
                    graph_id=str(graph_id),
                    graph_revision=int(graph_revision),
                    node_id=str(node_id),
                    expected_node_version=int(expected_node_version),
                    expected_attempt_count=int(expected_attempt_count),
                    owner_id=owner,
                    lease_ttl_ms=int(lease_ttl_ms),
                    operation_scope=scope,
                    operation_fingerprint=fingerprint,
                    attempt_created_outbox=attempt_created_outbox,
                    attempt_leased_outbox=attempt_leased_outbox,
                    observed_ts_ms=ts_ms,
                )
            self._require_version(
                node,
                expected_node_version,
                subject=f"TaskNode {node_id}",
            )
            if str(node["status"]) != TaskNodeStatus.READY.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"claim requires READY node, got {node['status']}",
                )
            if node["retry_not_before_ms"] is not None:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    f"READY TaskNode {node_id} retains a retry deadline",
                )
            attempt_stats = conn.execute(
                """
                SELECT COUNT(*) AS attempt_rows,
                       COALESCE(MAX(attempt_no), 0) AS max_attempt_no
                FROM task_runtime_attempt
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                """,
                (str(graph_id), int(graph_revision), str(node_id)),
            ).fetchone()
            durable_attempt_count = int(attempt_stats["max_attempt_no"])
            if (
                int(attempt_stats["attempt_rows"]) != durable_attempt_count
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    f"TaskNode {node_id} attempt journal is not contiguous",
                )
            if durable_attempt_count != int(expected_attempt_count):
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    f"TaskNode {node_id} attempt count changed",
                )
            node_payload = _validated_node_payload(node)
            self._assert_dependencies_accepted(
                conn,
                graph_id=graph_id,
                graph_revision=graph_revision,
                node_payload=node_payload,
            )
            retry_policy = dict(node_payload.get("retry_policy") or {})
            attempt_no = durable_attempt_count + 1
            if attempt_no > int(retry_policy["max_attempts"]):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"TaskNode {node_id} exhausted max_attempts",
                )
            occupied = conn.execute(
                """
                SELECT COUNT(*) AS occupied
                FROM task_runtime_lease AS lease
                JOIN task_runtime_attempt AS attempt
                  ON attempt.attempt_id = lease.attempt_id
                WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
                  AND lease.status = 'ACTIVE' AND lease.expires_ts_ms > ?
                  AND attempt.status IN ('LEASED', 'RUNNING')
                """,
                (str(graph_id), int(graph_revision), ts_ms),
            ).fetchone()
            if int(occupied["occupied"]) >= concurrency_limit:
                raise TaskStoreError(
                    TASK_STORE_SCHEDULER_CAPACITY,
                    f"TaskGraph {graph_id}@{graph_revision} has no free execution slot",
                )
            try:
                conn.execute(
                    """
                    INSERT INTO task_runtime_attempt(
                        attempt_id, graph_id, graph_revision, node_id, attempt_no,
                        status, status_version, owner_id, operation_scope,
                        operation_fingerprint, created_ts_ms, acquired_ts_ms,
                        started_ts_ms, completed_ts_ms, updated_ts_ms, error_ref
                    ) VALUES (?, ?, ?, ?, ?, 'CREATED', 0, NULL, ?, ?, ?, NULL, NULL, NULL, ?, NULL)
                    """,
                    (
                        attempt_ref,
                        str(graph_id),
                        int(graph_revision),
                        str(node_id),
                        attempt_no,
                        scope,
                        fingerprint,
                        ts_ms,
                        ts_ms,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    f"attempt identity or live ownership conflict for TaskNode {node_id}",
                ) from exc
            self._insert_outbox(
                conn,
                attempt_created_outbox,
                ts_ms=ts_ms,
                expected_aggregate=("TASK_ATTEMPT", attempt_ref, 0),
                expected_event_type="TASK_ATTEMPT_CREATED",
                expected_attempt_id=attempt_ref,
            )

            attempt_cursor = conn.execute(
                """
                UPDATE task_runtime_attempt
                SET status = 'LEASED', owner_id = ?, acquired_ts_ms = ?,
                    status_version = 1, updated_ts_ms = ?
                WHERE attempt_id = ? AND status = 'CREATED' AND status_version = 0
                """,
                (owner, ts_ms, ts_ms, attempt_ref),
            )
            self._require_cas(attempt_cursor, subject=f"Attempt {attempt_ref}")
            validate_task_node_transition(TaskNodeStatus.READY, TaskNodeStatus.LEASED)
            node_payload["status"] = TaskNodeStatus.LEASED.value
            node_payload["status_version"] = int(node["status_version"]) + 1
            node_cursor = conn.execute(
                """
                UPDATE task_runtime_node
                SET status = 'LEASED', status_version = status_version + 1,
                    node_json = ?, retry_not_before_ms = NULL, updated_ts_ms = ?
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                  AND status = 'READY' AND status_version = ?
                """,
                (
                    _dumps(node_payload),
                    ts_ms,
                    str(graph_id),
                    int(graph_revision),
                    str(node_id),
                    int(expected_node_version),
                ),
            )
            self._require_cas(node_cursor, subject=f"TaskNode {node_id}")
            node_aggregate_id = f"{graph_id}@{int(graph_revision)}/{node_id}"
            node_version = int(node["status_version"]) + 1
            self._insert_outbox(
                conn,
                OutboxMessage.create(
                    aggregate_type="TASK_NODE",
                    aggregate_id=node_aggregate_id,
                    aggregate_version=node_version,
                    event_type="TASK_NODE_LEASED",
                    payload_ref=build_outbox_payload_ref(
                        aggregate_type="TASK_NODE",
                        aggregate_id=node_aggregate_id,
                        aggregate_version=node_version,
                    ),
                    idempotency_key=f"task-node-leased:{node_aggregate_id}:{node_version}",
                    outbox_id=(
                        f"outbox::task-node-leased:{node_aggregate_id}:{node_version}"
                    ),
                    attempt_id=attempt_ref,
                ),
                ts_ms=ts_ms,
                expected_aggregate=("TASK_NODE", node_aggregate_id, node_version),
                expected_event_type="TASK_NODE_LEASED",
                expected_attempt_id=attempt_ref,
            )
            conn.execute(
                """
                INSERT INTO task_runtime_lease(
                    lease_id, attempt_id, owner_id, fencing_token, acquired_ts_ms,
                    heartbeat_ts_ms, expires_ts_ms, released_ts_ms, status, status_version
                ) VALUES (?, ?, ?, 1, ?, ?, ?, NULL, 'ACTIVE', 0)
                """,
                (lease_id, attempt_ref, owner, ts_ms, ts_ms, expires_ts_ms),
            )
            conn.execute(
                """
                INSERT INTO task_runtime_heartbeat(
                    heartbeat_id, lease_id, attempt_id, owner_id, fencing_token,
                    observed_ts_ms, expires_ts_ms
                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    heartbeat_id,
                    lease_id,
                    attempt_ref,
                    owner,
                    ts_ms,
                    expires_ts_ms,
                ),
            )
            self._insert_outbox(
                conn,
                attempt_leased_outbox,
                ts_ms=ts_ms,
                expected_aggregate=("TASK_ATTEMPT", attempt_ref, 1),
                expected_event_type="TASK_ATTEMPT_LEASED",
                expected_attempt_id=attempt_ref,
            )
            return {
                "attempt": dict(self._require_attempt(conn, attempt_ref)),
                "lease": dict(self._require_lease(conn, lease_id)),
                "node": _node_record(
                    self._require_node(conn, graph_id, graph_revision, node_id)
                ),
                "replayed": False,
            }

    def _replay_ready_node_claim(
        self,
        conn: sqlite3.Connection,
        *,
        attempt: sqlite3.Row,
        node: sqlite3.Row,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        expected_node_version: int,
        expected_attempt_count: int,
        owner_id: str,
        lease_ttl_ms: int,
        operation_scope: str | None,
        operation_fingerprint: str | None,
        attempt_created_outbox: OutboxMessage,
        attempt_leased_outbox: OutboxMessage,
        observed_ts_ms: int,
    ) -> dict[str, Any]:
        """Return an exact, still-live replay of a committed READY claim.

        A caller may lose the response after SQLite commits.  Reusing the
        deterministic attempt and outbox identities must not create another
        attempt or consume another concurrency slot.  Any ownership, binding,
        TTL, outbox, or post-claim state drift is rejected instead of handing
        stale execution authority back to the caller.
        """

        expected_attempt_no = int(expected_attempt_count) + 1
        attempt_binding = (
            str(attempt["graph_id"]),
            int(attempt["graph_revision"]),
            str(attempt["node_id"]),
            int(attempt["attempt_no"]),
        )
        if attempt_binding != (
            str(graph_id),
            int(graph_revision),
            str(node_id),
            expected_attempt_no,
        ):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "READY claim replay attempt binding drift",
            )
        if (
            str(attempt["status"]) != AttemptStatus.LEASED.value
            or int(attempt["status_version"]) != 1
            or str(attempt["owner_id"] or "") != owner_id
            or (str(attempt["operation_scope"] or "").strip() or None)
            != operation_scope
            or (str(attempt["operation_fingerprint"] or "").strip() or None)
            != operation_fingerprint
            or attempt["started_ts_ms"] is not None
            or attempt["completed_ts_ms"] is not None
            or attempt["error_ref"] is not None
        ):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "READY claim replay attempt state drift",
            )
        if (
            str(node["status"]) != TaskNodeStatus.LEASED.value
            or int(node["status_version"]) != int(expected_node_version) + 1
            or node["retry_not_before_ms"] is not None
        ):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "READY claim replay node state drift",
            )

        lease_rows = conn.execute(
            """
            SELECT * FROM task_runtime_lease
            WHERE attempt_id = ?
            ORDER BY acquired_ts_ms, lease_id
            """,
            (str(attempt["attempt_id"]),),
        ).fetchall()
        if len(lease_rows) != 1:
            raise TaskStoreError(
                TASK_STORE_LEASE_CONFLICT,
                "READY claim replay requires exactly one durable lease",
            )
        lease = lease_rows[0]
        if (
            str(lease["status"]) != LeaseStatus.ACTIVE.value
            or int(lease["status_version"]) != 0
            or str(lease["owner_id"]) != owner_id
            or int(lease["fencing_token"]) != 1
            or int(lease["expires_ts_ms"]) <= int(observed_ts_ms)
            or int(lease["expires_ts_ms"]) - int(lease["acquired_ts_ms"])
            != int(lease_ttl_ms)
            or int(attempt["acquired_ts_ms"] or -1)
            != int(lease["acquired_ts_ms"])
        ):
            raise TaskStoreError(
                TASK_STORE_LEASE_CONFLICT,
                "READY claim replay lease authority drift",
            )
        heartbeat_rows = conn.execute(
            """
            SELECT * FROM task_runtime_heartbeat
            WHERE lease_id = ?
            ORDER BY observed_ts_ms, heartbeat_id
            """,
            (str(lease["lease_id"]),),
        ).fetchall()
        if len(heartbeat_rows) != 1:
            raise TaskStoreError(
                TASK_STORE_LEASE_CONFLICT,
                "READY claim replay requires the initial durable heartbeat",
            )
        heartbeat = heartbeat_rows[0]
        if (
            str(heartbeat["attempt_id"]) != str(attempt["attempt_id"])
            or str(heartbeat["owner_id"]) != owner_id
            or int(heartbeat["fencing_token"]) != 1
            or int(heartbeat["observed_ts_ms"]) != int(lease["acquired_ts_ms"])
            or int(heartbeat["expires_ts_ms"]) != int(lease["expires_ts_ms"])
        ):
            raise TaskStoreError(
                TASK_STORE_LEASE_CONFLICT,
                "READY claim replay heartbeat authority drift",
            )

        attempt_ref = str(attempt["attempt_id"])
        self._assert_mutation_outbox_replay(
            conn,
            attempt_created_outbox,
            expected_aggregate=("TASK_ATTEMPT", attempt_ref, 0),
            expected_event_type="TASK_ATTEMPT_CREATED",
            expected_attempt_id=attempt_ref,
        )
        self._assert_mutation_outbox_replay(
            conn,
            attempt_leased_outbox,
            expected_aggregate=("TASK_ATTEMPT", attempt_ref, 1),
            expected_event_type="TASK_ATTEMPT_LEASED",
            expected_attempt_id=attempt_ref,
        )
        node_aggregate_id = f"{graph_id}@{int(graph_revision)}/{node_id}"
        node_version = int(expected_node_version) + 1
        node_outbox = OutboxMessage.create(
            aggregate_type="TASK_NODE",
            aggregate_id=node_aggregate_id,
            aggregate_version=node_version,
            event_type="TASK_NODE_LEASED",
            payload_ref=build_outbox_payload_ref(
                aggregate_type="TASK_NODE",
                aggregate_id=node_aggregate_id,
                aggregate_version=node_version,
            ),
            idempotency_key=f"task-node-leased:{node_aggregate_id}:{node_version}",
            outbox_id=f"outbox::task-node-leased:{node_aggregate_id}:{node_version}",
            attempt_id=attempt_ref,
        )
        self._assert_mutation_outbox_replay(
            conn,
            node_outbox,
            expected_aggregate=("TASK_NODE", node_aggregate_id, node_version),
            expected_event_type="TASK_NODE_LEASED",
            expected_attempt_id=attempt_ref,
        )
        return {
            "attempt": dict(attempt),
            "lease": dict(lease),
            "node": _node_record(node),
            "replayed": True,
        }
