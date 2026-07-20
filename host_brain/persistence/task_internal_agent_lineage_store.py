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


class TaskInternalAgentLineageStoreMixin:
    """Exact Internal Agent direct/takeover Lease lineage authority."""

    def resolve_internal_agent_completion_lineage(
        self,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        historical_lease_id: str,
        historical_lease_owner: str,
        historical_fencing_token: int,
    ) -> dict[str, Any]:
        """Reopen the exact direct-or-takeover lineage of an accepted result."""

        return self._resolve_internal_agent_terminal_lineage(
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            attempt_id=attempt_id,
            historical_lease_id=historical_lease_id,
            historical_lease_owner=historical_lease_owner,
            historical_fencing_token=historical_fencing_token,
            expected_graph_status="COMPLETED",
            expected_node_status="ACCEPTED",
            expected_attempt_status="SUCCEEDED",
            require_takeover=False,
        )

    def resolve_internal_agent_takeover_lineage(
        self,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        historical_lease_id: str,
        historical_lease_owner: str,
        historical_fencing_token: int,
        expected_graph_status: str,
        expected_node_status: str,
        expected_attempt_status: str,
    ) -> dict[str, Any]:
        """Reopen one exact historical→takeover terminal Lease lineage."""

        return self._resolve_internal_agent_terminal_lineage(
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            attempt_id=attempt_id,
            historical_lease_id=historical_lease_id,
            historical_lease_owner=historical_lease_owner,
            historical_fencing_token=historical_fencing_token,
            expected_graph_status=expected_graph_status,
            expected_node_status=expected_node_status,
            expected_attempt_status=expected_attempt_status,
            require_takeover=True,
        )

    def resolve_internal_agent_checkpoint_continuation_lineage(
        self,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        historical_lease_id: str,
        historical_lease_owner: str,
        historical_fencing_token: int,
        expected_node_status: str,
    ) -> dict[str, Any]:
        """Reopen the SUCCEEDED-before-Node-terminal continuation window."""

        if str(expected_node_status or "") not in {
            "RUNNING",
            "EXECUTED",
            "VERIFYING",
            "VERIFIED",
        }:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Internal Agent continuation TaskNode status is invalid",
            )
        return self._resolve_internal_agent_terminal_lineage(
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            attempt_id=attempt_id,
            historical_lease_id=historical_lease_id,
            historical_lease_owner=historical_lease_owner,
            historical_fencing_token=historical_fencing_token,
            expected_graph_status="ACTIVE",
            expected_node_status=expected_node_status,
            expected_attempt_status="SUCCEEDED",
            require_takeover=False,
        )

    def _resolve_internal_agent_terminal_lineage(
        self,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        historical_lease_id: str,
        historical_lease_owner: str,
        historical_fencing_token: int,
        expected_graph_status: str,
        expected_node_status: str,
        expected_attempt_status: str,
        require_takeover: bool,
    ) -> dict[str, Any]:
        identifiers = (
            str(graph_id or "").strip(),
            str(node_id or "").strip(),
            str(attempt_id or "").strip(),
            str(historical_lease_id or "").strip(),
            str(historical_lease_owner or "").strip(),
        )
        statuses = (
            str(expected_graph_status or "").strip(),
            str(expected_node_status or "").strip(),
            str(expected_attempt_status or "").strip(),
        )
        if (
            not all(identifiers)
            or type(graph_revision) is not int
            or graph_revision < 1
            or type(historical_fencing_token) is not int
            or historical_fencing_token < 1
            or not all(statuses)
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Internal Agent terminal lineage identity is invalid",
            )
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            resolved = self._resolve_internal_agent_terminal_lineage_uow(
                conn,
                graph_id=identifiers[0],
                graph_revision=graph_revision,
                node_id=identifiers[1],
                attempt_id=identifiers[2],
                historical_lease_id=identifiers[3],
                historical_lease_owner=identifiers[4],
                historical_fencing_token=historical_fencing_token,
                expected_graph_status=statuses[0],
                expected_node_status=statuses[1],
                expected_attempt_status=statuses[2],
                require_takeover=require_takeover,
            )
            conn.commit()
            return resolved
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _resolve_internal_agent_terminal_lineage_uow(
        self,
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        historical_lease_id: str,
        historical_lease_owner: str,
        historical_fencing_token: int,
        expected_graph_status: str,
        expected_node_status: str,
        expected_attempt_status: str,
        require_takeover: bool,
    ) -> dict[str, Any]:
        graph = self._require_graph(conn, graph_id, graph_revision)
        node = self._require_node(conn, graph_id, graph_revision, node_id)
        attempt = self._require_attempt(conn, attempt_id)
        historical = self._require_lease(conn, historical_lease_id)
        nodes = conn.execute(
            """
            SELECT * FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id
            """,
            (graph_id, graph_revision),
        ).fetchall()
        reservations = conn.execute(
            """
            SELECT * FROM task_runtime_idempotency_reservation
            WHERE attempt_id = ? ORDER BY reservation_id
            """,
            (attempt_id,),
        ).fetchall()
        leases = conn.execute(
            """
            SELECT * FROM task_runtime_lease
            WHERE attempt_id = ?
            ORDER BY fencing_token, acquired_ts_ms, lease_id
            """,
            (attempt_id,),
        ).fetchall()
        if len(nodes) != 1 or len(reservations) != 1 or not leases:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent terminal lineage cardinality drifted",
            )
        reservation = reservations[0]
        final_lease = leases[-1]
        projection_row = conn.execute(
            """
            SELECT * FROM task_runtime_worker_projection
            WHERE projection_id = ?
            """,
            (str(reservation["receipt_ref"] or ""),),
        ).fetchone()
        if projection_row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent terminal lineage lacks committed projection",
            )
        try:
            projection = validate_worker_projection_record(projection_row)
        except WorkerProjectionStoreError as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent terminal lineage projection is corrupt",
            ) from exc
        commands = conn.execute(
            """
            SELECT * FROM host_command_journal
            WHERE graph_id = ? AND graph_revision = ? ORDER BY command_id
            """,
            (graph_id, graph_revision),
        ).fetchall()
        command = commands[0] if len(commands) == 1 else None
        if command is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent terminal lineage HostCommand cardinality drifted",
            )
        try:
            node_payload = _validated_node_payload(node)
            work_order = WorkOrderV3.from_mapping(
                _loads_object(
                    str(command["work_order_json"]),
                    field_name="Internal Agent terminal WorkOrder",
                )
            )
        except (ContractViolation, TypeError, ValueError) as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent terminal WorkOrder/TaskNode is corrupt",
            ) from exc
        self._validate_internal_checkpoint_lease_lineage(
            conn,
            reservation=reservation,
            attempt=attempt,
            active_or_terminal_lease=final_lease,
            expected_lease_status="RELEASED",
            expected_lease_version=int(final_lease["status_version"]),
            owner_id=str(final_lease["owner_id"]),
            fencing_token=int(final_lease["fencing_token"]),
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
        exact = (
            str(graph["status"]) == expected_graph_status
            and str(node["status"]) == expected_node_status
            and str(attempt["status"]) == expected_attempt_status
            and str(attempt["graph_id"]) == graph_id
            and int(attempt["graph_revision"]) == graph_revision
            and str(attempt["node_id"]) == node_id
            and str(reservation["status"]) == "COMMITTED"
            and str(command["surface"]) == "internal"
            and str(command["route_type"]) == "delegate"
            and str(command["target_id"]) == "agent_runtime"
            and str(command["graph_id"]) == graph_id
            and int(command["graph_revision"]) == graph_revision
            and str(command["business_scope"])
            == str(reservation["business_scope"])
            and str(command["operation_fingerprint"])
            == str(reservation["operation_fingerprint"])
            and str(projection["command_id"]) == str(command["command_id"])
            and str(projection["graph_id"]) == graph_id
            and int(projection["graph_revision"]) == graph_revision
            and str(projection["node_id"]) == node_id
            and str(projection["attempt_id"]) == attempt_id
            and str(projection["route_type"]) == "delegate"
            and str(projection["target_id"]) == "agent_runtime"
            and str(node_payload.get("route") or "")
            == "delegate:agent_runtime"
            and str(reservation["receipt_ref"] or "")
            == str(projection["projection_id"])
            and str(reservation["lease_id"] or "") == historical_lease_id
            and str(reservation["owner_id"] or "") == historical_lease_owner
            and int(reservation["fencing_token"] or 0)
            == historical_fencing_token
            and str(historical["attempt_id"]) == attempt_id
            and str(historical["owner_id"]) == historical_lease_owner
            and int(historical["fencing_token"]) == historical_fencing_token
            and str(final_lease["status"]) == "RELEASED"
            and final_lease["released_ts_ms"] is not None
            and int(final_lease["released_ts_ms"])
            == int(attempt["completed_ts_ms"] or -1)
            and str(final_lease["owner_id"]) == str(attempt["owner_id"] or "")
            and (not require_takeover or historical_lease_id != str(final_lease["lease_id"]))
            and (
                historical_lease_id == str(final_lease["lease_id"])
                or (
                    str(historical["status"]) == "EXPIRED"
                    and historical["released_ts_ms"] is not None
                )
            )
        )
        if not exact:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent terminal lineage authority drifted",
            )
        return {
            "authority_kind": "INTERNAL_AGENT_TERMINAL_LINEAGE",
            "historical_lease": dict(historical),
            "completion_lease": dict(final_lease),
            "operation_reservation": self._operation_record(reservation),
            "projection": projection,
            "recovered": historical_lease_id != str(final_lease["lease_id"]),
        }

    def _validate_internal_checkpoint_lease_lineage(
        self,
        conn: sqlite3.Connection,
        *,
        reservation: sqlite3.Row,
        attempt: sqlite3.Row,
        active_or_terminal_lease: sqlite3.Row,
        expected_lease_status: str,
        expected_lease_version: int,
        owner_id: str,
        fencing_token: int,
    ) -> None:
        historical_ref = str(reservation["lease_id"] or "")
        if not historical_ref:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent COMMITTED reservation lacks its historical Lease",
            )
        historical = self._require_lease(conn, historical_ref)
        leases = conn.execute(
            """
            SELECT * FROM task_runtime_lease
            WHERE attempt_id = ?
            ORDER BY fencing_token, acquired_ts_ms, lease_id
            """,
            (str(attempt["attempt_id"]),),
        ).fetchall()
        current_ref = str(active_or_terminal_lease["lease_id"])
        historical_index = next(
            (
                index
                for index, item in enumerate(leases)
                if str(item["lease_id"]) == historical_ref
            ),
            -1,
        )
        current_index = next(
            (
                index
                for index, item in enumerate(leases)
                if str(item["lease_id"]) == current_ref
            ),
            -1,
        )
        lineage = (
            list(leases[historical_index : current_index + 1])
            if 0 <= historical_index <= current_index
            else []
        )
        fences = [int(item["fencing_token"]) for item in lineage]
        same_epoch = historical_ref == current_ref
        terminal_count = sum(
            str(item["status"]) == expected_lease_status for item in leases
        )
        prior_lineage = lineage[:-1]
        contiguous = bool(lineage) and all(
            right == left + 1
            for left, right in zip(fences, fences[1:], strict=False)
        )
        prior_exact = all(
            str(item["status"]) == "EXPIRED"
            and item["released_ts_ms"] is not None
            for item in prior_lineage
        ) and all(
            int(lineage[index + 1]["acquired_ts_ms"])
            >= int(item["released_ts_ms"])
            for index, item in enumerate(prior_lineage)
        )
        final_release_exact = (
            active_or_terminal_lease["released_ts_ms"] is not None
            if expected_lease_status == "RELEASED"
            else active_or_terminal_lease["released_ts_ms"] is None
        )
        exact = (
            bool(lineage)
            and historical_index == 0
            and current_index == len(leases) - 1
            and contiguous
            and len(fences) == len(set(fences))
            and prior_exact
            and str(historical["attempt_id"]) == str(attempt["attempt_id"])
            and str(historical["owner_id"])
            == str(reservation["owner_id"] or "")
            and int(historical["fencing_token"])
            == int(reservation["fencing_token"] or 0)
            and str(active_or_terminal_lease["attempt_id"])
            == str(attempt["attempt_id"])
            and str(active_or_terminal_lease["owner_id"]) == str(owner_id)
            and int(active_or_terminal_lease["fencing_token"])
            == int(fencing_token)
            and str(active_or_terminal_lease["status"]) == expected_lease_status
            and int(active_or_terminal_lease["status_version"])
            == expected_lease_version
            and int(active_or_terminal_lease["fencing_token"]) == fences[-1]
            and str(attempt["owner_id"] or "") == str(owner_id)
            and terminal_count == 1
            and final_release_exact
        )
        if same_epoch:
            exact = exact and (
                str(reservation["owner_id"] or "") == str(owner_id)
                and int(reservation["fencing_token"] or 0) == int(fencing_token)
            )
        else:
            exact = exact and (
                str(historical["status"]) == "EXPIRED"
                and historical["released_ts_ms"] is not None
                and int(active_or_terminal_lease["acquired_ts_ms"])
                >= int(prior_lineage[-1]["released_ts_ms"])
            )
        if not exact:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent checkpoint historical/takeover Lease lineage drifted",
            )

    @staticmethod
    def _validate_internal_checkpoint_projection_authority(
        projection: Mapping[str, Any],
        *,
        command: sqlite3.Row,
        work_order: WorkOrderV3,
        graph: sqlite3.Row,
        node_payload: Mapping[str, Any],
        reservation: sqlite3.Row,
        attempt: sqlite3.Row,
    ) -> None:
        effects = projection.get("acceptance_effect_intents")
        audit = projection.get("audit_refs")
        if (
            not isinstance(effects, list)
            or len(effects) != 1
            or not isinstance(effects[0], Mapping)
            or not isinstance(audit, Mapping)
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent checkpoint projection authority is incomplete",
            )
        effect = dict(effects[0])
        payload = effect.get("payload")
        if not isinstance(payload, Mapping):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent result effect payload is missing",
            )
        result = payload.get("result")
        result_payload = dict(result) if isinstance(result, Mapping) else {}
        expected_result_digest = "sha256:" + hashlib.sha256(
            _dumps(result_payload).encode("utf-8")
        ).hexdigest()
        protected = result_payload.get("protected_artifact")
        preview_ref = str(audit.get("action_preview_receipt_ref") or "")
        preview_event = str(audit.get("action_preview_event_id") or "")
        exact = (
            set(effect) == {"effect_type", "commit_scope", "payload"}
            and effect.get("effect_type") == "internal.agent.result_commit"
            and effect.get("commit_scope") == "NODE"
            and set(payload)
            == {
                "schema_version",
                "work_order_id",
                "target_id",
                "operation_fingerprint",
                "result_digest",
                "result",
            }
            and payload.get("schema_version")
            == "Natsume.internal.agent_result_proposal.v1"
            and payload.get("work_order_id") == work_order.work_order_id
            and payload.get("target_id") == "agent_runtime"
            and payload.get("operation_fingerprint")
            == str(reservation["operation_fingerprint"])
            and payload.get("result_digest") == expected_result_digest
            and isinstance(protected, Mapping)
            and set(protected) == {"schema_version", "ref", "digest"}
            and protected.get("schema_version")
            == "Natsume.agent.governed_artifact_stage.v1"
            and str(protected.get("ref") or "").startswith("agent-stage://")
            and _SHA256.fullmatch(str(protected.get("digest") or "")) is not None
            and str(projection.get("node_grant_ref") or "")
            == str(audit.get("node_grant_ref") or "")
            and _SHA256.fullmatch(str(audit.get("node_grant_digest") or ""))
            is not None
            and preview_ref.startswith("action-preview://")
            and preview_event.startswith("runtimeevent://")
            and str(command["governor_grant_ref"] or "")
            != str(projection.get("node_grant_ref") or "")
            and str(command["request_id"]) == str(projection["request_id"])
            and str(graph["root_work_order_id"]) == work_order.root_work_order_id
            and str(node_payload.get("node_id") or "") == str(attempt["node_id"])
        )
        if not exact:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent result effect/grant/Preview authority drifted",
            )


__all__ = ["TaskInternalAgentLineageStoreMixin"]
