"""Atomic Host command admission over the canonical TaskStore schema.

This adapter is intentionally narrow: one transaction binds a typed HostCommand,
canonical WorkOrderV3, ExecutorAssignment and TaskGraph, emits both required outbox
events, and (when present) attaches the committed Secure Ingress claim to the
new durable command.  It owns neither execution status nor delivery receipts;
those remain authoritative in migrations 0052/0054/0059.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
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
from host_brain.persistence.graph_revision_supersession_schema import (
    GraphRevisionSupersessionSchemaError,
    assert_graph_revision_supersession_schema,
)
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder, WorkOrderV3
from host_brain.core.ids import now_ms
from natsume_contracts.task.v1 import ExecutorAssignment, HostCommand

from host_brain.persistence.host_command_repository_codec import (
    assert_current_graph_identity as _assert_current_graph_identity,
    canonical_json as _canonical_json,
    digest as _digest,
    load_object as _load_object,
    mapping_from_typed as _mapping_from_typed,
    normalize_failure_evidence_refs as _normalize_failure_evidence_refs,
    optional_text as _optional_text,
    required_int as _required_int,
    required_sha256 as _required_sha256,
    required_text as _required_text,
    supersession_id as _supersession_id,
    typed_roundtrip as _typed_roundtrip,
    work_order_roundtrip as _work_order_roundtrip,
)
from host_brain.persistence.host_command_repository_types import (
    ALLOWED_MIGRATION_DISPOSITIONS as _ALLOWED_MIGRATION_DISPOSITIONS,
    GRAPH_REVISION_SUPERSESSION_SCHEMA_VERSION,
    HOST_COMMAND_COLUMNS as _HOST_COMMAND_COLUMNS,
    HOST_COMMAND_CONFLICT,
    HOST_COMMAND_DURABLE_DATA_INVALID,
    HOST_COMMAND_INGRESS_CONFLICT,
    HOST_COMMAND_INVALID,
    HOST_COMMAND_MIGRATION_ASSET_KEY,
    HOST_COMMAND_MIGRATION_SHA256,
    HOST_COMMAND_REVISION_MIGRATION_ASSET_KEY,
    HOST_COMMAND_SCHEMA_NOT_READY,
    HOST_COMMAND_SUPERSESSION_CONFLICT,
    HOST_COMMAND_SUPERSESSION_MIGRATION_ASSET_KEY,
    HOST_COMMAND_SUPERSESSION_MIGRATION_SHA256,
    REQUIRED_INDEXES as _REQUIRED_INDEXES,
    SUPERSESSION_TABLE as _SUPERSESSION_TABLE,
    HostCommandRepositoryError,
)
from host_brain.persistence.host_command_claim_result_validation import (
    HostCommandClaimResultValidationMixin,
)
from host_brain.persistence.task_graph_admission_authority import (
    TaskGraphAdmissionAuthoritySchemaError,
    assert_task_graph_admission_authority_schema,
    canonical_task_graph_admission_scope,
    register_task_graph_admission_authority,
)


class HostCommandRepository(HostCommandClaimResultValidationMixin):
    """SQLite repository and UoW for one immutable command admission."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
        fault_injector: Callable[[str], None] | None = None,
        authenticated_outcome_lookup: Callable[[str], Any | None] | None = None,
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
        self._authenticated_outcome_lookup = authenticated_outcome_lookup
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
        register_task_graph_admission_authority(conn)
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
                supersession_asset = conn.execute(
                    """
                    SELECT checksum, disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (HOST_COMMAND_SUPERSESSION_MIGRATION_ASSET_KEY,),
                ).fetchone()
                assert_graph_revision_supersession_schema(conn)
                assert_task_graph_admission_authority_schema(conn)
                foreign_keys_enabled = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
            finally:
                conn.close()
        except HostCommandRepositoryError:
            raise
        except (
            GraphRevisionSupersessionSchemaError,
            TaskGraphAdmissionAuthoritySchemaError,
            sqlite3.DatabaseError,
            TaskStoreError,
        ) as exc:
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
        supersession_ready = (
            supersession_asset is not None
            and str(supersession_asset["checksum"])
            == HOST_COMMAND_SUPERSESSION_MIGRATION_SHA256
            and str(supersession_asset["disposition"])
            in _ALLOWED_MIGRATION_DISPOSITIONS
        )
        if (
            missing_columns
            or missing_indexes
            or foreign_keys_enabled != 1
            or not revision_constraints_ready
            or not supersession_ready
        ):
            raise HostCommandRepositoryError(
                HOST_COMMAND_SCHEMA_NOT_READY,
                "host command schema is stale: "
                f"missing_columns={missing_columns}, missing_indexes={missing_indexes}, "
                f"foreign_keys={foreign_keys_enabled}, "
                f"revision_constraints_ready={revision_constraints_ready}, "
                f"supersession_ready={supersession_ready}",
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
        if int(normalized["graph_revision"]) != 1:
            raise HostCommandRepositoryError(
                HOST_COMMAND_INVALID,
                "replacement graph revisions require admit_replan supersession authority",
            )
        return self._admit_normalized(normalized, replan=None)

    def admit_replan(
        self,
        command: Any,
        work_order: AgentWorkOrder | WorkOrderV3,
        assignment: Any,
        graph: TaskGraph,
        *,
        previous_revision: int,
        failure_evidence_refs: Sequence[str],
    ) -> dict[str, Any]:
        """Atomically supersede one quiescent FAILED graph revision.

        The predecessor validation, replacement graph/command admission, both
        Outboxes and the append-only supersession authority share one
        ``BEGIN IMMEDIATE`` transaction.  An exact response-loss replay must
        find the already committed supersession row; it is never retrofitted.
        """

        normalized = self._normalize_admission(command, work_order, assignment, graph)
        predecessor = _required_int(
            previous_revision,
            field_name="previous_revision",
            minimum=1,
        )
        if (
            int(normalized["graph_revision"]) != predecessor + 1
            or predecessor < 1
        ):
            raise HostCommandRepositoryError(
                HOST_COMMAND_INVALID,
                "replan admission requires replacement revision == previous + 1",
            )
        evidence_json, evidence_digest = _normalize_failure_evidence_refs(
            failure_evidence_refs
        )
        return self._admit_normalized(
            normalized,
            replan={
                "previous_revision": predecessor,
                "failure_evidence_json": evidence_json,
                "failure_evidence_sha256": evidence_digest,
            },
        )

    def _admit_normalized(
        self,
        normalized: Mapping[str, Any],
        *,
        replan: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        admitted_at_ms = self._now()
        graph_outbox, command_outbox = self._build_outboxes(normalized)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._find_identity_collision(conn, normalized)
            if existing is not None:
                record = self._validated_record(conn, existing)
                self._assert_exact_replay(record, normalized)
                if replan is not None:
                    self._assert_exact_supersession_replay(
                        conn,
                        normalized=normalized,
                        replan=replan,
                    )
                self._bind_ingress_claim(conn, normalized)
                conn.commit()
                return {**record, "created": False, "replayed": True}

            predecessor = None
            if replan is not None:
                predecessor = self._validate_replan_predecessor_in_transaction(
                    conn,
                    normalized=normalized,
                    replan=replan,
                )
            self._validate_ingress_claim(conn, normalized)
            with canonical_task_graph_admission_scope(
                normalized["graph_id"],
                normalized["graph_revision"],
            ):
                self._task_store._insert_graph_from_host_command_in_transaction(
                    conn,
                    normalized["graph"],
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
            if replan is not None:
                if predecessor is None:
                    raise HostCommandRepositoryError(
                        HOST_COMMAND_DURABLE_DATA_INVALID,
                        "validated replan predecessor disappeared before supersession",
                    )
                self._insert_supersession_in_transaction(
                    conn,
                    normalized=normalized,
                    replan=replan,
                    predecessor=predecessor,
                    created_ts_ms=admitted_at_ms,
                )
                self._fault("after_supersession")
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

    def _validate_replan_predecessor_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        normalized: Mapping[str, Any],
        replan: Mapping[str, Any],
    ) -> dict[str, Any]:
        graph_id = str(normalized["graph_id"])
        replacement_revision = int(normalized["graph_revision"])
        previous_revision = int(replan["previous_revision"])
        predecessor = conn.execute(
            """
            SELECT * FROM task_runtime_graph
            WHERE graph_id = ? AND revision = ?
            """,
            (graph_id, previous_revision),
        ).fetchone()
        if predecessor is None:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "replan predecessor TaskGraph does not exist",
            )
        if (
            str(predecessor["root_work_order_id"])
            != str(normalized["root_work_order_id"])
            or str(predecessor["status"]) != "FAILED"
            or int(predecessor["status_version"]) < 1
            or replacement_revision != previous_revision + 1
        ):
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "replan predecessor must be the same-root FAILED revision immediately before replacement",
            )
        try:
            predecessor_graph = TaskGraph.from_mapping(
                _load_object(
                    predecessor["graph_json"],
                    field_name="replan predecessor task_runtime_graph.graph_json",
                )
            )
        except HostCommandRepositoryError:
            raise
        except Exception as exc:
            raise HostCommandRepositoryError(
                HOST_COMMAND_DURABLE_DATA_INVALID,
                "replan predecessor TaskGraph payload is invalid",
            ) from exc
        if (
            predecessor_graph.graph_id != graph_id
            or predecessor_graph.revision != previous_revision
            or predecessor_graph.root_work_order_id
            != str(normalized["root_work_order_id"])
            or predecessor_graph.status.value != "FAILED"
        ):
            raise HostCommandRepositoryError(
                HOST_COMMAND_DURABLE_DATA_INVALID,
                "replan predecessor SQL identity differs from its canonical TaskGraph",
            )

        previous_commands = conn.execute(
            """
            SELECT * FROM host_command_journal
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY command_id
            """,
            (graph_id, previous_revision),
        ).fetchall()
        if len(previous_commands) != 1:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "replan predecessor requires exactly one immutable HostCommand",
            )
        previous_record = self._validated_record(conn, previous_commands[0])
        if previous_record["root_work_order_id"] != normalized["root_work_order_id"]:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "replan predecessor HostCommand root lineage differs",
            )

        # Reuse the canonical 0099 unresolved-case validator in the same writer
        # transaction; an inventoried but unproved terminal anomaly may not be
        # hidden by admitting a replacement revision.
        self._task_store.assert_no_terminal_reconciliation_fence_in_transaction(
            conn,
            graph_id=graph_id,
            graph_revision=previous_revision,
            action="graph revision supersession",
        )

        continuable_node = conn.execute(
            """
            SELECT node_id, status FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ?
              AND status IN (
                'WAITING_APPROVAL', 'LEASED', 'RUNNING', 'EXECUTED',
                'VERIFYING', 'VERIFIED', 'WAITING_ACCEPTANCE_REVIEW',
                'RETRY_PENDING', 'CANCEL_REQUESTED'
              )
            ORDER BY node_id LIMIT 1
            """,
            (graph_id, previous_revision),
        ).fetchone()
        if continuable_node is not None:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "replan predecessor retains a continuable TaskNode: "
                f"{continuable_node['node_id']}:{continuable_node['status']}",
            )
        non_terminal_attempt = conn.execute(
            """
            SELECT attempt_id, status FROM task_runtime_attempt
            WHERE graph_id = ? AND graph_revision = ?
              AND status IN (
                'CREATED', 'LEASED', 'RUNNING', 'RECOVERABLE',
                'CANCEL_REQUESTED'
              )
            ORDER BY attempt_id LIMIT 1
            """,
            (graph_id, previous_revision),
        ).fetchone()
        if non_terminal_attempt is not None:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "replan predecessor retains a non-terminal Attempt: "
                f"{non_terminal_attempt['attempt_id']}:{non_terminal_attempt['status']}",
            )
        active_lease = conn.execute(
            """
            SELECT lease.lease_id
            FROM task_runtime_lease AS lease
            JOIN task_runtime_attempt AS attempt
              ON attempt.attempt_id = lease.attempt_id
            WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
              AND lease.status = 'ACTIVE'
            ORDER BY lease.lease_id LIMIT 1
            """,
            (graph_id, previous_revision),
        ).fetchone()
        if active_lease is not None:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "replan predecessor retains an ACTIVE Lease",
            )
        unresolved_operation = conn.execute(
            """
            SELECT reservation.reservation_id, reservation.status
            FROM task_runtime_idempotency_reservation AS reservation
            JOIN task_runtime_attempt AS attempt
              ON attempt.attempt_id = reservation.attempt_id
            WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
              AND reservation.status IN ('RESERVED', 'RECOVERY_REQUIRED')
            ORDER BY reservation.reservation_id LIMIT 1
            """,
            (graph_id, previous_revision),
        ).fetchone()
        if unresolved_operation is not None:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "replan predecessor retains unresolved operation authority: "
                f"{unresolved_operation['reservation_id']}:{unresolved_operation['status']}",
            )
        review_continuation = conn.execute(
            """
            SELECT review_id FROM task_runtime_review_continuation
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY review_id LIMIT 1
            """,
            (graph_id, previous_revision),
        ).fetchone()
        if review_continuation is not None:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "replan predecessor retains Review continuation authority",
            )

        budgets = conn.execute(
            """
            SELECT budget_id, status, node_id, attempt_id
            FROM task_runtime_budget_reservation
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY budget_id
            """,
            (graph_id, previous_revision),
        ).fetchall()
        if len(budgets) > 1:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "replan predecessor has multiple Budget authorities",
            )
        if budgets and (
            budgets[0]["node_id"] is not None
            or budgets[0]["attempt_id"] is not None
            or str(budgets[0]["status"]) not in {"RELEASED", "CANCELLED"}
        ):
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "replan predecessor Budget must be the unique graph-scope "
                "RELEASED/CANCELLED reservation",
            )
        cancellation = conn.execute(
            """
            SELECT intent_ref FROM task_runtime_graph_cancellation_intent
            WHERE graph_id = ? AND graph_revision = ?
            """,
            (graph_id, previous_revision),
        ).fetchone()
        if cancellation is not None:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "replan predecessor has separate graph-cancellation authority",
            )
        acceptance_effect = conn.execute(
            """
            SELECT effect_id, status FROM task_runtime_acceptance_effect_intent
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY effect_id LIMIT 1
            """,
            (graph_id, previous_revision),
        ).fetchone()
        if acceptance_effect is not None:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "replan predecessor contains Acceptance effect authority outside "
                "the proven-safe supersession subset: "
                f"{acceptance_effect['effect_id']}:{acceptance_effect['status']}",
            )
        unresolved_effect_case = conn.execute(
            """
            SELECT item.case_id
            FROM task_runtime_effect_reconciliation_case AS item
            WHERE item.graph_id = ? AND item.graph_revision = ?
              AND NOT EXISTS (
                SELECT 1 FROM task_runtime_effect_reconciliation_proof AS proof
                WHERE proof.case_id = item.case_id
              )
            ORDER BY item.case_id LIMIT 1
            """,
            (graph_id, previous_revision),
        ).fetchone()
        if unresolved_effect_case is not None:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "replan predecessor retains unresolved effect reconciliation",
            )

        terminal_outboxes = conn.execute(
            """
            SELECT payload_ref FROM task_runtime_outbox
            WHERE aggregate_type = 'TASK_GRAPH' AND aggregate_id = ?
              AND graph_revision = ? AND aggregate_version = ?
              AND event_type = 'TASK_GRAPH_FAILED'
            ORDER BY outbox_id
            """,
            (
                graph_id,
                previous_revision,
                int(predecessor["status_version"]),
            ),
        ).fetchall()
        if len(terminal_outboxes) != 1:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "replan predecessor lacks one exact FAILED Graph Outbox",
            )
        evidence = tuple(json.loads(str(replan["failure_evidence_json"])))
        if str(terminal_outboxes[0]["payload_ref"]) not in evidence:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "replan evidence does not include the exact FAILED Graph transition",
            )

        existing_supersession = conn.execute(
            f"""
            SELECT supersession_id FROM {_SUPERSESSION_TABLE}
            WHERE graph_id = ?
              AND (previous_revision = ? OR replacement_revision = ?)
            LIMIT 1
            """,
            (graph_id, previous_revision, replacement_revision),
        ).fetchone()
        if existing_supersession is not None:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "graph revision predecessor or replacement is already superseded",
            )
        return {
            "previous_command_id": str(previous_record["command_id"]),
            "previous_graph_status_version": int(predecessor["status_version"]),
        }

    def _insert_supersession_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        normalized: Mapping[str, Any],
        replan: Mapping[str, Any],
        predecessor: Mapping[str, Any],
        created_ts_ms: int,
    ) -> None:
        binding = {
            "schema_version": GRAPH_REVISION_SUPERSESSION_SCHEMA_VERSION,
            "graph_id": str(normalized["graph_id"]),
            "previous_revision": int(replan["previous_revision"]),
            "replacement_revision": int(normalized["graph_revision"]),
            "previous_command_id": str(predecessor["previous_command_id"]),
            "replacement_command_id": str(normalized["command_id"]),
            "previous_graph_status_version": int(
                predecessor["previous_graph_status_version"]
            ),
            "failure_evidence_sha256": str(replan["failure_evidence_sha256"]),
        }
        supersession_id = _supersession_id(binding)
        conn.execute(
            f"""
            INSERT INTO {_SUPERSESSION_TABLE}(
                supersession_id, schema_version, graph_id,
                previous_revision, replacement_revision,
                previous_command_id, replacement_command_id,
                previous_graph_status_version, failure_evidence_json,
                failure_evidence_sha256, created_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                supersession_id,
                binding["schema_version"],
                binding["graph_id"],
                binding["previous_revision"],
                binding["replacement_revision"],
                binding["previous_command_id"],
                binding["replacement_command_id"],
                binding["previous_graph_status_version"],
                replan["failure_evidence_json"],
                binding["failure_evidence_sha256"],
                int(created_ts_ms),
            ),
        )

    def _assert_exact_supersession_replay(
        self,
        conn: sqlite3.Connection,
        *,
        normalized: Mapping[str, Any],
        replan: Mapping[str, Any],
    ) -> None:
        row = conn.execute(
            f"""
            SELECT * FROM {_SUPERSESSION_TABLE}
            WHERE graph_id = ? AND previous_revision = ?
            """,
            (str(normalized["graph_id"]), int(replan["previous_revision"])),
        ).fetchone()
        if row is None:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "replacement replay has no committed supersession authority",
            )
        binding = {
            "schema_version": str(row["schema_version"]),
            "graph_id": str(row["graph_id"]),
            "previous_revision": int(row["previous_revision"]),
            "replacement_revision": int(row["replacement_revision"]),
            "previous_command_id": str(row["previous_command_id"]),
            "replacement_command_id": str(row["replacement_command_id"]),
            "previous_graph_status_version": int(
                row["previous_graph_status_version"]
            ),
            "failure_evidence_sha256": str(row["failure_evidence_sha256"]),
        }
        expected = {
            "schema_version": GRAPH_REVISION_SUPERSESSION_SCHEMA_VERSION,
            "graph_id": str(normalized["graph_id"]),
            "previous_revision": int(replan["previous_revision"]),
            "replacement_revision": int(normalized["graph_revision"]),
            "previous_command_id": binding["previous_command_id"],
            "replacement_command_id": str(normalized["command_id"]),
            "previous_graph_status_version": binding[
                "previous_graph_status_version"
            ],
            "failure_evidence_sha256": str(replan["failure_evidence_sha256"]),
        }
        if (
            binding != expected
            or str(row["failure_evidence_json"])
            != str(replan["failure_evidence_json"])
            or str(row["supersession_id"]) != _supersession_id(binding)
            or _digest(str(row["failure_evidence_json"]))
            != str(row["failure_evidence_sha256"])
        ):
            raise HostCommandRepositoryError(
                HOST_COMMAND_SUPERSESSION_CONFLICT,
                "durable graph revision supersession replay drifted",
            )

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

    def get_by_ingress_claim(self, claim_ref: str) -> dict[str, Any] | None:
        """Resolve the one immutable command admitted by an ingress claim."""

        normalized_claim = _required_text(
            claim_ref,
            field_name="ingress_claim_ref",
            max_length=2_016,
        )
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM host_command_journal
                WHERE ingress_claim_ref = ?
                ORDER BY command_id
                LIMIT 2
                """,
                (normalized_claim,),
            ).fetchall()
            if len(rows) > 1:
                raise HostCommandRepositoryError(
                    HOST_COMMAND_DURABLE_DATA_INVALID,
                    "ingress claim resolves to multiple durable commands",
                )
            return self._validated_record(conn, rows[0]) if rows else None
        finally:
            conn.close()

    def validate_ingress_claim_result(self, command_id: str) -> dict[str, Any]:
        """Reopen one command and verify its current terminal claim authority."""

        return self._validate_claim_result_record(command_id)

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

    def _bind_ingress_claim(
        self,
        conn: sqlite3.Connection,
        normalized: Mapping[str, Any],
    ) -> None:
        claim_ref = str(normalized.get("ingress_claim_ref") or "").strip()
        if not claim_ref:
            return
        self._validate_ingress_claim(conn, normalized)
        if str(normalized["surface"]) in {"internal", "scheduler", "operator", "tool"}:
            # Command admission is not a terminal result for governed worker
            # surfaces.  Native application services complete their claims
            # after an immutable worker receipt exists; Operator commands keep
            # the outer OBS outcome journal as the sole claim result authority.
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


__all__ = [
    "HOST_COMMAND_CONFLICT",
    "HOST_COMMAND_DURABLE_DATA_INVALID",
    "HOST_COMMAND_INGRESS_CONFLICT",
    "HOST_COMMAND_INVALID",
    "HOST_COMMAND_MIGRATION_ASSET_KEY",
    "HOST_COMMAND_MIGRATION_SHA256",
    "HOST_COMMAND_SCHEMA_NOT_READY",
    "HOST_COMMAND_SUPERSESSION_CONFLICT",
    "HOST_COMMAND_SUPERSESSION_MIGRATION_ASSET_KEY",
    "HOST_COMMAND_SUPERSESSION_MIGRATION_SHA256",
    "HostCommandRepository",
    "HostCommandRepositoryError",
]
