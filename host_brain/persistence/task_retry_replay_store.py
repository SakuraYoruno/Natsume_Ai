"""Exact in-transaction replay for durable retry scheduling.

Replay receives the mutation owner's live connection and never creates a
connection, transaction, or clock boundary of its own.
"""

from __future__ import annotations

import sqlite3
from typing import Any, NoReturn

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphStatus,
    TaskNodeStatus,
)
from host_brain.persistence.task_record_codec import (
    _add_amounts,
    _budget_usage_record,
    _graph_record,
    _node_record,
)
from host_brain.persistence.task_store_types import (
    AttemptStatus,
    LeaseStatus,
    OutboxMessage,
    TASK_STORE_BUDGET_EXCEEDED,
    TASK_STORE_CAS_CONFLICT,
    TaskStoreError,
    build_outbox_payload_ref,
)


class TaskRetryReplayStoreMixin:
    """Replay retry mutations on the caller's transaction connection."""

    def _replay_scheduled_retry_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        attempt: sqlite3.Row,
        usage: sqlite3.Row,
        attempt_id: str,
        budget_ref: str,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        failure_ref: str,
        attempt_outbox: OutboxMessage,
        retry_delta_json: str,
        retry_delta_digest: str,
        retry_usage_digest: str,
        retry_usage_id: str,
        expected_graph_version: int | None,
        terminalize_graph_on_budget_rejection: bool,
    ) -> dict[str, Any]:
        """Return one already-committed retry decision only on exact replay.

        This boundary deliberately covers response loss immediately after the
        retry transaction.  A rejected budget that has subsequently advanced
        from EXCEEDED to RELEASED is no longer the original decision state and
        must be reconciled by the caller instead of being synthesized here.
        """

        def reject(detail: str) -> NoReturn:
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                f"retry schedule replay drift for Attempt {attempt_id}: {detail}",
            )

        def persisted_outbox_id(idempotency_key: str) -> str:
            row = conn.execute(
                "SELECT outbox_id FROM task_runtime_outbox WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is None or not str(row["outbox_id"] or "").strip():
                reject(f"required outbox is missing: {idempotency_key}")
            return str(row["outbox_id"])

        usage_record = _budget_usage_record(usage)
        retry_accepted = bool(usage_record["accepted"])
        exceeded_dimensions = tuple(usage_record["exceeded_dimensions"])
        budget_reason_code = (
            None if retry_accepted else TASK_STORE_BUDGET_EXCEEDED
        )
        schedule_ts_ms = int(usage_record["created_ts_ms"])
        graph_id = str(attempt["graph_id"])
        graph_revision = int(attempt["graph_revision"])
        node_id = str(attempt["node_id"])
        if (
            str(usage["budget_id"]) != budget_ref
            or str(usage["usage_id"]) != retry_usage_id
            or str(usage["graph_id"]) != graph_id
            or int(usage["graph_revision"]) != graph_revision
            or str(usage["node_id"]) != node_id
            or str(usage["attempt_id"]) != attempt_id
            or str(usage["charge_kind"]) != "RETRY"
            or str(usage["delta_json"]) != retry_delta_json
            or str(usage["delta_digest"]) != retry_delta_digest
            or (str(usage["reason_code"]) if usage["reason_code"] else None)
            != budget_reason_code
        ):
            reject("BudgetUsage identity or outcome differs")

        unsafe_reservation = conn.execute(
            """
            SELECT reservation_id FROM task_runtime_idempotency_reservation
            WHERE attempt_id = ? LIMIT 1
            """,
            (attempt_id,),
        ).fetchone()
        if unsafe_reservation is not None:
            reject("an operation reservation exists")
        if (
            str(attempt["status"]) != AttemptStatus.FAILED.value
            or int(attempt["status_version"]) != expected_attempt_version + 1
            or str(attempt["owner_id"] or "") != owner_id
            or str(attempt["error_ref"] or "") != failure_ref
            or int(attempt["completed_ts_ms"] or -1) != schedule_ts_ms
            or int(attempt["updated_ts_ms"]) != schedule_ts_ms
        ):
            reject("Attempt terminal state, owner, error, or version differs")
        self._assert_mutation_outbox_replay(
            conn,
            attempt_outbox,
            expected_aggregate=(
                "TASK_ATTEMPT",
                attempt_id,
                expected_attempt_version + 1,
            ),
            expected_event_type="TASK_ATTEMPT_FAILED",
            expected_attempt_id=attempt_id,
        )

        lease_rows = conn.execute(
            """
            SELECT * FROM task_runtime_lease
            WHERE attempt_id = ? ORDER BY acquired_ts_ms, lease_id
            """,
            (attempt_id,),
        ).fetchall()
        if len(lease_rows) != 1:
            reject("exactly one durable lease is required")
        lease = lease_rows[0]
        if (
            str(lease["status"]) != LeaseStatus.RELEASED.value
            or str(lease["owner_id"]) != owner_id
            or int(lease["fencing_token"]) != fencing_token
            or int(lease["released_ts_ms"] or -1) != schedule_ts_ms
            or int(lease["status_version"]) < 1
        ):
            reject("Lease release authority or timestamp differs")
        lease_version = int(lease["status_version"])
        lease_outbox_key = (
            f"task-retry-lease-released:{lease['lease_id']}:{lease_version}"
        )
        lease_outbox = OutboxMessage.create(
            aggregate_type="TASK_LEASE",
            aggregate_id=str(lease["lease_id"]),
            aggregate_version=lease_version,
            event_type="TASK_LEASE_RELEASED",
            payload_ref=build_outbox_payload_ref(
                aggregate_type="TASK_LEASE",
                aggregate_id=str(lease["lease_id"]),
                aggregate_version=lease_version,
            ),
            idempotency_key=lease_outbox_key,
            outbox_id=persisted_outbox_id(lease_outbox_key),
            attempt_id=attempt_id,
            evidence_refs=(failure_ref, retry_usage_id),
        )
        self._assert_mutation_outbox_replay(
            conn,
            lease_outbox,
            expected_aggregate=("TASK_LEASE", str(lease["lease_id"]), lease_version),
            expected_event_type="TASK_LEASE_RELEASED",
            expected_attempt_id=attempt_id,
        )

        node = self._require_node(conn, graph_id, graph_revision, node_id)
        node_record = _node_record(node)
        expected_node_status = (
            TaskNodeStatus.RETRY_PENDING.value
            if retry_accepted
            else TaskNodeStatus.FAILED.value
        )
        retry_policy = dict(node_record["node"].get("retry_policy") or {})
        retry_not_before_ms = (
            schedule_ts_ms + int(retry_policy["retry_backoff_ms"])
            if retry_accepted
            else None
        )
        if (
            str(node["status"]) != expected_node_status
            or node["retry_not_before_ms"] != retry_not_before_ms
            or int(node["updated_ts_ms"]) != schedule_ts_ms
        ):
            reject("TaskNode retry state or deadline differs")
        node_aggregate_id = f"{graph_id}@{graph_revision}/{node_id}"
        node_version = int(node["status_version"])
        node_event_type = f"TASK_NODE_{expected_node_status}"
        node_outbox_key = (
            f"task-node-retry-{expected_node_status.lower()}:"
            f"{node_aggregate_id}:{node_version}"
        )
        node_outbox = OutboxMessage.create(
            aggregate_type="TASK_NODE",
            aggregate_id=node_aggregate_id,
            aggregate_version=node_version,
            event_type=node_event_type,
            payload_ref=build_outbox_payload_ref(
                aggregate_type="TASK_NODE",
                aggregate_id=node_aggregate_id,
                aggregate_version=node_version,
            ),
            idempotency_key=node_outbox_key,
            outbox_id=persisted_outbox_id(node_outbox_key),
            attempt_id=attempt_id,
            evidence_refs=(failure_ref, retry_usage_id),
        )
        self._assert_mutation_outbox_replay(
            conn,
            node_outbox,
            expected_aggregate=("TASK_NODE", node_aggregate_id, node_version),
            expected_event_type=node_event_type,
            expected_attempt_id=attempt_id,
        )

        graph = self._require_graph(conn, graph_id, graph_revision)
        expected_graph_status = (
            TaskGraphStatus.BLOCKED.value
            if not retry_accepted and terminalize_graph_on_budget_rejection
            else TaskGraphStatus.ACTIVE.value
        )
        if str(graph["status"]) != expected_graph_status:
            reject("TaskGraph outcome differs")
        if expected_graph_version is not None:
            expected_durable_graph_version = (
                expected_graph_version + 1
                if not retry_accepted and terminalize_graph_on_budget_rejection
                else expected_graph_version
            )
            if int(graph["status_version"]) != expected_durable_graph_version:
                reject("TaskGraph version differs from the retry mode")
        if not retry_accepted and terminalize_graph_on_budget_rejection:
            graph_version = int(graph["status_version"])
            graph_outbox = OutboxMessage.create(
                aggregate_type="TASK_GRAPH",
                aggregate_id=graph_id,
                aggregate_version=graph_version,
                event_type="TASK_GRAPH_BLOCKED",
                payload_ref=build_outbox_payload_ref(
                    aggregate_type="TASK_GRAPH",
                    aggregate_id=graph_id,
                    aggregate_version=graph_version,
                ),
                idempotency_key=(
                    f"task-retry-budget-blocked:{graph_id}:"
                    f"{graph_revision}:{graph_version}"
                ),
                outbox_id=(
                    f"outbox::task-retry-budget-blocked:{graph_id}:"
                    f"{graph_revision}:{graph_version}"
                ),
                attempt_id=attempt_id,
                evidence_refs=(failure_ref, retry_usage_id),
            )
            self._assert_mutation_outbox_replay(
                conn,
                graph_outbox,
                expected_aggregate=("TASK_GRAPH", graph_id, graph_version),
                expected_event_type="TASK_GRAPH_BLOCKED",
                expected_attempt_id=attempt_id,
            )
        elif not retry_accepted:
            unexpected_graph_outbox = conn.execute(
                """
                SELECT outbox_id FROM task_runtime_outbox
                WHERE aggregate_type = 'TASK_GRAPH' AND aggregate_id = ?
                  AND attempt_id = ? AND aggregate_version > ?
                LIMIT 1
                """,
                (graph_id, attempt_id, int(expected_graph_version or 0)),
            ).fetchone()
            if unexpected_graph_outbox is not None:
                reject("node-only retry unexpectedly emitted a TaskGraph outbox")

        budget = self._require_budget(conn, budget_ref)
        budget_record = self._budget_record(budget)
        expected_budget_status = (
            BudgetReservationStatus.ACTIVE.value
            if retry_accepted
            else BudgetReservationStatus.EXCEEDED.value
        )
        usage_version_before = int(usage["reservation_version_before"])
        usage_version_after = int(usage["reservation_version_after"])
        if (
            str(budget["graph_id"] or "") != graph_id
            or int(budget["graph_revision"] or 0) != graph_revision
            or budget["node_id"] is not None
            or budget["attempt_id"] is not None
            or str(budget["status"]) != expected_budget_status
            or int(budget["status_version"]) != usage_version_after
            or int(budget["updated_ts_ms"]) != schedule_ts_ms
        ):
            reject("BudgetReservation identity, status, or version differs")
        consumed = BudgetAmount.from_mapping(budget_record["consumed"])
        attempted = BudgetAmount.from_mapping(budget_record["attempted_consumed"])
        expected_attempted = (
            consumed
            if retry_accepted
            else _add_amounts(consumed, BudgetAmount(retry_count=1))
        )
        if (
            attempted.to_dict() != expected_attempted.to_dict()
            or tuple(budget_record["exceeded_dimensions"]) != exceeded_dimensions
        ):
            reject("BudgetReservation amounts differ from BudgetUsage")
        budget_event_type = (
            "BUDGET_CONSUMED" if retry_accepted else "BUDGET_EXCEEDED"
        )
        budget_outbox = OutboxMessage.create(
            aggregate_type="BUDGET_RESERVATION",
            aggregate_id=budget_ref,
            aggregate_version=usage_version_after,
            event_type=budget_event_type,
            payload_ref=build_outbox_payload_ref(
                aggregate_type="BUDGET_RESERVATION",
                aggregate_id=budget_ref,
                aggregate_version=usage_version_after,
            ),
            idempotency_key=f"task-retry-budget:{budget_ref}:{retry_usage_digest}",
            outbox_id=f"outbox::task-retry-budget::{retry_usage_digest}",
            attempt_id=attempt_id,
            evidence_refs=(failure_ref, retry_usage_id),
        )
        if (
            usage_version_after != usage_version_before + 1
            or str(usage["outbox_id"]) != budget_outbox.outbox_id
        ):
            reject("BudgetUsage reservation version or outbox binding differs")
        self._assert_mutation_outbox_replay(
            conn,
            budget_outbox,
            expected_aggregate=(
                "BUDGET_RESERVATION",
                budget_ref,
                usage_version_after,
            ),
            expected_event_type=budget_event_type,
            expected_attempt_id=attempt_id,
        )

        graph_nodes = conn.execute(
            """
            SELECT * FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id
            """,
            (graph_id, graph_revision),
        ).fetchall()
        return {
            "accepted": retry_accepted,
            "reason_code": budget_reason_code,
            "exceeded_dimensions": exceeded_dimensions,
            "retry_usage_id": retry_usage_id,
            "replayed": True,
            "attempt": dict(attempt),
            "lease": dict(lease),
            "node": node_record,
            "graph": _graph_record(graph, graph_nodes),
            "budget": budget_record,
            "usage": usage_record,
            "retry_not_before_ms": retry_not_before_ms,
        }
