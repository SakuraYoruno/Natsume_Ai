"""Retry scheduling mutations for the canonical durable TaskStore.

The mixin borrows connection, transaction, clock, and helper authorities from
the concrete store.  It must not create an independent persistence boundary.
"""

from __future__ import annotations

from contextlib import nullcontext
import sqlite3
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import BudgetReservationStatus
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphStatus,
    TaskNodeStatus,
    validate_task_node_transition,
)
from host_brain.persistence.task_record_codec import (
    _add_amounts,
    _amount_from_json,
    _budget_usage_record,
    _dumps,
    _graph_record,
    _node_record,
    _validate_attempt_transition,
    _validated_node_payload,
)
from host_brain.persistence.task_store_types import (
    AttemptStatus,
    OutboxMessage,
    TASK_STORE_BUDGET_EXCEEDED,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED,
    TASK_STORE_INVALID,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TaskStoreError,
    build_outbox_payload_ref,
)
from host_brain.persistence.task_retry_schedule_identity import (
    build_retry_schedule_identity,
)
from host_brain.persistence.task_retry_schedule_facade import (
    TaskRetryScheduleFacadeMixin,
)


class TaskRetryMutationStoreMixin(TaskRetryScheduleFacadeMixin):
    """Own retry promotion and scheduling mutations without store state."""

    def promote_retry_due(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        expected_version: int,
        outbox: OutboxMessage,
        _connection: sqlite3.Connection | None = None,
        _observed_ts_ms: int | None = None,
    ) -> dict[str, Any]:
        """Promote one due retry node, optionally inside a caller-owned UoW.

        ``_connection`` is deliberately private composition plumbing used by
        the canonical READY-claim writer.  It is never an externally exposed
        transaction API: callers without that writer capability still receive
        the normal single writer transaction here.
        """

        self.assert_dag_scheduling_ready()
        if _connection is not None and not _connection.in_transaction:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "retry promotion requires an active caller transaction",
            )
        if _observed_ts_ms is not None and int(_observed_ts_ms) < 0:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "retry promotion observed timestamp must be non-negative",
            )
        with (
            nullcontext(_connection)
            if _connection is not None
            else self._transaction()
        ) as conn:
            ts_ms = self._now() if _observed_ts_ms is None else int(_observed_ts_ms)
            self._assert_graph_not_cancelling(
                conn,
                graph_id=graph_id,
                graph_revision=graph_revision,
            )
            graph = self._require_graph(conn, graph_id, graph_revision)
            if str(graph["status"]) != TaskGraphStatus.ACTIVE.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "retry promotion requires an ACTIVE TaskGraph",
                )
            self._require_active_graph_budget(
                conn,
                graph_id=graph_id,
                graph_revision=graph_revision,
            )
            node = self._require_node(conn, graph_id, graph_revision, node_id)
            self._require_version(node, expected_version, subject=f"TaskNode {node_id}")
            if str(node["status"]) != TaskNodeStatus.RETRY_PENDING.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"retry promotion requires RETRY_PENDING node, got {node['status']}",
                )
            retry_not_before = node["retry_not_before_ms"]
            if retry_not_before is None or int(retry_not_before) > ts_ms:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"TaskNode {node_id} retry deadline is not due",
                )
            node_payload = _validated_node_payload(node)
            retry_policy = dict(node_payload.get("retry_policy") or {})
            attempt_stats = conn.execute(
                """
                SELECT COUNT(*) AS attempt_rows,
                       COALESCE(MAX(attempt_no), 0) AS max_attempt_no
                FROM task_runtime_attempt
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                """,
                (str(graph_id), int(graph_revision), str(node_id)),
            ).fetchone()
            attempt_count = int(attempt_stats["max_attempt_no"])
            if int(attempt_stats["attempt_rows"]) != attempt_count:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    f"TaskNode {node_id} attempt journal is not contiguous",
                )
            if attempt_count >= int(retry_policy["max_attempts"]):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"TaskNode {node_id} exhausted its retry attempt policy",
                )
            self._assert_dependencies_accepted(
                conn,
                graph_id=graph_id,
                graph_revision=graph_revision,
                node_payload=node_payload,
            )
            validate_task_node_transition(
                TaskNodeStatus.RETRY_PENDING,
                TaskNodeStatus.READY,
            )
            node_payload["status"] = TaskNodeStatus.READY.value
            node_payload["status_version"] = int(node["status_version"]) + 1
            cursor = conn.execute(
                """
                UPDATE task_runtime_node
                SET status = 'READY', status_version = status_version + 1,
                    node_json = ?, retry_not_before_ms = NULL,
                    updated_ts_ms = ?
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                  AND status = 'RETRY_PENDING' AND status_version = ?
                  AND retry_not_before_ms <= ?
                """,
                (
                    _dumps(node_payload),
                    ts_ms,
                    str(graph_id),
                    int(graph_revision),
                    str(node_id),
                    int(expected_version),
                    ts_ms,
                ),
            )
            self._require_cas(cursor, subject=f"TaskNode {node_id}")
            node_aggregate_id = f"{graph_id}@{int(graph_revision)}/{node_id}"
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "TASK_NODE",
                    node_aggregate_id,
                    int(expected_version) + 1,
                ),
                expected_event_type="TASK_NODE_READY",
                expected_attempt_id=None,
            )
            return _node_record(
                self._require_node(conn, graph_id, graph_revision, node_id)
            )

    def _schedule_retry(
        self,
        attempt_id: str,
        *,
        budget_id: str,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        expected_graph_version: int | None,
        error_ref: str,
        outbox: OutboxMessage,
        terminalize_graph_on_budget_rejection: bool,
    ) -> dict[str, Any]:
        """Atomically charge and fence one pre-reservation retry decision.

        The retry charge, BudgetUsage journal, Attempt/Node/Lease state and
        all state outboxes share one ``BEGIN IMMEDIATE`` transaction.  Any
        operation reservation, including a terminal one, proves that this
        narrow safe-retry boundary has already closed and fails the call.
        """

        self.assert_dag_scheduling_ready()
        identity = build_retry_schedule_identity(
            error_ref=error_ref,
            budget_id=budget_id,
            attempt_id=attempt_id,
            expected_graph_version=expected_graph_version,
            terminalize_graph_on_budget_rejection=(
                terminalize_graph_on_budget_rejection
            ),
            attempt_outbox=outbox,
        )
        failure_ref, budget_ref = identity.failure_ref, identity.budget_ref
        retry_delta = identity.retry_delta
        retry_delta_json = identity.retry_delta_json
        retry_delta_digest = identity.retry_delta_digest
        retry_usage_digest = identity.retry_usage_digest
        retry_usage_id = identity.retry_usage_id
        retry_evidence_refs = identity.retry_evidence_refs
        ts_ms = self._now()
        with self._transaction() as conn:
            attempt = self._require_attempt(conn, attempt_id)
            self._assert_graph_not_cancelling(
                conn,
                graph_id=str(attempt["graph_id"]),
                graph_revision=int(attempt["graph_revision"]),
            )
            existing_retry_usage = conn.execute(
                """
                SELECT * FROM task_runtime_budget_usage
                WHERE budget_id = ? AND usage_id = ?
                """,
                (budget_ref, retry_usage_id),
            ).fetchone()
            if existing_retry_usage is not None:
                return self._replay_scheduled_retry_in_transaction(
                    conn,
                    attempt=attempt,
                    usage=existing_retry_usage,
                    attempt_id=str(attempt_id),
                    budget_ref=budget_ref,
                    owner_id=str(owner_id),
                    fencing_token=int(fencing_token),
                    expected_attempt_version=int(expected_attempt_version),
                    failure_ref=failure_ref,
                    attempt_outbox=outbox,
                    retry_delta_json=retry_delta_json,
                    retry_delta_digest=retry_delta_digest,
                    retry_usage_digest=retry_usage_digest,
                    retry_usage_id=retry_usage_id,
                    expected_graph_version=expected_graph_version,
                    terminalize_graph_on_budget_rejection=(
                        terminalize_graph_on_budget_rejection
                    ),
                )
            self._assert_no_terminal_reconciliation_fence(
                conn,
                graph_id=str(attempt["graph_id"]),
                graph_revision=int(attempt["graph_revision"]),
                action="retry Budget mutation",
            )
            self._require_version(
                attempt,
                expected_attempt_version,
                subject=f"Attempt {attempt_id}",
            )
            if str(attempt["status"]) != AttemptStatus.RUNNING.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"retry scheduling requires RUNNING attempt, got {attempt['status']}",
                )
            lease = self._active_lease(conn, attempt_id)
            if lease is None:
                raise TaskStoreError(
                    TASK_STORE_OWNERSHIP_CONFLICT,
                    f"attempt {attempt_id} has no active lease",
                )
            self._assert_lease_owner(
                lease,
                owner_id=owner_id,
                fencing_token=fencing_token,
                now_ts_ms=ts_ms,
            )
            unsafe_reservation = conn.execute(
                """
                SELECT reservation_id, status
                FROM task_runtime_idempotency_reservation
                WHERE attempt_id = ?
                ORDER BY reservation_id
                LIMIT 1
                """,
                (str(attempt_id),),
            ).fetchone()
            if unsafe_reservation is not None:
                raise TaskStoreError(
                    TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED,
                    "retry is forbidden after any operation reservation: "
                    f"{unsafe_reservation['reservation_id']}:{unsafe_reservation['status']}",
                )
            node = self._require_node(
                conn,
                str(attempt["graph_id"]),
                int(attempt["graph_revision"]),
                str(attempt["node_id"]),
            )
            graph = self._require_graph(
                conn,
                str(attempt["graph_id"]),
                int(attempt["graph_revision"]),
            )
            if str(graph["status"]) != TaskGraphStatus.ACTIVE.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "retry scheduling requires an ACTIVE TaskGraph",
                )
            if expected_graph_version is not None:
                self._require_version(
                    graph,
                    expected_graph_version,
                    subject=(
                        f"TaskGraph {attempt['graph_id']}@"
                        f"{int(attempt['graph_revision'])}"
                    ),
                )
            budget = self._require_active_graph_budget(
                conn,
                graph_id=str(attempt["graph_id"]),
                graph_revision=int(attempt["graph_revision"]),
            )
            if str(budget["budget_id"]) != budget_ref:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "retry scheduling budget differs from the ACTIVE graph budget",
                )
            if str(node["status"]) != TaskNodeStatus.RUNNING.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"retry scheduling requires RUNNING node, got {node['status']}",
                )
            node_payload = _validated_node_payload(node)
            retry_policy = dict(node_payload.get("retry_policy") or {})
            attempt_stats = conn.execute(
                """
                SELECT COUNT(*) AS attempt_rows,
                       COALESCE(MAX(attempt_no), 0) AS max_attempt_no
                FROM task_runtime_attempt
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                """,
                (
                    str(attempt["graph_id"]),
                    int(attempt["graph_revision"]),
                    str(attempt["node_id"]),
                ),
            ).fetchone()
            attempt_count = int(attempt_stats["max_attempt_no"])
            if (
                int(attempt_stats["attempt_rows"]) != attempt_count
                or attempt_count != int(attempt["attempt_no"])
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    f"TaskNode {attempt['node_id']} attempt journal is inconsistent",
                )
            if attempt_count >= int(retry_policy["max_attempts"]):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"TaskNode {attempt['node_id']} exhausted max_attempts",
                )

            consumed = _amount_from_json(
                budget["consumed_json"],
                field_name="budget.consumed_json",
            )
            reserved = _amount_from_json(
                budget["reserved_json"],
                field_name="budget.reserved_json",
            )
            attempted_consumed = _add_amounts(consumed, retry_delta)
            parent_exceeded_dimensions = tuple(
                key
                for key, value in attempted_consumed.to_dict().items()
                if value > reserved.to_dict()[key]
            )
            node_budget_ready = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'task_runtime_node_budget_reservation'
                """
            ).fetchone() is not None
            child_exceeded_dimensions: tuple[str, ...] = ()
            if node_budget_ready:
                child_budget = self._require_node_budget(
                    conn,
                    parent_budget_id=budget_ref,
                    attempt_id=str(attempt_id),
                )
                child_exceeded_dimensions = (
                    self._node_budget_exceeded_dimensions(
                        child_budget,
                        retry_delta,
                    )
                )
            exceeded_dimensions = tuple(
                key
                for key in reserved.to_dict()
                if key in {
                    *parent_exceeded_dimensions,
                    *child_exceeded_dimensions,
                }
            )
            retry_accepted = not exceeded_dimensions
            next_budget_status = (
                BudgetReservationStatus.ACTIVE.value
                if retry_accepted
                else BudgetReservationStatus.EXCEEDED.value
            )
            next_consumed = attempted_consumed if retry_accepted else consumed
            budget_version = int(budget["status_version"])
            next_budget_version = budget_version + 1
            budget_outbox = OutboxMessage.create(
                aggregate_type="BUDGET_RESERVATION",
                aggregate_id=budget_ref,
                aggregate_version=next_budget_version,
                event_type=(
                    "BUDGET_CONSUMED" if retry_accepted else "BUDGET_EXCEEDED"
                ),
                payload_ref=build_outbox_payload_ref(
                    aggregate_type="BUDGET_RESERVATION",
                    aggregate_id=budget_ref,
                    aggregate_version=next_budget_version,
                ),
                idempotency_key=f"task-retry-budget:{budget_ref}:{retry_usage_digest}",
                outbox_id=f"outbox::task-retry-budget::{retry_usage_digest}",
                attempt_id=str(attempt_id),
                evidence_refs=retry_evidence_refs,
            )
            budget_cursor = conn.execute(
                """
                UPDATE task_runtime_budget_reservation
                SET consumed_json = ?, attempted_consumed_json = ?,
                    exceeded_dimensions_json = ?, status = ?,
                    status_version = status_version + 1, updated_ts_ms = ?
                WHERE budget_id = ? AND status = 'ACTIVE' AND status_version = ?
                """,
                (
                    _dumps(next_consumed.to_dict()),
                    _dumps(attempted_consumed.to_dict()),
                    _dumps(list(exceeded_dimensions)),
                    next_budget_status,
                    ts_ms,
                    budget_ref,
                    budget_version,
                ),
            )
            self._require_cas(
                budget_cursor,
                subject=f"BudgetReservation {budget_ref}",
            )
            budget_reason_code = (
                None if retry_accepted else TASK_STORE_BUDGET_EXCEEDED
            )
            conn.execute(
                """
                INSERT INTO task_runtime_budget_usage(
                    budget_id, usage_id, graph_id, graph_revision, node_id,
                    attempt_id, charge_kind, delta_json, delta_digest, accepted,
                    reason_code, exceeded_dimensions_json,
                    reservation_version_before, reservation_version_after,
                    outbox_id, created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, 'RETRY', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    budget_ref,
                    retry_usage_id,
                    str(attempt["graph_id"]),
                    int(attempt["graph_revision"]),
                    str(attempt["node_id"]),
                    str(attempt_id),
                    retry_delta_json,
                    retry_delta_digest,
                    1 if retry_accepted else 0,
                    budget_reason_code,
                    _dumps(list(exceeded_dimensions)),
                    budget_version,
                    next_budget_version,
                    budget_outbox.outbox_id,
                    ts_ms,
                ),
            )
            self._insert_outbox(
                conn,
                budget_outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "BUDGET_RESERVATION",
                    budget_ref,
                    next_budget_version,
                ),
                expected_event_type=(
                    "BUDGET_CONSUMED" if retry_accepted else "BUDGET_EXCEEDED"
                ),
                expected_attempt_id=str(attempt_id),
            )
            if node_budget_ready:
                self._apply_node_budget_usage_in_transaction(
                    conn,
                    parent_budget=budget,
                    attempt_id=str(attempt_id),
                    usage_id=retry_usage_id,
                    delta=retry_delta,
                    accepted=retry_accepted,
                    exceeded_dimensions=exceeded_dimensions,
                    parent_outbox_id=budget_outbox.outbox_id,
                    ts_ms=ts_ms,
                )

            retry_not_before_ms = (
                ts_ms + int(retry_policy["retry_backoff_ms"])
                if retry_accepted
                else None
            )
            _validate_attempt_transition(AttemptStatus.RUNNING, AttemptStatus.FAILED)
            attempt_cursor = conn.execute(
                """
                UPDATE task_runtime_attempt
                SET status = 'FAILED', status_version = status_version + 1,
                    completed_ts_ms = ?, updated_ts_ms = ?, error_ref = ?
                WHERE attempt_id = ? AND status = 'RUNNING'
                  AND status_version = ? AND owner_id = ?
                """,
                (
                    ts_ms,
                    ts_ms,
                    failure_ref,
                    str(attempt_id),
                    int(expected_attempt_version),
                    str(owner_id),
                ),
            )
            self._require_cas(attempt_cursor, subject=f"Attempt {attempt_id}")
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "TASK_ATTEMPT",
                    str(attempt_id),
                    int(expected_attempt_version) + 1,
                ),
                expected_event_type="TASK_ATTEMPT_FAILED",
                expected_attempt_id=str(attempt_id),
            )

            node_target = (
                TaskNodeStatus.RETRY_PENDING
                if retry_accepted
                else TaskNodeStatus.FAILED
            )
            validate_task_node_transition(TaskNodeStatus.RUNNING, node_target)
            node_payload["status"] = node_target.value
            node_payload["status_version"] = int(node["status_version"]) + 1
            node_cursor = conn.execute(
                """
                UPDATE task_runtime_node
                SET status = ?, status_version = status_version + 1,
                    node_json = ?, retry_not_before_ms = ?, updated_ts_ms = ?
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                  AND status = 'RUNNING' AND status_version = ?
                """,
                (
                    node_target.value,
                    _dumps(node_payload),
                    retry_not_before_ms,
                    ts_ms,
                    str(attempt["graph_id"]),
                    int(attempt["graph_revision"]),
                    str(attempt["node_id"]),
                    int(node["status_version"]),
                ),
            )
            self._require_cas(node_cursor, subject=f"TaskNode {attempt['node_id']}")
            node_aggregate_id = (
                f"{attempt['graph_id']}@{int(attempt['graph_revision'])}/"
                f"{attempt['node_id']}"
            )
            node_version = int(node["status_version"]) + 1
            node_event_type = f"TASK_NODE_{node_target.value}"
            self._insert_outbox(
                conn,
                OutboxMessage.create(
                    aggregate_type="TASK_NODE",
                    aggregate_id=node_aggregate_id,
                    aggregate_version=node_version,
                    event_type=node_event_type,
                    payload_ref=build_outbox_payload_ref(
                        aggregate_type="TASK_NODE",
                        aggregate_id=node_aggregate_id,
                        aggregate_version=node_version,
                    ),
                    idempotency_key=(
                        f"task-node-retry-{node_target.value.lower()}:"
                        f"{node_aggregate_id}:{node_version}"
                    ),
                    attempt_id=str(attempt_id),
                    evidence_refs=retry_evidence_refs,
                ),
                ts_ms=ts_ms,
                expected_aggregate=("TASK_NODE", node_aggregate_id, node_version),
                expected_event_type=node_event_type,
                expected_attempt_id=str(attempt_id),
            )

            if not retry_accepted and terminalize_graph_on_budget_rejection:
                graph_version = int(graph["status_version"])
                self._transition_graph_in_transaction(
                    conn,
                    graph_id=str(attempt["graph_id"]),
                    revision=int(attempt["graph_revision"]),
                    expected_version=graph_version,
                    target_status=TaskGraphStatus.BLOCKED,
                    outbox=OutboxMessage.create(
                        aggregate_type="TASK_GRAPH",
                        aggregate_id=str(attempt["graph_id"]),
                        graph_revision=int(attempt["graph_revision"]),
                        aggregate_version=graph_version + 1,
                        event_type="TASK_GRAPH_BLOCKED",
                        payload_ref=build_outbox_payload_ref(
                            aggregate_type="TASK_GRAPH",
                            aggregate_id=str(attempt["graph_id"]),
                            aggregate_version=graph_version + 1,
                            graph_revision=int(attempt["graph_revision"]),
                        ),
                        idempotency_key=(
                            f"task-retry-budget-blocked:{attempt['graph_id']}:"
                            f"{int(attempt['graph_revision'])}:{graph_version + 1}"
                        ),
                        outbox_id=(
                            f"outbox::task-retry-budget-blocked:{attempt['graph_id']}:"
                            f"{int(attempt['graph_revision'])}:{graph_version + 1}"
                        ),
                        attempt_id=str(attempt_id),
                        evidence_refs=retry_evidence_refs,
                    ),
                    ts_ms=ts_ms,
                    attempt_id=str(attempt_id),
                )

            lease_version = int(lease["status_version"]) + 1
            lease_cursor = conn.execute(
                """
                UPDATE task_runtime_lease
                SET status = 'RELEASED', released_ts_ms = ?,
                    status_version = status_version + 1
                WHERE lease_id = ? AND status = 'ACTIVE' AND status_version = ?
                """,
                (ts_ms, str(lease["lease_id"]), int(lease["status_version"])),
            )
            self._require_cas(lease_cursor, subject=f"Lease {lease['lease_id']}")
            self._insert_outbox(
                conn,
                OutboxMessage.create(
                    aggregate_type="TASK_LEASE",
                    aggregate_id=str(lease["lease_id"]),
                    aggregate_version=lease_version,
                    event_type="TASK_LEASE_RELEASED",
                    payload_ref=build_outbox_payload_ref(
                        aggregate_type="TASK_LEASE",
                        aggregate_id=str(lease["lease_id"]),
                        aggregate_version=lease_version,
                    ),
                    idempotency_key=(
                        f"task-retry-lease-released:{lease['lease_id']}:{lease_version}"
                    ),
                    attempt_id=str(attempt_id),
                    evidence_refs=retry_evidence_refs,
                ),
                ts_ms=ts_ms,
                expected_aggregate=(
                    "TASK_LEASE",
                    str(lease["lease_id"]),
                    lease_version,
                ),
                expected_event_type="TASK_LEASE_RELEASED",
                expected_attempt_id=str(attempt_id),
            )
            result = {
                "accepted": retry_accepted,
                "reason_code": budget_reason_code,
                "exceeded_dimensions": exceeded_dimensions,
                "retry_usage_id": retry_usage_id,
                "replayed": False,
                "attempt": dict(self._require_attempt(conn, attempt_id)),
                "lease": dict(self._require_lease(conn, str(lease["lease_id"]))),
                "node": _node_record(
                    self._require_node(
                        conn,
                        str(attempt["graph_id"]),
                        int(attempt["graph_revision"]),
                        str(attempt["node_id"]),
                    )
                ),
                "graph": _graph_record(
                    self._require_graph(
                        conn,
                        str(attempt["graph_id"]),
                        int(attempt["graph_revision"]),
                    ),
                    conn.execute(
                        """
                        SELECT * FROM task_runtime_node
                        WHERE graph_id = ? AND graph_revision = ?
                        ORDER BY node_id
                        """,
                        (
                            str(attempt["graph_id"]),
                            int(attempt["graph_revision"]),
                        ),
                    ).fetchall(),
                ),
                "budget": self._budget_record(
                    self._require_budget(conn, budget_ref)
                ),
                "usage": _budget_usage_record(
                    conn.execute(
                        """
                        SELECT * FROM task_runtime_budget_usage
                        WHERE budget_id = ? AND usage_id = ?
                        """,
                        (budget_ref, retry_usage_id),
                    ).fetchone()
                ),
                "retry_not_before_ms": retry_not_before_ms,
            }
            if node_budget_ready:
                result["node_budget"] = self._node_budget_record(
                    conn,
                    self._require_node_budget(
                        conn,
                        parent_budget_id=budget_ref,
                        attempt_id=str(attempt_id),
                    ),
                )
            return result
