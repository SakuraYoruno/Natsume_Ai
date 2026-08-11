"""Atomic Review-denial terminal settlement for the canonical TaskStore."""

from __future__ import annotations

import sqlite3
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetReservationStatus,
    validate_budget_transition,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphStatus,
    TaskNodeStatus,
    validate_task_node_transition,
)
from host_brain.persistence.task_record_codec import (
    _dumps,
    _graph_record,
    _node_record,
    _validated_node_payload,
)
from host_brain.persistence.task_review_denial_common import (
    REVIEW_DENIAL_REQUIRED_COLUMNS,
    REVIEW_DENIAL_REQUIRED_TRIGGERS,
    REVIEW_DENIAL_SETTLEMENT_SCHEMA_VERSION,
    build_review_denial_settlement_ref,
)
from host_brain.persistence.task_review_denial_validation import (
    assert_post_review_acceptance,
    blocking_decision_ref,
    review_denial_authority,
    targets_for_phase,
    validate_review_denial_outboxes,
    validated_review_denial_record,
)
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_NOT_FOUND,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
)


class TaskReviewDenialStoreMixin:
    """Own the sole Node/Graph/Budget terminal write for denied Reviews."""

    def _cancel_unstarted_review_node_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        expected_version: int,
        outbox: OutboxMessage,
        ts_ms: int,
    ) -> dict[str, Any]:
        """Cancel a pre-execution Review node after proving no worker authority exists."""

        node = self._require_node(conn, graph_id, graph_revision, node_id)
        self._require_version(
            node,
            expected_version,
            subject=f"TaskNode {node_id}",
        )
        target = validate_task_node_transition(
            str(node["status"]),
            TaskNodeStatus.CANCELLED,
        )
        worker_authority = conn.execute(
            """
            SELECT 1
            FROM task_runtime_attempt
            WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
            LIMIT 1
            """,
            (str(graph_id), int(graph_revision), str(node_id)),
        ).fetchone()
        if worker_authority is not None:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "pre-execution Review denial cannot cancel a node with an Attempt",
            )
        payload = _validated_node_payload(node)
        payload["status"] = target.value
        payload["status_version"] = int(expected_version) + 1
        cursor = conn.execute(
            """
            UPDATE task_runtime_node
            SET status = 'CANCELLED', status_version = status_version + 1,
                node_json = ?, cancellation_requested_ts_ms = ?, updated_ts_ms = ?
            WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
              AND status_version = ?
            """,
            (
                _dumps(payload),
                int(ts_ms),
                int(ts_ms),
                str(graph_id),
                int(graph_revision),
                str(node_id),
                int(expected_version),
            ),
        )
        self._require_cas(cursor, subject=f"TaskNode {node_id}")
        self._insert_outbox(
            conn,
            outbox,
            ts_ms=int(ts_ms),
            expected_aggregate=(
                "TASK_NODE",
                f"{graph_id}@{int(graph_revision)}/{node_id}",
                int(expected_version) + 1,
            ),
            expected_event_type="TASK_NODE_CANCELLED",
            expected_attempt_id=None,
        )
        return _node_record(
            self._require_node(conn, graph_id, graph_revision, node_id)
        )

    def assert_review_denial_settlement_ready(self) -> None:
        conn = self._connect()
        try:
            columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(task_runtime_review_denial_settlement)"
                ).fetchall()
            }
            triggers = {
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'trigger'
                      AND (
                        name LIKE 'trg_task_runtime_review_denial_%'
                        OR name LIKE 'trg_agent_runtime_settled_review_%'
                      )
                    """
                ).fetchall()
            }
        finally:
            conn.close()
        missing_columns = sorted(REVIEW_DENIAL_REQUIRED_COLUMNS - columns)
        missing_triggers = sorted(REVIEW_DENIAL_REQUIRED_TRIGGERS - triggers)
        if missing_columns or missing_triggers:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task review denial migration 0135 is not applied: "
                f"columns={missing_columns},triggers={missing_triggers}",
            )

    def get_review_denial_settlement(
        self,
        settlement_ref: str,
    ) -> dict[str, Any]:
        self.assert_review_denial_settlement_ready()
        reference = str(settlement_ref or "").strip()
        if not reference:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "review denial settlement_ref is required",
            )
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            row = conn.execute(
                """
                SELECT * FROM task_runtime_review_denial_settlement
                WHERE settlement_ref = ?
                """,
                (reference,),
            ).fetchone()
            if row is None:
                raise TaskStoreError(
                    TASK_STORE_NOT_FOUND,
                    f"review denial settlement not found: {reference}",
                )
            return validated_review_denial_record(self, conn, row)
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    def get_review_denial_settlement_for_node(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
    ) -> dict[str, Any]:
        self.assert_review_denial_settlement_ready()
        graph_ref = _required_text(graph_id, "graph_id")
        node_ref = _required_text(node_id, "node_id")
        revision = _positive_int(graph_revision, "graph_revision")
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            row = conn.execute(
                """
                SELECT * FROM task_runtime_review_denial_settlement
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                ORDER BY node_version_after DESC, settlement_ref DESC
                LIMIT 1
                """,
                (graph_ref, revision, node_ref),
            ).fetchone()
            if row is None:
                raise TaskStoreError(
                    TASK_STORE_NOT_FOUND,
                    "review denial settlement not found for "
                    f"{graph_ref}@{revision}/{node_ref}",
                )
            return validated_review_denial_record(self, conn, row)
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    def settle_review_denial(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        settlement_ref: str,
        review_id: str,
        expected_node_version: int,
        expected_graph_version: int,
        budget_id: str,
        expected_budget_version: int,
        node_outbox: OutboxMessage,
        graph_outbox: OutboxMessage,
        budget_outbox: OutboxMessage,
        acceptance_ref: str | None = None,
        attempt_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically close a terminal Review checkpoint and its shared budget."""

        self.assert_review_denial_settlement_ready()
        graph_ref = _required_text(graph_id, "graph_id")
        node_ref = _required_text(node_id, "node_id")
        review_ref = _required_text(review_id, "review_id")
        budget_ref = _required_text(budget_id, "budget_id")
        revision = _positive_int(graph_revision, "graph_revision")
        node_version = _non_negative_int(
            expected_node_version,
            "expected_node_version",
        )
        graph_version = _non_negative_int(
            expected_graph_version,
            "expected_graph_version",
        )
        budget_version = _non_negative_int(
            expected_budget_version,
            "expected_budget_version",
        )
        requested_settlement_ref = _required_text(
            settlement_ref,
            "settlement_ref",
        )
        acceptance_reference = _optional_text(acceptance_ref)
        attempt_ref = _optional_text(attempt_id)
        for outbox in (node_outbox, graph_outbox, budget_outbox):
            if not isinstance(outbox, OutboxMessage):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "review denial settlement requires three OutboxMessage values",
                )
            outbox.validate()

        with self._transaction() as conn:
            existing = conn.execute(
                """
                SELECT * FROM task_runtime_review_denial_settlement
                WHERE review_id = ? OR settlement_ref = ?
                """,
                (review_ref, requested_settlement_ref),
            ).fetchall()
            if existing:
                if len(existing) != 1:
                    raise TaskStoreError(
                        TASK_STORE_DURABLE_DATA_INVALID,
                        "review denial settlement identity is ambiguous",
                    )
                return self._replay_review_denial_settlement(
                    conn,
                    row=existing[0],
                    graph_id=graph_ref,
                    graph_revision=revision,
                    node_id=node_ref,
                    settlement_ref=requested_settlement_ref,
                    review_id=review_ref,
                    expected_node_version=node_version,
                    expected_graph_version=graph_version,
                    budget_id=budget_ref,
                    expected_budget_version=budget_version,
                    node_outbox=node_outbox,
                    graph_outbox=graph_outbox,
                    budget_outbox=budget_outbox,
                    acceptance_ref=acceptance_reference,
                    attempt_id=attempt_ref,
                )

            self._assert_graph_not_cancelling(
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
            )
            self._assert_no_terminal_reconciliation_fence(
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
                action="Review denial terminal settlement",
            )
            graph = self._require_graph(conn, graph_ref, revision)
            node = self._require_node(conn, graph_ref, revision, node_ref)
            budget = self._require_budget(conn, budget_ref)
            self._require_version(
                graph,
                graph_version,
                subject=f"TaskGraph {graph_ref}@{revision}",
            )
            self._require_version(
                node,
                node_version,
                subject=f"TaskNode {node_ref}",
            )
            self._require_version(
                budget,
                budget_version,
                subject=f"BudgetReservation {budget_ref}",
            )
            graph_status = str(graph["status"])
            node_status = str(node["status"])
            budget_status = str(budget["status"])
            if graph_status not in {"ACTIVE", "BLOCKED"}:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "review denial requires an ACTIVE or BLOCKED TaskGraph",
                )
            if node_status not in {
                TaskNodeStatus.WAITING_APPROVAL.value,
                TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW.value,
            }:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "review denial requires an exact waiting TaskNode",
                )
            if (
                str(budget["graph_id"] or "") != graph_ref
                or int(budget["graph_revision"] or 0) != revision
                or budget["node_id"] is not None
                or budget["attempt_id"] is not None
                or budget_status
                not in {
                    BudgetReservationStatus.ACTIVE.value,
                    BudgetReservationStatus.EXCEEDED.value,
                }
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "review denial requires the active graph-scoped BudgetReservation",
                )
            self._assert_graph_execution_authority_quiescent(
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
                action="Review denial terminal settlement",
            )

            review_record, review_sha256 = review_denial_authority(
                conn,
                review_id=review_ref,
                graph_id=graph_ref,
                graph_revision=revision,
                node_id=node_ref,
                node_version=node_version,
            )
            review = dict(review_record["review"])
            phase = str(review["resume_phase"])
            review_status = str(review["status"])
            review_status_version = int(review_record["status_version"])
            node_target, graph_target = targets_for_phase(phase)
            if phase == "PRE_EXECUTION":
                if (
                    node_status != TaskNodeStatus.WAITING_APPROVAL.value
                    or acceptance_reference is not None
                    or attempt_ref is not None
                ):
                    raise TaskStoreError(
                        TASK_STORE_INVALID,
                        "pre-execution Review denial lineage is inconsistent",
                    )
            else:
                if (
                    node_status
                    != TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW.value
                    or acceptance_reference is None
                    or attempt_ref is None
                ):
                    raise TaskStoreError(
                        TASK_STORE_INVALID,
                        "post-execution Review denial lineage is inconsistent",
                    )
                assert_post_review_acceptance(
                    self,
                    conn,
                    acceptance_ref=acceptance_reference,
                    review_id=review_ref,
                    graph_id=graph_ref,
                    graph_revision=revision,
                    node_id=node_ref,
                    node_version=node_version,
                    attempt_id=attempt_ref,
                )

            expected_ref = build_review_denial_settlement_ref(
                review_id=review_ref,
                review_status=review_status,
                review_status_version=review_status_version,
                resume_phase=phase,
                graph_id=graph_ref,
                graph_revision=revision,
                node_id=node_ref,
                node_version_before=node_version,
                node_target_status=node_target.value,
                graph_status_before=graph_status,
                graph_version_before=graph_version,
                graph_target_status=graph_target.value,
                budget_id=budget_ref,
                budget_version_before=budget_version,
                acceptance_ref=acceptance_reference,
                attempt_id=attempt_ref,
            )
            if expected_ref != requested_settlement_ref:
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    "review denial settlement identity drifted",
                )
            validate_review_denial_outboxes(
                settlement_ref=expected_ref,
                review_id=review_ref,
                graph_id=graph_ref,
                graph_revision=revision,
                node_id=node_ref,
                node_version=node_version,
                node_target=node_target,
                graph_version=graph_version,
                graph_target=graph_target,
                budget_id=budget_ref,
                budget_version=budget_version,
                attempt_id=attempt_ref,
                node_outbox=node_outbox,
                graph_outbox=graph_outbox,
                budget_outbox=budget_outbox,
            )
            prior_blocking_decision_ref = blocking_decision_ref(
                self,
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
                graph_status=graph_status,
                graph_version=graph_version,
            )
            ts_ms = self._now()
            if node_target is TaskNodeStatus.CANCELLED:
                node_record = self._cancel_unstarted_review_node_in_transaction(
                    conn,
                    graph_id=graph_ref,
                    graph_revision=revision,
                    node_id=node_ref,
                    expected_version=node_version,
                    outbox=node_outbox,
                    ts_ms=ts_ms,
                )
            else:
                node_record = self._transition_node_in_transaction(
                    conn,
                    graph_id=graph_ref,
                    graph_revision=revision,
                    node_id=node_ref,
                    expected_version=node_version,
                    target_status=node_target,
                    outbox=node_outbox,
                    verification_result=None,
                    acceptance_judgement=None,
                    ts_ms=ts_ms,
                    outbox_attempt_id=attempt_ref,
                )
            validate_budget_transition(budget_status, BudgetReservationStatus.RELEASED)
            budget_cursor = conn.execute(
                """
                UPDATE task_runtime_budget_reservation
                SET status = 'RELEASED', status_version = status_version + 1,
                    updated_ts_ms = ?
                WHERE budget_id = ? AND status_version = ?
                  AND status IN ('ACTIVE', 'EXCEEDED')
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
                target_status=graph_target,
                outbox=graph_outbox,
                ts_ms=ts_ms,
                attempt_id=attempt_ref,
            )
            try:
                conn.execute(
                    """
                    INSERT INTO task_runtime_review_denial_settlement(
                        settlement_ref, schema_version, review_id, review_status,
                        review_status_version, review_sha256, resume_phase,
                        graph_id, graph_revision, node_id, node_version_before,
                        node_version_after, node_target_status,
                        graph_status_before, graph_version_before,
                        graph_version_after, graph_target_status, budget_id,
                        budget_version_before, budget_version_after,
                        acceptance_ref, attempt_id, blocking_decision_ref,
                        node_outbox_id, graph_outbox_id, budget_outbox_id,
                        created_ts_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        expected_ref,
                        REVIEW_DENIAL_SETTLEMENT_SCHEMA_VERSION,
                        review_ref,
                        review_status,
                        review_status_version,
                        review_sha256,
                        phase,
                        graph_ref,
                        revision,
                        node_ref,
                        node_version,
                        node_version + 1,
                        node_target.value,
                        graph_status,
                        graph_version,
                        graph_version + 1,
                        graph_target.value,
                        budget_ref,
                        budget_version,
                        budget_version + 1,
                        acceptance_reference,
                        attempt_ref,
                        prior_blocking_decision_ref,
                        node_outbox.outbox_id,
                        graph_outbox.outbox_id,
                        budget_outbox.outbox_id,
                        ts_ms,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    "review denial settlement identity already exists",
                ) from exc
            row = conn.execute(
                """
                SELECT * FROM task_runtime_review_denial_settlement
                WHERE settlement_ref = ?
                """,
                (expected_ref,),
            ).fetchone()
            if row is None:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "review denial settlement insert was not durable",
                )
            return {
                "node": node_record,
                "graph": graph_record,
                "budget": self._budget_record(
                    self._require_budget(conn, budget_ref)
                ),
                "settlement": validated_review_denial_record(self, conn, row),
                "replayed": False,
            }

    def _replay_review_denial_settlement(
        self,
        conn: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        settlement_ref: str,
        review_id: str,
        expected_node_version: int,
        expected_graph_version: int,
        budget_id: str,
        expected_budget_version: int,
        node_outbox: OutboxMessage,
        graph_outbox: OutboxMessage,
        budget_outbox: OutboxMessage,
        acceptance_ref: str | None,
        attempt_id: str | None,
    ) -> dict[str, Any]:
        expected = (
            settlement_ref,
            review_id,
            graph_id,
            graph_revision,
            node_id,
            expected_node_version,
            expected_graph_version,
            budget_id,
            expected_budget_version,
            acceptance_ref,
            attempt_id,
            node_outbox.outbox_id,
            graph_outbox.outbox_id,
            budget_outbox.outbox_id,
        )
        actual = (
            str(row["settlement_ref"]),
            str(row["review_id"]),
            str(row["graph_id"]),
            int(row["graph_revision"]),
            str(row["node_id"]),
            int(row["node_version_before"]),
            int(row["graph_version_before"]),
            str(row["budget_id"]),
            int(row["budget_version_before"]),
            row["acceptance_ref"],
            row["attempt_id"],
            str(row["node_outbox_id"]),
            str(row["graph_outbox_id"]),
            str(row["budget_outbox_id"]),
        )
        if actual != expected:
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "review denial settlement replay identity drifted",
            )
        record = validated_review_denial_record(self, conn, row)
        node_target = TaskNodeStatus(str(row["node_target_status"]))
        graph_target = TaskGraphStatus(str(row["graph_target_status"]))
        validate_review_denial_outboxes(
            settlement_ref=settlement_ref,
            review_id=review_id,
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            node_version=expected_node_version,
            node_target=node_target,
            graph_version=expected_graph_version,
            graph_target=graph_target,
            budget_id=budget_id,
            budget_version=expected_budget_version,
            attempt_id=attempt_id,
            node_outbox=node_outbox,
            graph_outbox=graph_outbox,
            budget_outbox=budget_outbox,
        )
        for outbox, aggregate, event, outbox_attempt in (
            (
                node_outbox,
                (
                    "TASK_NODE",
                    f"{graph_id}@{graph_revision}/{node_id}",
                    expected_node_version + 1,
                ),
                f"TASK_NODE_{node_target.value}",
                attempt_id,
            ),
            (
                graph_outbox,
                ("TASK_GRAPH", graph_id, expected_graph_version + 1),
                f"TASK_GRAPH_{graph_target.value}",
                attempt_id,
            ),
            (
                budget_outbox,
                (
                    "BUDGET_RESERVATION",
                    budget_id,
                    expected_budget_version + 1,
                ),
                "BUDGET_RESERVATION_RELEASED",
                None,
            ),
        ):
            self._assert_mutation_outbox_replay(
                conn,
                outbox,
                expected_aggregate=aggregate,
                expected_event_type=event,
                expected_attempt_id=outbox_attempt,
                expected_graph_revision=(
                    graph_revision if outbox.aggregate_type == "TASK_GRAPH" else None
                ),
            )
        graph_rows = conn.execute(
            """
            SELECT * FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id
            """,
            (graph_id, graph_revision),
        ).fetchall()
        return {
            "node": _node_record(
                self._require_node(conn, graph_id, graph_revision, node_id)
            ),
            "graph": _graph_record(
                self._require_graph(conn, graph_id, graph_revision),
                graph_rows,
            ),
            "budget": self._budget_record(
                self._require_budget(conn, budget_id)
            ),
            "settlement": record,
            "replayed": True,
        }

def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TaskStoreError(TASK_STORE_INVALID, f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    return str(value or "").strip() or None


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"{field_name} must be a non-negative integer",
        )
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"{field_name} must be a non-negative integer",
        ) from exc
    if normalized < 0:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"{field_name} must be a non-negative integer",
        )
    return normalized


def _positive_int(value: Any, field_name: str) -> int:
    normalized = _non_negative_int(value, field_name)
    if normalized < 1:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"{field_name} must be positive",
        )
    return normalized


__all__ = ["TaskReviewDenialStoreMixin"]
