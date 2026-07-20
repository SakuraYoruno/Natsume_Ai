"""Atomic closeout for one exact rejected COMMITTED worker checkpoint.

The original seal-bound Internal Agent path and the canonical single-node path
share one C-boundary SQL transaction.  Each mode validates its own immutable
command/projection authority before closing Attempt, Node, Graph, Lease and
Budget together.  This remains a rejected-usage terminal boundary, not a
generic unknown-effect reconciliation bypass.
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
    _validated_node_payload,
)
from host_brain.persistence.task_checkpoint_budget_terminal_read_store import (
    TaskCheckpointBudgetTerminalReadStoreMixin,
)
from host_brain.persistence.task_budget_usage_store import (
    _assert_terminal_budget_authority,
)
from host_brain.persistence.task_checkpoint_budget_route_authority import (
    canonical_checkpoint_route_authority_matches,
    requires_internal_agent_authority,
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
from natsume_contracts.task.v1 import HostCommand, TaskContractError


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_AUTHORITY_INTERNAL = "internal"
_AUTHORITY_CANONICAL = "canonical"
_AUTHORITY_MODES = frozenset({_AUTHORITY_INTERNAL, _AUTHORITY_CANONICAL})


class TaskInternalAgentBudgetRejectionStoreMixin(
    TaskCheckpointBudgetTerminalReadStoreMixin
):
    """Atomic budget rejection and exact rejected terminal authority."""

    def _reject_internal_agent_checkpoint_budget_bundle_uow(
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
        authority_mode: str = _AUTHORITY_INTERNAL,
    ) -> dict[str, Any]:
        """Atomically close an exact post-checkpoint budget rejection."""

        authority = self._validated_budget_rejection_authority_mode(
            authority_mode
        )
        attempt_ref, owner, lease_ref, failure_ref, usage_ref = (
            self._validated_internal_agent_budget_rejection_request(
                attempt_id=attempt_id,
                lease_id=lease_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                error_ref=error_ref,
                usage_id=usage_id,
                attempt_outbox=attempt_outbox,
            )
        )
        evidence_refs = tuple(attempt_outbox.evidence_refs)
        ts_ms = self._now()
        with self._transaction() as conn:
            attempt = self._require_attempt(conn, attempt_ref)
            if (
                str(attempt["status"]) == "FAILED"
                and int(attempt["status_version"]) == expected_attempt_version + 1
            ):
                return self._reopen_internal_agent_budget_rejection_mutation_uow(
                    conn,
                    attempt_id=attempt_ref,
                    reservation_id=reservation_id,
                    budget_id=budget_id,
                    lease_id=lease_ref,
                    owner_id=owner,
                    fencing_token=fencing_token,
                    expected_attempt_version=expected_attempt_version,
                    expected_node_version=expected_node_version,
                    expected_graph_version=expected_graph_version,
                    expected_budget_version=expected_budget_version,
                    expected_lease_version=expected_lease_version,
                    error_ref=failure_ref,
                    usage_id=usage_ref,
                    attempt_outbox=attempt_outbox,
                    authority_mode=authority,
                )

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
                authority_mode=authority,
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
                authority_mode=authority,
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

    def _reopen_internal_agent_budget_rejection_mutation(
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
        authority_mode: str = _AUTHORITY_INTERNAL,
    ) -> dict[str, Any]:
        """Read-only exact replay authority for one ambiguous C-boundary call."""

        authority = self._validated_budget_rejection_authority_mode(
            authority_mode
        )
        attempt_ref, owner, lease_ref, failure_ref, usage_ref = (
            self._validated_internal_agent_budget_rejection_request(
                attempt_id=attempt_id,
                lease_id=lease_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                error_ref=error_ref,
                usage_id=usage_id,
                attempt_outbox=attempt_outbox,
            )
        )
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            reopened = self._reopen_internal_agent_budget_rejection_mutation_uow(
                conn,
                attempt_id=attempt_ref,
                reservation_id=reservation_id,
                budget_id=budget_id,
                lease_id=lease_ref,
                owner_id=owner,
                fencing_token=fencing_token,
                expected_attempt_version=expected_attempt_version,
                expected_node_version=expected_node_version,
                expected_graph_version=expected_graph_version,
                expected_budget_version=expected_budget_version,
                expected_lease_version=expected_lease_version,
                error_ref=failure_ref,
                usage_id=usage_ref,
                attempt_outbox=attempt_outbox,
                authority_mode=authority,
            )
            conn.commit()
            return reopened
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _reopen_internal_agent_budget_rejection_mutation_uow(
        self,
        conn: sqlite3.Connection,
        *,
        attempt_id: str,
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
        authority_mode: str = _AUTHORITY_INTERNAL,
    ) -> dict[str, Any]:
        authority = self._validated_budget_rejection_authority_mode(
            authority_mode
        )
        attempt = self._require_attempt(conn, attempt_id)
        if (
            str(attempt["status"]) != "FAILED"
            or int(attempt["status_version"]) != expected_attempt_version + 1
        ):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "Internal Agent budget rejection terminal does not match the call",
            )
        evidence_refs = tuple(attempt_outbox.evidence_refs)
        replay = self._replay_running_attempt_failure_bundle(
            conn,
            attempt=attempt,
            attempt_id=attempt_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            expected_attempt_version=expected_attempt_version,
            expected_node_version=expected_node_version,
            expected_graph_version=expected_graph_version,
            error_ref=error_ref,
            graph_target_status=TaskGraphStatus.BLOCKED,
            attempt_outbox=attempt_outbox,
            evidence_refs=evidence_refs,
        )
        self._validate_internal_agent_rejected_usage_uow(
            conn,
            reservation_id=reservation_id,
            budget_id=budget_id,
            attempt_id=attempt_id,
            expected_budget_version=expected_budget_version,
            expected_budget_status="RELEASED",
            expected_usage_id=usage_id,
            lease_id=lease_id,
            expected_lease_status="RELEASED",
            expected_lease_version=expected_lease_version + 1,
            owner_id=owner_id,
            fencing_token=fencing_token,
            authority_mode=authority,
        )
        if (
            str(replay["lease"].get("lease_id") or "") != lease_id
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
            attempt_id=attempt_id,
            evidence_refs=evidence_refs,
            authority_mode=authority,
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
            expected_attempt_id=attempt_id,
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
            "budget": self._budget_record(self._require_budget(conn, budget_id)),
            "usage_id": usage_id,
            "replayed": True,
        }

    def _validated_internal_agent_budget_rejection_request(
        self,
        *,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        error_ref: str,
        usage_id: str,
        attempt_outbox: OutboxMessage,
    ) -> tuple[str, str, str, str, str]:
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
        return attempt_ref, owner, lease_ref, failure_ref, usage_ref

    @staticmethod
    def _validated_budget_rejection_authority_mode(value: str) -> str:
        authority = str(value or "").strip().lower()
        if authority not in _AUTHORITY_MODES:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "checkpoint budget rejection authority mode is invalid",
            )
        return authority

    @staticmethod
    def _validated_canonical_host_command(
        command: sqlite3.Row,
    ) -> HostCommand:
        try:
            payload = _loads_object(
                str(command["command_json"]),
                field_name="canonical HostCommand",
            )
            typed = HostCommand.from_mapping(payload)
        except (TaskContractError, TypeError, ValueError) as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal rejected budget HostCommand is corrupt",
            ) from exc
        digest = "sha256:" + hashlib.sha256(
            _dumps(payload).encode("utf-8")
        ).hexdigest()
        row_bindings = (
            ("command_id", typed.command_id),
            ("schema_version", typed.schema_version),
            ("request_id", typed.request_id),
            ("surface", typed.surface.value),
            ("principal_id", typed.principal_id),
            ("session_id", typed.session_id),
            ("ingress_claim_ref", typed.ingress_claim_ref),
            ("ingress_audit_ref", typed.ingress_audit_ref),
            ("governor_grant_ref", typed.governor_grant_ref),
            ("input_ref", typed.input_ref),
            ("input_sha256", typed.input_sha256),
            ("root_work_order_id", typed.root_work_order_id),
            ("graph_id", typed.graph_id),
            ("graph_revision", typed.graph_revision),
            ("route_type", typed.route_type),
            ("target_id", typed.target_id),
            ("business_scope", typed.business_scope),
            ("operation_fingerprint", typed.operation_fingerprint),
        )
        if (
            typed.to_dict() != payload
            or digest != str(command["command_digest"])
            or any(command[field] != expected for field, expected in row_bindings)
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal rejected budget HostCommand authority drifted",
            )
        return typed

    @staticmethod
    def _internal_rejected_budget_outbox(
        *,
        budget_id: str,
        aggregate_version: int,
        attempt_id: str,
        evidence_refs: tuple[str, ...],
        authority_mode: str = _AUTHORITY_INTERNAL,
    ) -> OutboxMessage:
        authority = (
            TaskInternalAgentBudgetRejectionStoreMixin
            ._validated_budget_rejection_authority_mode(authority_mode)
        )
        prefix = (
            "internal-agent-budget-rejected"
            if authority == _AUTHORITY_INTERNAL
            else "committed-checkpoint-budget-rejected"
        )
        key = f"{prefix}:{attempt_id}:{budget_id}:{aggregate_version}"
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
        authority_mode: str = _AUTHORITY_INTERNAL,
    ) -> str:
        authority = self._validated_budget_rejection_authority_mode(
            authority_mode
        )
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
        typed_command = (
            self._validated_canonical_host_command(command)
            if authority == _AUTHORITY_CANONICAL
            else None
        )
        try:
            work_order_payload = _loads_object(
                str(command["work_order_json"]),
                field_name=(
                    "canonical WorkOrder"
                    if authority == _AUTHORITY_CANONICAL
                    else "Internal Agent WorkOrder"
                ),
            )
            work_order = WorkOrderV3.from_mapping(
                work_order_payload
            )
        except (ContractViolation, TypeError, ValueError) as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal rejected budget WorkOrder is corrupt",
            ) from exc
        if authority == _AUTHORITY_CANONICAL:
            work_order_digest = "sha256:" + hashlib.sha256(
                _dumps(work_order_payload).encode("utf-8")
            ).hexdigest()
            if work_order_digest != str(command["work_order_digest"]):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "terminal rejected budget WorkOrder authority drifted",
                )
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
                (
                    "Internal Agent rejected checkpoint requires exactly one TaskNode"
                    if authority == _AUTHORITY_INTERNAL
                    else "committed checkpoint budget rejection requires exactly one TaskNode"
                ),
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
        if authority == _AUTHORITY_INTERNAL:
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
        if authority == _AUTHORITY_INTERNAL:
            route_authority_exact = (
                str(command["surface"]) == "internal"
                and str(command["route_type"]) == "delegate"
                and str(command["target_id"]) == "agent_runtime"
                and str(node_payload.get("route") or "")
                == "delegate:agent_runtime"
                and str(projection["route_type"]) == "delegate"
                and str(projection["target_id"]) == "agent_runtime"
            )
        else:
            if typed_command is None:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "terminal rejected budget lacks canonical HostCommand authority",
                )
            route_authority_exact = canonical_checkpoint_route_authority_matches(
                command_surface=command["surface"],
                command_route=typed_command.route_type,
                command_target=typed_command.target_id,
                node_route=node_payload.get("route"),
                projection_route=projection["route_type"],
                projection_target=projection["target_id"],
            )
        work_order_operation_exact = (
            work_order.business_idempotency_scope
            == str(reservation["business_scope"])
            and work_order.operation_fingerprint
            == str(reservation["operation_fingerprint"])
        )
        if authority == _AUTHORITY_CANONICAL:
            # The canonical HostCommand is the required Operation authority.
            # Older admitted WorkOrderV3 rows legitimately leave these two
            # compatibility fields empty; a supplied value must still agree.
            work_order_operation_exact = (
                work_order.business_idempotency_scope
                in {"", str(reservation["business_scope"])}
                and work_order.operation_fingerprint
                in {"", str(reservation["operation_fingerprint"])}
            )
        exact = (
            str(reservation["status"]) == "COMMITTED"
            and str(reservation["attempt_id"]) == attempt_id
            and str(reservation["receipt_ref"] or "")
            == str(projection["projection_id"])
            and route_authority_exact
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
            and work_order_operation_exact
            and str(projection["command_id"]) == str(command["command_id"])
            and str(projection["request_id"]) == str(command["request_id"])
            and str(projection["graph_id"]) == str(attempt["graph_id"])
            and int(projection["graph_revision"]) == int(attempt["graph_revision"])
            and str(projection["node_id"]) == str(attempt["node_id"])
            and str(projection["attempt_id"]) == attempt_id
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
