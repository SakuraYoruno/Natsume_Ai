"""Read-only authority for exact checkpoint budget-rejection terminals."""

from __future__ import annotations

import sqlite3
from typing import Any

from .task_budget_usage_store import _assert_terminal_budget_authority
from .task_record_codec import _graph_record, _node_record, _outbox_record
from .task_store_types import (
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TaskStoreError,
)


_AUTHORITY_INTERNAL = "internal"
_AUTHORITY_CANONICAL = "canonical"


class TaskCheckpointBudgetTerminalReadStoreMixin:
    """Recognize internal/canonical C-boundary terminals without mutation."""

    def resolve_internal_agent_budget_rejection_terminal(
        self,
        attempt_id: str,
    ) -> dict[str, Any]:
        """Reopen one exact Internal Agent budget-rejection terminal."""

        return self._resolve_checkpoint_budget_rejection_terminal(
            attempt_id,
            authority_mode=_AUTHORITY_INTERNAL,
        )

    def try_resolve_checkpoint_budget_rejection_terminal(
        self,
        attempt_id: str,
    ) -> dict[str, Any] | None:
        """Return an exact internal/canonical terminal, or no known authority."""

        attempt_ref = self._checkpoint_budget_attempt_ref(attempt_id)
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            resolved = self._try_resolve_checkpoint_budget_rejection_terminal_uow(
                conn,
                attempt_ref,
            )
            conn.commit()
            return resolved
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _resolve_checkpoint_budget_rejection_terminal(
        self,
        attempt_id: str,
        *,
        authority_mode: str,
    ) -> dict[str, Any]:
        attempt_ref = self._checkpoint_budget_attempt_ref(attempt_id)
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            resolved = self._resolve_checkpoint_budget_rejection_terminal_uow(
                conn,
                attempt_ref,
                authority_mode=authority_mode,
            )
            conn.commit()
            return resolved
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _checkpoint_budget_attempt_ref(value: object) -> str:
        attempt_ref = str(value or "").strip()
        if not attempt_ref:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "checkpoint budget terminal requires attempt_id",
            )
        return attempt_ref

    def _try_resolve_checkpoint_budget_rejection_terminal_uow(
        self,
        conn: sqlite3.Connection,
        attempt_id: str,
    ) -> dict[str, Any] | None:
        rows = conn.execute(
            """
            SELECT outbox_id FROM task_runtime_outbox
            WHERE attempt_id = ?
              AND event_type = 'BUDGET_RESERVATION_RELEASED'
            ORDER BY outbox_id
            """,
            (str(attempt_id),),
        ).fetchall()
        modes = [
            _AUTHORITY_INTERNAL
            if str(row["outbox_id"]).startswith(
                "outbox::internal-agent-budget-rejected:"
            )
            else _AUTHORITY_CANONICAL
            if str(row["outbox_id"]).startswith(
                "outbox::committed-checkpoint-budget-rejected:"
            )
            else None
            for row in rows
        ]
        known = [mode for mode in modes if mode is not None]
        if not known:
            if self._has_checkpoint_budget_terminal_shape_uow(
                conn,
                attempt_id,
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "checkpoint budget terminal lacks exact Outbox authority",
                )
            return None
        if len(rows) != 1 or len(known) != 1:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "checkpoint budget terminal Outbox authority is ambiguous",
            )
        return self._resolve_checkpoint_budget_rejection_terminal_uow(
            conn,
            attempt_id,
            authority_mode=known[0],
        )

    @staticmethod
    def _has_checkpoint_budget_terminal_shape_uow(
        conn: sqlite3.Connection,
        attempt_id: str,
    ) -> bool:
        """Detect a checkpoint terminal claim without trusting its Outbox name.

        A normal blocked failure has no committed worker effect paired with an
        exact rejected BudgetUsage.  Once those durable facts coexist with the
        FAILED/BLOCKED state matrix, treating a missing or unknown release
        Outbox prefix as "no authority" would allow corruption to fall through
        to an ordinary failure replay.
        """

        row = conn.execute(
            """
            SELECT
                attempt.status AS attempt_status,
                node.status AS node_status,
                graph.status AS graph_status,
                EXISTS (
                    SELECT 1
                    FROM task_runtime_idempotency_reservation AS operation
                    WHERE operation.attempt_id = attempt.attempt_id
                      AND operation.status = 'COMMITTED'
                ) AS has_committed_operation,
                EXISTS (
                    SELECT 1
                    FROM task_runtime_budget_usage AS usage
                    JOIN task_runtime_budget_reservation AS budget
                      ON budget.budget_id = usage.budget_id
                    WHERE usage.attempt_id = attempt.attempt_id
                      AND usage.graph_id = attempt.graph_id
                      AND usage.graph_revision = attempt.graph_revision
                      AND usage.node_id = attempt.node_id
                      AND usage.accepted = 0
                      AND usage.reason_code = 'TASK_STORE_BUDGET_EXCEEDED'
                      AND budget.graph_id = attempt.graph_id
                      AND budget.graph_revision = attempt.graph_revision
                      AND budget.node_id IS NULL
                      AND budget.attempt_id IS NULL
                      AND budget.status = 'RELEASED'
                ) AS has_rejected_released_budget
            FROM task_runtime_attempt AS attempt
            JOIN task_runtime_node AS node
              ON node.graph_id = attempt.graph_id
             AND node.graph_revision = attempt.graph_revision
             AND node.node_id = attempt.node_id
            JOIN task_runtime_graph AS graph
              ON graph.graph_id = attempt.graph_id
             AND graph.revision = attempt.graph_revision
            WHERE attempt.attempt_id = ?
            """,
            (str(attempt_id),),
        ).fetchone()
        return bool(
            row is not None
            and str(row["attempt_status"]) == "FAILED"
            and str(row["node_status"]) == "FAILED"
            and str(row["graph_status"]) == "BLOCKED"
            and int(row["has_committed_operation"]) == 1
            and int(row["has_rejected_released_budget"]) == 1
        )

    def _resolve_internal_agent_budget_rejection_terminal_uow(
        self,
        conn: sqlite3.Connection,
        attempt_id: str,
    ) -> dict[str, Any]:
        return self._resolve_checkpoint_budget_rejection_terminal_uow(
            conn,
            attempt_id,
            authority_mode=_AUTHORITY_INTERNAL,
        )

    def _resolve_checkpoint_budget_rejection_terminal_uow(
        self,
        conn: sqlite3.Connection,
        attempt_id: str,
        *,
        authority_mode: str,
    ) -> dict[str, Any]:
        authority = self._validated_budget_rejection_authority_mode(
            authority_mode
        )
        attempt_ref = str(attempt_id or "").strip()
        attempt = self._require_attempt(conn, attempt_ref)
        graph_id = str(attempt["graph_id"])
        revision = int(attempt["graph_revision"])
        node = self._require_node(
            conn,
            graph_id,
            revision,
            str(attempt["node_id"]),
        )
        graph = self._require_graph(conn, graph_id, revision)
        budgets = conn.execute(
            """
            SELECT * FROM task_runtime_budget_reservation
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY budget_id
            """,
            (graph_id, revision),
        ).fetchall()
        operations = conn.execute(
            """
            SELECT * FROM task_runtime_idempotency_reservation
            WHERE attempt_id = ? ORDER BY reservation_id
            """,
            (attempt_ref,),
        ).fetchall()
        leases = conn.execute(
            """
            SELECT * FROM task_runtime_lease
            WHERE attempt_id = ? ORDER BY fencing_token, lease_id
            """,
            (attempt_ref,),
        ).fetchall()
        if (
            str(attempt["status"]) != "FAILED"
            or str(node["status"]) != "FAILED"
            or str(graph["status"]) != "BLOCKED"
            or len(budgets) != 1
            or budgets[0]["node_id"] is not None
            or budgets[0]["attempt_id"] is not None
            or str(budgets[0]["status"]) != "RELEASED"
            or len(operations) != 1
            or str(operations[0]["status"]) != "COMMITTED"
            or not leases
            or sum(str(item["status"]) == "RELEASED" for item in leases) != 1
            or str(leases[-1]["status"]) != "RELEASED"
            or any(str(item["status"]) == "ACTIVE" for item in leases)
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "checkpoint budget terminal state matrix drifted",
            )
        budget = self._budget_record(budgets[0])
        terminal_version = int(budget["status_version"]) - 1
        if terminal_version < 1:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "checkpoint budget terminal version is invalid",
            )
        winner, _dimensions = _assert_terminal_budget_authority(
            conn,
            budget_row=budgets[0],
            terminal_version=terminal_version,
        )
        final_lease = leases[-1]
        self._validate_internal_agent_rejected_usage_uow(
            conn,
            reservation_id=str(operations[0]["reservation_id"]),
            budget_id=str(budget["budget_id"]),
            attempt_id=attempt_ref,
            expected_budget_version=terminal_version,
            expected_budget_status="RELEASED",
            expected_usage_id=str(winner["usage_id"]),
            lease_id=str(final_lease["lease_id"]),
            expected_lease_status="RELEASED",
            expected_lease_version=int(final_lease["status_version"]),
            owner_id=str(final_lease["owner_id"]),
            fencing_token=int(final_lease["fencing_token"]),
            authority_mode=authority,
        )
        self._validate_checkpoint_budget_terminal_outboxes_uow(
            conn,
            attempt=attempt,
            node=node,
            graph=graph,
            lease=final_lease,
            budget=budgets[0],
            usage_id=str(winner["usage_id"]),
            authority_mode=authority,
        )
        return {
            "authority_kind": (
                "INTERNAL_AGENT_BUDGET_REJECTED"
                if authority == _AUTHORITY_INTERNAL
                else "CANONICAL_CHECKPOINT_BUDGET_REJECTED"
            ),
            "attempt": dict(attempt),
            "node": _node_record(node),
            "graph": _graph_record(graph, [node]),
            "lease": dict(final_lease),
            "historical_lease_id": str(operations[0]["lease_id"]),
            "budget": budget,
            "reservation": self._operation_record(operations[0]),
            "usage": dict(winner),
        }

    def _validate_checkpoint_budget_terminal_outboxes_uow(
        self,
        conn: sqlite3.Connection,
        *,
        attempt: sqlite3.Row,
        node: sqlite3.Row,
        graph: sqlite3.Row,
        lease: sqlite3.Row,
        budget: sqlite3.Row,
        usage_id: str,
        authority_mode: str,
    ) -> None:
        attempt_ref = str(attempt["attempt_id"])
        rows = conn.execute(
            """
            SELECT * FROM task_runtime_outbox
            WHERE attempt_id = ? AND event_type IN (
                'TASK_ATTEMPT_FAILED', 'TASK_NODE_FAILED',
                'TASK_GRAPH_BLOCKED', 'TASK_LEASE_RELEASED_TERMINAL',
                'BUDGET_RESERVATION_RELEASED'
            )
            ORDER BY event_type, outbox_id
            """,
            (attempt_ref,),
        ).fetchall()
        if len(rows) != 5:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "checkpoint budget terminal requires five exact Outboxes",
            )
        records = {
            _outbox_record(row)["event_type"]: _outbox_record(row)
            for row in rows
        }
        budget_record = records.get("BUDGET_RESERVATION_RELEASED")
        if budget_record is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "checkpoint budget terminal lacks its release Outbox",
            )
        expected_budget_outbox = self._internal_rejected_budget_outbox(
            budget_id=str(budget["budget_id"]),
            aggregate_version=int(budget["status_version"]),
            attempt_id=attempt_ref,
            evidence_refs=tuple(budget_record.get("evidence_refs") or ()),
            authority_mode=authority_mode,
        )
        self._assert_mutation_outbox_replay(
            conn,
            expected_budget_outbox,
            expected_aggregate=(
                "BUDGET_RESERVATION",
                str(budget["budget_id"]),
                int(budget["status_version"]),
            ),
            expected_event_type="BUDGET_RESERVATION_RELEASED",
            expected_attempt_id=attempt_ref,
        )
        graph_ref = str(graph["graph_id"])
        revision = int(graph["revision"])
        expected = {
            "TASK_ATTEMPT_FAILED": (
                "TASK_ATTEMPT",
                attempt_ref,
                int(attempt["status_version"]),
            ),
            "TASK_NODE_FAILED": (
                "TASK_NODE",
                f"{graph_ref}@{revision}/{node['node_id']}",
                int(node["status_version"]),
            ),
            "TASK_GRAPH_BLOCKED": (
                "TASK_GRAPH",
                graph_ref,
                int(graph["status_version"]),
            ),
            "TASK_LEASE_RELEASED_TERMINAL": (
                "TASK_LEASE",
                str(lease["lease_id"]),
                int(lease["status_version"]),
            ),
            "BUDGET_RESERVATION_RELEASED": (
                "BUDGET_RESERVATION",
                str(budget["budget_id"]),
                int(budget["status_version"]),
            ),
        }
        completed = int(attempt["completed_ts_ms"] or -1)
        error_ref = str(attempt["error_ref"] or "")
        if completed < 0 or not error_ref:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "checkpoint budget terminal Attempt evidence is incomplete",
            )
        for event_type, identity in expected.items():
            record = records.get(event_type)
            if (
                record is None
                or (
                    str(record["aggregate_type"]),
                    str(record["aggregate_id"]),
                    int(record["aggregate_version"]),
                )
                != identity
                or str(record.get("attempt_id") or "") != attempt_ref
                or int(record.get("created_ts_ms") or -1) != completed
                or error_ref not in tuple(record.get("evidence_refs") or ())
                or usage_id not in tuple(record.get("evidence_refs") or ())
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "checkpoint budget terminal Outbox authority drifted",
                )


__all__ = ["TaskCheckpointBudgetTerminalReadStoreMixin"]
