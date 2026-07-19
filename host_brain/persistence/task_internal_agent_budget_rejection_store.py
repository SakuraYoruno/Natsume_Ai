"""Narrow atomic recovery for a seal-bound Internal Agent checkpoint.

This is not a generic reconciliation bypass.  It accepts only the production
Internal/Agent route, validates the complete canonical worker projection in
the same SQLite transaction, and then commits that exact projection as the
already-completed operation checkpoint.  Other routes and effects continue to
require the normal signed unknown-effect reconciliation path.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
import sqlite3
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetExceededAction,
)
from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.contracts.agent_runtime.task_graph import TaskGraphStatus
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.persistence.task_store_types import (
    IdempotencyStatus,
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TaskStoreError,
    build_outbox_payload_ref,
)
from host_brain.persistence.task_record_codec import (
    _dumps,
    _graph_record,
    _loads_object,
    _node_record,
    _outbox_record,
    _validated_node_payload,
)
from host_brain.persistence.task_budget_usage_store import (
    _assert_terminal_budget_authority,
)
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_payload,
    validate_worker_projection_record,
)
from host_brain.contracts.agent_runtime.worker_budget_usage import (
    WorkerBudgetUsage,
    WorkerBudgetUsageError,
)


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class TaskInternalAgentBudgetRejectionStoreMixin:
    """Atomic budget rejection and exact rejected terminal authority."""

    def reject_internal_agent_checkpoint_budget_bundle(
        self,
        attempt_id: str,
        *,
        reservation_id: str,
        budget_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        expected_node_version: int,
        expected_graph_version: int,
        expected_budget_version: int,
        expected_lease_version: int,
        error_ref: str,
        usage_id: str,
        attempt_outbox: OutboxMessage,
    ) -> dict[str, Any]:
        """Atomically close an exact post-checkpoint budget rejection."""

        attempt_ref = str(attempt_id or "").strip()
        owner = str(owner_id or "").strip()
        lease_ref = str(lease_id or "").strip()
        failure_ref = self._validated_reference(error_ref, field_name="error_ref")
        usage_ref = self._validated_reference(usage_id, field_name="usage_id")
        if (
            not attempt_ref
            or not lease_ref
            or not owner
            or type(fencing_token) is not int
            or fencing_token < 1
            or not isinstance(attempt_outbox, OutboxMessage)
            or attempt_outbox.attempt_id != attempt_ref
            or failure_ref not in attempt_outbox.evidence_refs
            or usage_ref not in attempt_outbox.evidence_refs
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Internal Agent rejected checkpoint bundle identity is invalid",
            )
        attempt_outbox.validate()
        evidence_refs = tuple(attempt_outbox.evidence_refs)
        ts_ms = self._now()
        with self._transaction() as conn:
            attempt = self._require_attempt(conn, attempt_ref)
            if (
                str(attempt["status"]) == "FAILED"
                and int(attempt["status_version"]) == expected_attempt_version + 1
            ):
                replay = self._replay_running_attempt_failure_bundle(
                    conn,
                    attempt=attempt,
                    attempt_id=attempt_ref,
                    owner_id=owner,
                    fencing_token=fencing_token,
                    expected_attempt_version=expected_attempt_version,
                    expected_node_version=expected_node_version,
                    expected_graph_version=expected_graph_version,
                    error_ref=failure_ref,
                    graph_target_status=TaskGraphStatus.BLOCKED,
                    attempt_outbox=attempt_outbox,
                    evidence_refs=evidence_refs,
                )
                self._validate_internal_agent_rejected_usage_uow(
                    conn,
                    reservation_id=reservation_id,
                    budget_id=budget_id,
                    attempt_id=attempt_ref,
                    expected_budget_version=expected_budget_version,
                    expected_budget_status="RELEASED",
                    expected_usage_id=usage_ref,
                    lease_id=lease_ref,
                    expected_lease_status="RELEASED",
                    expected_lease_version=expected_lease_version + 1,
                    owner_id=owner,
                    fencing_token=fencing_token,
                )
                if (
                    str(replay["lease"].get("lease_id") or "") != lease_ref
                    or int(replay["lease"].get("status_version") or 0)
                    != expected_lease_version + 1
                ):
                    raise TaskStoreError(
                        TASK_STORE_CAS_CONFLICT,
                        "rejected checkpoint replay Lease identity drifted",
                    )
                budget_outbox = self._internal_rejected_budget_outbox(
                    budget_id=budget_id,
                    aggregate_version=expected_budget_version + 1,
                    attempt_id=attempt_ref,
                    evidence_refs=evidence_refs,
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
                    expected_attempt_id=attempt_ref,
                )
                budget_outbox_row = conn.execute(
                    "SELECT created_ts_ms FROM task_runtime_outbox WHERE outbox_id = ?",
                    (budget_outbox.outbox_id,),
                ).fetchone()
                if (
                    budget_outbox_row is None
                    or int(budget_outbox_row["created_ts_ms"])
                    != int(attempt["completed_ts_ms"])
                ):
                    raise TaskStoreError(
                        TASK_STORE_CAS_CONFLICT,
                        "rejected checkpoint Budget Outbox timestamp drifted",
                    )
                return {
                    **replay,
                    "budget": self._budget_record(
                        self._require_budget(conn, budget_id)
                    ),
                    "usage_id": usage_ref,
                    "replayed": True,
                }

            self._require_version(
                attempt,
                expected_attempt_version,
                subject=f"Attempt {attempt_ref}",
            )
            graph_id = str(attempt["graph_id"])
            revision = int(attempt["graph_revision"])
            node_id = str(attempt["node_id"])
            node = self._require_node(conn, graph_id, revision, node_id)
            graph = self._require_graph(conn, graph_id, revision)
            self._require_version(node, expected_node_version, subject=f"TaskNode {node_id}")
            self._require_version(graph, expected_graph_version, subject=f"TaskGraph {graph_id}@{revision}")
            lease = self._require_lease(conn, lease_ref)
            if (
                str(attempt["status"]) != "RUNNING"
                or str(node["status"]) != "RUNNING"
                or str(graph["status"]) != "ACTIVE"
                or str(lease["status"]) != "ACTIVE"
                or str(lease["attempt_id"]) != attempt_ref
                or int(lease["status_version"]) != expected_lease_version
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "rejected checkpoint bundle requires RUNNING/ACTIVE authority",
                )
            self._assert_lease_owner(
                lease,
                owner_id=owner,
                fencing_token=fencing_token,
                now_ts_ms=ts_ms,
            )
            self._validate_internal_agent_rejected_usage_uow(
                conn,
                reservation_id=reservation_id,
                budget_id=budget_id,
                attempt_id=attempt_ref,
                expected_budget_version=expected_budget_version,
                expected_budget_status="EXCEEDED",
                expected_usage_id=usage_ref,
                lease_id=lease_ref,
                expected_lease_status="ACTIVE",
                expected_lease_version=expected_lease_version,
                owner_id=owner,
                fencing_token=fencing_token,
            )

            attempt_cursor = conn.execute(
                """
                UPDATE task_runtime_attempt
                SET status = 'FAILED', status_version = status_version + 1,
                    completed_ts_ms = ?, updated_ts_ms = ?, error_ref = ?
                WHERE attempt_id = ? AND status = 'RUNNING'
                  AND status_version = ? AND owner_id = ?
                """,
                (ts_ms, ts_ms, failure_ref, attempt_ref, expected_attempt_version, owner),
            )
            self._require_cas(attempt_cursor, subject=f"Attempt {attempt_ref}")
            self._insert_outbox(
                conn,
                attempt_outbox,
                ts_ms=ts_ms,
                expected_aggregate=("TASK_ATTEMPT", attempt_ref, expected_attempt_version + 1),
                expected_event_type="TASK_ATTEMPT_FAILED",
                expected_attempt_id=attempt_ref,
            )
            node_payload = _validated_node_payload(node)
            node_payload["status"] = "FAILED"
            node_payload["status_version"] = expected_node_version + 1
            node_cursor = conn.execute(
                """
                UPDATE task_runtime_node
                SET status = 'FAILED', status_version = status_version + 1,
                    node_json = ?, retry_not_before_ms = NULL, updated_ts_ms = ?
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                  AND status = 'RUNNING' AND status_version = ?
                """,
                (_dumps(node_payload), ts_ms, graph_id, revision, node_id, expected_node_version),
            )
            self._require_cas(node_cursor, subject=f"TaskNode {node_id}")
            node_outbox = self._terminal_failure_bundle_outbox(
                kind="node",
                aggregate_type="TASK_NODE",
                aggregate_id=f"{graph_id}@{revision}/{node_id}",
                aggregate_version=expected_node_version + 1,
                event_type="TASK_NODE_FAILED",
                attempt_id=attempt_ref,
                evidence_refs=evidence_refs,
            )
            self._insert_outbox(
                conn,
                node_outbox,
                ts_ms=ts_ms,
                expected_aggregate=("TASK_NODE", f"{graph_id}@{revision}/{node_id}", expected_node_version + 1),
                expected_event_type="TASK_NODE_FAILED",
                expected_attempt_id=attempt_ref,
            )
            graph_outbox = self._terminal_failure_bundle_outbox(
                kind="graph",
                aggregate_type="TASK_GRAPH",
                aggregate_id=graph_id,
                graph_revision=revision,
                aggregate_version=expected_graph_version + 1,
                event_type="TASK_GRAPH_BLOCKED",
                attempt_id=attempt_ref,
                evidence_refs=evidence_refs,
            )
            self._transition_graph_in_transaction(
                conn,
                graph_id=graph_id,
                revision=revision,
                expected_version=expected_graph_version,
                target_status=TaskGraphStatus.BLOCKED,
                outbox=graph_outbox,
                ts_ms=ts_ms,
                attempt_id=attempt_ref,
            )
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
                aggregate_version=int(lease["status_version"]) + 1,
                event_type="TASK_LEASE_RELEASED_TERMINAL",
                attempt_id=attempt_ref,
                evidence_refs=evidence_refs,
            )
            self._insert_outbox(
                conn,
                lease_outbox,
                ts_ms=ts_ms,
                expected_aggregate=("TASK_LEASE", str(lease["lease_id"]), int(lease["status_version"]) + 1),
                expected_event_type="TASK_LEASE_RELEASED_TERMINAL",
                expected_attempt_id=attempt_ref,
            )
            budget_cursor = conn.execute(
                """
                UPDATE task_runtime_budget_reservation
                SET status = 'RELEASED', status_version = status_version + 1,
                    updated_ts_ms = ?
                WHERE budget_id = ? AND status = 'EXCEEDED'
                  AND status_version = ?
                """,
                (ts_ms, budget_id, expected_budget_version),
            )
            self._require_cas(budget_cursor, subject=f"BudgetReservation {budget_id}")
            budget_outbox = self._internal_rejected_budget_outbox(
                budget_id=budget_id,
                aggregate_version=expected_budget_version + 1,
                attempt_id=attempt_ref,
                evidence_refs=evidence_refs,
            )
            self._insert_outbox(
                conn,
                budget_outbox,
                ts_ms=ts_ms,
                expected_aggregate=("BUDGET_RESERVATION", budget_id, expected_budget_version + 1),
                expected_event_type="BUDGET_RESERVATION_RELEASED",
                expected_attempt_id=attempt_ref,
            )
            return {
                "attempt": dict(self._require_attempt(conn, attempt_ref)),
                "node": _node_record(self._require_node(conn, graph_id, revision, node_id)),
                "graph": _graph_record(
                    self._require_graph(conn, graph_id, revision),
                    conn.execute(
                        "SELECT * FROM task_runtime_node WHERE graph_id = ? "
                        "AND graph_revision = ? ORDER BY node_id",
                        (graph_id, revision),
                    ).fetchall(),
                ),
                "lease": dict(self._require_lease(conn, str(lease["lease_id"]))),
                "budget": self._budget_record(self._require_budget(conn, budget_id)),
                "usage_id": usage_ref,
                "replayed": False,
            }

    def resolve_internal_agent_budget_rejection_terminal(
        self,
        attempt_id: str,
    ) -> dict[str, Any]:
        """Reopen one exact atomic Internal budget-rejection terminal.

        This is the sole read authority used by artifact cleanup.  A status
        tuple is insufficient: the committed projection, rejected usage,
        historical/current Lease lineage and all five same-time terminal
        Outboxes must remain exact.
        """

        attempt_ref = str(attempt_id or "").strip()
        if not attempt_ref:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Internal Agent budget terminal requires attempt_id",
            )
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            resolved = self._resolve_internal_agent_budget_rejection_terminal_uow(
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

    def _resolve_internal_agent_budget_rejection_terminal_uow(
        self,
        conn: sqlite3.Connection,
        attempt_id: str,
    ) -> dict[str, Any]:
        """Validate and reopen the exact rejected terminal in an existing UoW."""

        attempt_ref = str(attempt_id or "").strip()
        attempt = self._require_attempt(conn, attempt_ref)
        graph_id = str(attempt["graph_id"])
        revision = int(attempt["graph_revision"])
        node_id = str(attempt["node_id"])
        node = self._require_node(conn, graph_id, revision, node_id)
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
                "Internal Agent budget terminal state matrix drifted",
            )
        budget = self._budget_record(budgets[0])
        terminal_version = int(budget["status_version"]) - 1
        if terminal_version < 1:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent budget terminal version is invalid",
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
        )
        self._validate_internal_rejected_terminal_outboxes_uow(
            conn,
            attempt=attempt,
            node=node,
            graph=graph,
            lease=final_lease,
            budget=budgets[0],
            usage_id=str(winner["usage_id"]),
        )
        return {
            "authority_kind": "INTERNAL_AGENT_BUDGET_REJECTED",
            "attempt": dict(attempt),
            "node": _node_record(node),
            "graph": _graph_record(graph, [node]),
            "lease": dict(final_lease),
            "historical_lease_id": str(operations[0]["lease_id"]),
            "budget": budget,
            "reservation": self._operation_record(operations[0]),
            "usage": dict(winner),
        }

    @staticmethod
    def _validate_internal_rejected_terminal_outboxes_uow(
        conn: sqlite3.Connection,
        *,
        attempt: sqlite3.Row,
        node: sqlite3.Row,
        graph: sqlite3.Row,
        lease: sqlite3.Row,
        budget: sqlite3.Row,
        usage_id: str,
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
                "Internal Agent budget terminal requires five exact Outboxes",
            )
        records = {_outbox_record(row)["event_type"]: _outbox_record(row) for row in rows}
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
                "Internal Agent rejected Attempt terminal evidence is incomplete",
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
                    "Internal Agent budget terminal Outbox authority drifted",
                )

    @staticmethod
    def _internal_rejected_budget_outbox(
        *,
        budget_id: str,
        aggregate_version: int,
        attempt_id: str,
        evidence_refs: tuple[str, ...],
    ) -> OutboxMessage:
        key = (
            f"internal-agent-budget-rejected:{attempt_id}:{budget_id}:"
            f"{aggregate_version}"
        )
        return OutboxMessage.create(
            aggregate_type="BUDGET_RESERVATION",
            aggregate_id=budget_id,
            aggregate_version=aggregate_version,
            event_type="BUDGET_RESERVATION_RELEASED",
            payload_ref=build_outbox_payload_ref(
                aggregate_type="BUDGET_RESERVATION",
                aggregate_id=budget_id,
                aggregate_version=aggregate_version,
            ),
            idempotency_key=key,
            outbox_id=f"outbox::{key}",
            attempt_id=attempt_id,
            evidence_refs=evidence_refs,
        )

    def _validate_internal_agent_rejected_usage_uow(
        self,
        conn: sqlite3.Connection,
        *,
        reservation_id: str,
        budget_id: str,
        attempt_id: str,
        expected_budget_version: int,
        expected_budget_status: str,
        expected_usage_id: str,
        lease_id: str,
        expected_lease_status: str,
        expected_lease_version: int,
        owner_id: str,
        fencing_token: int,
    ) -> str:
        reservation = self._require_operation_reservation(conn, reservation_id)
        attempt = self._require_attempt(conn, attempt_id)
        lease = self._require_lease(conn, lease_id)
        graph = self._require_graph(
            conn,
            str(attempt["graph_id"]),
            int(attempt["graph_revision"]),
        )
        node = self._require_node(
            conn,
            str(attempt["graph_id"]),
            int(attempt["graph_revision"]),
            str(attempt["node_id"]),
        )
        projection_row = conn.execute(
            "SELECT * FROM task_runtime_worker_projection WHERE projection_id = ?",
            (str(reservation["receipt_ref"] or ""),),
        ).fetchone()
        budget_row = self._require_budget(conn, budget_id)
        if projection_row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal rejected budget lacks projection authority",
            )
        try:
            projection = validate_worker_projection_record(projection_row)
        except WorkerProjectionStoreError as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal rejected budget projection is corrupt",
            ) from exc
        command = conn.execute(
            "SELECT * FROM host_command_journal WHERE command_id = ?",
            (str(projection["command_id"]),),
        ).fetchone()
        if command is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal rejected budget lacks HostCommand authority",
            )
        try:
            work_order = WorkOrderV3.from_mapping(
                _loads_object(
                    str(command["work_order_json"]),
                    field_name="Internal Agent WorkOrder",
                )
            )
        except (ContractViolation, TypeError, ValueError) as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal rejected budget WorkOrder is corrupt",
            ) from exc
        graph_nodes = conn.execute(
            """
            SELECT * FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY node_id
            """,
            (str(attempt["graph_id"]), int(attempt["graph_revision"])),
        ).fetchall()
        graph_record = _graph_record(graph, list(graph_nodes))
        if len(graph_nodes) != 1 or len(graph_record.get("nodes") or ()) != 1:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent rejected checkpoint requires exactly one TaskNode",
            )
        node_payload = _validated_node_payload(node)
        budget = self._budget_record(budget_row)
        winner, authoritative_dimensions = _assert_terminal_budget_authority(
            conn,
            budget_row=budget_row,
            terminal_version=expected_budget_version,
        )
        if (
            str(winner.get("usage_id") or "") != expected_usage_id
            or str(winner.get("attempt_id") or "") != attempt_id
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent rejected BudgetUsage winner identity drifted",
            )
        policy = work_order.budget_policy
        if (
            policy is None
            or policy.on_exceeded is not BudgetExceededAction.BLOCK
            or policy.scope != str(budget["scope"])
            or policy.limits.to_dict() != dict(budget["limits"])
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent rejected checkpoint BudgetPolicy authority drifted",
            )
        self._validate_internal_checkpoint_lease_lineage(
            conn,
            reservation=reservation,
            attempt=attempt,
            active_or_terminal_lease=lease,
            expected_lease_status=expected_lease_status,
            expected_lease_version=expected_lease_version,
            owner_id=owner_id,
            fencing_token=fencing_token,
        )
        self._validate_internal_checkpoint_projection_authority(
            projection,
            command=command,
            work_order=work_order,
            graph=graph,
            node_payload=node_payload,
            reservation=reservation,
            attempt=attempt,
        )
        usage = winner
        expected_usage_id, expected_kind, expected_delta = (
            _projection_worker_usage_identity(
                projection,
                budget_id=budget_id,
                node_id=str(attempt["node_id"]),
                attempt_id=attempt_id,
            )
        )
        actual_delta = dict(usage.get("delta") or {})
        exact_delta = (
            set(actual_delta) == set(expected_delta)
            and all(
                int(actual_delta.get(key) or 0) == int(expected_delta[key])
                for key in expected_delta
                if key != "time_ms"
            )
            and int(actual_delta.get("time_ms") or 0)
            >= int(expected_delta.get("time_ms") or 0)
        )
        consumed = dict(budget["consumed"])
        reserved = dict(budget["reserved"])
        attempted = dict(budget["attempted_consumed"])
        amount_fields = tuple(BudgetAmount().to_dict())
        recomputed_attempted = {
            field: int(consumed[field]) + int(actual_delta[field])
            for field in amount_fields
        }
        recomputed_exceeded = tuple(
            field
            for field in amount_fields
            if recomputed_attempted[field] > int(reserved[field])
        )
        durable_exceeded = tuple(budget.get("exceeded_dimensions") or ())
        usage_exceeded = tuple(usage.get("exceeded_dimensions") or ())
        exact = (
            str(reservation["status"]) == "COMMITTED"
            and str(reservation["attempt_id"]) == attempt_id
            and str(reservation["receipt_ref"] or "")
            == str(projection["projection_id"])
            and str(command["surface"]) == "internal"
            and str(command["route_type"]) == "delegate"
            and str(command["target_id"]) == "agent_runtime"
            and str(command["graph_id"]) == str(attempt["graph_id"])
            and int(command["graph_revision"]) == int(attempt["graph_revision"])
            and str(command["root_work_order_id"])
            == str(graph["root_work_order_id"])
            and str(command["root_work_order_id"]) == work_order.root_work_order_id
            and str(command["business_scope"])
            == str(reservation["business_scope"])
            and str(command["operation_fingerprint"])
            == str(reservation["operation_fingerprint"])
            and str(attempt["operation_scope"])
            == str(reservation["business_scope"])
            and str(attempt["operation_fingerprint"])
            == str(reservation["operation_fingerprint"])
            and work_order.work_order_id == str(command["root_work_order_id"])
            and work_order.task_graph_id == str(attempt["graph_id"])
            and work_order.task_node_id == str(attempt["node_id"])
            and work_order.graph_revision == int(attempt["graph_revision"])
            and work_order.business_idempotency_scope
            == str(reservation["business_scope"])
            and work_order.operation_fingerprint
            == str(reservation["operation_fingerprint"])
            and str(node_payload.get("route") or "")
            == "delegate:agent_runtime"
            and str(projection["command_id"]) == str(command["command_id"])
            and str(projection["graph_id"]) == str(attempt["graph_id"])
            and int(projection["graph_revision"]) == int(attempt["graph_revision"])
            and str(projection["node_id"]) == str(attempt["node_id"])
            and str(projection["attempt_id"]) == attempt_id
            and str(projection["route_type"]) == "delegate"
            and str(projection["target_id"]) == "agent_runtime"
            and str(budget["status"]) == expected_budget_status
            and int(budget["status_version"])
            == (
                expected_budget_version
                if expected_budget_status == "EXCEEDED"
                else expected_budget_version + 1
            )
            and str(budget["graph_id"]) == str(attempt["graph_id"])
            and int(budget["graph_revision"]) == int(attempt["graph_revision"])
            and budget["node_id"] is None
            and budget["attempt_id"] is None
            and (
                expected_budget_status != "RELEASED"
                or int(budget["updated_ts_ms"])
                == int(attempt["completed_ts_ms"] or -1)
            )
            and str(usage["usage_id"]) == expected_usage_id
            and str(usage["charge_kind"]).upper() == expected_kind
            and str(usage["graph_id"]) == str(attempt["graph_id"])
            and int(usage["graph_revision"]) == int(attempt["graph_revision"])
            and str(usage["node_id"]) == str(attempt["node_id"])
            and str(usage["attempt_id"]) == attempt_id
            and usage["accepted"] is False
            and str(usage["reason_code"]) == "TASK_STORE_BUDGET_EXCEEDED"
            and int(usage["reservation_version_after"])
            == expected_budget_version
            and int(usage["reservation_version_before"]) + 1
            == expected_budget_version
            and bool(usage["exceeded_dimensions"])
            and attempted == recomputed_attempted
            and durable_exceeded == usage_exceeded == recomputed_exceeded
            and durable_exceeded == tuple(authoritative_dimensions)
            and exact_delta
        )
        if not exact:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal rejected budget authority drifted",
            )
        return str(usage["usage_id"])


def _projection_worker_usage_identity(
    projection: Mapping[str, Any],
    *,
    budget_id: str,
    node_id: str,
    attempt_id: str,
) -> tuple[str, str, dict[str, int]]:
    payload = projection.get("budget_usage")
    if payload is None:
        digest = hashlib.sha256(
            f"{budget_id}\n{node_id}\n{attempt_id}\nworker-elapsed".encode(
                "utf-8"
            )
        ).hexdigest()
        return (
            f"budget-usage://{digest}",
            "WORKER_ELAPSED",
            BudgetAmount().to_dict(),
        )
    try:
        typed = WorkerBudgetUsage.from_payload(
            payload if isinstance(payload, Mapping) else {}
        )
    except WorkerBudgetUsageError as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "Internal Agent rejected projection BudgetUsage is invalid",
        ) from exc
    labels = {
        "token_count": "TOKEN",
        "tool_call_count": "TOOL",
        "time_ms": "TIME",
        "retry_count": "RETRY",
        "model_cost_micros": "COST",
    }
    kind = "WORKER_USAGE:" + "+".join(
        labels[item] for item in typed.measured_dimensions()
    )
    return typed.usage_id, kind, typed.to_budget_amount(elapsed_ms=0).to_dict()


__all__ = ["TaskInternalAgentBudgetRejectionStoreMixin"]
