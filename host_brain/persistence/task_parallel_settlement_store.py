"""Parallel-settlement 0090 atomic persistence and replay authority.

The mixin is stateless and borrows the owning ``DurableTaskStore`` connection,
transaction, clock, Graph/Budget primitives, and shared parallel-state helpers.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetReservationStatus,
    validate_budget_transition,
)
from host_brain.contracts.agent_runtime.parallel_settlement import (
    CanonicalParallelSettlementDecision,
    PARALLEL_SETTLEMENT_SCHEMA_VERSION,
    ParallelSettlementContractError,
    normalize_parallel_settlement_decision,
)
from host_brain.contracts.agent_runtime.task_graph import TaskGraphStatus, TaskNodeStatus
from host_brain.persistence.task_decision_integrity import (
    _assert_parallel_budget_usage_lineage,
    _assert_parallel_settlement_decision_outbox_binding,
    _find_invalid_declared_parallel_settlement_decision_ref,
)
from host_brain.persistence.task_record_codec import (
    _graph_record,
    _parallel_settlement_decision_record,
)
from host_brain.persistence.task_store_types import (
    AttemptStatus,
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_NOT_FOUND,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
    _PARALLEL_SETTLEMENT_DECISION_REQUIRED_COLUMNS,
)


class TaskParallelSettlementStoreMixin:
    """Read, settle, and replay canonical parallel terminal decisions."""

    def assert_parallel_settlement_decision_ready(self) -> None:
        """Require the NE-01 B4 canonical parallel-decision journal."""

        conn = self._connect()
        try:
            columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(task_runtime_parallel_settlement_decision)"
                ).fetchall()
            }
        finally:
            conn.close()
        missing = sorted(_PARALLEL_SETTLEMENT_DECISION_REQUIRED_COLUMNS - columns)
        if missing:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime parallel decision migration 0090 is not applied: "
                f"{missing}",
            )

    def get_parallel_settlement_decision(self, decision_ref: str) -> dict[str, Any]:
        """Resolve and integrity-check one canonical parallel decision."""

        self.assert_parallel_settlement_decision_ready()
        reference = str(decision_ref or "").strip()
        if not reference:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "parallel settlement decision_ref is required",
            )
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            row = conn.execute(
                """
                SELECT * FROM task_runtime_parallel_settlement_decision
                WHERE decision_ref = ?
                """,
                (reference,),
            ).fetchone()
            if row is None:
                raise TaskStoreError(
                    TASK_STORE_NOT_FOUND,
                    f"parallel settlement decision not found: {reference}",
                )
            record = _parallel_settlement_decision_record(row)
            _assert_parallel_settlement_decision_outbox_binding(conn, record)
            self._validate_parallel_decision_attempt_lineage(
                conn,
                decision=normalize_parallel_settlement_decision(record["payload"]),
                graph_id=str(record["graph_id"]),
                revision=int(record["graph_revision"]),
                require_latest=False,
                reason_code=TASK_STORE_DURABLE_DATA_INVALID,
            )
            return record
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    def get_latest_parallel_settlement_decision(
        self,
        graph_id: str,
        revision: int,
    ) -> dict[str, Any] | None:
        """Return the newest decision for a graph revision, if one exists."""

        self.assert_parallel_settlement_decision_ready()
        graph_ref = str(graph_id or "").strip()
        if not graph_ref or isinstance(revision, bool) or int(revision) < 1:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "parallel settlement graph identity is invalid",
            )
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            declared_ref = _find_invalid_declared_parallel_settlement_decision_ref(
                conn,
                graph_id=graph_ref,
                graph_revision=int(revision),
            )
            if declared_ref is not None:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "parallel settlement Graph Outbox has an invalid or missing "
                    f"decision for {graph_ref}@{int(revision)}: {declared_ref}",
                )
            row = conn.execute(
                """
                SELECT * FROM task_runtime_parallel_settlement_decision
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY graph_version_after DESC, decision_ref DESC
                LIMIT 1
                """,
                (graph_ref, int(revision)),
            ).fetchone()
            if row is None:
                return None
            record = _parallel_settlement_decision_record(row)
            _assert_parallel_settlement_decision_outbox_binding(conn, record)
            self._validate_parallel_decision_attempt_lineage(
                conn,
                decision=normalize_parallel_settlement_decision(record["payload"]),
                graph_id=graph_ref,
                revision=int(revision),
                require_latest=False,
                reason_code=TASK_STORE_DURABLE_DATA_INVALID,
            )
            return record
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    def settle_parallel_graph_budget(
        self,
        graph_id: str,
        revision: int,
        *,
        budget_id: str,
        expected_graph_version: int,
        expected_budget_version: int,
        expected_node_states: Mapping[str, Mapping[str, Any]],
        graph_target_status: TaskGraphStatus | str,
        budget_target_status: BudgetReservationStatus | str | None,
        decision_ref: str,
        decision_payload: Mapping[str, Any],
        graph_outbox: OutboxMessage,
        budget_outbox: OutboxMessage | None = None,
    ) -> dict[str, Any]:
        """Atomically settle one completed parallel batch at graph scope.

        Children may mutate only their Attempt/Node/Lease and shared budget
        usage.  This outer fan-in boundary verifies the complete durable node
        snapshot and absence of active leases before it changes the TaskGraph
        and, optionally, releases the shared BudgetReservation.  The canonical
        decision payload, Graph/Budget mutations, and their Outboxes commit in
        the same transaction.  An exact response-loss replay returns the
        already committed decision; any snapshot, state, version, payload, or
        outbox drift fails closed.
        """

        self.assert_parallel_settlement_decision_ready()

        graph_ref = str(graph_id or "").strip()
        budget_ref = str(budget_id or "").strip()
        if not graph_ref or not budget_ref or int(revision) < 1:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "parallel settlement requires graph, revision, and budget identity",
            )
        if (
            isinstance(expected_graph_version, bool)
            or not isinstance(expected_graph_version, int)
            or expected_graph_version < 0
            or isinstance(expected_budget_version, bool)
            or not isinstance(expected_budget_version, int)
            or expected_budget_version < 0
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "parallel settlement expected versions must be non-negative integers",
            )
        try:
            graph_target = TaskGraphStatus(str(graph_target_status))
        except ValueError as exc:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                f"unsupported parallel graph settlement status: {graph_target_status}",
            ) from exc
        if graph_target not in {TaskGraphStatus.FAILED, TaskGraphStatus.BLOCKED}:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "parallel graph settlement target must be FAILED or BLOCKED",
            )
        budget_target: BudgetReservationStatus | None = None
        if budget_target_status is not None:
            try:
                budget_target = BudgetReservationStatus(str(budget_target_status))
            except ValueError as exc:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"unsupported parallel budget settlement status: {budget_target_status}",
                ) from exc
            if budget_target is not BudgetReservationStatus.RELEASED:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "parallel budget settlement target may only be RELEASED",
                )
        if graph_target is TaskGraphStatus.FAILED and budget_target is None:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "FAILED parallel settlement must release the shared budget",
            )
        if (budget_target is None) != (budget_outbox is None):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "budget_outbox is required exactly when a budget target is requested",
            )
        normalized_states = self._normalize_parallel_node_states(expected_node_states)
        self._validate_parallel_terminal_snapshot(
            normalized_states,
            graph_target=graph_target,
            budget_target=budget_target,
        )
        try:
            decision = normalize_parallel_settlement_decision(decision_payload)
        except ParallelSettlementContractError as exc:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                f"parallel settlement decision is invalid: {exc}",
            ) from exc
        decision_reference = str(decision_ref or "").strip()
        expected_state_payload = {
            node_id: {"status": status, "status_version": version}
            for node_id, (status, version) in normalized_states.items()
        }
        expected_budget_target = budget_target.value if budget_target is not None else None
        if (
            decision.decision_ref != decision_reference
            or decision.payload["graph_id"] != graph_ref
            or decision.payload["graph_revision"] != int(revision)
            or decision.payload["graph_status_version_before"]
            != expected_graph_version
            or decision.payload["graph_target_status"] != graph_target.value
            or decision.payload["budget_id"] != budget_ref
            or decision.payload["budget_status_version_before"]
            != expected_budget_version
            or decision.payload["budget_target_status"] != expected_budget_target
            or decision.payload["node_states"] != expected_state_payload
            or decision.payload["graph_outbox_id"] != graph_outbox.outbox_id
            or graph_outbox.graph_revision != int(revision)
            or decision.payload["budget_outbox_id"]
            != (budget_outbox.outbox_id if budget_outbox is not None else None)
            or decision_reference not in graph_outbox.evidence_refs
            or (
                budget_outbox is not None
                and decision_reference not in budget_outbox.evidence_refs
            )
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "parallel settlement decision does not bind the requested mutation",
            )
        ts_ms = self._now()

        with self._transaction() as conn:
            self._assert_graph_not_cancelling(
                conn,
                graph_id=graph_ref,
                graph_revision=int(revision),
            )
            node_rows = conn.execute(
                """
                SELECT * FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY node_id
                """,
                (graph_ref, int(revision)),
            ).fetchall()
            self._assert_parallel_node_states(
                node_rows,
                normalized_states,
                graph_id=graph_ref,
                revision=int(revision),
            )
            active_lease = conn.execute(
                """
                SELECT lease.lease_id
                FROM task_runtime_lease AS lease
                JOIN task_runtime_attempt AS attempt
                  ON attempt.attempt_id = lease.attempt_id
                WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
                  AND lease.status = 'ACTIVE'
                LIMIT 1
                """,
                (graph_ref, int(revision)),
            ).fetchone()
            if active_lease is not None:
                raise TaskStoreError(
                    TASK_STORE_OWNERSHIP_CONFLICT,
                    "parallel graph settlement requires every child lease to be closed",
                )
            active_attempt = conn.execute(
                """
                SELECT attempt_id
                FROM task_runtime_attempt
                WHERE graph_id = ? AND graph_revision = ?
                  AND status IN (
                    'CREATED', 'LEASED', 'RUNNING', 'RECOVERABLE',
                    'CANCEL_REQUESTED'
                  )
                LIMIT 1
                """,
                (graph_ref, int(revision)),
            ).fetchone()
            if active_attempt is not None:
                raise TaskStoreError(
                    TASK_STORE_OWNERSHIP_CONFLICT,
                    "parallel graph settlement requires every child Attempt to be stable",
                )
            unresolved_reservation = conn.execute(
                """
                SELECT reservation.reservation_id
                FROM task_runtime_idempotency_reservation AS reservation
                JOIN task_runtime_attempt AS attempt
                  ON attempt.attempt_id = reservation.attempt_id
                WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
                  AND reservation.status IN ('RESERVED', 'RECOVERY_REQUIRED')
                LIMIT 1
                """,
                (graph_ref, int(revision)),
            ).fetchone()
            if unresolved_reservation is not None:
                raise TaskStoreError(
                    TASK_STORE_OWNERSHIP_CONFLICT,
                    "parallel graph settlement requires every operation reservation to be reconciled",
                )
            self._validate_parallel_decision_attempt_lineage(
                conn,
                decision=decision,
                graph_id=graph_ref,
                revision=int(revision),
            )

            graph = self._require_graph(conn, graph_ref, int(revision))
            budget = self._require_budget(conn, budget_ref)
            if (
                str(budget["graph_id"] or "") != graph_ref
                or int(budget["graph_revision"] or 0) != int(revision)
                or budget["node_id"] is not None
                or budget["attempt_id"] is not None
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "parallel settlement budget is not the shared graph reservation",
                )

            graph_replayed = (
                str(graph["status"]) == graph_target.value
                and int(graph["status_version"]) == expected_graph_version + 1
            )
            if graph_replayed:
                self._assert_mutation_outbox_replay(
                    conn,
                    graph_outbox,
                    expected_aggregate=(
                        "TASK_GRAPH",
                        graph_ref,
                        expected_graph_version + 1,
                    ),
                    expected_event_type=f"TASK_GRAPH_{graph_target.value}",
                    expected_attempt_id=None,
                    expected_graph_revision=int(revision),
                )
                if budget_target is None:
                    if (
                        int(budget["status_version"]) != expected_budget_version
                        or str(budget["status"])
                        != BudgetReservationStatus.ACTIVE.value
                    ):
                        raise TaskStoreError(
                            TASK_STORE_CAS_CONFLICT,
                            "parallel settlement replay budget state differs",
                        )
                else:
                    if (
                        str(budget["status"]) != budget_target.value
                        or int(budget["status_version"])
                        != expected_budget_version + 1
                    ):
                        raise TaskStoreError(
                            TASK_STORE_CAS_CONFLICT,
                            "parallel settlement replay budget terminal state differs",
                        )
                    self._assert_mutation_outbox_replay(
                        conn,
                        budget_outbox,
                        expected_aggregate=(
                            "BUDGET_RESERVATION",
                            budget_ref,
                            expected_budget_version + 1,
                        ),
                        expected_event_type="BUDGET_RESERVATION_RELEASED",
                        expected_attempt_id=None,
                    )
                decision_record = self._assert_parallel_settlement_decision_replay(
                    conn,
                    decision=decision,
                    graph_version_before=expected_graph_version,
                    budget_version_before=expected_budget_version,
                    graph_outbox=graph_outbox,
                    budget_outbox=budget_outbox,
                )
                return {
                    "graph": _graph_record(graph, node_rows),
                    "budget": self._budget_record(budget),
                    "decision": decision_record,
                    "replayed": True,
                }

            self._require_version(
                graph,
                expected_graph_version,
                subject=f"TaskGraph {graph_ref}@{int(revision)}",
            )
            if str(graph["status"]) != TaskGraphStatus.ACTIVE.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "parallel graph settlement requires an ACTIVE TaskGraph",
                )
            if decision.payload["graph_status_before"] != str(graph["status"]):
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    "parallel decision Graph source status differs",
                )
            self._require_version(
                budget,
                expected_budget_version,
                subject=f"BudgetReservation {budget_ref}",
            )
            allowed_budget_statuses = (
                {BudgetReservationStatus.ACTIVE.value}
                if budget_target is None
                else {
                    BudgetReservationStatus.ACTIVE.value,
                    BudgetReservationStatus.EXCEEDED.value,
                }
            )
            if str(budget["status"]) not in allowed_budget_statuses:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "parallel graph settlement requires an ACTIVE or EXCEEDED budget",
                )
            if decision.payload["budget_status_before"] != str(budget["status"]):
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    "parallel decision Budget source status differs",
                )

            if budget_target is not None:
                validate_budget_transition(str(budget["status"]), budget_target)
                budget_cursor = conn.execute(
                    """
                    UPDATE task_runtime_budget_reservation
                    SET status = 'RELEASED', status_version = status_version + 1,
                        updated_ts_ms = ?
                    WHERE budget_id = ? AND status_version = ?
                      AND status IN ('ACTIVE', 'EXCEEDED')
                    """,
                    (ts_ms, budget_ref, expected_budget_version),
                )
                self._require_cas(
                    budget_cursor,
                    subject=f"BudgetReservation {budget_ref}",
                )
                self._insert_outbox(
                    conn,
                    budget_outbox,
                    ts_ms=ts_ms,
                    expected_aggregate=(
                        "BUDGET_RESERVATION",
                        budget_ref,
                        expected_budget_version + 1,
                    ),
                    expected_event_type="BUDGET_RESERVATION_RELEASED",
                    expected_attempt_id=None,
                )

            graph_record = self._transition_graph_in_transaction(
                conn,
                graph_id=graph_ref,
                revision=int(revision),
                expected_version=expected_graph_version,
                target_status=graph_target,
                outbox=graph_outbox,
                ts_ms=ts_ms,
            )
            decision_record = self._insert_parallel_settlement_decision(
                conn,
                decision=decision,
                graph_version_before=expected_graph_version,
                budget_version_before=expected_budget_version,
                graph_outbox=graph_outbox,
                budget_outbox=budget_outbox,
                ts_ms=ts_ms,
            )
            return {
                "graph": graph_record,
                "budget": self._budget_record(self._require_budget(conn, budget_ref)),
                "decision": decision_record,
                "replayed": False,
            }

    @staticmethod
    def _validate_parallel_terminal_snapshot(
        states: Mapping[str, tuple[str, int]],
        *,
        graph_target: TaskGraphStatus,
        budget_target: BudgetReservationStatus | None,
    ) -> None:
        statuses = {status for status, _ in states.values()}
        unstable = statuses.intersection(
            {
                TaskNodeStatus.LEASED.value,
                TaskNodeStatus.RUNNING.value,
                TaskNodeStatus.EXECUTED.value,
                TaskNodeStatus.VERIFYING.value,
                TaskNodeStatus.VERIFIED.value,
                TaskNodeStatus.CANCEL_REQUESTED.value,
            }
        )
        if unstable:
            raise TaskStoreError(
                TASK_STORE_OWNERSHIP_CONFLICT,
                "parallel settlement contains an in-flight TaskNode state",
            )
        failure_states = {
            TaskNodeStatus.FAILED.value,
            TaskNodeStatus.VERIFICATION_FAILED.value,
            TaskNodeStatus.CANCELLED.value,
        }
        review_states = {
            TaskNodeStatus.WAITING_APPROVAL.value,
            TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW.value,
        }
        if graph_target is TaskGraphStatus.FAILED and not statuses.intersection(
            failure_states
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "FAILED parallel settlement lacks a durable failed TaskNode",
            )
        if graph_target is TaskGraphStatus.BLOCKED and not statuses.intersection(
            failure_states | review_states
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "BLOCKED parallel settlement lacks a durable blocking TaskNode",
            )
        if budget_target is None:
            if statuses.intersection(failure_states):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "parallel review settlement cannot retain a failed TaskNode",
                )
            if not statuses.intersection(review_states):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "parallel settlement may keep Budget ACTIVE only for a durable review gate",
                )

    @staticmethod
    def _validate_parallel_decision_attempt_lineage(
        conn: sqlite3.Connection,
        *,
        decision: CanonicalParallelSettlementDecision,
        graph_id: str,
        revision: int,
        require_latest: bool = True,
        reason_code: str = TASK_STORE_CAS_CONFLICT,
    ) -> None:
        expected_attempt_statuses = {
            "ACCEPTED": {AttemptStatus.SUCCEEDED.value},
            "WAITING_ACCEPTANCE_REVIEW": {AttemptStatus.SUCCEEDED.value},
            "VERIFICATION_FAILED": {AttemptStatus.SUCCEEDED.value},
            "FAILED": {AttemptStatus.FAILED.value},
            "BUDGET_BLOCKED": {AttemptStatus.FAILED.value},
            "RETRY_PENDING": {AttemptStatus.FAILED.value},
            "CANCELLED": {AttemptStatus.CANCELLED.value},
        }
        for outcome in decision.payload["outcomes"]:
            node_id = str(outcome["node_id"])
            outcome_status = str(outcome["status"])
            if outcome_status == TaskNodeStatus.WAITING_APPROVAL.value:
                if outcome["attempt_id"] is not None:
                    raise TaskStoreError(
                        reason_code,
                        f"parallel settlement pre-execution review bound an Attempt: {node_id}",
                    )
                continue
            attempt_id = str(outcome["attempt_id"])
            attempt = conn.execute(
                "SELECT * FROM task_runtime_attempt WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            latest = conn.execute(
                """
                SELECT attempt_id FROM task_runtime_attempt
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                ORDER BY attempt_no DESC, attempt_id DESC
                LIMIT 1
                """,
                (str(graph_id), int(revision), node_id),
            ).fetchone()
            allowed = expected_attempt_statuses.get(outcome_status)
            if (
                attempt is None
                or (
                    require_latest
                    and (
                        latest is None
                        or str(latest["attempt_id"]) != attempt_id
                    )
                )
                or str(attempt["graph_id"]) != str(graph_id)
                or int(attempt["graph_revision"]) != int(revision)
                or str(attempt["node_id"]) != node_id
                or allowed is None
                or str(attempt["status"]) not in allowed
            ):
                raise TaskStoreError(
                    reason_code,
                    f"parallel settlement Attempt lineage drifted: {node_id}",
                )
        if decision.payload["decision_kind"] == "BUDGET":
            _assert_parallel_budget_usage_lineage(
                conn,
                decision.payload,
                reason_code=reason_code,
            )

    @staticmethod
    def _insert_parallel_settlement_decision(
        conn: sqlite3.Connection,
        *,
        decision: CanonicalParallelSettlementDecision,
        graph_version_before: int,
        budget_version_before: int,
        graph_outbox: OutboxMessage,
        budget_outbox: OutboxMessage | None,
        ts_ms: int,
    ) -> dict[str, Any]:
        payload = decision.payload
        budget_released = payload["budget_target_status"] == "RELEASED"
        try:
            conn.execute(
                """
                INSERT INTO task_runtime_parallel_settlement_decision(
                    decision_ref, schema_version, graph_id, graph_revision,
                    graph_version_before, graph_version_after, budget_id,
                    budget_version_before, budget_version_after, decision_kind,
                    selected_node_id, graph_target_status, budget_target_status,
                    payload_json, payload_sha256, graph_outbox_id,
                    budget_outbox_id, created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_ref,
                    PARALLEL_SETTLEMENT_SCHEMA_VERSION,
                    payload["graph_id"],
                    payload["graph_revision"],
                    int(graph_version_before),
                    int(graph_version_before) + 1,
                    payload["budget_id"],
                    int(budget_version_before),
                    int(budget_version_before) + (1 if budget_released else 0),
                    payload["decision_kind"],
                    payload["selected_node_id"],
                    payload["graph_target_status"],
                    payload["budget_target_status"],
                    decision.payload_json,
                    decision.payload_sha256,
                    graph_outbox.outbox_id,
                    budget_outbox.outbox_id if budget_outbox is not None else None,
                    int(ts_ms),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "parallel settlement decision identity already exists",
            ) from exc
        row = conn.execute(
            """
            SELECT * FROM task_runtime_parallel_settlement_decision
            WHERE decision_ref = ?
            """,
            (decision.decision_ref,),
        ).fetchone()
        if row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "parallel settlement decision insert was not durable",
            )
        return _parallel_settlement_decision_record(row)

    @staticmethod
    def _assert_parallel_settlement_decision_replay(
        conn: sqlite3.Connection,
        *,
        decision: CanonicalParallelSettlementDecision,
        graph_version_before: int,
        budget_version_before: int,
        graph_outbox: OutboxMessage,
        budget_outbox: OutboxMessage | None,
    ) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT * FROM task_runtime_parallel_settlement_decision
            WHERE decision_ref = ?
            """,
            (decision.decision_ref,),
        ).fetchone()
        if row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal Graph replay is missing its canonical parallel decision",
            )
        record = _parallel_settlement_decision_record(row)
        budget_released = decision.payload["budget_target_status"] == "RELEASED"
        if (
            record["payload_json"] != decision.payload_json
            or record["payload_sha256"] != decision.payload_sha256
            or record["graph_version_before"] != int(graph_version_before)
            or record["graph_version_after"] != int(graph_version_before) + 1
            or record["budget_version_before"] != int(budget_version_before)
            or record["budget_version_after"]
            != int(budget_version_before) + (1 if budget_released else 0)
            or record["graph_outbox_id"] != graph_outbox.outbox_id
            or record["budget_outbox_id"]
            != (budget_outbox.outbox_id if budget_outbox is not None else None)
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "canonical parallel decision replay drifted",
            )
        return record
