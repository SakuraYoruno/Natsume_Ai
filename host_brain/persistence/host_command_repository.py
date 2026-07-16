"""Atomic Host command admission over the canonical TaskStore schema.

This adapter is intentionally narrow: one transaction binds a typed HostCommand,
canonical WorkOrderV3, ExecutorAssignment and TaskGraph, emits both required outbox
events, and (when present) attaches the committed Secure Ingress claim to the
new durable command.  It owns neither execution status nor delivery receipts;
those remain authoritative in migrations 0052/0054/0059.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import quote

from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    validate_task_graph_admission_state,
)
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    OutboxMessage,
    TaskStoreError,
    build_outbox_payload_ref,
)
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder, WorkOrderV3
from host_brain.core.ids import now_ms
from natsume_contracts.task.v1 import ExecutorAssignment, HostCommand


HOST_COMMAND_SCHEMA_NOT_READY = "HOST_COMMAND_SCHEMA_NOT_READY"
HOST_COMMAND_INVALID = "HOST_COMMAND_INVALID"
HOST_COMMAND_CONFLICT = "HOST_COMMAND_CONFLICT"
HOST_COMMAND_INGRESS_CONFLICT = "HOST_COMMAND_INGRESS_CONFLICT"
HOST_COMMAND_DURABLE_DATA_INVALID = "HOST_COMMAND_DURABLE_DATA_INVALID"

HOST_COMMAND_MIGRATION_ASSET_KEY = "0062#host_command_admission"
HOST_COMMAND_MIGRATION_SHA256 = "e003783038278ca82c7b8ee18fb87916ee7e6c109592a59a4f3579da378f5d59"
HOST_COMMAND_REVISION_MIGRATION_ASSET_KEY = "0088#host_command_graph_revision"
_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_HOST_COMMAND_COLUMNS = frozenset(
    {
        "command_id",
        "schema_version",
        "request_id",
        "surface",
        "principal_id",
        "session_id",
        "ingress_claim_ref",
        "ingress_audit_ref",
        "governor_grant_ref",
        "input_ref",
        "input_sha256",
        "route_type",
        "target_id",
        "business_scope",
        "operation_fingerprint",
        "root_work_order_id",
        "graph_id",
        "graph_revision",
        "assignment_id",
        "command_json",
        "command_digest",
        "work_order_json",
        "work_order_digest",
        "assignment_json",
        "assignment_digest",
        "graph_json",
        "graph_digest",
        "created_at_ms",
    }
)
_REQUIRED_INDEXES = frozenset(
    {
        "idx_host_command_journal_principal_created",
        "idx_host_command_journal_graph",
    }
)


class HostCommandRepositoryError(RuntimeError):
    """Fail-closed command admission error with a stable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or HOST_COMMAND_INVALID)
        self.detail = str(detail or "host command persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "detail": self.detail}


class HostCommandRepository:
    """SQLite repository and UoW for one immutable command admission."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        raw_path = str(db_path or "").strip()
        if not raw_path or raw_path == ":memory:" or raw_path.lower().startswith("file:"):
            raise HostCommandRepositoryError(
                HOST_COMMAND_INVALID,
                "HostCommandRepository requires a durable SQLite file path",
            )
        if not callable(clock):
            raise HostCommandRepositoryError(HOST_COMMAND_INVALID, "clock must be callable")
        self.db_path = Path(raw_path).expanduser().resolve()
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        self._fault_injector = fault_injector
        self._task_store = DurableTaskStore(
            self.db_path,
            clock=clock,
            timeout_ms=self._timeout_ms,
            verify_schema=False,
        )
        if verify_schema:
            self.assert_schema_ready()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self._timeout_ms / 1000.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
        return conn

    def _now(self) -> int:
        try:
            value = int(self._clock())
        except (TypeError, ValueError) as exc:
            raise HostCommandRepositoryError(HOST_COMMAND_INVALID, "clock returned an invalid value") from exc
        if value < 0:
            raise HostCommandRepositoryError(HOST_COMMAND_INVALID, "clock must be non-negative")
        return value

    def _fault(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(str(stage))

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise HostCommandRepositoryError(
                HOST_COMMAND_SCHEMA_NOT_READY,
                f"host command database does not exist: {self.db_path}",
            )
        try:
            self._task_store.assert_schema_ready()
            conn = self._connect()
            try:
                asset = conn.execute(
                    """
                    SELECT checksum, disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (HOST_COMMAND_MIGRATION_ASSET_KEY,),
                ).fetchone()
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(host_command_journal)").fetchall()
                }
                indexes = {
                    str(row[1])
                    for row in conn.execute("PRAGMA index_list(host_command_journal)").fetchall()
                }
                unique_indexes = {
                    tuple(
                        str(column[2])
                        for column in conn.execute(
                            f"PRAGMA index_info('{str(index_row[1])}')"
                        ).fetchall()
                    )
                    for index_row in conn.execute(
                        "PRAGMA index_list(host_command_journal)"
                    ).fetchall()
                    if int(index_row[2]) == 1
                }
                revision_asset = conn.execute(
                    """
                    SELECT disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (HOST_COMMAND_REVISION_MIGRATION_ASSET_KEY,),
                ).fetchone()
                foreign_keys_enabled = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
            finally:
                conn.close()
        except HostCommandRepositoryError:
            raise
        except (sqlite3.DatabaseError, TaskStoreError) as exc:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SCHEMA_NOT_READY,
                "host command schema cannot be verified",
            ) from exc
        if (
            asset is None
            or str(asset["checksum"]) != HOST_COMMAND_MIGRATION_SHA256
            or str(asset["disposition"]) not in _ALLOWED_MIGRATION_DISPOSITIONS
        ):
            raise HostCommandRepositoryError(
                HOST_COMMAND_SCHEMA_NOT_READY,
                "host command migration 0062 is missing or checksum-bound metadata drifted",
            )
        missing_columns = sorted(_HOST_COMMAND_COLUMNS - columns)
        missing_indexes = sorted(_REQUIRED_INDEXES - indexes)
        revision_constraints_ready = (
            revision_asset is not None
            and str(revision_asset["disposition"]) in _ALLOWED_MIGRATION_DISPOSITIONS
            and ("root_work_order_id", "graph_revision") in unique_indexes
            and ("graph_id", "graph_revision") in unique_indexes
            and ("root_work_order_id",) not in unique_indexes
            and ("assignment_id",) not in unique_indexes
        )
        if (
            missing_columns
            or missing_indexes
            or foreign_keys_enabled != 1
            or not revision_constraints_ready
        ):
            raise HostCommandRepositoryError(
                HOST_COMMAND_SCHEMA_NOT_READY,
                "host command schema is stale: "
                f"missing_columns={missing_columns}, missing_indexes={missing_indexes}, "
                f"foreign_keys={foreign_keys_enabled}, "
                f"revision_constraints_ready={revision_constraints_ready}",
            )

    def admit(
        self,
        command: Any,
        work_order: AgentWorkOrder | WorkOrderV3,
        assignment: Any,
        graph: TaskGraph,
    ) -> dict[str, Any]:
        """Atomically admit one typed command and its canonical TaskGraph.

        An exact replay returns the already committed row with ``replayed=True``.
        Reuse of any durable identity with different typed payloads fails closed.
        """

        normalized = self._normalize_admission(command, work_order, assignment, graph)
        admitted_at_ms = self._now()
        graph_outbox, command_outbox = self._build_outboxes(normalized)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._find_identity_collision(conn, normalized)
            if existing is not None:
                record = self._validated_record(conn, existing)
                self._assert_exact_replay(record, normalized)
                self._bind_ingress_claim(conn, normalized)
                conn.commit()
                return {**record, "created": False, "replayed": True}

            self._validate_ingress_claim(conn, normalized)
            self._task_store.insert_graph_in_transaction(
                conn,
                graph,
                outbox=graph_outbox,
                ts_ms=admitted_at_ms,
            )
            self._fault("after_graph")
            conn.execute(
                """
                INSERT INTO host_command_journal(
                    command_id, schema_version, request_id, surface, principal_id,
                    session_id, ingress_claim_ref, ingress_audit_ref,
                    governor_grant_ref, input_ref, input_sha256, route_type,
                    target_id, business_scope, operation_fingerprint, root_work_order_id,
                    graph_id, graph_revision, assignment_id,
                    command_json, command_digest, work_order_json,
                    work_order_digest, assignment_json, assignment_digest,
                    graph_json, graph_digest, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["command_id"],
                    normalized["schema_version"],
                    normalized["request_id"],
                    normalized["surface"],
                    normalized["principal_id"],
                    normalized["session_id"],
                    normalized["ingress_claim_ref"],
                    normalized["ingress_audit_ref"],
                    normalized["governor_grant_ref"],
                    normalized["input_ref"],
                    normalized["input_sha256"],
                    normalized["route_type"],
                    normalized["target_id"],
                    normalized["business_scope"],
                    normalized["operation_fingerprint"],
                    normalized["root_work_order_id"],
                    normalized["graph_id"],
                    normalized["graph_revision"],
                    normalized["assignment_id"],
                    normalized["command_json"],
                    normalized["command_digest"],
                    normalized["work_order_json"],
                    normalized["work_order_digest"],
                    normalized["assignment_json"],
                    normalized["assignment_digest"],
                    normalized["graph_json"],
                    normalized["graph_digest"],
                    admitted_at_ms,
                ),
            )
            self._fault("after_command")
            self._task_store.insert_outbox_in_transaction(
                conn,
                command_outbox,
                ts_ms=admitted_at_ms,
                expected_aggregate=("HOST_COMMAND", normalized["command_id"], 0),
                expected_event_type="HOST_COMMAND_ADMITTED",
                expected_attempt_id=None,
            )
            self._fault("after_outboxes")
            self._bind_ingress_claim(conn, normalized)
            self._fault("after_ingress_claim")
            row = self._require_row(conn, normalized["command_id"])
            record = self._validated_record(conn, row)
            conn.commit()
            return {**record, "created": True, "replayed": False}
        except HostCommandRepositoryError:
            conn.rollback()
            raise
        except TaskStoreError as exc:
            conn.rollback()
            raise HostCommandRepositoryError(exc.reason_code, exc.detail) from exc
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise HostCommandRepositoryError(
                HOST_COMMAND_CONFLICT,
                "command, graph, assignment, claim, or outbox identity conflicts",
            ) from exc
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get(self, command_id: str) -> dict[str, Any] | None:
        normalized_id = _required_text(command_id, field_name="command_id", max_length=512)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM host_command_journal WHERE command_id = ?",
                (normalized_id,),
            ).fetchone()
            return self._validated_record(conn, row) if row is not None else None
        finally:
            conn.close()

    def get_by_graph(
        self,
        *,
        graph_id: str,
        graph_revision: int,
    ) -> dict[str, Any] | None:
        """Resolve the immutable command admission for one exact Graph revision."""

        normalized_graph = _required_text(
            graph_id,
            field_name="graph_id",
            max_length=240,
        )
        revision = _required_int(
            graph_revision,
            field_name="graph_revision",
            minimum=1,
        )
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM host_command_journal
                WHERE graph_id = ? AND graph_revision = ?
                """,
                (normalized_graph, revision),
            ).fetchone()
            return self._validated_record(conn, row) if row is not None else None
        finally:
            conn.close()

    def get_by_request(self, *, surface: str, request_id: str) -> dict[str, Any] | None:
        normalized_surface = _required_text(surface, field_name="surface", max_length=160)
        normalized_request = _required_text(request_id, field_name="request_id", max_length=240)
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM host_command_journal
                WHERE surface = ? AND request_id = ?
                """,
                (normalized_surface, normalized_request),
            ).fetchone()
            return self._validated_record(conn, row) if row is not None else None
        finally:
            conn.close()

    def _normalize_admission(
        self,
        command: Any,
        work_order: AgentWorkOrder | WorkOrderV3,
        assignment: Any,
        graph: TaskGraph,
    ) -> dict[str, Any]:
        if not isinstance(work_order, WorkOrderV3):
            raise HostCommandRepositoryError(
                HOST_COMMAND_INVALID,
                "governed command admission requires an explicit WorkOrderV3",
            )
        command_payload, command_value = _typed_roundtrip(
            command,
            HostCommand,
            field_name="command",
        )
        work_order_payload, work_order_value = _work_order_roundtrip(work_order)
        if work_order_value.budget_policy is None:
            raise HostCommandRepositoryError(
                HOST_COMMAND_INVALID,
                "governed WorkOrderV3 requires an explicit BudgetPolicy",
            )
        assignment_payload, assignment_value = _typed_roundtrip(
            assignment,
            ExecutorAssignment,
            field_name="assignment",
        )
        graph_payload, graph_value = _typed_roundtrip(
            graph,
            TaskGraph,
            field_name="graph",
        )

        command_id = _required_text(command_payload.get("command_id"), field_name="command.command_id", max_length=512)
        work_order_id = _required_text(
            command_payload.get("root_work_order_id"),
            field_name="command.root_work_order_id",
            max_length=240,
        )
        graph_id = _required_text(command_payload.get("graph_id"), field_name="command.graph_id", max_length=240)
        graph_revision = _required_int(
            command_payload.get("graph_revision"),
            field_name="command.graph_revision",
            minimum=1,
        )
        if work_order_id != work_order_value.root_work_order_id:
            raise HostCommandRepositoryError(HOST_COMMAND_INVALID, "command/work_order root identity mismatch")
        if work_order_id != graph_value.root_work_order_id:
            raise HostCommandRepositoryError(HOST_COMMAND_INVALID, "work_order/TaskGraph root identity mismatch")
        if graph_id != graph_value.graph_id or graph_revision != graph_value.revision:
            raise HostCommandRepositoryError(HOST_COMMAND_INVALID, "command/TaskGraph revision mismatch")
        if work_order_value.task_graph_id is not None:
            if (
                work_order_value.task_graph_id != graph_id
                or work_order_value.graph_revision != graph_revision
            ):
                raise HostCommandRepositoryError(
                    HOST_COMMAND_INVALID,
                    "WorkOrderV3/TaskGraph lineage mismatch",
                )
            graph_node_ids = {node.node_id for node in graph_value.nodes}
            if work_order_value.task_node_id not in graph_node_ids:
                raise HostCommandRepositoryError(
                    HOST_COMMAND_INVALID,
                    "WorkOrderV3.task_node_id is outside the admitted TaskGraph",
                )
        if (
            work_order_value.business_idempotency_scope
            and work_order_value.business_idempotency_scope
            != str(command_payload.get("business_scope") or "")
        ):
            raise HostCommandRepositoryError(
                HOST_COMMAND_INVALID,
                "WorkOrderV3 business idempotency scope differs from HostCommand",
            )
        if (
            work_order_value.operation_fingerprint
            and work_order_value.operation_fingerprint
            != str(command_payload.get("operation_fingerprint") or "")
        ):
            raise HostCommandRepositoryError(
                HOST_COMMAND_INVALID,
                "WorkOrderV3 operation fingerprint differs from HostCommand",
            )
        try:
            validate_task_graph_admission_state(graph_value)
        except Exception as exc:
            raise HostCommandRepositoryError(
                HOST_COMMAND_INVALID,
                f"command admission TaskGraph state is invalid: {exc}",
            ) from exc
        if _required_text(
            assignment_payload.get("work_order_id"),
            field_name="assignment.work_order_id",
            max_length=240,
        ) != work_order_id:
            raise HostCommandRepositoryError(HOST_COMMAND_INVALID, "assignment/work_order identity mismatch")

        normalized = {
            "command": command_value,
            "work_order": work_order_value,
            "assignment": assignment_value,
            "graph": graph_value,
            "command_id": command_id,
            "schema_version": _required_text(
                command_payload.get("schema_version"),
                field_name="command.schema_version",
                max_length=128,
            ),
            "request_id": _required_text(
                command_payload.get("request_id"),
                field_name="command.request_id",
                max_length=240,
            ),
            "surface": _required_text(command_payload.get("surface"), field_name="command.surface", max_length=160),
            "principal_id": _required_text(
                command_payload.get("principal_id"),
                field_name="command.principal_id",
                max_length=240,
            ),
            "session_id": _required_text(
                command_payload.get("session_id"),
                field_name="command.session_id",
                max_length=240,
            ),
            "ingress_claim_ref": _optional_text(
                command_payload.get("ingress_claim_ref"),
                field_name="command.ingress_claim_ref",
                max_length=512,
            ),
            "ingress_audit_ref": _optional_text(
                command_payload.get("ingress_audit_ref"),
                field_name="command.ingress_audit_ref",
                max_length=512,
            ),
            "governor_grant_ref": _optional_text(
                command_payload.get("governor_grant_ref"),
                field_name="command.governor_grant_ref",
                max_length=512,
            ),
            "input_ref": _required_text(
                command_payload.get("input_ref"),
                field_name="command.input_ref",
                max_length=512,
            ),
            "input_sha256": _required_sha256(
                command_payload.get("input_sha256"),
                field_name="command.input_sha256",
            ),
            "route_type": _required_text(
                command_payload.get("route_type"),
                field_name="command.route_type",
                max_length=64,
            ),
            "target_id": _required_text(
                command_payload.get("target_id"),
                field_name="command.target_id",
                max_length=255,
            ),
            "business_scope": _required_text(
                command_payload.get("business_scope"),
                field_name="command.business_scope",
                max_length=255,
            ),
            "operation_fingerprint": _required_sha256(
                command_payload.get("operation_fingerprint"),
                field_name="command.operation_fingerprint",
            ),
            "root_work_order_id": work_order_id,
            "graph_id": graph_id,
            "graph_revision": graph_revision,
            "assignment_id": _required_text(
                assignment_payload.get("assignment_id"),
                field_name="assignment.assignment_id",
                max_length=512,
            ),
        }
        for name, payload in (
            ("command", command_payload),
            ("work_order", work_order_payload),
            ("assignment", assignment_payload),
            ("graph", graph_payload),
        ):
            serialized = _canonical_json(payload)
            normalized[f"{name}_json"] = serialized
            normalized[f"{name}_digest"] = _digest(serialized)
        return normalized

    def _build_outboxes(self, normalized: Mapping[str, Any]) -> tuple[OutboxMessage, OutboxMessage]:
        command_id = str(normalized["command_id"])
        graph_id = str(normalized["graph_id"])
        identity_digest = hashlib.sha256(
            command_id.encode("utf-8")
        ).hexdigest()
        command_ref = command_id
        graph_ref = f"task-graph://{quote(graph_id, safe='')}/revisions/{int(normalized['graph_revision'])}"
        evidence = tuple(
            item
            for item in (
                command_ref,
                graph_ref,
                str(normalized.get("input_ref") or ""),
                str(normalized.get("governor_grant_ref") or ""),
                str(normalized.get("ingress_claim_ref") or ""),
                str(normalized.get("ingress_audit_ref") or ""),
            )
            if item
        )
        graph_outbox = OutboxMessage.create(
            aggregate_type="TASK_GRAPH",
            aggregate_id=graph_id,
            graph_revision=int(normalized["graph_revision"]),
            aggregate_version=0,
            event_type="TASK_GRAPH_CREATED",
            payload_ref=build_outbox_payload_ref(
                aggregate_type="TASK_GRAPH",
                aggregate_id=graph_id,
                aggregate_version=0,
                graph_revision=int(normalized["graph_revision"]),
            ),
            idempotency_key=f"host-command:{identity_digest}:task-graph-created",
            evidence_refs=evidence,
            outbox_id=f"outbox::host-command::{identity_digest}::task-graph-created",
        )
        command_outbox = OutboxMessage.create(
            aggregate_type="HOST_COMMAND",
            aggregate_id=command_id,
            aggregate_version=0,
            event_type="HOST_COMMAND_ADMITTED",
            payload_ref=build_outbox_payload_ref(
                aggregate_type="HOST_COMMAND",
                aggregate_id=command_id,
                aggregate_version=0,
            ),
            idempotency_key=f"host-command:{identity_digest}:admitted",
            evidence_refs=evidence,
            outbox_id=f"outbox::host-command::{identity_digest}::admitted",
        )
        return graph_outbox, command_outbox

    def _find_identity_collision(
        self,
        conn: sqlite3.Connection,
        normalized: Mapping[str, Any],
    ) -> sqlite3.Row | None:
        rows = conn.execute(
            """
            SELECT * FROM host_command_journal
            WHERE command_id = ?
               OR (surface = ? AND request_id = ?)
               OR (ingress_claim_ref IS NOT NULL AND ingress_claim_ref = ?)
               OR (root_work_order_id = ? AND graph_revision = ?)
               OR (graph_id = ? AND graph_revision = ?)
            ORDER BY command_id
            """,
            (
                normalized["command_id"],
                normalized["surface"],
                normalized["request_id"],
                normalized["ingress_claim_ref"],
                normalized["root_work_order_id"],
                normalized["graph_revision"],
                normalized["graph_id"],
                normalized["graph_revision"],
            ),
        ).fetchall()
        distinct = {str(row["command_id"]) for row in rows}
        if len(distinct) > 1:
            raise HostCommandRepositoryError(
                HOST_COMMAND_CONFLICT,
                "incoming command identities collide with multiple durable commands",
            )
        return rows[0] if rows else None

    def _assert_exact_replay(
        self,
        record: Mapping[str, Any],
        normalized: Mapping[str, Any],
    ) -> None:
        scalar_fields = (
            "command_id",
            "schema_version",
            "request_id",
            "surface",
            "principal_id",
            "session_id",
            "ingress_claim_ref",
            "ingress_audit_ref",
            "governor_grant_ref",
            "input_ref",
            "input_sha256",
            "route_type",
            "target_id",
            "business_scope",
            "operation_fingerprint",
            "root_work_order_id",
            "graph_id",
            "graph_revision",
            "assignment_id",
            "command_digest",
            "work_order_digest",
            "assignment_digest",
            "graph_digest",
        )
        drift = [name for name in scalar_fields if record.get(name) != normalized.get(name)]
        if drift:
            raise HostCommandRepositoryError(
                HOST_COMMAND_CONFLICT,
                f"durable command replay payload drift: {drift}",
            )

    def _validate_ingress_claim(
        self,
        conn: sqlite3.Connection,
        normalized: Mapping[str, Any],
    ) -> None:
        claim_ref = str(normalized.get("ingress_claim_ref") or "").strip()
        if not claim_ref:
            return
        try:
            row = conn.execute(
                """
                SELECT principal_id, surface, request_id, body_sha256,
                       claim_status, result_ref
                FROM secure_ingress_idempotency_claim
                WHERE claim_ref = ?
                """,
                (claim_ref,),
            ).fetchone()
            audit = conn.execute(
                """
                SELECT request_id, surface, principal_id, body_sha256,
                       decision, claim_ref, side_effects_executed
                FROM secure_ingress_audit
                WHERE audit_ref = ?
                """,
                (str(normalized.get("ingress_audit_ref") or ""),),
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SCHEMA_NOT_READY,
                "Secure Ingress migration 0058 is unavailable",
            ) from exc
        if row is None or audit is None or str(row["claim_status"]) != "COMMITTED":
            raise HostCommandRepositoryError(
                HOST_COMMAND_INGRESS_CONFLICT,
                "ingress claim/audit is absent or the claim is not COMMITTED",
            )
        expected_result = str(normalized["command_id"])
        actual_result = str(row["result_ref"] or "").strip()
        surface = str(normalized["surface"])
        terminal_result_matches = True
        if surface == "internal" and actual_result:
            terminal_result_matches = (
                conn.execute(
                    """
                    SELECT 1
                    FROM agent_execution_result
                    WHERE receipt_ref = ? AND command_id = ?
                    LIMIT 1
                    """,
                    (actual_result, expected_result),
                ).fetchone()
                is not None
            )
        elif surface == "scheduler" and actual_result:
            terminal_result_matches = (
                conn.execute(
                    """
                    SELECT 1
                    FROM scheduler_execution_result
                    WHERE receipt_ref = ? AND command_id = ?
                    LIMIT 1
                    """,
                    (actual_result, expected_result),
                ).fetchone()
                is not None
            )
        claim_body_sha256 = _normalize_external_sha256(
            row["body_sha256"],
            field_name="secure_ingress_idempotency_claim.body_sha256",
        )
        audit_body_sha256 = _normalize_external_sha256(
            audit["body_sha256"],
            field_name="secure_ingress_audit.body_sha256",
        )
        mismatched = (
            str(row["principal_id"]) != str(normalized["principal_id"])
            or str(row["surface"]) != _secure_ingress_surface(str(normalized["surface"]))
            or str(row["request_id"]) != str(normalized["request_id"])
            or claim_body_sha256 != str(normalized["input_sha256"])
            or (
                surface in {"internal", "scheduler"}
                and not terminal_result_matches
            )
            or (
                surface not in {"internal", "scheduler"}
                and actual_result not in {"", expected_result}
            )
        )
        audit_mismatched = (
            str(audit["request_id"] or "") != str(normalized["request_id"])
            or str(audit["surface"]) != _secure_ingress_surface(str(normalized["surface"]))
            or str(audit["principal_id"] or "") != str(normalized["principal_id"])
            or audit_body_sha256 != str(normalized["input_sha256"])
            or str(audit["decision"]) != "ALLOW"
            or str(audit["claim_ref"] or "") != claim_ref
            or int(audit["side_effects_executed"]) != 0
        )
        if mismatched or audit_mismatched:
            raise HostCommandRepositoryError(
                HOST_COMMAND_INGRESS_CONFLICT,
                "ingress claim binding does not match the command",
            )

    def _bind_ingress_claim(
        self,
        conn: sqlite3.Connection,
        normalized: Mapping[str, Any],
    ) -> None:
        claim_ref = str(normalized.get("ingress_claim_ref") or "").strip()
        if not claim_ref:
            return
        self._validate_ingress_claim(conn, normalized)
        if str(normalized["surface"]) in {"internal", "scheduler"}:
            # Command admission is not a terminal result for governed worker
            # surfaces.  Their application services complete the Secure
            # Ingress claim only after an immutable worker receipt exists.
            return
        result_ref = str(normalized["command_id"])
        current = conn.execute(
            "SELECT result_ref FROM secure_ingress_idempotency_claim WHERE claim_ref = ?",
            (claim_ref,),
        ).fetchone()
        if current is not None and str(current["result_ref"] or "") == result_ref:
            return
        cursor = conn.execute(
            """
            UPDATE secure_ingress_idempotency_claim
            SET result_ref = ?, updated_at_ms = ?
            WHERE claim_ref = ? AND claim_status = 'COMMITTED'
              AND (result_ref IS NULL OR result_ref = ?)
            """,
            (result_ref, self._now(), claim_ref, result_ref),
        )
        if cursor.rowcount != 1:
            raise HostCommandRepositoryError(
                HOST_COMMAND_INGRESS_CONFLICT,
                "ingress claim result CAS failed",
            )

    def _require_row(self, conn: sqlite3.Connection, command_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM host_command_journal WHERE command_id = ?",
            (str(command_id),),
        ).fetchone()
        if row is None:
            raise HostCommandRepositoryError(
                HOST_COMMAND_DURABLE_DATA_INVALID,
                "committed command cannot be read back",
            )
        return row

    def _validated_record(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        record = dict(row)
        command_payload = _load_object(record["command_json"], field_name="command_json")
        work_order_payload = _load_object(record["work_order_json"], field_name="work_order_json")
        assignment_payload = _load_object(record["assignment_json"], field_name="assignment_json")
        frozen_graph_payload = _load_object(
            record["graph_json"],
            field_name="host_command_journal.graph_json",
        )
        graph_row = conn.execute(
            """
            SELECT graph_json FROM task_runtime_graph
            WHERE graph_id = ? AND revision = ?
            """,
            (record["graph_id"], int(record["graph_revision"])),
        ).fetchone()
        if graph_row is None:
            raise HostCommandRepositoryError(
                HOST_COMMAND_DURABLE_DATA_INVALID,
                "command references a missing TaskGraph revision",
            )
        current_graph_payload = _load_object(
            graph_row["graph_json"],
            field_name="task_runtime_graph.graph_json",
        )
        node_rows = conn.execute(
            """
            SELECT node_id, node_json FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY node_id
            """,
            (record["graph_id"], int(record["graph_revision"])),
        ).fetchall()
        _assert_current_graph_identity(
            record=record,
            frozen_graph=frozen_graph_payload,
            current_graph=current_graph_payload,
            current_nodes=node_rows,
        )
        if _digest(_canonical_json(work_order_payload)) != str(record["work_order_digest"]):
            raise HostCommandRepositoryError(
                HOST_COMMAND_DURABLE_DATA_INVALID,
                "durable work_order digest mismatch",
            )
        _, durable_work_order = _work_order_roundtrip(
            work_order_payload,
            require_instance=False,
        )
        typed_payloads = (
            ("command", command_payload, HostCommand, record["command_digest"]),
            ("assignment", assignment_payload, ExecutorAssignment, record["assignment_digest"]),
            ("graph", frozen_graph_payload, TaskGraph, record["graph_digest"]),
        )
        typed_values: dict[str, Any] = {"work_order": durable_work_order}
        for name, payload, expected_type, expected_digest in typed_payloads:
            canonical = _canonical_json(payload)
            if _digest(canonical) != str(expected_digest):
                raise HostCommandRepositoryError(
                    HOST_COMMAND_DURABLE_DATA_INVALID,
                    f"durable {name} digest mismatch",
                )
            _, typed = _typed_roundtrip(payload, expected_type, field_name=name, require_instance=False)
            typed_values[name] = typed
        canonical = self._normalize_admission(
            typed_values["command"],
            typed_values["work_order"],
            typed_values["assignment"],
            typed_values["graph"],
        )
        for name in (
            "command_id",
            "schema_version",
            "request_id",
            "surface",
            "principal_id",
            "session_id",
            "ingress_claim_ref",
            "ingress_audit_ref",
            "governor_grant_ref",
            "input_ref",
            "input_sha256",
            "route_type",
            "target_id",
            "business_scope",
            "operation_fingerprint",
            "root_work_order_id",
            "graph_id",
            "graph_revision",
            "assignment_id",
            "command_digest",
            "work_order_digest",
            "assignment_digest",
            "graph_digest",
        ):
            if record.get(name) != canonical.get(name):
                raise HostCommandRepositoryError(
                    HOST_COMMAND_DURABLE_DATA_INVALID,
                    f"durable command binding drift: {name}",
                )
        return {
            **record,
            "command": typed_values["command"],
            "work_order": typed_values["work_order"],
            "assignment": typed_values["assignment"],
            "graph": typed_values["graph"],
        }


def _assert_current_graph_identity(
    *,
    record: Mapping[str, Any],
    frozen_graph: Mapping[str, Any],
    current_graph: Mapping[str, Any],
    current_nodes: list[sqlite3.Row],
) -> None:
    """Verify mutable runtime state still belongs to the frozen admission.

    TaskStore legitimately rewrites graph/node JSON as statuses advance.  The
    admission journal therefore owns the immutable CREATED/READY snapshot and
    compares only identity, topology, route, and execution policy against the
    live rows.  Status and status_version remain TaskStore authority.
    """

    graph_fields = (
        "schema_version",
        "graph_id",
        "root_work_order_id",
        "revision",
        "planner_ref",
    )
    for field_name in graph_fields:
        if current_graph.get(field_name) != frozen_graph.get(field_name):
            raise HostCommandRepositoryError(
                HOST_COMMAND_DURABLE_DATA_INVALID,
                f"current TaskGraph immutable binding drift: {field_name}",
            )
    if (
        str(current_graph.get("graph_id") or "") != str(record["graph_id"])
        or int(current_graph.get("revision") or 0) != int(record["graph_revision"])
        or str(current_graph.get("root_work_order_id") or "")
        != str(record["root_work_order_id"])
    ):
        raise HostCommandRepositoryError(
            HOST_COMMAND_DURABLE_DATA_INVALID,
            "current TaskGraph no longer belongs to the admitted command",
        )

    frozen_nodes = frozen_graph.get("nodes")
    if not isinstance(frozen_nodes, list):
        raise HostCommandRepositoryError(
            HOST_COMMAND_DURABLE_DATA_INVALID,
            "frozen TaskGraph nodes are invalid",
        )
    frozen_by_id = {
        str(item.get("node_id") or ""): dict(item)
        for item in frozen_nodes
        if isinstance(item, Mapping) and str(item.get("node_id") or "")
    }
    current_by_id: dict[str, dict[str, Any]] = {}
    for row in current_nodes:
        node_id = str(row["node_id"] or "")
        current_by_id[node_id] = _load_object(
            row["node_json"],
            field_name=f"task_runtime_node[{node_id}].node_json",
        )
    if set(current_by_id) != set(frozen_by_id):
        raise HostCommandRepositoryError(
            HOST_COMMAND_DURABLE_DATA_INVALID,
            "current TaskGraph node identities differ from frozen admission",
        )
    mutable_fields = {"status", "status_version"}
    for node_id, frozen_node in frozen_by_id.items():
        current_node = current_by_id[node_id]
        frozen_immutable = {
            key: value for key, value in frozen_node.items() if key not in mutable_fields
        }
        current_immutable = {
            key: value for key, value in current_node.items() if key not in mutable_fields
        }
        if _canonical_json(frozen_immutable) != _canonical_json(current_immutable):
            raise HostCommandRepositoryError(
                HOST_COMMAND_DURABLE_DATA_INVALID,
                f"current TaskNode immutable binding drift: {node_id}",
            )


def _work_order_roundtrip(
    value: Any,
    *,
    require_instance: bool = True,
) -> tuple[dict[str, Any], WorkOrderV3]:
    """Version-dispatch a v2/v3 WorkOrder and return canonical durable v3."""

    if require_instance and not isinstance(value, (AgentWorkOrder, WorkOrderV3)):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            "work_order must be AgentWorkOrder or WorkOrderV3",
        )
    try:
        payload = (
            _mapping_from_typed(value, field_name="work_order")
            if require_instance
            else dict(value)
        )
        typed = WorkOrderV3.from_mapping(deepcopy(payload))
        typed.validate()
        return _mapping_from_typed(typed, field_name="work_order"), typed
    except HostCommandRepositoryError:
        raise
    except Exception as exc:
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID if require_instance else HOST_COMMAND_DURABLE_DATA_INVALID,
            f"work_order violates its versioned contract: {exc}",
        ) from exc


