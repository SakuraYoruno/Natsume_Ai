"""Durable TaskNode verification decision and direct terminal authority."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Mapping

from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.contracts.agent_runtime.task_graph import TaskNodeStatus
from host_brain.contracts.agent_runtime.verification_contract import (
    VerificationResult,
)
from host_brain.persistence.task_node_verification_common import (
    NODE_VERIFICATION_REQUIRED_COLUMNS,
    NODE_VERIFICATION_REQUIRED_TRIGGERS,
    NODE_VERIFICATION_SCHEMA_VERSION,
    non_negative_version,
    required_text,
    validated_verification,
    verification_ref,
    verification_subject,
    verification_target,
)
from host_brain.persistence.task_record_codec import (
    _dumps,
    _node_record,
    _outbox_record,
)
from host_brain.persistence.task_store_types import (
    AttemptStatus,
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_NOT_FOUND,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
)


class TaskNodeVerificationStoreMixin:
    """Persist and replay verification before any Acceptance decision."""

    def assert_node_verification_ready(self) -> None:
        conn = self._connect()
        try:
            columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(task_runtime_node_verification_decision)"
                ).fetchall()
            }
            triggers = {
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'trigger'
                      AND name LIKE 'trg_task_runtime_node_verification_%'
                    """
                ).fetchall()
            }
        finally:
            conn.close()
        missing_columns = sorted(NODE_VERIFICATION_REQUIRED_COLUMNS - columns)
        missing_triggers = sorted(NODE_VERIFICATION_REQUIRED_TRIGGERS - triggers)
        if missing_columns or missing_triggers:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task node verification migration 0133 is not applied: "
                f"columns={missing_columns},triggers={missing_triggers}",
            )

    def get_node_verification_decision(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        node_version_after: int | None = None,
    ) -> dict[str, Any]:
        self.assert_node_verification_ready()
        graph_ref, revision, node_ref = verification_subject(
            graph_id,
            graph_revision,
            node_id,
        )
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            if node_version_after is None:
                row = conn.execute(
                    """
                    SELECT * FROM task_runtime_node_verification_decision
                    WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                    ORDER BY node_version_after DESC, verification_ref DESC
                    LIMIT 1
                    """,
                    (graph_ref, revision, node_ref),
                ).fetchone()
            else:
                if (
                    isinstance(node_version_after, bool)
                    or not isinstance(node_version_after, int)
                    or node_version_after < 1
                ):
                    raise TaskStoreError(
                        TASK_STORE_INVALID,
                        "node_version_after must be a positive integer",
                    )
                row = conn.execute(
                    """
                    SELECT * FROM task_runtime_node_verification_decision
                    WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                      AND node_version_after = ?
                    """,
                    (
                        graph_ref,
                        revision,
                        node_ref,
                        int(node_version_after),
                    ),
                ).fetchone()
            if row is None:
                raise TaskStoreError(
                    TASK_STORE_NOT_FOUND,
                    "TaskNode verification decision not found: "
                    f"{graph_ref}@{revision}/{node_ref}",
                )
            return self._node_verification_record(conn, row)
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    def commit_node_verification(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        attempt_id: str,
        reservation_id: str,
        expected_node_version: int,
        target_status: TaskNodeStatus | str,
        verification_result: VerificationResult | Mapping[str, Any],
        node_outbox: OutboxMessage,
    ) -> dict[str, Any]:
        """Atomically persist one VerificationResult and Node transition."""

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
        target = verification_target(target_status)
        expected_version = non_negative_version(expected_node_version)
        if not isinstance(node_outbox, OutboxMessage):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "node verification requires a Node OutboxMessage",
            )
        node_outbox.validate()
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
                target=target,
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
                (graph_ref, revision, node_ref, expected_version + 1),
            ).fetchone()
            if existing is not None:
                decision = self._node_verification_record(conn, existing)
                self._assert_verification_replay(
                    decision,
                    verification=verification,
                    target=target,
                    attempt_id=attempt_ref,
                    reservation_id=reservation_ref,
                    node_outbox=node_outbox,
                    conn=conn,
                )
                return {
                    "node": _node_record(
                        self._require_node(conn, graph_ref, revision, node_ref)
                    ),
                    "decision": decision,
                    "replayed": True,
                }
            self._assert_verification_execution_authority(
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
                node_id=node_ref,
                attempt_id=attempt_ref,
                reservation_id=reservation_ref,
                verification=verification,
                require_terminal_attempt=False,
            )
            node = self._transition_node_in_transaction(
                conn,
                graph_id=graph_ref,
                graph_revision=revision,
                node_id=node_ref,
                expected_version=expected_version,
                target_status=target,
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
                node_version_before=expected_version,
                target=target,
                verification=verification,
                node_outbox=node_outbox,
                ts_ms=ts_ms,
            )
            return {
                "node": node,
                "decision": decision,
                "replayed": False,
            }

    def _insert_node_verification_decision(
        self,
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        node_version_before: int,
        target: TaskNodeStatus,
        verification: VerificationResult,
        node_outbox: OutboxMessage,
        ts_ms: int,
    ) -> dict[str, Any]:
        result_json = _dumps(verification.to_dict())
        result_sha256 = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
        reference = verification_ref(
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            attempt_id=attempt_id,
            node_version_after=node_version_before + 1,
            result_sha256=result_sha256,
            node_outbox_id=node_outbox.outbox_id,
        )
        try:
            conn.execute(
                """
                INSERT INTO task_runtime_node_verification_decision(
                    verification_ref, schema_version, verification_id,
                    graph_id, graph_revision, node_id, attempt_id,
                    node_version_before, node_version_after, target_status,
                    result_json, result_sha256, evidence_refs_json,
                    node_outbox_id, created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reference,
                    NODE_VERIFICATION_SCHEMA_VERSION,
                    verification.verification_id,
                    graph_id,
                    graph_revision,
                    node_id,
                    attempt_id,
                    node_version_before,
                    node_version_before + 1,
                    target.value,
                    result_json,
                    result_sha256,
                    _dumps(list(verification.evidence_refs)),
                    node_outbox.outbox_id,
                    int(ts_ms),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "TaskNode verification decision identity already exists",
            ) from exc
        row = conn.execute(
            """
            SELECT * FROM task_runtime_node_verification_decision
            WHERE verification_ref = ?
            """,
            (reference,),
        ).fetchone()
        if row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "TaskNode verification decision was not durable",
            )
        return self._node_verification_record(conn, row)

    def _node_verification_record(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        try:
            payload = json.loads(str(row["result_json"]))
            verification = VerificationResult.from_mapping(payload)
        except (json.JSONDecodeError, ContractViolation, TypeError, ValueError) as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"durable VerificationResult is invalid: {exc}",
            ) from exc
        canonical_json = _dumps(verification.to_dict())
        digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        target = verification_target(str(row["target_status"]))
        evidence_refs = tuple(
            item
            for item in json.loads(str(row["evidence_refs_json"]))
            if isinstance(item, str) and item
        )
        expected_ref = verification_ref(
            graph_id=str(row["graph_id"]),
            graph_revision=int(row["graph_revision"]),
            node_id=str(row["node_id"]),
            attempt_id=str(row["attempt_id"]),
            node_version_after=int(row["node_version_after"]),
            result_sha256=digest,
            node_outbox_id=str(row["node_outbox_id"]),
        )
        if (
            str(row["schema_version"]) != NODE_VERIFICATION_SCHEMA_VERSION
            or str(row["verification_ref"]) != expected_ref
            or str(row["verification_id"]) != verification.verification_id
            or str(row["result_json"]) != canonical_json
            or str(row["result_sha256"]) != digest
            or tuple(verification.evidence_refs) != evidence_refs
            or verification.task_graph_id != str(row["graph_id"])
            or verification.task_node_id != str(row["node_id"])
            or verification.attempt_id != str(row["attempt_id"])
            or int(row["node_version_after"])
            != int(row["node_version_before"]) + 1
            or (
                target is TaskNodeStatus.VERIFIED
                and not verification.allows_verified_transition
            )
            or (
                target is TaskNodeStatus.VERIFICATION_FAILED
                and verification.allows_verified_transition
            )
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "TaskNode verification decision lineage drifted",
            )
        attempt = self._require_attempt(conn, str(row["attempt_id"]))
        node = self._require_node(
            conn,
            str(row["graph_id"]),
            int(row["graph_revision"]),
            str(row["node_id"]),
        )
        outbox_row = conn.execute(
            "SELECT * FROM task_runtime_outbox WHERE outbox_id = ?",
            (str(row["node_outbox_id"]),),
        ).fetchone()
        if outbox_row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "TaskNode verification decision lost its Outbox",
            )
        outbox = _outbox_record(outbox_row)
        outbox_evidence = tuple(outbox.get("evidence_refs") or ())
        if (
            str(attempt["graph_id"]) != str(row["graph_id"])
            or int(attempt["graph_revision"]) != int(row["graph_revision"])
            or str(attempt["node_id"]) != str(row["node_id"])
            or int(node["status_version"]) < int(row["node_version_after"])
            or (
                int(node["status_version"]) == int(row["node_version_after"])
                and str(node["status"]) != target.value
            )
            or outbox.get("aggregate_type") != "TASK_NODE"
            or outbox.get("aggregate_id")
            != (
                f"{row['graph_id']}@{int(row['graph_revision'])}/"
                f"{row['node_id']}"
            )
            or int(outbox.get("aggregate_version") or -1)
            != int(row["node_version_after"])
            or outbox.get("event_type") != f"TASK_NODE_{target.value}"
            or outbox.get("attempt_id") != str(row["attempt_id"])
            or set(evidence_refs) - set(outbox_evidence)
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "TaskNode verification decision state or Outbox drifted",
            )
        return {
            **dict(row),
            "verification_result": verification.to_dict(),
            "evidence_refs": evidence_refs,
        }

    def _assert_verification_execution_authority(
        self,
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        reservation_id: str,
        verification: VerificationResult,
        require_terminal_attempt: bool,
    ) -> None:
        attempt = self._require_attempt(conn, attempt_id)
        reservation = self._require_operation_reservation(conn, reservation_id)
        latest = conn.execute(
            """
            SELECT attempt_id FROM task_runtime_attempt
            WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
            ORDER BY attempt_no DESC, attempt_id DESC
            LIMIT 1
            """,
            (graph_id, graph_revision, node_id),
        ).fetchone()
        allowed_attempt_statuses = (
            {AttemptStatus.SUCCEEDED.value}
            if require_terminal_attempt
            else {
                AttemptStatus.RUNNING.value,
                AttemptStatus.SUCCEEDED.value,
            }
        )
        receipt_ref = str(reservation["receipt_ref"] or "").strip()
        if (
            str(attempt["graph_id"]) != graph_id
            or int(attempt["graph_revision"]) != graph_revision
            or str(attempt["node_id"]) != node_id
            or str(attempt["status"]) not in allowed_attempt_statuses
            or latest is None
            or str(latest["attempt_id"]) != attempt_id
            or str(reservation["attempt_id"]) != attempt_id
            or str(reservation["status"]) != "COMMITTED"
            or not receipt_ref
            or receipt_ref not in verification.evidence_refs
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "verification decision lacks the exact latest Attempt, committed "
                "Operation, or worker receipt authority",
            )
        if require_terminal_attempt:
            active_lease = conn.execute(
                """
                SELECT lease_id FROM task_runtime_lease
                WHERE attempt_id = ? AND status = 'ACTIVE'
                LIMIT 1
                """,
                (attempt_id,),
            ).fetchone()
            if active_lease is not None:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "verification failure terminalization requires a closed Lease",
                )

    def _assert_verification_replay(
        self,
        decision: Mapping[str, Any],
        *,
        verification: VerificationResult,
        target: TaskNodeStatus,
        attempt_id: str,
        reservation_id: str,
        node_outbox: OutboxMessage,
        conn: sqlite3.Connection,
    ) -> None:
        self._assert_verification_execution_authority(
            conn,
            graph_id=str(decision["graph_id"]),
            graph_revision=int(decision["graph_revision"]),
            node_id=str(decision["node_id"]),
            attempt_id=attempt_id,
            reservation_id=reservation_id,
            verification=verification,
            require_terminal_attempt=False,
        )
        if (
            str(decision["target_status"]) != target.value
            or decision["verification_result"] != verification.to_dict()
            or str(decision["attempt_id"]) != attempt_id
            or str(decision["node_outbox_id"]) != node_outbox.outbox_id
        ):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "TaskNode verification replay differs from durable decision",
            )
        self._assert_mutation_outbox_replay(
            conn,
            node_outbox,
            expected_aggregate=(
                "TASK_NODE",
                (
                    f"{decision['graph_id']}@{int(decision['graph_revision'])}/"
                    f"{decision['node_id']}"
                ),
                int(decision["node_version_after"]),
            ),
            expected_event_type=f"TASK_NODE_{target.value}",
            expected_attempt_id=attempt_id,
        )

__all__ = [
    "NODE_VERIFICATION_REQUIRED_COLUMNS",
    "NODE_VERIFICATION_REQUIRED_TRIGGERS",
    "NODE_VERIFICATION_SCHEMA_VERSION",
    "TaskNodeVerificationStoreMixin",
]
