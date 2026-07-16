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
)
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    TASK_STORE_INVALID,
    TASK_STORE_NOT_FOUND,
    TaskStoreError,
)


class TaskDagStateStoreMixin:
    """Persist and read DAG state without owning concrete store state."""

    def create_graph(
        self,
        graph: TaskGraph,
        *,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        ts_ms = self._now()
        with self._transaction() as conn:
            self.insert_graph_in_transaction(
                conn,
                graph,
                outbox=outbox,
                ts_ms=ts_ms,
            )
        return self.get_graph(graph.graph_id, graph.revision)

    def insert_graph_in_transaction(
        self,
        conn: sqlite3.Connection,
        graph: TaskGraph,
        *,
        outbox: OutboxMessage,
        ts_ms: int,
    ) -> None:
        """Insert one canonical graph and its event into an existing UoW.

        The caller owns commit, rollback, and connection lifetime.  This narrow
        hook lets a higher-level admission repository atomically bind a command
        to the exact graph it creates without duplicating TaskStore SQL or
        opening a nested SQLite transaction.
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

    def read_scheduler_snapshot(self, graph_id: str, revision: int) -> dict[str, Any]:
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
            snapshot = {
                "observed_ts_ms": observed_ts_ms,
                "graph_record": _graph_record(graph_row, node_rows),
                "attempts": [dict(row) for row in attempt_rows],
                "active_leases": [dict(row) for row in lease_rows],
                "budgets": [self._budget_record(row) for row in budget_rows],
                "budget_usage": [_budget_usage_record(row) for row in budget_usage_rows],
            }
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