def _typed_roundtrip(
    value: Any,
    expected_type: type[Any],
    *,
    field_name: str,
    require_instance: bool = True,
) -> tuple[dict[str, Any], Any]:
    if require_instance and not isinstance(value, expected_type):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            f"{field_name} must be {expected_type.__name__}",
        )
    try:
        payload = (
            _mapping_from_typed(value, field_name=field_name)
            if require_instance
            else dict(value)
        )
        typed = expected_type.from_mapping(deepcopy(payload))
        validator = getattr(typed, "validate", None)
        if callable(validator):
            validator()
        normalized = _mapping_from_typed(typed, field_name=field_name)
        if _canonical_json(payload) != _canonical_json(normalized):
            payload = normalized
        return payload, typed
    except HostCommandRepositoryError:
        raise
    except Exception as exc:
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID if require_instance else HOST_COMMAND_DURABLE_DATA_INVALID,
            f"{field_name} violates its typed contract: {exc}",
        ) from exc


def _mapping_from_typed(value: Any, *, field_name: str) -> dict[str, Any]:
    serializer = getattr(value, "to_dict", None)
    if not callable(serializer):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            f"{field_name} lacks to_dict()",
        )
    payload = serializer()
    if not isinstance(payload, Mapping):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            f"{field_name}.to_dict() must return a mapping",
        )
    return dict(payload)


