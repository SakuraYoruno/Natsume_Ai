"""Atomic signed failure settlement for a post-reservation interruption.

This store boundary is deliberately distinct from retry/reacquisition and
Graph cancellation.  A signed ``RELEASE + NO_SIDE_EFFECT`` decision closes one
exact Operation, Attempt, TaskNode and expired Lease.  A single-node Graph is
also failed and its Budget released in that transaction.  A multi-node Graph
remains ACTIVE until the existing append-only 0090 parallel-settlement
authority atomically closes the shared Graph and Budget.  Both phases can be
reopened exactly after response loss.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
import sqlite3
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.operation_reconciliation import (
    OperationReconciliationContractError,
    build_operation_failure_outbox_identity,
    build_reconciliation_attestation_binding,
)
from host_brain.contracts.agent_runtime.parallel_settlement import (
    normalize_parallel_settlement_decision,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphStatus,
    TaskNodeStatus,
    validate_task_graph_transition,
    validate_task_node_transition,
)
from host_brain.persistence.task_decision_integrity import (
    _assert_parallel_settlement_decision_outbox_binding,
    _find_invalid_declared_parallel_settlement_decision_ref,
)
from host_brain.persistence.task_budget_usage_store import (
    _assert_terminal_budget_authority,
)
from host_brain.persistence.reconciliation_proof_verifier import (
    TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
    TASK_STORE_RECONCILIATION_PROOF_INVALID,
)
from host_brain.persistence.task_record_codec import (
    _dumps,
    _graph_record,
    _node_record,
    _parallel_settlement_decision_record,
    _validate_attempt_transition,
    _validated_node_payload,
)
from host_brain.persistence.task_store_types import (
    AttemptStatus,
    IdempotencyStatus,
    LeaseStatus,
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TaskStoreError,
    build_outbox_payload_ref,
)


_SIGNATURE_PATTERN = re.compile(r"^ed25519:[A-Za-z0-9_-]{86}==$")
_REASON_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,119}$")
_FAILURE_AUTHORITY_KIND = "RECONCILED_OPERATION_TERMINAL_FAILURE"
_NODE_FAILURE_AUTHORITY_KIND = "RECONCILED_OPERATION_NODE_FAILURE"
_INTERNAL_AUTHORITY_KIND = "INTERNAL_AGENT_RAW_FAILURE_TERMINAL"


class TaskOperationFailureReconciliationStoreMixin:
    """Persist and reopen one signed no-side-effect node failure."""

    def reconcile_operation_failure(
        self,
        reservation_id: str,
        *,
        expected_version: int,
        outbox: OutboxMessage,
        reconciler_actor_id: str,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
    ) -> dict[str, Any]:
        request = self._normalize_operation_failure_request(
            reservation_id=reservation_id,
            expected_version=expected_version,
            reconciler_actor_id=reconciler_actor_id,
            attestation_ref=attestation_ref,
            attestation_key_id=attestation_key_id,
            attestation_signature=attestation_signature,
        )
        if not isinstance(outbox, OutboxMessage):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "operation failure reconciliation requires an OutboxMessage",
            )
        outbox.validate()
        ts_ms = self._now()
        with self._transaction() as conn:
            operation = self._require_operation_reservation(
                conn,
                request["reservation_id"],
            )
            operation_record = self._operation_record(operation)
            if self._operation_failure_request_matches_terminal(
                operation_record,
                request=request,
            ):
                terminal = self._resolve_operation_failure_uow(
                    conn,
                    request["reservation_id"],
                )
                self._assert_mutation_outbox_replay(
                    conn,
                    outbox,
                    expected_aggregate=(
                        "OPERATION_RESERVATION",
                        request["reservation_id"],
                        request["expected_version"] + 1,
                    ),
                    expected_event_type="OPERATION_RECOVERY_RELEASE",
                    expected_attempt_id=str(
                        terminal["attempt"]["attempt_id"]
                    ),
                )
                return {**terminal, "replayed": True}

            if (
                str(operation_record.get("status") or "")
                != IdempotencyStatus.RECOVERY_REQUIRED.value
                or int(operation_record.get("status_version") or -1)
                != request["expected_version"]
            ):
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    "operation is not the exact RECOVERY_REQUIRED version",
                )
            attempt_id = str(
                operation_record.get("attempt_id") or ""
            ).strip()
            lease_id = str(operation_record.get("lease_id") or "").strip()
            if not attempt_id or not lease_id:
                raise TaskStoreError(
                    TASK_STORE_OWNERSHIP_CONFLICT,
                    "operation failure reconciliation requires Attempt and Lease bindings",
                )
            attempt = self._require_attempt(conn, attempt_id)
            graph_id = str(attempt["graph_id"])
            graph_revision = int(attempt["graph_revision"])
            node_id = str(attempt["node_id"])
            graph = self._require_graph(conn, graph_id, graph_revision)
            node = self._require_node(conn, graph_id, graph_revision, node_id)
            lease = self._require_lease(conn, lease_id)
            nodes = conn.execute(
                """
                SELECT * FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY node_id
                """,
                (graph_id, graph_revision),
            ).fetchall()
            budgets = conn.execute(
                """
                SELECT * FROM task_runtime_budget_reservation
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY budget_id
                """,
                (graph_id, graph_revision),
            ).fetchall()
            cancellation = conn.execute(
                """
                SELECT intent_ref FROM task_runtime_graph_cancellation_intent
                WHERE graph_id = ? AND graph_revision = ?
                LIMIT 1
                """,
                (graph_id, graph_revision),
            ).fetchone()
            projections = conn.execute(
                """
                SELECT projection_id FROM task_runtime_worker_projection
                WHERE attempt_id = ? LIMIT 1
                """,
                (attempt_id,),
            ).fetchone()
            effects = conn.execute(
                """
                SELECT effect_id FROM task_runtime_acceptance_effect_intent
                WHERE attempt_id = ? LIMIT 1
                """,
                (attempt_id,),
            ).fetchone()
            if (
                not nodes
                or len(budgets) != 1
                or cancellation is not None
                or projections is not None
                or effects is not None
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "failure reconciliation requires TaskNodes, one graph Budget, "
                    "no cancellation, projection, or Acceptance Effect",
                )
            graph_wide_terminal = len(nodes) == 1
            budget = budgets[0]
            if (
                str(attempt["status"]) != AttemptStatus.RUNNING.value
                or str(node["status"]) != TaskNodeStatus.RUNNING.value
                or str(graph["status"]) != TaskGraphStatus.ACTIVE.value
                or str(operation["attempt_id"] or "") != attempt_id
                or str(operation["lease_id"] or "") != lease_id
                or str(operation["owner_id"] or "")
                != str(lease["owner_id"] or "")
                or int(operation["fencing_token"] or 0)
                != int(lease["fencing_token"] or 0)
                or str(attempt["owner_id"] or "")
                != str(lease["owner_id"] or "")
                or str(lease["attempt_id"]) != attempt_id
                or str(budget["status"])
                not in {
                    BudgetReservationStatus.ACTIVE.value,
                    BudgetReservationStatus.EXCEEDED.value,
                }
            ):
                raise TaskStoreError(
                    TASK_STORE_OWNERSHIP_CONFLICT,
                    "operation failure Task, Lease, or Budget binding drifted",
                )
            lease_status = str(lease["status"])
            if (
                lease_status != LeaseStatus.ACTIVE.value
                or int(lease["expires_ts_ms"]) > ts_ms
            ):
                raise TaskStoreError(
                    TASK_STORE_OWNERSHIP_CONFLICT,
                    "terminal failure requires the exact expired-in-time active Lease",
                )
            reason_code = str(
                operation_record.get("recovery_reason_code") or ""
            ).strip().upper()
            if (
                not _REASON_PATTERN.fullmatch(reason_code)
                or reason_code
                in {
                    "GOVERNED_CALLER_CANCELLED",
                    "GRAPH_CANCELLATION_REQUESTED",
                }
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "operation recovery reason is not a terminal worker failure",
                )
            proof_binding = self._verify_operation_failure_proof(
                operation_record,
                request=request,
            )
            proof_digest = (
                "sha256:" + hashlib.sha256(proof_binding).hexdigest()
            )
            error_ref = self._operation_failure_error_ref(
                reservation_id=request["reservation_id"],
                attempt_id=attempt_id,
                reason_code=reason_code,
                proof_ref=request["attestation_ref"],
                proof_digest=proof_digest,
            )
            evidence_refs = (
                error_ref,
                request["attestation_ref"],
            )
            self._require_outbox_attempt(outbox, attempt_id)
            canonical_operation_outbox = (
                self._operation_failure_reconciliation_outbox(
                    request=request,
                    attempt_id=attempt_id,
                )
            )
            expected_operation_aggregate = (
                "OPERATION_RESERVATION",
                request["reservation_id"],
                request["expected_version"] + 1,
            )
            if (
                outbox != canonical_operation_outbox
                or (
                    outbox.aggregate_type,
                    outbox.aggregate_id,
                    outbox.aggregate_version,
                )
                != expected_operation_aggregate
                or outbox.event_type != "OPERATION_RECOVERY_RELEASE"
                or request["attestation_ref"] not in outbox.evidence_refs
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "operation failure Outbox binding is invalid",
                )

            operation_cursor = conn.execute(
                """
                UPDATE task_runtime_idempotency_reservation
                SET status = 'RELEASED', receipt_ref = NULL,
                    reconciliation_proof_ref = ?,
                    reconciliation_proof_digest = ?,
                    reconciliation_proof_key_id = ?,
                    reconciliation_proof_signature = ?,
                    reconciliation_action = 'RELEASE',
                    reconciliation_outcome = 'NO_SIDE_EFFECT',
                    reconciler_actor_id = ?,
                    reconciliation_recovery_version = ?,
                    status_version = status_version + 1,
                    updated_ts_ms = ?
                WHERE reservation_id = ? AND status = 'RECOVERY_REQUIRED'
                  AND status_version = ?
                """,
                (
                    request["attestation_ref"],
                    proof_digest,
                    request["attestation_key_id"],
                    request["attestation_signature"],
                    request["reconciler_actor_id"],
                    request["expected_version"],
                    ts_ms,
                    request["reservation_id"],
                    request["expected_version"],
                ),
            )
            self._require_cas(
                operation_cursor,
                subject=f"OperationReservation {request['reservation_id']}",
            )
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=expected_operation_aggregate,
                expected_event_type="OPERATION_RECOVERY_RELEASE",
                expected_attempt_id=attempt_id,
            )

            attempt_version = int(attempt["status_version"]) + 1
            _validate_attempt_transition(
                AttemptStatus.RUNNING,
                AttemptStatus.FAILED,
            )
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
                    error_ref,
                    attempt_id,
                    int(attempt["status_version"]),
                    str(lease["owner_id"]),
                ),
            )
            self._require_cas(
                attempt_cursor,
                subject=f"Attempt {attempt_id}",
            )
            attempt_outbox = self._operation_failure_outbox(
                kind="attempt",
                aggregate_type="TASK_ATTEMPT",
                aggregate_id=attempt_id,
                aggregate_version=attempt_version,
                event_type="TASK_ATTEMPT_FAILED",
                attempt_id=attempt_id,
                evidence_refs=evidence_refs,
            )
            self._insert_outbox(
                conn,
                attempt_outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "TASK_ATTEMPT",
                    attempt_id,
                    attempt_version,
                ),
                expected_event_type="TASK_ATTEMPT_FAILED",
                expected_attempt_id=attempt_id,
            )

            validate_task_node_transition(
                TaskNodeStatus.RUNNING,
                TaskNodeStatus.FAILED,
            )
            node_version = int(node["status_version"]) + 1
            node_payload = _validated_node_payload(node)
            node_payload["status"] = TaskNodeStatus.FAILED.value
            node_payload["status_version"] = node_version
            node_cursor = conn.execute(
                """
                UPDATE task_runtime_node
                SET status = 'FAILED', status_version = status_version + 1,
                    node_json = ?, retry_not_before_ms = NULL,
                    updated_ts_ms = ?
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                  AND status = 'RUNNING' AND status_version = ?
                """,
                (
                    _dumps(node_payload),
                    ts_ms,
                    graph_id,
                    graph_revision,
                    node_id,
                    int(node["status_version"]),
                ),
            )
            self._require_cas(
                node_cursor,
                subject=f"TaskNode {node_id}",
            )
            node_aggregate_id = (
                f"{graph_id}@{graph_revision}/{node_id}"
            )
            node_outbox = self._operation_failure_outbox(
                kind="node",
                aggregate_type="TASK_NODE",
                aggregate_id=node_aggregate_id,
                aggregate_version=node_version,
                event_type="TASK_NODE_FAILED",
                attempt_id=attempt_id,
                evidence_refs=evidence_refs,
            )
            self._insert_outbox(
                conn,
                node_outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "TASK_NODE",
                    node_aggregate_id,
                    node_version,
                ),
                expected_event_type="TASK_NODE_FAILED",
                expected_attempt_id=attempt_id,
            )

            if graph_wide_terminal:
                validate_task_graph_transition(
                    TaskGraphStatus.ACTIVE,
                    TaskGraphStatus.FAILED,
                )
                graph_version = int(graph["status_version"]) + 1
                graph_outbox = self._operation_failure_outbox(
                    kind="graph",
                    aggregate_type="TASK_GRAPH",
                    aggregate_id=graph_id,
                    graph_revision=graph_revision,
                    aggregate_version=graph_version,
                    event_type="TASK_GRAPH_FAILED",
                    attempt_id=attempt_id,
                    evidence_refs=evidence_refs,
                )
                self._transition_graph_in_transaction(
                    conn,
                    graph_id=graph_id,
                    revision=graph_revision,
                    expected_version=int(graph["status_version"]),
                    target_status=TaskGraphStatus.FAILED,
                    outbox=graph_outbox,
                    ts_ms=ts_ms,
                    attempt_id=attempt_id,
                )

            lease_version = int(lease["status_version"]) + 1
            lease_cursor = conn.execute(
                """
                UPDATE task_runtime_lease
                SET status = 'EXPIRED', released_ts_ms = ?,
                    status_version = status_version + 1
                WHERE lease_id = ? AND status = 'ACTIVE'
                  AND status_version = ? AND expires_ts_ms <= ?
                """,
                (
                    ts_ms,
                    lease_id,
                    int(lease["status_version"]),
                    ts_ms,
                ),
            )
            self._require_cas(
                lease_cursor,
                subject=f"Lease {lease_id}",
            )
            lease_outbox = self._operation_failure_outbox(
                kind="lease",
                aggregate_type="TASK_LEASE",
                aggregate_id=lease_id,
                aggregate_version=lease_version,
                event_type="TASK_LEASE_EXPIRED",
                attempt_id=attempt_id,
                evidence_refs=evidence_refs,
            )
            self._insert_outbox(
                conn,
                lease_outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "TASK_LEASE",
                    lease_id,
                    lease_version,
                ),
                expected_event_type="TASK_LEASE_EXPIRED",
                expected_attempt_id=attempt_id,
            )

            if graph_wide_terminal:
                budget_version = int(budget["status_version"]) + 1
                budget_cursor = conn.execute(
                    """
                    UPDATE task_runtime_budget_reservation
                    SET status = 'RELEASED',
                        status_version = status_version + 1,
                        updated_ts_ms = ?
                    WHERE budget_id = ? AND status IN ('ACTIVE', 'EXCEEDED')
                      AND status_version = ?
                    """,
                    (
                        ts_ms,
                        str(budget["budget_id"]),
                        int(budget["status_version"]),
                    ),
                )
                self._require_cas(
                    budget_cursor,
                    subject=f"BudgetReservation {budget['budget_id']}",
                )
                budget_outbox = self._operation_failure_outbox(
                    kind="budget",
                    aggregate_type="BUDGET_RESERVATION",
                    aggregate_id=str(budget["budget_id"]),
                    aggregate_version=budget_version,
                    event_type="BUDGET_RESERVATION_RELEASED",
                    attempt_id=attempt_id,
                    evidence_refs=evidence_refs,
                )
                self._insert_outbox(
                    conn,
                    budget_outbox,
                    ts_ms=ts_ms,
                    expected_aggregate=(
                        "BUDGET_RESERVATION",
                        str(budget["budget_id"]),
                        budget_version,
                    ),
                    expected_event_type="BUDGET_RESERVATION_RELEASED",
                    expected_attempt_id=attempt_id,
                )
            terminal = self._resolve_operation_failure_uow(
                conn,
                request["reservation_id"],
            )
            return {**terminal, "replayed": False}

    def resolve_reconciled_operation_failure(
        self,
        reservation_id: str,
    ) -> dict[str, Any]:
        reference = str(reservation_id or "").strip()
        if not reference:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "operation failure reservation_id is required",
            )
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            result = self._resolve_operation_failure_uow(conn, reference)
            conn.commit()
            return result
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def try_resolve_internal_agent_raw_failure_terminal(
        self,
        attempt_id: str,
    ) -> dict[str, Any] | None:
        attempt_ref = str(attempt_id or "").strip()
        if not attempt_ref:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Internal raw failure attempt_id is required",
            )
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            rows = conn.execute(
                """
                SELECT reservation_id
                FROM task_runtime_idempotency_reservation
                WHERE attempt_id = ? AND status = 'RELEASED'
                  AND reconciliation_action = 'RELEASE'
                  AND reconciliation_outcome = 'NO_SIDE_EFFECT'
                ORDER BY reservation_id
                """,
                (attempt_ref,),
            ).fetchall()
            if not rows:
                conn.commit()
                return None
            if len(rows) != 1:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "Internal raw failure Operation cardinality drifted",
                )
            terminal = self._resolve_operation_failure_uow(
                conn,
                str(rows[0]["reservation_id"]),
            )
            command = terminal.get("command")
            graph = terminal.get("graph")
            node = terminal.get("node")
            attempt = terminal.get("attempt")
            if (
                not isinstance(command, Mapping)
                or not isinstance(graph, Mapping)
                or not isinstance(node, Mapping)
                or not isinstance(attempt, Mapping)
                or str(command.get("surface") or "") != "internal"
                or str(command.get("route_type") or "") != "delegate"
                or str(command.get("target_id") or "") != "agent_runtime"
                or str(attempt.get("attempt_id") or "") != attempt_ref
                or str(graph.get("status") or "") != "FAILED"
                or str(node.get("status") or "") != "FAILED"
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "Internal raw failure authority drifted",
                )
            conn.commit()
            return {
                **terminal,
                "authority_kind": _INTERNAL_AUTHORITY_KIND,
            }
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _resolve_operation_failure_uow(
        self,
        conn: sqlite3.Connection,
        reservation_id: str,
    ) -> dict[str, Any]:
        operation_row = self._require_operation_reservation(
            conn,
            reservation_id,
        )
        operation = self._operation_record(operation_row)
        attempt_id = str(operation.get("attempt_id") or "").strip()
        lease_id = str(operation.get("lease_id") or "").strip()
        if (
            str(operation.get("status") or "") != "RELEASED"
            or str(operation.get("reconciliation_action") or "") != "RELEASE"
            or str(operation.get("reconciliation_outcome") or "")
            != "NO_SIDE_EFFECT"
            or operation.get("receipt_ref") is not None
            or not attempt_id
            or not lease_id
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "operation is not a reconciled terminal failure",
            )
        request = {
            "reservation_id": str(operation["reservation_id"]),
            "expected_version": int(
                operation.get("reconciliation_recovery_version") or -1
            ),
            "reconciler_actor_id": str(
                operation.get("reconciler_actor_id") or ""
            ),
            "attestation_ref": str(
                operation.get("reconciliation_proof_ref") or ""
            ),
            "attestation_key_id": str(
                operation.get("reconciliation_proof_key_id") or ""
            ),
            "attestation_signature": str(
                operation.get("reconciliation_proof_signature") or ""
            ),
        }
        normalized = self._normalize_operation_failure_request(**request)
        if not self._operation_failure_request_matches_terminal(
            operation,
            request=normalized,
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal Operation reconciliation binding drifted",
            )
        proof_binding = self._verify_operation_failure_proof(
            operation,
            request=normalized,
        )
        expected_proof_digest = (
            "sha256:" + hashlib.sha256(proof_binding).hexdigest()
        )
        if (
            str(operation.get("reconciliation_proof_digest") or "")
            != expected_proof_digest
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal Operation reconciliation proof digest drifted",
            )
        attempt = self._require_attempt(conn, attempt_id)
        graph_id = str(attempt["graph_id"])
        graph_revision = int(attempt["graph_revision"])
        node_id = str(attempt["node_id"])
        graph = self._require_graph(conn, graph_id, graph_revision)
        node = self._require_node(conn, graph_id, graph_revision, node_id)
        lease = self._require_lease(conn, lease_id)
        nodes = conn.execute(
            """
            SELECT * FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY node_id
            """,
            (graph_id, graph_revision),
        ).fetchall()
        budgets = conn.execute(
            """
            SELECT * FROM task_runtime_budget_reservation
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY budget_id
            """,
            (graph_id, graph_revision),
        ).fetchall()
        commands = conn.execute(
            """
            SELECT * FROM host_command_journal
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY command_id
            """,
            (graph_id, graph_revision),
        ).fetchall()
        projections = conn.execute(
            """
            SELECT projection_id FROM task_runtime_worker_projection
            WHERE attempt_id = ? LIMIT 1
            """,
            (attempt_id,),
        ).fetchone()
        effects = conn.execute(
            """
            SELECT effect_id FROM task_runtime_acceptance_effect_intent
            WHERE attempt_id = ? LIMIT 1
            """,
            (attempt_id,),
        ).fetchone()
        cancellation = conn.execute(
            """
            SELECT intent_ref FROM task_runtime_graph_cancellation_intent
            WHERE graph_id = ? AND graph_revision = ? LIMIT 1
            """,
            (graph_id, graph_revision),
        ).fetchone()
        reason_code = str(
            operation.get("recovery_reason_code") or ""
        ).strip().upper()
        error_ref = self._operation_failure_error_ref(
            reservation_id=reservation_id,
            attempt_id=attempt_id,
            reason_code=reason_code,
            proof_ref=normalized["attestation_ref"],
            proof_digest=expected_proof_digest,
        )
        graph_wide_terminal = len(nodes) == 1
        exact = (
            bool(nodes)
            and len(budgets) == 1
            and len(commands) == 1
            and projections is None
            and effects is None
            and cancellation is None
            and str(attempt["status"]) == AttemptStatus.FAILED.value
            and str(attempt["error_ref"] or "") == error_ref
            and str(node["status"]) == TaskNodeStatus.FAILED.value
            and str(lease["status"]) == LeaseStatus.EXPIRED.value
            and lease["released_ts_ms"] is not None
            and str(lease["attempt_id"]) == attempt_id
            and str(lease["owner_id"] or "")
            == str(operation.get("owner_id") or "")
            and int(lease["fencing_token"] or 0)
            == int(operation.get("fencing_token") or 0)
            and str(commands[0]["graph_id"]) == graph_id
            and int(commands[0]["graph_revision"]) == graph_revision
            and _REASON_PATTERN.fullmatch(reason_code) is not None
        )
        if not exact:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "reconciled terminal failure Task lineage drifted",
            )
        settlement_scope = "GRAPH_TERMINAL" if graph_wide_terminal else "NODE_ONLY"
        graph_settlement_status = "COMMITTED"
        parallel_settlement_ref: str | None = None
        authority_kind = _FAILURE_AUTHORITY_KIND
        if graph_wide_terminal:
            if (
                str(graph["status"]) != TaskGraphStatus.FAILED.value
                or str(budgets[0]["status"])
                != BudgetReservationStatus.RELEASED.value
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "single-node failure Graph or Budget terminal drifted",
                )
        else:
            authority_kind = _NODE_FAILURE_AUTHORITY_KIND
            invalid_declared_ref = (
                _find_invalid_declared_parallel_settlement_decision_ref(
                    conn,
                    graph_id=graph_id,
                    graph_revision=graph_revision,
                )
            )
            if invalid_declared_ref is not None:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "multi-node failure declares an invalid parallel settlement",
                )
            decision_rows = conn.execute(
                """
                SELECT * FROM task_runtime_parallel_settlement_decision
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY graph_version_after, decision_ref
                """,
                (graph_id, graph_revision),
            ).fetchall()
            decision_records: list[
                tuple[dict[str, Any], Any]
            ] = []
            for decision_row in decision_rows:
                record = _parallel_settlement_decision_record(
                    decision_row
                )
                _assert_parallel_settlement_decision_outbox_binding(
                    conn,
                    record,
                )
                normalized_decision = (
                    normalize_parallel_settlement_decision(
                        record["payload"]
                    )
                )
                self._validate_parallel_decision_attempt_lineage(
                    conn,
                    decision=normalized_decision,
                    graph_id=graph_id,
                    revision=graph_revision,
                    require_latest=False,
                    reason_code=TASK_STORE_DURABLE_DATA_INVALID,
                )
                decision_records.append(
                    (record, normalized_decision)
                )
            graph_status = str(graph["status"])
            budget_status = str(budgets[0]["status"])
            if (
                graph_status == TaskGraphStatus.ACTIVE.value
                and budget_status
                in {
                    BudgetReservationStatus.ACTIVE.value,
                    BudgetReservationStatus.EXCEEDED.value,
                }
            ):
                if any(
                    int(record["graph_version_after"])
                    >= int(graph["status_version"])
                    for record, _ in decision_records
                ):
                    raise TaskStoreError(
                        TASK_STORE_DURABLE_DATA_INVALID,
                        "active multi-node failure has a non-historical settlement",
                    )
                graph_settlement_status = "PENDING"
            elif (
                graph_status
                in {
                    TaskGraphStatus.FAILED.value,
                    TaskGraphStatus.BLOCKED.value,
                }
                and budget_status == BudgetReservationStatus.RELEASED.value
            ):
                terminal_decisions = [
                    item
                    for item in decision_records
                    if int(item[0]["graph_version_after"])
                    == int(graph["status_version"])
                ]
                if len(terminal_decisions) != 1:
                    raise TaskStoreError(
                        TASK_STORE_DURABLE_DATA_INVALID,
                        "terminal multi-node failure requires one latest parallel settlement",
                    )
                decision_record, decision = terminal_decisions[0]
                selected = next(
                    (
                        outcome
                        for outcome in decision.payload["outcomes"]
                        if outcome["node_id"] == node_id
                    ),
                    None,
                )
                budget_terminal = (
                    graph_status == TaskGraphStatus.BLOCKED.value
                )
                expected_decision_kind = (
                    "BUDGET" if budget_terminal else "FAILURE"
                )
                expected_selected_status = (
                    "BUDGET_BLOCKED" if budget_terminal else "FAILED"
                )
                if (
                    decision.payload["decision_kind"]
                    != expected_decision_kind
                    or decision.payload["graph_target_status"]
                    != graph_status
                    or decision.payload["budget_target_status"] != "RELEASED"
                    or decision.payload["budget_id"]
                    != str(budgets[0]["budget_id"])
                    or int(decision_record["graph_version_after"])
                    != int(graph["status_version"])
                    or int(decision_record["budget_version_after"])
                    != int(budgets[0]["status_version"])
                    or not isinstance(selected, Mapping)
                    or selected["attempt_id"] != attempt_id
                    or selected["status"] != expected_selected_status
                    or error_ref not in selected["evidence_refs"]
                ):
                    raise TaskStoreError(
                        TASK_STORE_DURABLE_DATA_INVALID,
                        "parallel failure settlement does not bind the reconciled node",
                    )
                if budget_terminal:
                    winner, _dimensions = _assert_terminal_budget_authority(
                        conn,
                        budget_row=budgets[0],
                        terminal_version=int(
                            decision.payload[
                                "budget_status_version_before"
                            ]
                        ),
                    )
                    if (
                        str(winner["node_id"]) != node_id
                        or str(winner["attempt_id"]) != attempt_id
                        or str(winner["usage_id"])
                        not in tuple(selected["evidence_refs"])
                    ):
                        raise TaskStoreError(
                            TASK_STORE_DURABLE_DATA_INVALID,
                            "parallel Budget settlement does not bind the "
                            "signed failed node",
                        )
                    self._assert_node_budget_usage_replay_in_transaction(
                        conn,
                        parent_budget_id=str(budgets[0]["budget_id"]),
                        attempt_id=attempt_id,
                        usage_id=str(winner["usage_id"]),
                        accepted=False,
                        parent_outbox_id=str(winner["outbox_id"]),
                    )
                parallel_settlement_ref = decision.decision_ref
            else:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "multi-node failure Graph or Budget settlement drifted",
                )
        self._assert_operation_failure_terminal_outboxes(
            conn,
            request=normalized,
            graph=graph,
            node=node,
            attempt=attempt,
            lease=lease,
            budget=budgets[0],
            error_ref=error_ref,
            graph_wide_terminal=graph_wide_terminal,
        )
        return {
            "authority_kind": authority_kind,
            "operation": operation,
            "attempt": dict(attempt),
            "node": _node_record(node),
            "graph": _graph_record(graph, list(nodes)),
            "lease": dict(lease),
            "budget": self._budget_record(budgets[0]),
            "command": dict(commands[0]),
            "failure_reason_code": reason_code,
            "error_ref": error_ref,
            "settlement_scope": settlement_scope,
            "graph_settlement_status": graph_settlement_status,
            "parallel_settlement_ref": parallel_settlement_ref,
        }

    def _assert_operation_failure_terminal_outboxes(
        self,
        conn: sqlite3.Connection,
        *,
        request: Mapping[str, Any],
        graph: sqlite3.Row,
        node: sqlite3.Row,
        attempt: sqlite3.Row,
        lease: sqlite3.Row,
        budget: sqlite3.Row,
        error_ref: str,
        graph_wide_terminal: bool,
    ) -> None:
        """Reopen every Outbox owned by this atomic failure phase."""

        graph_id = str(graph["graph_id"])
        graph_revision = int(graph["revision"])
        node_id = str(node["node_id"])
        attempt_id = str(attempt["attempt_id"])
        evidence_refs = (error_ref, request["attestation_ref"])
        messages = [
            (
                self._operation_failure_reconciliation_outbox(
                    request=request,
                    attempt_id=attempt_id,
                ),
                (
                    "OPERATION_RESERVATION",
                    request["reservation_id"],
                    request["expected_version"] + 1,
                ),
                "OPERATION_RECOVERY_RELEASE",
                None,
            ),
            (
                self._operation_failure_outbox(
                    kind="attempt",
                    aggregate_type="TASK_ATTEMPT",
                    aggregate_id=attempt_id,
                    aggregate_version=int(attempt["status_version"]),
                    event_type="TASK_ATTEMPT_FAILED",
                    attempt_id=attempt_id,
                    evidence_refs=evidence_refs,
                ),
                (
                    "TASK_ATTEMPT",
                    attempt_id,
                    int(attempt["status_version"]),
                ),
                "TASK_ATTEMPT_FAILED",
                None,
            ),
            (
                self._operation_failure_outbox(
                    kind="node",
                    aggregate_type="TASK_NODE",
                    aggregate_id=(
                        f"{graph_id}@{graph_revision}/{node_id}"
                    ),
                    aggregate_version=int(node["status_version"]),
                    event_type="TASK_NODE_FAILED",
                    attempt_id=attempt_id,
                    evidence_refs=evidence_refs,
                ),
                (
                    "TASK_NODE",
                    f"{graph_id}@{graph_revision}/{node_id}",
                    int(node["status_version"]),
                ),
                "TASK_NODE_FAILED",
                None,
            ),
            (
                self._operation_failure_outbox(
                    kind="lease",
                    aggregate_type="TASK_LEASE",
                    aggregate_id=str(lease["lease_id"]),
                    aggregate_version=int(lease["status_version"]),
                    event_type="TASK_LEASE_EXPIRED",
                    attempt_id=attempt_id,
                    evidence_refs=evidence_refs,
                ),
                (
                    "TASK_LEASE",
                    str(lease["lease_id"]),
                    int(lease["status_version"]),
                ),
                "TASK_LEASE_EXPIRED",
                None,
            ),
        ]
        if graph_wide_terminal:
            messages.extend(
                (
                    (
                        self._operation_failure_outbox(
                            kind="graph",
                            aggregate_type="TASK_GRAPH",
                            aggregate_id=graph_id,
                            graph_revision=graph_revision,
                            aggregate_version=int(graph["status_version"]),
                            event_type="TASK_GRAPH_FAILED",
                            attempt_id=attempt_id,
                            evidence_refs=evidence_refs,
                        ),
                        (
                            "TASK_GRAPH",
                            graph_id,
                            int(graph["status_version"]),
                        ),
                        "TASK_GRAPH_FAILED",
                        graph_revision,
                    ),
                    (
                        self._operation_failure_outbox(
                            kind="budget",
                            aggregate_type="BUDGET_RESERVATION",
                            aggregate_id=str(budget["budget_id"]),
                            aggregate_version=int(budget["status_version"]),
                            event_type="BUDGET_RESERVATION_RELEASED",
                            attempt_id=attempt_id,
                            evidence_refs=evidence_refs,
                        ),
                        (
                            "BUDGET_RESERVATION",
                            str(budget["budget_id"]),
                            int(budget["status_version"]),
                        ),
                        "BUDGET_RESERVATION_RELEASED",
                        None,
                    ),
                )
            )
        for message, aggregate, event_type, revision in messages:
            self._assert_mutation_outbox_replay(
                conn,
                message,
                expected_aggregate=aggregate,
                expected_event_type=event_type,
                expected_attempt_id=attempt_id,
                expected_graph_revision=revision,
            )

    def _verify_operation_failure_proof(
        self,
        operation: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
    ) -> bytes:
        try:
            binding = build_reconciliation_attestation_binding(
                reservation_id=request["reservation_id"],
                business_scope=str(
                    operation.get("business_scope") or ""
                ),
                operation_fingerprint=str(
                    operation.get("operation_fingerprint") or ""
                ),
                recovery_version=request["expected_version"],
                reconciler_actor_id=request["reconciler_actor_id"],
                attestation_ref=request["attestation_ref"],
                recovery_action="RELEASE",
                reconciliation_outcome="NO_SIDE_EFFECT",
            )
        except OperationReconciliationContractError as exc:
            raise TaskStoreError(
                TASK_STORE_RECONCILIATION_PROOF_INVALID,
                str(exc),
            ) from exc
        verifier = self._reconciliation_proof_verifier
        if verifier is None:
            raise TaskStoreError(
                TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
                "operation failure reconciliation verifier is unavailable",
            )
        if (
            not verifier.has_key(request["attestation_key_id"])
            or not verifier.verify(
                key_id=request["attestation_key_id"],
                message=binding,
                signature=request["attestation_signature"],
            )
        ):
            raise TaskStoreError(
                TASK_STORE_RECONCILIATION_PROOF_INVALID,
                "operation failure reconciliation signature is invalid",
            )
        return binding

    @staticmethod
    def _normalize_operation_failure_request(
        *,
        reservation_id: str,
        expected_version: int,
        reconciler_actor_id: str,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
    ) -> dict[str, Any]:
        request = {
            "reservation_id": str(reservation_id or "").strip(),
            "expected_version": expected_version,
            "reconciler_actor_id": str(
                reconciler_actor_id or ""
            ).strip(),
            "attestation_ref": str(attestation_ref or "").strip(),
            "attestation_key_id": str(
                attestation_key_id or ""
            ).strip(),
            "attestation_signature": str(
                attestation_signature or ""
            ).strip(),
        }
        if (
            not request["reservation_id"]
            or isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 0
            or not request["reconciler_actor_id"]
            or not request["attestation_ref"]
            or not request["attestation_key_id"]
            or not _SIGNATURE_PATTERN.fullmatch(
                request["attestation_signature"]
            )
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "operation failure reconciliation request is invalid",
            )
        return request

    @staticmethod
    def _operation_failure_request_matches_terminal(
        operation: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
    ) -> bool:
        return (
            str(operation.get("reservation_id") or "")
            == request["reservation_id"]
            and str(operation.get("status") or "") == "RELEASED"
            and int(operation.get("status_version") or -1)
            == request["expected_version"] + 1
            and str(operation.get("reconciliation_action") or "")
            == "RELEASE"
            and str(operation.get("reconciliation_outcome") or "")
            == "NO_SIDE_EFFECT"
            and operation.get("receipt_ref") is None
            and str(operation.get("reconciler_actor_id") or "")
            == request["reconciler_actor_id"]
            and str(operation.get("reconciliation_proof_ref") or "")
            == request["attestation_ref"]
            and str(operation.get("reconciliation_proof_key_id") or "")
            == request["attestation_key_id"]
            and str(
                operation.get("reconciliation_proof_signature") or ""
            )
            == request["attestation_signature"]
            and int(
                operation.get("reconciliation_recovery_version") or -1
            )
            == request["expected_version"]
        )

    @staticmethod
    def _operation_failure_error_ref(
        *,
        reservation_id: str,
        attempt_id: str,
        reason_code: str,
        proof_ref: str,
        proof_digest: str,
    ) -> str:
        material = "\n".join(
            (
                str(reservation_id),
                str(attempt_id),
                str(reason_code),
                str(proof_ref),
                str(proof_digest),
            )
        )
        return (
            "taskerror://operation-failure/sha256/"
            + hashlib.sha256(material.encode("utf-8")).hexdigest()
        )

    @staticmethod
    def _operation_failure_reconciliation_outbox(
        *,
        request: Mapping[str, Any],
        attempt_id: str,
    ) -> OutboxMessage:
        try:
            outbox_id, idempotency_key = (
                build_operation_failure_outbox_identity(
                    reservation_id=request["reservation_id"],
                    recovery_version=request["expected_version"],
                    reconciler_actor_id=request["reconciler_actor_id"],
                    attestation_ref=request["attestation_ref"],
                    attestation_key_id=request["attestation_key_id"],
                )
            )
        except OperationReconciliationContractError as exc:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                str(exc),
            ) from exc
        version = int(request["expected_version"]) + 1
        return OutboxMessage.create(
            aggregate_type="OPERATION_RESERVATION",
            aggregate_id=request["reservation_id"],
            aggregate_version=version,
            event_type="OPERATION_RECOVERY_RELEASE",
            payload_ref=build_outbox_payload_ref(
                aggregate_type="OPERATION_RESERVATION",
                aggregate_id=request["reservation_id"],
                aggregate_version=version,
            ),
            idempotency_key=idempotency_key,
            outbox_id=outbox_id,
            attempt_id=attempt_id,
            evidence_refs=(request["attestation_ref"],),
        )

    @staticmethod
    def _operation_failure_outbox(
        *,
        kind: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        event_type: str,
        attempt_id: str,
        evidence_refs: tuple[str, ...],
        graph_revision: int | None = None,
    ) -> OutboxMessage:
        material = "\n".join(
            (
                kind,
                aggregate_type,
                aggregate_id,
                str(aggregate_version),
                str(graph_revision or ""),
                attempt_id,
                event_type,
                *evidence_refs,
            )
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return OutboxMessage.create(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            graph_revision=graph_revision,
            aggregate_version=int(aggregate_version),
            event_type=event_type,
            payload_ref=build_outbox_payload_ref(
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                graph_revision=graph_revision,
                aggregate_version=int(aggregate_version),
            ),
            idempotency_key=f"operation-failure:{digest}",
            outbox_id=f"outbox::operation-failure::{digest}",
            attempt_id=attempt_id,
            evidence_refs=evidence_refs,
        )


__all__ = ["TaskOperationFailureReconciliationStoreMixin"]
