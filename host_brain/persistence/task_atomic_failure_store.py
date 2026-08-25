"""Atomic running-attempt failure mutations for durable TaskStore.

The failure bundle and exact replay borrow the concrete store's connection,
transaction, clock, and validation authorities.  A direct, graph-terminal
pre-reservation failure may additionally release its already-bound graph
BudgetReservation in the same transaction; node-only parallel children never
receive that authority.
"""

from __future__ import annotations

import sqlite3
from typing import Any, NoReturn

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphStatus,
    TaskNodeStatus,
    validate_task_graph_transition,
    validate_task_node_transition,
)
from host_brain.persistence.task_record_codec import (
    _dumps,
    _graph_record,
    _node_record,
    _validate_attempt_transition,
    _validated_budget_record,
    _validated_node_payload,
)
from host_brain.persistence.task_atomic_failure_budget import (
    assert_terminal_failure_budget_binding,
    validated_terminal_failure_budget_request,
)
from host_brain.persistence.task_store_types import (
    AttemptStatus,
    LeaseStatus,
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED,
    TASK_STORE_INVALID,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TaskStoreError,
    build_outbox_payload_ref,
)


class TaskAtomicFailureStoreMixin:
    """Fail a running attempt atomically on the owner's transaction."""

    def fail_running_attempt_node_bundle(
        self,
        attempt_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        expected_node_version: int,
        expected_graph_version: int,
        error_ref: str,
        attempt_outbox: OutboxMessage,
    ) -> dict[str, Any]:
        """Atomically fail one child Attempt/Node and release its Lease.

        The TaskGraph is version-checked and must remain ACTIVE, but is never
        mutated.  The outer fan-in owner therefore remains the only authority
        allowed to choose a graph-wide terminal state.
        """

        return self.fail_running_attempt_bundle(
            attempt_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            expected_attempt_version=expected_attempt_version,
            expected_node_version=expected_node_version,
            expected_graph_version=expected_graph_version,
            error_ref=error_ref,
            graph_target_status=None,
            attempt_outbox=attempt_outbox,
            budget_id=None,
            expected_budget_version=None,
            budget_outbox=None,
        )

    def fail_running_attempt_bundle(
        self,
        attempt_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        expected_node_version: int,
        expected_graph_version: int,
        error_ref: str,
        graph_target_status: TaskGraphStatus | str | None,
        attempt_outbox: OutboxMessage,
        budget_id: str | None = None,
        expected_budget_version: int | None = None,
        budget_outbox: OutboxMessage | None = None,
    ) -> dict[str, Any]:
        """Atomically close one pre-reservation RUNNING attempt as failed.

        This boundary is intentionally narrower than generic attempt/node
        transitions.  It is valid only before *any* operation reservation has
        existed for the attempt, and commits the Attempt, TaskNode, TaskGraph,
        active Lease, and their state outboxes in one ``BEGIN IMMEDIATE``.
        A direct graph-terminal caller may additionally bind one graph-scoped
        BudgetReservation release to that same unit of work.  The graph
        mutation is optional for the node-only parallel-child entry point,
        which must never mutate a graph-scoped budget.

        Repeating the exact call after a committed response was lost returns
        the already-durable outcome.  Any state, authority, evidence, version,
        or outbox drift fails closed instead of synthesizing success.
        """

        attempt_ref = str(attempt_id or "").strip()
        owner = str(owner_id or "").strip()
        if not attempt_ref or not owner or int(fencing_token) < 1:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "terminal failure requires attempt, owner, and positive fencing_token",
            )
        failure_ref = self._validated_reference(error_ref, field_name="error_ref")
        graph_target: TaskGraphStatus | None = None
        if graph_target_status is not None:
            try:
                graph_target = TaskGraphStatus(str(graph_target_status))
            except ValueError as exc:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"unsupported terminal failure graph status: {graph_target_status}",
                ) from exc
            if graph_target not in {TaskGraphStatus.FAILED, TaskGraphStatus.BLOCKED}:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "terminal failure graph status must be FAILED or BLOCKED",
                )
        if not isinstance(attempt_outbox, OutboxMessage):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "terminal failure requires an Attempt OutboxMessage",
            )
        attempt_outbox.validate()
        evidence_refs = tuple(attempt_outbox.evidence_refs)
        if attempt_outbox.attempt_id != attempt_ref or failure_ref not in evidence_refs:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "terminal failure Attempt outbox must bind attempt_id and error_ref evidence",
            )
        expected_attempt_aggregate = (
            "TASK_ATTEMPT",
            attempt_ref,
            int(expected_attempt_version) + 1,
        )
        if (
            (
                attempt_outbox.aggregate_type,
                attempt_outbox.aggregate_id,
                attempt_outbox.aggregate_version,
            )
            != expected_attempt_aggregate
            or attempt_outbox.event_type != "TASK_ATTEMPT_FAILED"
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "terminal failure Attempt outbox does not bind the requested version",
            )
        budget_request = validated_terminal_failure_budget_request(
            budget_id=budget_id,
            expected_budget_version=expected_budget_version,
            budget_outbox=budget_outbox,
        )
        if graph_target is None and budget_request is not None:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "node-only terminal failure cannot release a graph BudgetReservation",
            )

        ts_ms = self._now()
        with self._transaction() as conn:
            attempt = self._require_attempt(conn, attempt_ref)
            unsafe_reservation = conn.execute(
                """
                SELECT reservation_id, status
                FROM task_runtime_idempotency_reservation
                WHERE attempt_id = ?
                ORDER BY reservation_id
                LIMIT 1
                """,
                (attempt_ref,),
            ).fetchone()
            if unsafe_reservation is not None:
                raise TaskStoreError(
                    TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED,
                    "terminal failure is forbidden after any operation reservation: "
                    f"{unsafe_reservation['reservation_id']}:"
                    f"{unsafe_reservation['status']}",
                )

            if (
                str(attempt["status"]) == AttemptStatus.FAILED.value
                and int(attempt["status_version"])
                == int(expected_attempt_version) + 1
            ):
                return self._replay_running_attempt_failure_bundle(
                    conn,
                    attempt=attempt,
                    attempt_id=attempt_ref,
                    owner_id=owner,
                    fencing_token=int(fencing_token),
                    expected_attempt_version=int(expected_attempt_version),
                    expected_node_version=int(expected_node_version),
                    expected_graph_version=int(expected_graph_version),
                    error_ref=failure_ref,
                    graph_target_status=graph_target,
                    attempt_outbox=attempt_outbox,
                    evidence_refs=evidence_refs,
                    budget_request=budget_request,
                )

            self._require_version(
                attempt,
                expected_attempt_version,
                subject=f"Attempt {attempt_ref}",
            )
            if str(attempt["status"]) != AttemptStatus.RUNNING.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "terminal failure requires a RUNNING Attempt, got "
                    f"{attempt['status']}",
                )
            graph_id = str(attempt["graph_id"])
            graph_revision = int(attempt["graph_revision"])
            node_id = str(attempt["node_id"])
            node = self._require_node(conn, graph_id, graph_revision, node_id)
            graph = self._require_graph(conn, graph_id, graph_revision)
            self._require_version(
                node,
                expected_node_version,
                subject=f"TaskNode {node_id}",
            )
            self._require_version(
                graph,
                expected_graph_version,
                subject=f"TaskGraph {graph_id}@{graph_revision}",
            )
            if str(node["status"]) != TaskNodeStatus.RUNNING.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"terminal failure requires a RUNNING TaskNode, got {node['status']}",
                )
            if str(graph["status"]) != TaskGraphStatus.ACTIVE.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"terminal failure requires an ACTIVE TaskGraph, got {graph['status']}",
                )
            if budget_request is not None:
                budget_ref, budget_version, _ = budget_request
                budget_record = self._require_budget(conn, budget_ref)
                assert_terminal_failure_budget_binding(
                    budget_record,
                    budget_id=budget_ref,
                    graph_id=graph_id,
                    graph_revision=graph_revision,
                )
                self._require_version(
                    budget_record,
                    budget_version,
                    subject=f"BudgetReservation {budget_ref}",
                )
                if str(budget_record["status"]) not in {
                    BudgetReservationStatus.ACTIVE.value,
                    BudgetReservationStatus.EXCEEDED.value,
                }:
                    raise TaskStoreError(
                        TASK_STORE_INVALID,
                        "terminal failure BudgetReservation must be ACTIVE or EXCEEDED",
                    )
            lease = self._active_lease(conn, attempt_ref)
            if lease is None:
                raise TaskStoreError(
                    TASK_STORE_OWNERSHIP_CONFLICT,
                    f"attempt {attempt_ref} has no active lease",
                )
            self._assert_lease_owner(
                lease,
                owner_id=owner,
                fencing_token=int(fencing_token),
                now_ts_ms=ts_ms,
            )
            _validate_attempt_transition(AttemptStatus.RUNNING, AttemptStatus.FAILED)
            validate_task_node_transition(TaskNodeStatus.RUNNING, TaskNodeStatus.FAILED)
            if graph_target is not None:
                validate_task_graph_transition(TaskGraphStatus.ACTIVE, graph_target)

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
                    attempt_ref,
                    int(expected_attempt_version),
                    owner,
                ),
            )
            self._require_cas(attempt_cursor, subject=f"Attempt {attempt_ref}")
            self._insert_outbox(
                conn,
                attempt_outbox,
                ts_ms=ts_ms,
                expected_aggregate=expected_attempt_aggregate,
                expected_event_type="TASK_ATTEMPT_FAILED",
                expected_attempt_id=attempt_ref,
            )

            node_payload = _validated_node_payload(node)
            node_payload["status"] = TaskNodeStatus.FAILED.value
            node_payload["status_version"] = int(expected_node_version) + 1
            node_cursor = conn.execute(
                """
                UPDATE task_runtime_node
                SET status = 'FAILED', status_version = status_version + 1,
                    node_json = ?, retry_not_before_ms = NULL, updated_ts_ms = ?
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                  AND status = 'RUNNING' AND status_version = ?
                """,
                (
                    _dumps(node_payload),
                    ts_ms,
                    graph_id,
                    graph_revision,
                    node_id,
                    int(expected_node_version),
                ),
            )
            self._require_cas(node_cursor, subject=f"TaskNode {node_id}")
            node_aggregate_id = f"{graph_id}@{graph_revision}/{node_id}"
            node_version = int(expected_node_version) + 1
            node_outbox = self._terminal_failure_bundle_outbox(
                kind="node",
                aggregate_type="TASK_NODE",
                aggregate_id=node_aggregate_id,
                aggregate_version=node_version,
                event_type="TASK_NODE_FAILED",
                attempt_id=attempt_ref,
                evidence_refs=evidence_refs,
            )
            self._insert_outbox(
                conn,
                node_outbox,
                ts_ms=ts_ms,
                expected_aggregate=("TASK_NODE", node_aggregate_id, node_version),
                expected_event_type="TASK_NODE_FAILED",
                expected_attempt_id=attempt_ref,
            )

            if graph_target is not None:
                graph_version = int(expected_graph_version) + 1
                graph_outbox = self._terminal_failure_bundle_outbox(
                    kind="graph",
                    aggregate_type="TASK_GRAPH",
                    aggregate_id=graph_id,
                    graph_revision=graph_revision,
                    aggregate_version=graph_version,
                    event_type=f"TASK_GRAPH_{graph_target.value}",
                    attempt_id=attempt_ref,
                    evidence_refs=evidence_refs,
                )
                self._transition_graph_in_transaction(
                    conn,
                    graph_id=graph_id,
                    revision=graph_revision,
                    expected_version=int(expected_graph_version),
                    target_status=graph_target,
                    outbox=graph_outbox,
                    ts_ms=ts_ms,
                    attempt_id=attempt_ref,
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
            lease_outbox = self._terminal_failure_bundle_outbox(
                kind="lease",
                aggregate_type="TASK_LEASE",
                aggregate_id=str(lease["lease_id"]),
                aggregate_version=lease_version,
                event_type="TASK_LEASE_RELEASED_TERMINAL",
                attempt_id=attempt_ref,
                evidence_refs=evidence_refs,
            )
            self._insert_outbox(
                conn,
                lease_outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "TASK_LEASE",
                    str(lease["lease_id"]),
                    lease_version,
                ),
                expected_event_type="TASK_LEASE_RELEASED_TERMINAL",
                expected_attempt_id=attempt_ref,
            )

            if budget_request is not None:
                budget_ref, budget_version, release_outbox = budget_request
                self._assert_graph_execution_authority_quiescent(
                    conn,
                    graph_id=graph_id,
                    graph_revision=graph_revision,
                    action="direct terminal BudgetReservation release",
                )
                budget_cursor = conn.execute(
                    """
                    UPDATE task_runtime_budget_reservation
                    SET status = 'RELEASED', status_version = status_version + 1,
                        updated_ts_ms = ?
                    WHERE budget_id = ? AND status IN ('ACTIVE', 'EXCEEDED')
                      AND status_version = ?
                    """,
                    (ts_ms, budget_ref, budget_version),
                )
                self._require_cas(
                    budget_cursor,
                    subject=f"BudgetReservation {budget_ref}",
                )
                self._insert_outbox(
                    conn,
                    release_outbox,
                    ts_ms=ts_ms,
                    expected_aggregate=(
                        "BUDGET_RESERVATION",
                        budget_ref,
                        budget_version + 1,
                    ),
                    expected_event_type="BUDGET_RESERVATION_RELEASED",
                    expected_attempt_id=None,
                )

            final_node = self._require_node(conn, graph_id, graph_revision, node_id)
            final_graph = self._require_graph(conn, graph_id, graph_revision)
            graph_nodes = conn.execute(
                """
                SELECT * FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id
                """,
                (graph_id, graph_revision),
            ).fetchall()
            result = {
                "attempt": dict(self._require_attempt(conn, attempt_ref)),
                "node": _node_record(final_node),
                "graph": _graph_record(final_graph, graph_nodes),
                "lease": dict(self._require_lease(conn, str(lease["lease_id"]))),
                "replayed": False,
            }
            if budget_request is not None:
                result["budget"] = _validated_budget_record(
                    self._require_budget(conn, budget_request[0])
                )
            return result

    @staticmethod
    def _terminal_failure_bundle_outbox(
        *,
        kind: str,
        aggregate_type: str,
        aggregate_id: str,
        graph_revision: int | None = None,
        aggregate_version: int,
        event_type: str,
        attempt_id: str,
        evidence_refs: tuple[str, ...],
    ) -> OutboxMessage:
        outbox_key = (
            f"attempt-terminal-{kind}:{attempt_id}:{aggregate_id}:"
            f"{int(aggregate_version)}"
        )
        return OutboxMessage.create(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            graph_revision=graph_revision,
            aggregate_version=int(aggregate_version),
            event_type=event_type,
            payload_ref=build_outbox_payload_ref(
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=int(aggregate_version),
                graph_revision=graph_revision,
            ),
            idempotency_key=outbox_key,
            outbox_id=f"outbox::{outbox_key}",
            attempt_id=attempt_id,
            evidence_refs=evidence_refs,
        )

    def _replay_running_attempt_failure_bundle(
        self,
        conn: sqlite3.Connection,
        *,
        attempt: sqlite3.Row,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        expected_node_version: int,
        expected_graph_version: int,
        error_ref: str,
        graph_target_status: TaskGraphStatus | None,
        attempt_outbox: OutboxMessage,
        evidence_refs: tuple[str, ...],
        budget_request: tuple[str, int, OutboxMessage] | None,
    ) -> dict[str, Any]:
        """Return an already committed terminal failure only on exact replay."""

        def reject(detail: str) -> NoReturn:
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                f"terminal failure replay drift for Attempt {attempt_id}: {detail}",
            )

        terminal_ts_ms = int(attempt["completed_ts_ms"] or -1)
        if (
            str(attempt["status"]) != AttemptStatus.FAILED.value
            or int(attempt["status_version"]) != int(expected_attempt_version) + 1
            or str(attempt["owner_id"] or "") != owner_id
            or str(attempt["error_ref"] or "") != error_ref
            or terminal_ts_ms < 0
            or int(attempt["updated_ts_ms"]) != terminal_ts_ms
        ):
            reject("Attempt state, owner, error, timestamp, or version differs")
        self._assert_mutation_outbox_replay(
            conn,
            attempt_outbox,
            expected_aggregate=(
                "TASK_ATTEMPT",
                attempt_id,
                int(expected_attempt_version) + 1,
            ),
            expected_event_type="TASK_ATTEMPT_FAILED",
            expected_attempt_id=attempt_id,
        )

        graph_id = str(attempt["graph_id"])
        graph_revision = int(attempt["graph_revision"])
        node_id = str(attempt["node_id"])
        budget_record: dict[str, Any] | None = None
        if budget_request is not None:
            budget_ref, budget_version, release_outbox = budget_request
            budget = self._require_budget(conn, budget_ref)
            assert_terminal_failure_budget_binding(
                budget,
                budget_id=budget_ref,
                graph_id=graph_id,
                graph_revision=graph_revision,
            )
            if (
                str(budget["status"]) != BudgetReservationStatus.RELEASED.value
                or int(budget["status_version"]) != budget_version + 1
                or int(budget["updated_ts_ms"]) != terminal_ts_ms
            ):
                reject("BudgetReservation state, timestamp, or version differs")
            self._assert_mutation_outbox_replay(
                conn,
                release_outbox,
                expected_aggregate=(
                    "BUDGET_RESERVATION",
                    budget_ref,
                    budget_version + 1,
                ),
                expected_event_type="BUDGET_RESERVATION_RELEASED",
                expected_attempt_id=None,
            )
            budget_record = _validated_budget_record(budget)

        node = self._require_node(conn, graph_id, graph_revision, node_id)
        if (
            str(node["status"]) != TaskNodeStatus.FAILED.value
            or int(node["status_version"]) != int(expected_node_version) + 1
            or node["retry_not_before_ms"] is not None
            or int(node["updated_ts_ms"]) != terminal_ts_ms
        ):
            reject("TaskNode state, retry deadline, timestamp, or version differs")
        node_record = _node_record(node)
        if (
            str(node_record["node"]["status"]) != TaskNodeStatus.FAILED.value
            or int(node_record["node"]["status_version"])
            != int(expected_node_version) + 1
        ):
            reject("TaskNode SQL/JSON projection differs")
        node_aggregate_id = f"{graph_id}@{graph_revision}/{node_id}"
        node_version = int(expected_node_version) + 1
        node_outbox = self._terminal_failure_bundle_outbox(
            kind="node",
            aggregate_type="TASK_NODE",
            aggregate_id=node_aggregate_id,
            aggregate_version=node_version,
            event_type="TASK_NODE_FAILED",
            attempt_id=attempt_id,
            evidence_refs=evidence_refs,
        )
        self._assert_mutation_outbox_replay(
            conn,
            node_outbox,
            expected_aggregate=("TASK_NODE", node_aggregate_id, node_version),
            expected_event_type="TASK_NODE_FAILED",
            expected_attempt_id=attempt_id,
        )

        graph = self._require_graph(conn, graph_id, graph_revision)
        if graph_target_status is None:
            if (
                str(graph["status"]) != TaskGraphStatus.ACTIVE.value
                or int(graph["status_version"]) != int(expected_graph_version)
            ):
                reject("node-only failure no longer owns the original ACTIVE TaskGraph")
            unexpected_graph_outbox = conn.execute(
                """
                SELECT outbox_id FROM task_runtime_outbox
                WHERE aggregate_type = 'TASK_GRAPH' AND aggregate_id = ?
                  AND graph_revision = ?
                  AND attempt_id = ? AND aggregate_version > ?
                LIMIT 1
                """,
                (
                    graph_id,
                    graph_revision,
                    attempt_id,
                    int(expected_graph_version),
                ),
            ).fetchone()
            if unexpected_graph_outbox is not None:
                reject("node-only failure unexpectedly emitted a TaskGraph outbox")
        else:
            if (
                str(graph["status"]) != graph_target_status.value
                or int(graph["status_version"]) != int(expected_graph_version) + 1
                or int(graph["updated_ts_ms"]) != terminal_ts_ms
            ):
                reject("TaskGraph state, timestamp, or version differs")
            graph_version = int(expected_graph_version) + 1
            graph_outbox = self._terminal_failure_bundle_outbox(
                kind="graph",
                aggregate_type="TASK_GRAPH",
                aggregate_id=graph_id,
                graph_revision=graph_revision,
                aggregate_version=graph_version,
                event_type=f"TASK_GRAPH_{graph_target_status.value}",
                attempt_id=attempt_id,
                evidence_refs=evidence_refs,
            )
            self._assert_mutation_outbox_replay(
                conn,
                graph_outbox,
                expected_aggregate=("TASK_GRAPH", graph_id, graph_version),
                expected_event_type=f"TASK_GRAPH_{graph_target_status.value}",
                expected_attempt_id=attempt_id,
                expected_graph_revision=graph_revision,
            )

        lease_rows = conn.execute(
            """
            SELECT * FROM task_runtime_lease
            WHERE attempt_id = ? AND owner_id = ? AND fencing_token = ?
            ORDER BY acquired_ts_ms, lease_id
            """,
            (attempt_id, owner_id, int(fencing_token)),
        ).fetchall()
        if len(lease_rows) != 1:
            reject("exactly one authority-matched Lease is required")
        lease = lease_rows[0]
        if (
            str(lease["status"]) != LeaseStatus.RELEASED.value
            or int(lease["released_ts_ms"] or -1) != terminal_ts_ms
            or int(lease["status_version"]) < 1
        ):
            reject("Lease terminal state, timestamp, or version differs")
        lease_version = int(lease["status_version"])
        lease_outbox = self._terminal_failure_bundle_outbox(
            kind="lease",
            aggregate_type="TASK_LEASE",
            aggregate_id=str(lease["lease_id"]),
            aggregate_version=lease_version,
            event_type="TASK_LEASE_RELEASED_TERMINAL",
            attempt_id=attempt_id,
            evidence_refs=evidence_refs,
        )
        self._assert_mutation_outbox_replay(
            conn,
            lease_outbox,
            expected_aggregate=(
                "TASK_LEASE",
                str(lease["lease_id"]),
                lease_version,
            ),
            expected_event_type="TASK_LEASE_RELEASED_TERMINAL",
            expected_attempt_id=attempt_id,
        )

        graph_nodes = conn.execute(
            """
            SELECT * FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id
            """,
            (graph_id, graph_revision),
        ).fetchall()
        result = {
            "attempt": dict(attempt),
            "node": node_record,
            "graph": _graph_record(graph, graph_nodes),
            "lease": dict(lease),
            "replayed": True,
        }
        if budget_record is not None:
            result["budget"] = budget_record
        return result