def _required_text(value: Any, *, field_name: str, max_length: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > int(max_length):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            f"{field_name} must contain 1..{int(max_length)} characters",
        )
    return text


def _optional_text(value: Any, *, field_name: str, max_length: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) > int(max_length):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            f"{field_name} exceeds {int(max_length)} characters",
        )
    return text


def _required_int(value: Any, *, field_name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < int(minimum):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            f"{field_name} must be an integer >= {int(minimum)}",
        )
    return int(value)


def _required_sha256(value: Any, *, field_name: str) -> str:
    text = str(value or "")
    if (
        len(text) != 71
        or not text.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in text[7:])
    ):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            f"{field_name} must use canonical sha256:<64 lower-case hex> form",
        )
    return text


def _normalize_external_sha256(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) == 64 and all(character in "0123456789abcdef" for character in text):
        return f"sha256:{text}"
    return _required_sha256(text, field_name=field_name)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_object(value: Any, *, field_name: str) -> dict[str, Any]:
    try:
        parsed = json.loads(
            str(value),
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HostCommandRepositoryError(
            HOST_COMMAND_DURABLE_DATA_INVALID,
            f"{field_name} is not strict JSON",
        ) from exc
    if not isinstance(parsed, dict):
        raise HostCommandRepositoryError(
            HOST_COMMAND_DURABLE_DATA_INVALID,
            f"{field_name} must contain a JSON object",
        )
    return parsed


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            f"typed payload is not canonical JSON: {exc}",
        ) from exc


def _digest(serialized: str) -> str:
    return f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"


def _secure_ingress_surface(command_surface: str) -> str:
    return {
        "host_input": "host.input",
        "gateway": "gateway.webhook",
        "internal": "internal.agent",
        "scheduler": "scheduler.admin",
    }.get(str(command_surface), str(command_surface))


__all__ = [
    "HOST_COMMAND_CONFLICT",
    "HOST_COMMAND_DURABLE_DATA_INVALID",
    "HOST_COMMAND_INGRESS_CONFLICT",
    "HOST_COMMAND_INVALID",
    "HOST_COMMAND_MIGRATION_ASSET_KEY",
    "HOST_COMMAND_MIGRATION_SHA256",
    "HOST_COMMAND_SCHEMA_NOT_READY",
    "HostCommandRepository",
    "HostCommandRepositoryError",
]
