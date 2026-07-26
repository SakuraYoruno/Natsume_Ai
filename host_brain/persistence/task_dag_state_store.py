"""DAG admission, projection reads, and public state transitions.

The mixin borrows connection, transaction, clock, and shared transition
authorities from the concrete TaskStore.  Connection-scoped inserts never own
commit, rollback, or connection lifetime.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from host_brain.contracts.agent_runtime.acceptance_judge import (
    AcceptanceJudgement,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.verification_contract import (
    VerificationResult,
)
from host_brain.persistence.task_record_codec import (
    _budget_usage_record,
    _dumps,
    _graph_record,
    _node_record,
    _outbox_record,
)
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_INVALID,
    TASK_STORE_NOT_FOUND,
    TaskStoreError,
)


class TaskDagStateStoreMixin:
    """Persist and read DAG state without owning concrete store state."""

    @staticmethod
    def _assert_graph_accepts_execution_authority(
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        action: str,
    ) -> None:
        """Fence new Attempt/Lease/Operation authority after Graph closeout."""

        graph = conn.execute(
            "SELECT status FROM task_runtime_graph WHERE graph_id = ? AND revision = ?",
            (str(graph_id), int(graph_revision)),
        ).fetchone()
        if graph is None:
            raise TaskStoreError(
                TASK_STORE_NOT_FOUND,
                f"TaskGraph not found: {graph_id}@{graph_revision}",
            )
        status = str(graph["status"] or "").strip().upper()
        if status not in {TaskGraphStatus.CREATED.value, TaskGraphStatus.ACTIVE.value}:
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                f"TaskGraph {graph_id}@{graph_revision} in {status or 'UNKNOWN'} "
                f"cannot grant {str(action or 'execution authority')}",
            )
        graph_budgets = conn.execute(
            """
            SELECT budget_id, status
            FROM task_runtime_budget_reservation
            WHERE graph_id = ? AND graph_revision = ?
              AND node_id IS NULL AND attempt_id IS NULL
            ORDER BY budget_id
            """,
            (str(graph_id), int(graph_revision)),
        ).fetchall()
        if (
            len(graph_budgets) != 1
            or str(graph_budgets[0]["status"]) != "ACTIVE"
        ):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                f"TaskGraph {graph_id}@{graph_revision} cannot grant "
                f"{str(action or 'execution authority')} without exactly one "
                "ACTIVE graph-scoped BudgetReservation",
            )

    @staticmethod
    def _assert_graph_execution_authority_quiescent(
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        action: str,
    ) -> None:
        """Fence aggregate terminal writes while node execution can still act.

        The caller owns a ``BEGIN IMMEDIATE`` transaction.  Consequently this
        check and the Graph CAS form the opposite half of the execution-start
        fence: either an Attempt/Lease/Operation wins first and terminalization
        is rejected, or the terminal Graph wins and subsequent authority
        creation observes a non-executable Graph.
        """

        unsettled_node = conn.execute(
            """
            SELECT node_id, status
            FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ?
              AND status IN (
                'LEASED', 'RUNNING', 'EXECUTED', 'VERIFYING', 'VERIFIED',
                'CANCEL_REQUESTED'
              )
            ORDER BY node_id
            LIMIT 1
            """,
            (str(graph_id), int(graph_revision)),
        ).fetchone()
        if unsettled_node is not None:
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                f"unsettled TaskNode execution fences "
                f"{str(action or 'Graph terminalization')}: "
                f"{unsettled_node['node_id']}:{unsettled_node['status']}",
            )

        unsettled = conn.execute(
            """
            SELECT attempt.attempt_id,
                   attempt.status AS attempt_status,
                   lease.lease_id,
                   operation.reservation_id,
                   operation.status AS operation_status
            FROM task_runtime_attempt AS attempt
            LEFT JOIN task_runtime_lease AS lease
              ON lease.attempt_id = attempt.attempt_id
             AND lease.status = 'ACTIVE'
            LEFT JOIN task_runtime_idempotency_reservation AS operation
              ON operation.attempt_id = attempt.attempt_id
             AND operation.status IN ('RESERVED', 'RECOVERY_REQUIRED')
            WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
              AND (
                attempt.status IN (
                  'CREATED', 'LEASED', 'RUNNING', 'RECOVERABLE',
                  'CANCEL_REQUESTED'
                )
                OR lease.lease_id IS NOT NULL
                OR operation.reservation_id IS NOT NULL
              )
            ORDER BY attempt.attempt_id, lease.lease_id, operation.reservation_id
            LIMIT 1
            """,
            (str(graph_id), int(graph_revision)),
        ).fetchone()
        if unsettled is None:
            return
        identity = (
            str(unsettled["reservation_id"] or "")
            or str(unsettled["lease_id"] or "")
            or str(unsettled["attempt_id"])
        )
        raise TaskStoreError(
            TASK_STORE_CAS_CONFLICT,
            f"unsettled execution authority fences {str(action or 'Graph terminalization')}: "
            f"{identity}",
        )

    def _insert_graph_from_host_command_in_transaction(
        self,
        conn: sqlite3.Connection,
        graph: TaskGraph,
        *,
        outbox: OutboxMessage,
        ts_ms: int,
    ) -> None:
        """Insert one Host-admitted canonical graph into its owning UoW.

        The caller owns commit, rollback, and connection lifetime.  This narrow
        private hook lets ``HostCommandRepository`` atomically bind a command
        to the exact graph it creates without exposing a second standalone
        graph-creation authority or opening a nested SQLite transaction.
        """

        if not isinstance(conn, sqlite3.Connection) or not conn.in_transaction:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "connection-scoped graph insertion requires an active SQLite transaction",
            )
        graph.validate()
        try:
            trusted_ts_ms = int(ts_ms)
        except (TypeError, ValueError) as exc:
            raise TaskStoreError(TASK_STORE_INVALID, "graph timestamp is invalid") from exc
        if trusted_ts_ms < 0:
            raise TaskStoreError(TASK_STORE_INVALID, "graph timestamp must be non-negative")
        if (
            not isinstance(outbox, OutboxMessage)
            or outbox.graph_revision != graph.revision
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "TaskGraph creation outbox must bind the exact graph revision",
            )
        conn.execute(
            """
            INSERT INTO task_runtime_graph(
                graph_id, revision, root_work_order_id, planner_ref, status,
                status_version, graph_json, source_kind, source_ref,
                legacy_snapshot_json, created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, ?, 0, ?, 'CANONICAL', ?, NULL, ?, ?)
            """,
            (
                graph.graph_id,
                graph.revision,
                graph.root_work_order_id,
                graph.planner_ref,
                graph.status.value,
                _dumps(graph.to_dict()),
                f"{graph.graph_id}@{graph.revision}",
                trusted_ts_ms,
                trusted_ts_ms,
            ),
        )
        for node in graph.nodes:
            conn.execute(
                """
                INSERT INTO task_runtime_node(
                    graph_id, graph_revision, node_id, status, status_version,
                    node_json, cancellation_requested_ts_ms, created_ts_ms, updated_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    graph.graph_id,
                    graph.revision,
                    node.node_id,
                    node.status.value,
                    node.status_version,
                    _dumps(node.to_dict()),
                    trusted_ts_ms,
                    trusted_ts_ms,
                ),
            )
        self.insert_outbox_in_transaction(
            conn,
            outbox,
            ts_ms=trusted_ts_ms,
            expected_aggregate=("TASK_GRAPH", graph.graph_id, 0),
            expected_event_type="TASK_GRAPH_CREATED",
            expected_attempt_id=None,
            expected_graph_revision=graph.revision,
        )

    def insert_outbox_in_transaction(
        self,
        conn: sqlite3.Connection,
        message: OutboxMessage,
        *,
        ts_ms: int,
        expected_aggregate: tuple[str, str, int],
        expected_event_type: str,
        expected_attempt_id: str | None = None,
        expected_graph_revision: int | None = None,
    ) -> None:
        """Append a validated TaskStore outbox row inside an existing UoW."""

        if not isinstance(conn, sqlite3.Connection) or not conn.in_transaction:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "connection-scoped outbox insertion requires an active SQLite transaction",
            )
        self._insert_outbox(
            conn,
            message,
            ts_ms=int(ts_ms),
            expected_aggregate=expected_aggregate,
            expected_event_type=expected_event_type,
            expected_attempt_id=expected_attempt_id,
            expected_graph_revision=expected_graph_revision,
        )

    def get_graph(self, graph_id: str, revision: int) -> dict[str, Any]:
        conn = self._connect()
        try:
            graph_row = conn.execute(
                "SELECT * FROM task_runtime_graph WHERE graph_id = ? AND revision = ?",
                (str(graph_id), int(revision)),
            ).fetchone()
            if graph_row is None:
                raise TaskStoreError(TASK_STORE_NOT_FOUND, f"TaskGraph not found: {graph_id}@{revision}")
            node_rows = conn.execute(
                """
                SELECT * FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY node_id
                """,
                (str(graph_id), int(revision)),
            ).fetchall()
            return _graph_record(graph_row, node_rows)
        finally:
            conn.close()

    def list_graphs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent validated graph records for versioned read adapters."""

        size = max(1, min(int(limit), 500))
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            graph_rows = conn.execute(
                """
                SELECT * FROM task_runtime_graph
                ORDER BY updated_ts_ms DESC, graph_id, revision DESC
                LIMIT ?
                """,
                (size,),
            ).fetchall()
            records: list[dict[str, Any]] = []
            for graph_row in graph_rows:
                node_rows = conn.execute(
                    """
                    SELECT * FROM task_runtime_node
                    WHERE graph_id = ? AND graph_revision = ?
                    ORDER BY node_id
                    """,
                    (str(graph_row["graph_id"]), int(graph_row["revision"])),
                ).fetchall()
                records.append(_graph_record(graph_row, node_rows))
            conn.commit()
            return records
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_nonterminal_graphs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return a bounded, consistent inventory of resumable Graphs.

        Startup continuation must never infer unfinished work from a generic
        recent-history query: a busy installation can have far more completed
        Graphs than active work.  This read adapter exposes only the durable
        states that may still require a worker, Review, or terminal recovery.
        It deliberately has no write authority and returns the same validated
        projection shape as :meth:`list_graphs`.
        """

        size = max(1, min(int(limit), 500))
        resumable_statuses = (
            TaskGraphStatus.CREATED.value,
            TaskGraphStatus.ACTIVE.value,
            TaskGraphStatus.BLOCKED.value,
        )
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            graph_rows = conn.execute(
                """
                SELECT * FROM task_runtime_graph
                WHERE status IN (?, ?, ?)
                ORDER BY updated_ts_ms DESC, graph_id, revision DESC
                LIMIT ?
                """,
                (*resumable_statuses, size),
            ).fetchall()
            records: list[dict[str, Any]] = []
            for graph_row in graph_rows:
                node_rows = conn.execute(
                    """
                    SELECT * FROM task_runtime_node
                    WHERE graph_id = ? AND graph_revision = ?
                    ORDER BY node_id
                    """,
                    (str(graph_row["graph_id"]), int(graph_row["revision"])),
                ).fetchall()
                records.append(_graph_record(graph_row, node_rows))
            conn.commit()
            return records
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_node(self, graph_id: str, graph_revision: int, node_id: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                """,
                (str(graph_id), int(graph_revision), str(node_id)),
            ).fetchone()
            if row is None:
                raise TaskStoreError(TASK_STORE_NOT_FOUND, f"TaskNode not found: {node_id}")
            return _node_record(row)
        finally:
            conn.close()

    def read_scheduler_snapshot(
        self,
        graph_id: str,
        revision: int,
        *,
        include_graph_outboxes: bool = False,
    ) -> dict[str, Any]:
        """Return one consistent read-only view for shadow scheduling."""
        observed_ts_ms = self._now()
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            graph_row = self._require_graph(conn, graph_id, revision)
            node_rows = conn.execute(
                "SELECT * FROM task_runtime_node WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id",
                (str(graph_id), int(revision)),
            ).fetchall()
            attempt_rows = conn.execute(
                "SELECT * FROM task_runtime_attempt WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id, attempt_no, attempt_id",
                (str(graph_id), int(revision)),
            ).fetchall()
            lease_rows = conn.execute(
                """SELECT lease.* FROM task_runtime_lease AS lease
                   JOIN task_runtime_attempt AS attempt ON attempt.attempt_id = lease.attempt_id
                   WHERE attempt.graph_id = ? AND attempt.graph_revision = ? AND lease.status = 'ACTIVE'
                   ORDER BY lease.attempt_id, lease.fencing_token""",
                (str(graph_id), int(revision)),
            ).fetchall()
            budget_rows = conn.execute(
                """SELECT * FROM task_runtime_budget_reservation
                   WHERE graph_id = ? AND graph_revision = ?
                   ORDER BY node_id, budget_id""",
                (str(graph_id), int(revision)),
            ).fetchall()
            graph_outbox_rows: list[sqlite3.Row] = []
            if include_graph_outboxes and getattr(
                self, "_outbox_graph_revision_enabled", False
            ):
                graph_outbox_rows = conn.execute(
                    """
                    SELECT * FROM task_runtime_outbox
                    WHERE aggregate_type = 'TASK_GRAPH'
                      AND aggregate_id = ? AND graph_revision = ?
                    ORDER BY aggregate_version, created_ts_ms, outbox_id
                    """,
                    (str(graph_id), int(revision)),
                ).fetchall()
            elif include_graph_outboxes:
                graph_outbox_rows = conn.execute(
                    """
                    SELECT * FROM task_runtime_outbox
                    WHERE aggregate_type = 'TASK_GRAPH' AND aggregate_id = ?
                    ORDER BY aggregate_version, created_ts_ms, outbox_id
                    """,
                    (str(graph_id),),
                ).fetchall()
            usage_ready = conn.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type = 'table' AND name = 'task_runtime_budget_usage'"""
            ).fetchone()
            budget_usage_rows = (
                conn.execute(
                    """SELECT usage.* FROM task_runtime_budget_usage AS usage
                       JOIN task_runtime_budget_reservation AS budget
                         ON budget.budget_id = usage.budget_id
                       WHERE budget.graph_id = ? AND budget.graph_revision = ?
                       ORDER BY usage.created_ts_ms, usage.budget_id, usage.usage_id""",
                    (str(graph_id), int(revision)),
                ).fetchall()
                if usage_ready is not None
                else []
            )
            node_budget_ready = conn.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type = 'table'
                     AND name = 'task_runtime_node_budget_reservation'"""
            ).fetchone()
            node_budget_rows = (
                conn.execute(
                    """
                    SELECT * FROM task_runtime_node_budget_reservation
                    WHERE graph_id = ? AND graph_revision = ?
                    ORDER BY node_id, attempt_id, node_budget_id
                    """,
                    (str(graph_id), int(revision)),
                ).fetchall()
                if node_budget_ready is not None
                else []
            )
            node_budget_usage_rows = (
                conn.execute(
                    """
                    SELECT * FROM task_runtime_node_budget_usage
                    WHERE node_budget_id IN (
                        SELECT node_budget_id
                        FROM task_runtime_node_budget_reservation
                        WHERE graph_id = ? AND graph_revision = ?
                    )
                    ORDER BY created_ts_ms, node_budget_id, usage_id
                    """,
                    (str(graph_id), int(revision)),
                ).fetchall()
                if node_budget_ready is not None
                else []
            )
            node_verification_ready = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'task_runtime_node_verification_decision'
                """
            ).fetchone()
            node_verification_rows = (
                conn.execute(
                    """
                    SELECT * FROM task_runtime_node_verification_decision
                    WHERE graph_id = ? AND graph_revision = ?
                    ORDER BY node_id, node_version_after, verification_ref
                    """,
                    (str(graph_id), int(revision)),
                ).fetchall()
                if node_verification_ready is not None
                else []
            )
            node_acceptance_ready = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'task_runtime_node_acceptance_decision'
                """
            ).fetchone()
            node_acceptance_rows = (
                conn.execute(
                    """
                    SELECT * FROM task_runtime_node_acceptance_decision
                    WHERE graph_id = ? AND graph_revision = ?
                    ORDER BY node_id, node_version_after, acceptance_ref
                    """,
                    (str(graph_id), int(revision)),
                ).fetchall()
                if node_acceptance_ready is not None
                else []
            )
            snapshot = {
                "observed_ts_ms": observed_ts_ms,
                "graph_record": _graph_record(graph_row, node_rows),
                "attempts": [dict(row) for row in attempt_rows],
                "active_leases": [dict(row) for row in lease_rows],
                "budgets": [self._budget_record(row) for row in budget_rows],
                "budget_usage": [_budget_usage_record(row) for row in budget_usage_rows],
                "node_budgets": [
                    self._node_budget_record(conn, row)
                    for row in node_budget_rows
                ],
                "node_budget_usage": [
                    {
                        **dict(row),
                        "accepted": bool(row["accepted"]),
                    }
                    for row in node_budget_usage_rows
                ],
                "node_verifications": [
                    self._node_verification_record(conn, row)
                    for row in node_verification_rows
                ],
                "node_acceptances": [
                    self._node_acceptance_record(conn, row)
                    for row in node_acceptance_rows
                ],
            }
            if include_graph_outboxes:
                snapshot["graph_outboxes"] = [
                    _outbox_record(row) for row in graph_outbox_rows
                ]
            conn.commit()
            return snapshot
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def transition_graph(
        self,
        graph_id: str,
        revision: int,
        *,
        expected_version: int,
        target_status: TaskGraphStatus | str,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        ts_ms = self._now()
        with self._transaction() as conn:
            self._assert_graph_not_cancelling(
                conn,
                graph_id=graph_id,
                graph_revision=revision,
            )
            target_value = str(
                getattr(target_status, "value", target_status) or ""
            ).strip().upper()
            if target_value in {
                TaskGraphStatus.BLOCKED.value,
                TaskGraphStatus.COMPLETED.value,
                TaskGraphStatus.FAILED.value,
                TaskGraphStatus.CANCELLED.value,
            }:
                self._assert_graph_execution_authority_quiescent(
                    conn,
                    graph_id=graph_id,
                    graph_revision=revision,
                    action=f"TaskGraph {target_value} transition",
                )
            return self._transition_graph_in_transaction(
                conn,
                graph_id=graph_id,
                revision=revision,
                expected_version=expected_version,
                target_status=target_status,
                outbox=outbox,
                ts_ms=ts_ms,
            )
    def transition_node(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        expected_version: int,
        target_status: TaskNodeStatus | str,
        outbox: OutboxMessage,
        verification_result: VerificationResult | Mapping[str, Any] | None = None,
        acceptance_judgement: AcceptanceJudgement | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        ts_ms = self._now()
        with self._transaction() as conn:
            self._assert_graph_not_cancelling(
                conn,
                graph_id=graph_id,
                graph_revision=graph_revision,
            )
            return self._transition_node_in_transaction(
                conn,
                graph_id=graph_id,
                graph_revision=graph_revision,
                node_id=node_id,
                expected_version=expected_version,
                target_status=target_status,
                outbox=outbox,
                verification_result=verification_result,
                acceptance_judgement=acceptance_judgement,
                ts_ms=ts_ms,
            )
