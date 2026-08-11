"""Atomic direct Graph closeout after a durable verification rejection."""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetReservationStatus,
    validate_budget_transition,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphStatus,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.verification_contract import (
    VerificationResult,
)
from host_brain.persistence.task_node_verification_common import (
    non_negative_version,
    required_text,
    validated_verification,
    verification_subject,
)
from host_brain.persistence.task_record_codec import _graph_record, _node_record
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TaskStoreError,
)


class TaskNodeVerificationTerminalStoreMixin:
    """Own the direct Node/Graph/Budget verification-failure transaction."""

    def finalize_node_verification_failure(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        attempt_id: str,
        reservation_id: str,
        budget_id: str,
        expected_node_version: int,
        expected_graph_version: int,
        expected_budget_version: int,
        verification_result: VerificationResult | Mapping[str, Any],
        node_outbox: OutboxMessage,
        graph_outbox: OutboxMessage,
        budget_outbox: OutboxMessage,
    ) -> dict[str, Any]:
        """Close direct verification rejection in one exact-replay UoW."""

        self.assert_node_verification_ready()
        graph_ref, revision, node_ref = verification_subject(
            graph_id,
            graph_revision,
            node_id,
        )
        attempt_ref = required_text(attempt_id, field_name="attempt_id")
        reservation_ref = required_text(
            reservation_id,
            field_name="reservation_id",
        )
        budget_ref = required_text(budget_id, field_name="budget_id")
        node_version = non_negative_version(expected_node_version)
        graph_version = non_negative_version(expected_graph_version)
        budget_version = non_negative_version(expected_budget_version)
        for message, field_name in (
            (node_outbox, "node_outbox"),
            (graph_outbox, "graph_outbox"),
            (budget_outbox, "budget_outbox"),
        ):
            if not isinstance(message, OutboxMessage):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"{field_name} must be an OutboxMessage",
                )
            message.validate()

        ts_ms = self._now()
        with self._transaction() as conn:
            self._assert_graph_not_cancelling(
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
            )
            graph = self._require_graph(conn, graph_ref, revision)
            verification = validated_verification(
                verification_result,
                target=TaskNodeStatus.VERIFICATION_FAILED,
                graph_id=graph_ref,
                node_id=node_ref,
                root_work_order_id=str(graph["root_work_order_id"]),
                attempt_id=attempt_ref,
                outbox=node_outbox,
            )
            existing = conn.execute(
                """
                SELECT * FROM task_runtime_node_verification_decision
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                  AND node_version_after = ?
                """,
                (graph_ref, revision, node_ref, node_version + 1),
            ).fetchone()
            if existing is not None:
                return self._replay_verification_failure_bundle(
                    conn,
                    graph_id=graph_ref,
                    graph_revision=revision,
                    node_id=node_ref,
                    attempt_id=attempt_ref,
                    reservation_id=reservation_ref,
                    budget_id=budget_ref,
                    expected_node_version=node_version,
                    expected_graph_version=graph_version,
                    expected_budget_version=budget_version,
                    verification=verification,
                    node_outbox=node_outbox,
                    graph_outbox=graph_outbox,
                    budget_outbox=budget_outbox,
                )

            self._assert_verification_execution_authority(
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
                node_id=node_ref,
                attempt_id=attempt_ref,
                reservation_id=reservation_ref,
                verification=verification,
                require_terminal_attempt=True,
            )
            node = self._require_node(conn, graph_ref, revision, node_ref)
            budget = self._require_budget(conn, budget_ref)
            self._require_version(
                node,
                node_version,
                subject=f"TaskNode {node_ref}",
            )
            self._require_version(
                graph,
                graph_version,
                subject=f"TaskGraph {graph_ref}@{revision}",
            )
            self._require_version(
                budget,
                budget_version,
                subject=f"BudgetReservation {budget_ref}",
            )
            if (
                str(graph["status"]) != TaskGraphStatus.ACTIVE.value
                or str(node["status"]) != TaskNodeStatus.VERIFYING.value
                or str(budget["status"]) != BudgetReservationStatus.ACTIVE.value
                or str(budget["graph_id"] or "") != graph_ref
                or int(budget["graph_revision"] or 0) != revision
                or budget["node_id"] is not None
                or budget["attempt_id"] is not None
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "verification failure bundle requires ACTIVE Graph/Budget, "
                    "VERIFYING Node, and graph-scoped Budget identity",
                )
            node_record = self._transition_node_in_transaction(
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
                node_id=node_ref,
                expected_version=node_version,
                target_status=TaskNodeStatus.VERIFICATION_FAILED,
                outbox=node_outbox,
                verification_result=verification,
                acceptance_judgement=None,
                ts_ms=ts_ms,
                outbox_attempt_id=attempt_ref,
            )
            decision = self._insert_node_verification_decision(
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
                node_id=node_ref,
                attempt_id=attempt_ref,
                node_version_before=node_version,
                target=TaskNodeStatus.VERIFICATION_FAILED,
                verification=verification,
                node_outbox=node_outbox,
                ts_ms=ts_ms,
            )
            self._assert_graph_execution_authority_quiescent(
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
                action="verification failure terminalization",
            )
            validate_budget_transition(
                str(budget["status"]),
                BudgetReservationStatus.RELEASED,
            )
            budget_cursor = conn.execute(
                """
                UPDATE task_runtime_budget_reservation
                SET status = 'RELEASED', status_version = status_version + 1,
                    updated_ts_ms = ?
                WHERE budget_id = ? AND status = 'ACTIVE'
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
                budget_outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "BUDGET_RESERVATION",
                    budget_ref,
                    budget_version + 1,
                ),
                expected_event_type="BUDGET_RESERVATION_RELEASED",
                expected_attempt_id=None,
            )
            graph_record = self._transition_graph_in_transaction(
                conn,
                graph_id=graph_ref,
                revision=revision,
                expected_version=graph_version,
                target_status=TaskGraphStatus.FAILED,
                outbox=graph_outbox,
                ts_ms=ts_ms,
            )
            return {
                "node": node_record,
                "decision": decision,
                "graph": graph_record,
                "budget": self._budget_record(
                    self._require_budget(conn, budget_ref)
                ),
                "replayed": False,
            }

    def _replay_verification_failure_bundle(
        self,
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        reservation_id: str,
        budget_id: str,
        expected_node_version: int,
        expected_graph_version: int,
        expected_budget_version: int,
        verification: VerificationResult,
        node_outbox: OutboxMessage,
        graph_outbox: OutboxMessage,
        budget_outbox: OutboxMessage,
    ) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT * FROM task_runtime_node_verification_decision
            WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
              AND node_version_after = ?
            """,
            (
                graph_id,
                graph_revision,
                node_id,
                expected_node_version + 1,
            ),
        ).fetchone()
        if row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "verification failure replay lost its decision",
            )
        decision = self._node_verification_record(conn, row)
        self._assert_verification_replay(
            decision,
            verification=verification,
            target=TaskNodeStatus.VERIFICATION_FAILED,
            attempt_id=attempt_id,
            reservation_id=reservation_id,
            node_outbox=node_outbox,
            conn=conn,
        )
        graph = self._require_graph(conn, graph_id, graph_revision)
        node = self._require_node(conn, graph_id, graph_revision, node_id)
        budget = self._require_budget(conn, budget_id)
        if (
            str(node["status"]) != TaskNodeStatus.VERIFICATION_FAILED.value
            or int(node["status_version"]) != expected_node_version + 1
            or str(graph["status"]) != TaskGraphStatus.FAILED.value
            or int(graph["status_version"]) != expected_graph_version + 1
            or str(budget["status"]) != BudgetReservationStatus.RELEASED.value
            or int(budget["status_version"]) != expected_budget_version + 1
        ):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "verification failure replay terminal state differs",
            )
        self._assert_mutation_outbox_replay(
            conn,
            graph_outbox,
            expected_aggregate=(
                "TASK_GRAPH",
                graph_id,
                expected_graph_version + 1,
            ),
            expected_event_type="TASK_GRAPH_FAILED",
            expected_attempt_id=None,
            expected_graph_revision=graph_revision,
        )
        self._assert_mutation_outbox_replay(
            conn,
            budget_outbox,
            expected_aggregate=(
                "BUDGET_RESERVATION",
                budget_id,
                expected_budget_version + 1,
            ),
            expected_event_type="BUDGET_RESERVATION_RELEASED",
            expected_attempt_id=None,
        )
        return {
            "node": _node_record(node),
            "decision": decision,
            "graph": _graph_record(
                graph,
                conn.execute(
                    """
                    SELECT * FROM task_runtime_node
                    WHERE graph_id = ? AND graph_revision = ?
                    ORDER BY node_id
                    """,
                    (graph_id, graph_revision),
                ).fetchall(),
            ),
            "budget": self._budget_record(budget),
            "replayed": True,
        }


__all__ = ["TaskNodeVerificationTerminalStoreMixin"]
