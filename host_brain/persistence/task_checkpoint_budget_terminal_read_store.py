"""Read-only authority for exact checkpoint budget-rejection terminals."""

from __future__ import annotations

import sqlite3
from typing import Any

from .task_budget_usage_store import _assert_terminal_budget_authority
from .task_record_codec import (
    _graph_record,
    _node_record,
    _outbox_record,
    _validated_budget_record,
)
from .task_terminal_reconciliation_decision_authority import (
    canonical_current_parallel_terminal_decision,
)
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
        parallel = (
            self._try_resolve_parallel_checkpoint_budget_rejection_terminal_uow(
                conn,
                attempt_id,
            )
        )
        if parallel is not None:
            if rows or known:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "checkpoint budget terminal has competing direct and parallel authority",
                )
            return parallel
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

    def _try_resolve_parallel_checkpoint_budget_rejection_terminal_uow(
        self,
        conn: sqlite3.Connection,
        attempt_id: str,
    ) -> dict[str, Any] | None:
        """Reopen one 0090-owned multi-node checkpoint budget terminal.

        A multi-node child closes only its Attempt/Node/Lease after the
        committed worker usage is rejected.  The outer 0090 decision is the
        sole writer for the shared Graph and Budget terminal.  This reader
        therefore requires both authorities in one SQLite snapshot instead of
        pretending that the single-node five-Outbox bundle exists.
        """

        decision_table = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'task_runtime_parallel_settlement_decision'
            """
        ).fetchone()
        if decision_table is None:
            return None
        attempt_identity = conn.execute(
            """
            SELECT graph_id, graph_revision
            FROM task_runtime_attempt
            WHERE attempt_id = ?
            """,
            (str(attempt_id),),
        ).fetchone()
        if attempt_identity is None:
            return None
        graph_id = str(attempt_identity["graph_id"])
        revision = int(attempt_identity["graph_revision"])
        candidate_rows = conn.execute(
            """
            SELECT decision_ref
            FROM task_runtime_parallel_settlement_decision
            WHERE graph_id = ? AND graph_revision = ?
              AND decision_kind = 'BUDGET'
            ORDER BY graph_version_after, decision_ref
            """,
            (graph_id, revision),
        ).fetchall()
        if not candidate_rows:
            return None
        attempt = self._require_attempt(conn, str(attempt_id))
        graph = self._require_graph(conn, graph_id, revision)
        decision = canonical_current_parallel_terminal_decision(
            conn,
            {
                "graph_id": graph_id,
                "revision": revision,
                "status": str(graph["status"]),
                "status_version": int(graph["status_version"]),
            },
        )
        if not isinstance(decision, dict):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "parallel checkpoint budget terminal lacks one canonical current decision",
            )
        node = self._require_node(
            conn,
            graph_id,
            revision,
            str(attempt["node_id"]),
        )
        selected = [
            dict(outcome)
            for outcome in decision.get("outcomes") or ()
            if isinstance(outcome, dict)
            and str(outcome.get("node_id") or "") == str(node["node_id"])
            and str(outcome.get("attempt_id") or "") == str(attempt_id)
        ]
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
            (str(attempt_id),),
        ).fetchall()
        leases = conn.execute(
            """
            SELECT * FROM task_runtime_lease
            WHERE attempt_id = ? ORDER BY fencing_token, lease_id
            """,
            (str(attempt_id),),
        ).fetchall()
        if (
            decision.get("decision_kind") != "BUDGET"
            or decision.get("selected_node_id") != str(node["node_id"])
            or decision.get("graph_target_status") != "BLOCKED"
            or decision.get("budget_target_status") != "RELEASED"
            or len(selected) != 1
            or selected[0].get("status") != "BUDGET_BLOCKED"
            or str(attempt["status"]) != "FAILED"
            or str(node["status"]) != "FAILED"
            or str(graph["status"]) != "BLOCKED"
            or len(budgets) != 1
            or budgets[0]["node_id"] is not None
            or budgets[0]["attempt_id"] is not None
            or str(budgets[0]["budget_id"])
            != str(decision.get("budget_id") or "")
            or str(budgets[0]["status"]) != "RELEASED"
            or int(budgets[0]["status_version"])
            != int(decision.get("budget_status_version_before") or -1) + 1
            or len(operations) != 1
            or str(operations[0]["status"]) != "COMMITTED"
            or not str(operations[0]["receipt_ref"] or "").strip()
            or not leases
            or sum(str(item["status"]) == "RELEASED" for item in leases) != 1
            or str(leases[-1]["status"]) != "RELEASED"
            or any(str(item["status"]) == "ACTIVE" for item in leases)
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "parallel checkpoint budget terminal state matrix drifted",
            )
        terminal_version = int(
            decision["budget_status_version_before"]
        )
        winner, _dimensions = _assert_terminal_budget_authority(
            conn,
            budget_row=budgets[0],
            terminal_version=terminal_version,
        )
        if (
            str(winner["node_id"]) != str(node["node_id"])
            or str(winner["attempt_id"]) != str(attempt_id)
            or str(winner["usage_id"])
            not in tuple(selected[0].get("evidence_refs") or ())
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "parallel checkpoint budget winner differs from the selected write node",
            )
        self._assert_node_budget_usage_replay_in_transaction(
            conn,
            parent_budget_id=str(budgets[0]["budget_id"]),
            attempt_id=str(attempt_id),
            usage_id=str(winner["usage_id"]),
            accepted=False,
            parent_outbox_id=str(winner["outbox_id"]),
        )
        final_lease = leases[-1]
        self._validate_parallel_checkpoint_budget_terminal_outboxes_uow(
            conn,
            attempt=attempt,
            node=node,
            lease=final_lease,
            usage_id=str(winner["usage_id"]),
        )
        nodes = conn.execute(
            """
            SELECT * FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY node_id
            """,
            (graph_id, revision),
        ).fetchall()
        decision_row = conn.execute(
            """
            SELECT decision_ref
            FROM task_runtime_parallel_settlement_decision
            WHERE graph_id = ? AND graph_revision = ?
              AND graph_version_after = ?
            """,
            (graph_id, revision, int(graph["status_version"])),
        ).fetchone()
        if decision_row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "parallel checkpoint budget terminal decision identity disappeared",
            )
        return {
            "authority_kind": "PARALLEL_CHECKPOINT_BUDGET_REJECTED",
            "attempt": dict(attempt),
            "node": _node_record(node),
            "graph": _graph_record(graph, nodes),
            "lease": dict(final_lease),
            "historical_lease_id": str(operations[0]["lease_id"]),
            "budget": _validated_budget_record(budgets[0]),
            "reservation": self._operation_record(operations[0]),
            "usage": dict(winner),
            "parallel_settlement_ref": str(decision_row["decision_ref"]),
        }

    @staticmethod
    def _validate_parallel_checkpoint_budget_terminal_outboxes_uow(
        conn: sqlite3.Connection,
        *,
        attempt: sqlite3.Row,
        node: sqlite3.Row,
        lease: sqlite3.Row,
        usage_id: str,
    ) -> None:
        """Require exact child terminal Outboxes beside the validated 0090 pair."""

        attempt_ref = str(attempt["attempt_id"])
        graph_id = str(attempt["graph_id"])
        revision = int(attempt["graph_revision"])
        identities = (
            (
                "TASK_ATTEMPT_FAILED",
                "TASK_ATTEMPT",
                attempt_ref,
                int(attempt["status_version"]),
                attempt_ref,
                True,
            ),
            (
                "TASK_NODE_FAILED",
                "TASK_NODE",
                f"{graph_id}@{revision}/{node['node_id']}",
                int(node["status_version"]),
                None,
                True,
            ),
            (
                "TASK_LEASE_RELEASED_TERMINAL",
                "TASK_LEASE",
                str(lease["lease_id"]),
                int(lease["status_version"]),
                attempt_ref,
                False,
            ),
        )
        error_ref = str(attempt["error_ref"] or "")
        if not error_ref:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "parallel checkpoint budget terminal Attempt lacks error evidence",
            )
        for (
            event_type,
            aggregate_type,
            aggregate_id,
            aggregate_version,
            expected_attempt_id,
            require_evidence,
        ) in identities:
            rows = conn.execute(
                """
                SELECT * FROM task_runtime_outbox
                WHERE aggregate_type = ? AND aggregate_id = ?
                  AND aggregate_version = ? AND event_type = ?
                ORDER BY outbox_id
                """,
                (
                    aggregate_type,
                    aggregate_id,
                    aggregate_version,
                    event_type,
                ),
            ).fetchall()
            if len(rows) != 1:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "parallel checkpoint budget terminal child Outbox is ambiguous",
                )
            record = _outbox_record(rows[0])
            evidence = tuple(record.get("evidence_refs") or ())
            if (
                record.get("attempt_id") != expected_attempt_id
                or (
                    require_evidence
                    and (
                        error_ref not in evidence
                        or usage_id not in evidence
                    )
                )
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "parallel checkpoint budget terminal child Outbox drifted",
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
        budget = _validated_budget_record(budgets[0])
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
