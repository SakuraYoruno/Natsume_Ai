"""Single-snapshot discovery and trusted reads for migration 0099."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from host_brain.persistence.task_record_codec import (
    _dumps,
    _graph_record,
)
from host_brain.persistence.task_budget_usage_store import (
    _assert_terminal_budget_authority,
)
from host_brain.persistence.reconciliation_proof_verifier import (
    TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
)
from host_brain.persistence.task_store_types import (
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_NOT_FOUND,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
    build_outbox_payload_ref,
)

from .task_terminal_reconciliation_common import (
    _canonical_digest,
    _outbox_projection,
    _terminal_reconciliation_case_record,
    _terminal_reconciliation_proof_record,
    _terminal_reconciliation_supersession_record,
    _value_digest,
)
from .task_terminal_reconciliation_schema import (
    assert_terminal_reconciliation_schema,
)
from .task_terminal_reconciliation_decision_authority import (
    canonical_parallel_terminal_decision,
    graph_cancellation_authority_valid,
    parallel_success_authority_valid,
)
from .task_terminal_reconciliation_projection import (
    _ACTIVE_REVIEW_STATUSES,
    _attempt_projection,
    _bounded_limit,
    _build_terminal_case as _build_terminal_case_projection,
    _budget_projection,
    _graph_projection,
    _has_exact_review_checkpoint,
    _healthy_closure_if_required,
    _lease_projection,
    _node_projection,
    _one_outbox,
    _only_or_none,
    _operation_projection,
    _quarantine,
    _review_projection,
    _selected_failure_lineage,
    _structural_terminal_observation,
    _terminal_execution_lineage_valid,
)


_TERMINAL_GRAPH_STATUSES = ("BLOCKED", "COMPLETED", "FAILED", "CANCELLED")
_UNSETTLED_BUDGET_STATUSES = frozenset({"ACTIVE", "EXCEEDED"})
_FAILURE_NODE_STATUSES = frozenset(
    {"FAILED", "VERIFICATION_FAILED", "CANCELLED"}
)
_LIVE_ATTEMPT_STATUSES = frozenset(
    {"CREATED", "LEASED", "RUNNING", "RECOVERABLE", "CANCEL_REQUESTED"}
)
_UNRESOLVED_OPERATION_STATUSES = frozenset({"RESERVED", "RECOVERY_REQUIRED"})


class TaskTerminalReconciliationReadMixin:
    """Own 0099 readiness, immutable inventory reads and current discovery."""

    def assert_terminal_reconciliation_ready(
        self,
        *,
        force_revalidate: bool = False,
    ) -> None:
        if (
            not force_revalidate
            and getattr(self, "_terminal_reconciliation_schema_ready", False)
        ):
            return
        self._terminal_reconciliation_schema_ready = False
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            assert_terminal_reconciliation_schema(conn)
            for row in conn.execute(
                "SELECT * FROM task_runtime_terminal_reconciliation_case ORDER BY case_id"
            ).fetchall():
                _terminal_reconciliation_case_record(row)
            verifier = getattr(self, "_reconciliation_proof_verifier", None)
            for row in conn.execute(
                "SELECT * FROM task_runtime_terminal_reconciliation_proof ORDER BY proof_id"
            ).fetchall():
                _terminal_reconciliation_proof_record(
                    conn,
                    row,
                    verifier=verifier,
                )
            for row in conn.execute(
                """
                SELECT *
                FROM task_runtime_terminal_reconciliation_supersession
                ORDER BY supersession_id
                """
            ).fetchall():
                _terminal_reconciliation_supersession_record(conn, row)
            foreign_key_violations = conn.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "terminal reconciliation schema could not be inspected",
            ) from exc
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()
        if foreign_key_violations:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal reconciliation readiness found foreign-key violations",
            )
        self._terminal_reconciliation_schema_ready = True

    def assert_terminal_reconciliation_production_ready(self) -> None:
        """Require a complete, trusted 0099 inventory before production use."""

        self.assert_terminal_reconciliation_ready()
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            terminal_graph_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM task_runtime_graph
                    WHERE status IN ('BLOCKED', 'COMPLETED', 'FAILED', 'CANCELLED')
                    """
                ).fetchone()[0]
            )
            if terminal_graph_count > 10_000:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "terminal reconciliation production inventory exceeds the "
                    "verified startup scan bound",
                )
            rows = conn.execute(
                """
                SELECT * FROM task_runtime_terminal_reconciliation_case
                ORDER BY created_ts_ms, case_id
                """
            ).fetchall()
            verifier = getattr(self, "_reconciliation_proof_verifier", None)
            cases_by_id: dict[str, dict[str, Any]] = {}
            proved_case_ids: set[str] = set()
            for row in rows:
                case = _terminal_reconciliation_case_record(row)
                cases_by_id[str(case["case_id"])] = case
                proof_row = conn.execute(
                    """
                    SELECT * FROM task_runtime_terminal_reconciliation_proof
                    WHERE case_id = ?
                    """,
                    (case["case_id"],),
                ).fetchone()
                if proof_row is not None:
                    _terminal_reconciliation_proof_record(
                        conn,
                        proof_row,
                        verifier=verifier,
                    )
                    proved_case_ids.add(str(case["case_id"]))
            supersessions: dict[str, str] = {}
            for row in conn.execute(
                """
                SELECT *
                FROM task_runtime_terminal_reconciliation_supersession
                ORDER BY created_ts_ms, supersession_id
                """
            ).fetchall():
                record = _terminal_reconciliation_supersession_record(conn, row)
                supersessions[str(record["prior_case_id"])] = str(
                    record["replacement_case_id"]
                )

            def has_trusted_closure(case_id: str) -> bool:
                seen: set[str] = set()
                current_id = str(case_id)
                while current_id not in proved_case_ids:
                    if current_id in seen:
                        raise TaskStoreError(
                            TASK_STORE_DURABLE_DATA_INVALID,
                            "terminal reconciliation supersession cycle detected",
                        )
                    seen.add(current_id)
                    replacement_id = supersessions.get(current_id)
                    if replacement_id is None or replacement_id not in cases_by_id:
                        return False
                    current_id = replacement_id
                return True

            unresolved_cases = [
                case
                for case_id, case in cases_by_id.items()
                if not has_trusted_closure(case_id)
            ]
            if unresolved_cases:
                structural = any(
                    case["classification"] == "QUARANTINED_STRUCTURAL"
                    for case in unresolved_cases
                )
                raise TaskStoreError(
                    (
                        TASK_STORE_DURABLE_DATA_INVALID
                        if structural
                        else TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED
                    ),
                    "terminal reconciliation inventory lacks trusted closure",
                )
            current = self._discover_terminal_reconciliation_cases_in_transaction(
                conn,
                10_000,
            )
            if current:
                structural = any(
                    item["classification"] == "QUARANTINED_STRUCTURAL"
                    for item in current
                )
                raise TaskStoreError(
                    (
                        TASK_STORE_DURABLE_DATA_INVALID
                        if structural
                        else TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED
                    ),
                    "current terminal Graph half-state requires 0099 reconciliation",
                )
            conn.rollback()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def list_terminal_reconciliation_inventory_candidates(
        self,
        *,
        limit: int = 500,
    ) -> tuple[dict[str, Any], ...]:
        """Discover current anomalies without writing or changing B4 authority."""

        self.assert_terminal_reconciliation_ready()
        bounded = _bounded_limit(limit, ceiling=2_000)
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            return self._discover_terminal_reconciliation_cases_in_transaction(
                conn,
                bounded,
            )
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    def _discover_terminal_reconciliation_cases_in_transaction(
        self,
        conn: sqlite3.Connection,
        limit: int,
        *,
        exclude_persisted: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        """Classify terminal anomalies from one caller-owned SQLite snapshot."""

        bounded = _bounded_limit(limit, ceiling=10_000)
        # The returned/mutated batch is bounded, but discovery must always
        # inspect the complete production-supported terminal window.  A small
        # API limit must not report an empty inventory merely because healthy
        # terminal Graphs precede the first anomaly.
        graph_scan_limit = 10_000
        graphs = conn.execute(
            """
            SELECT * FROM task_runtime_graph
            WHERE status IN ('BLOCKED', 'COMPLETED', 'FAILED', 'CANCELLED')
            ORDER BY updated_ts_ms, graph_id, revision
            LIMIT ?
            """,
            (graph_scan_limit,),
        ).fetchall()
        cases: list[dict[str, Any]] = []
        for graph in graphs:
            try:
                observation = self._terminal_observation(conn, graph)
                classified = self._classify_terminal_observation(conn, observation)
                classified = _healthy_closure_if_required(
                    conn,
                    observation,
                    classified=classified,
                )
            except TaskStoreError:
                observation = self._structural_terminal_observation(conn, graph)
                classified = _quarantine(
                    "TERMINAL_DURABLE_PROJECTION_INVALID",
                    selected_budget=None,
                    selected_node=None,
                    selected_attempt=None,
                )
            if classified is None:
                continue
            candidate = self._build_terminal_case(observation, **classified)
            if exclude_persisted:
                existing = conn.execute(
                    """
                    SELECT 1
                    FROM task_runtime_terminal_reconciliation_case
                    WHERE case_id = ?
                    """,
                    (str(candidate["case_id"]),),
                ).fetchone()
                if existing is not None:
                    continue
            cases.append(candidate)
            if len(cases) >= bounded:
                break
        return tuple(cases)

    @staticmethod
    def _structural_terminal_observation(
        conn: sqlite3.Connection,
        graph: sqlite3.Row | Mapping[str, Any],
    ) -> dict[str, Any]:
        """Freeze only non-secret row identities when a codec fails closed."""

        return _structural_terminal_observation(conn, graph)

    def list_terminal_reconciliation_cases(
        self,
        *,
        classification: str | None = None,
        limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        self.assert_terminal_reconciliation_ready()
        bounded = _bounded_limit(limit, ceiling=1_000)
        parameters: list[Any] = []
        where = ""
        if classification is not None:
            normalized = str(classification or "").strip().upper()
            if normalized not in {
                "EXACT_REPAIRABLE",
                "SIGNED_REPAIR_REQUIRED",
                "QUARANTINED_STRUCTURAL",
            }:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "terminal reconciliation classification is invalid",
                )
            where = " WHERE classification = ?"
            parameters.append(normalized)
        parameters.append(bounded)
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            rows = conn.execute(
                "SELECT * FROM task_runtime_terminal_reconciliation_case"
                + where
                + " ORDER BY created_ts_ms, case_id LIMIT ?",
                tuple(parameters),
            ).fetchall()
            return tuple(_terminal_reconciliation_case_record(row) for row in rows)
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    def list_terminal_reconciliation_candidates(
        self,
        graph_id: str | None = None,
        graph_revision: int | None = None,
        *,
        classification: str | None = None,
        limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        """Return persisted, non-quarantined cases with exact signing subjects."""

        self.assert_terminal_reconciliation_ready()
        bounded = _bounded_limit(limit, ceiling=1_000)
        graph_ref = str(graph_id or "").strip() or None
        if graph_revision is not None and (
            type(graph_revision) is not int or graph_revision < 1 or graph_ref is None
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "graph_revision requires a graph_id and a positive integer",
            )
        clauses = [
            "classification <> 'QUARANTINED_STRUCTURAL'",
            "NOT EXISTS ("
            "SELECT 1 FROM task_runtime_terminal_reconciliation_proof AS proof "
            "WHERE proof.case_id = task_runtime_terminal_reconciliation_case.case_id"
            ")",
            "NOT EXISTS ("
            "SELECT 1 FROM task_runtime_terminal_reconciliation_supersession AS supersession "
            "WHERE supersession.prior_case_id = "
            "task_runtime_terminal_reconciliation_case.case_id"
            ")",
        ]
        parameters: list[Any] = []
        if classification is not None:
            normalized_classification = str(classification or "").strip().upper()
            if normalized_classification not in {
                "EXACT_REPAIRABLE",
                "SIGNED_REPAIR_REQUIRED",
            }:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "terminal reconciliation candidate classification is invalid",
                )
            clauses.append("classification = ?")
            parameters.append(normalized_classification)
        if graph_ref is not None:
            clauses.append("graph_id = ?")
            parameters.append(graph_ref)
        if graph_revision is not None:
            clauses.append("graph_revision = ?")
            parameters.append(graph_revision)
        parameters.append(10_000)
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            rows = conn.execute(
                "SELECT * FROM task_runtime_terminal_reconciliation_case WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_ts_ms, case_id LIMIT ?",
                tuple(parameters),
            ).fetchall()
            candidates: list[dict[str, Any]] = []
            for row in rows:
                case = _terminal_reconciliation_case_record(row)
                current = self._current_terminal_reconciliation_case(conn, case)
                if current is None or current["case_id"] != case["case_id"]:
                    continue
                candidates.append(self._case_aggregate(conn, row))
                if len(candidates) >= bounded:
                    break
            return tuple(candidates)
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    def read_terminal_reconciliation_case(self, case_id: str) -> dict[str, Any]:
        self.assert_terminal_reconciliation_ready()
        reference = str(case_id or "").strip()
        if not reference:
            raise TaskStoreError(TASK_STORE_INVALID, "case_id is required")
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT * FROM task_runtime_terminal_reconciliation_case WHERE case_id = ?",
                (reference,),
            ).fetchone()
            if row is None:
                raise TaskStoreError(
                    TASK_STORE_NOT_FOUND,
                    f"terminal reconciliation case not found: {reference}",
                )
            return self._case_aggregate(conn, row)
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    def _case_aggregate(
        self,
        conn: sqlite3.Connection,
        case_row: sqlite3.Row | Mapping[str, Any],
    ) -> dict[str, Any]:
        case = _terminal_reconciliation_case_record(case_row)
        proof_row = conn.execute(
            "SELECT * FROM task_runtime_terminal_reconciliation_proof WHERE case_id = ?",
            (case["case_id"],),
        ).fetchone()
        proof = None
        if proof_row is not None:
            proof = _terminal_reconciliation_proof_record(
                conn,
                proof_row,
                verifier=getattr(self, "_reconciliation_proof_verifier", None),
            )
            subject = {
                "snapshot": proof["subject_snapshot"],
                "snapshot_json": _dumps(proof["subject_snapshot"]),
                "snapshot_sha256": proof["subject_snapshot_sha256"],
            }
        else:
            supersession_row = conn.execute(
                """
                SELECT *
                FROM task_runtime_terminal_reconciliation_supersession
                WHERE prior_case_id = ?
                """,
                (str(case["case_id"]),),
            ).fetchone()
            if supersession_row is not None:
                _terminal_reconciliation_supersession_record(
                    conn,
                    supersession_row,
                )
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "terminal reconciliation case was superseded",
                )
            try:
                if case["classification"] != "QUARANTINED_STRUCTURAL":
                    self._assert_current_terminal_reconciliation_case(
                        conn,
                        case,
                    )
                subject = self._terminal_reconciliation_subject(conn, case)
            except TaskStoreError:
                if case["classification"] != "QUARANTINED_STRUCTURAL":
                    raise
                payload = {
                    "schema_version": (
                        "Natsume.task_runtime.terminal_reconciliation.subject.v1"
                    ),
                    "case_id": str(case["case_id"]),
                    "observation": dict(case["snapshot"]["observation"]),
                }
                snapshot_json, snapshot_sha256 = _canonical_digest(payload)
                subject = {
                    "snapshot": payload,
                    "snapshot_json": snapshot_json,
                    "snapshot_sha256": snapshot_sha256,
                }
        return {"case": case, "subject": subject, "proof": proof}

    def _assert_current_terminal_reconciliation_case(
        self,
        conn: sqlite3.Connection,
        case: Mapping[str, Any],
    ) -> None:
        """Reclassify an unsettled case from current facts in one snapshot.

        Graph/Budget versions alone do not capture late Operation, Review,
        Effect or decision authority.  An immutable case therefore remains
        executable only while a fresh classifier result is byte-for-byte the
        same case; a changed authority fact must create a new case/quarantine.
        """

        current = self._current_terminal_reconciliation_case(conn, case)
        if current is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal reconciliation case is no longer a current anomaly",
            )
        exact_fields = (
            "case_id",
            "schema_version",
            "case_kind",
            "classification",
            "reason_code",
            "graph_id",
            "graph_revision",
            "graph_status",
            "graph_status_version",
            "budget_id",
            "budget_status",
            "budget_status_version",
            "node_id",
            "node_status",
            "node_status_version",
            "attempt_id",
            "attempt_status",
            "attempt_status_version",
            "recommended_action",
            "snapshot_sha256",
        )
        if any(current.get(field) != case.get(field) for field in exact_fields):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal reconciliation case authority was reclassified",
            )

    def _current_terminal_reconciliation_case(
        self,
        conn: sqlite3.Connection,
        case: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        graph = conn.execute(
            "SELECT * FROM task_runtime_graph WHERE graph_id = ? AND revision = ?",
            (str(case["graph_id"]), int(case["graph_revision"])),
        ).fetchone()
        if graph is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal reconciliation case Graph disappeared",
            )
        observation = self._terminal_observation(conn, graph)
        classified = self._classify_terminal_observation(conn, observation)
        classified = _healthy_closure_if_required(
            conn,
            observation,
            classified=classified,
        )
        if classified is None:
            return None
        return self._build_terminal_case(observation, **classified)

    def _terminal_reconciliation_subject(
        self,
        conn: sqlite3.Connection,
        case: Mapping[str, Any],
    ) -> dict[str, Any]:
        graph = conn.execute(
            "SELECT * FROM task_runtime_graph WHERE graph_id = ? AND revision = ?",
            (str(case["graph_id"]), int(case["graph_revision"])),
        ).fetchone()
        if graph is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal reconciliation subject Graph disappeared",
            )
        observation = self._terminal_observation(conn, graph)
        payload = {
            "schema_version": "Natsume.task_runtime.terminal_reconciliation.subject.v1",
            "case_id": str(case["case_id"]),
            "observation": observation,
        }
        snapshot_json, snapshot_sha256 = _canonical_digest(payload)
        return {
            "snapshot": payload,
            "snapshot_json": snapshot_json,
            "snapshot_sha256": snapshot_sha256,
        }

    def _terminal_observation(
        self,
        conn: sqlite3.Connection,
        graph: sqlite3.Row | Mapping[str, Any],
    ) -> dict[str, Any]:
        graph_id = str(graph["graph_id"])
        revision = int(graph["revision"])
        budgets = conn.execute(
            """
            SELECT * FROM task_runtime_budget_reservation
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY CASE WHEN node_id IS NULL AND attempt_id IS NULL
                          THEN 0 ELSE 1 END,
                     node_id, attempt_id, budget_id
            """,
            (graph_id, revision),
        ).fetchall()
        nodes = conn.execute(
            """
            SELECT * FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id
            """,
            (graph_id, revision),
        ).fetchall()
        # Reuse the canonical TaskStore codec so terminal discovery cannot
        # treat a SQL/JSON identity, embedded-node or source-kind drift as a
        # clean terminal aggregate.
        _graph_record(graph, list(nodes))
        attempts = conn.execute(
            """
            SELECT * FROM task_runtime_attempt
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY node_id, attempt_no, attempt_id
            """,
            (graph_id, revision),
        ).fetchall()
        leases = conn.execute(
            """
            SELECT lease.* FROM task_runtime_lease AS lease
            JOIN task_runtime_attempt AS attempt
              ON attempt.attempt_id = lease.attempt_id
            WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
            ORDER BY lease.attempt_id, lease.fencing_token, lease.lease_id
            """,
            (graph_id, revision),
        ).fetchall()
        operations = conn.execute(
            """
            SELECT reservation.*
            FROM task_runtime_idempotency_reservation AS reservation
            JOIN task_runtime_attempt AS attempt
              ON attempt.attempt_id = reservation.attempt_id
            WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
            ORDER BY reservation.reservation_id
            """,
            (graph_id, revision),
        ).fetchall()
        encoded_graph = quote(graph_id, safe="")
        review_prefix = f"task-node://{encoded_graph}/{revision}/"
        review_rows = conn.execute(
            """
            SELECT *
            FROM agent_runtime_review
            WHERE task_graph_id = ?
            ORDER BY review_id
            """,
            (graph_id,),
        ).fetchall()
        reviews = [
            _review_projection(
                conn,
                row,
                graph_id=graph_id,
                graph_revision=revision,
            )
            for row in review_rows
            if str(row["subject_ref"]).startswith(review_prefix)
        ]
        effects = conn.execute(
            """
            SELECT effect_id, node_id, attempt_id, status, status_version,
                   commit_scope, receipt_ref, reason_code, updated_ts_ms
            FROM task_runtime_acceptance_effect_intent
            WHERE graph_id = ? AND graph_revision = ? ORDER BY effect_id
            """,
            (graph_id, revision),
        ).fetchall()
        parallel = conn.execute(
            """
            SELECT * FROM task_runtime_parallel_settlement_decision
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY graph_version_after, decision_ref
            """,
            (graph_id, revision),
        ).fetchall()
        success = conn.execute(
            """
            SELECT decision_ref, graph_version_before, graph_version_after,
                   budget_id, budget_version_before, budget_version_after,
                   payload_sha256, graph_outbox_id, budget_outbox_id
            FROM task_runtime_parallel_success_decision
            WHERE graph_id = ? AND graph_revision = ? ORDER BY decision_ref
            """,
            (graph_id, revision),
        ).fetchall()
        cancellations = conn.execute(
            """
            SELECT intent.intent_ref, intent.status, intent.status_version,
                   decision.decision_ref, decision.payload_sha256
            FROM task_runtime_graph_cancellation_intent AS intent
            LEFT JOIN task_runtime_graph_cancellation_decision AS decision
              ON decision.intent_ref = intent.intent_ref
            WHERE intent.graph_id = ? AND intent.graph_revision = ?
            ORDER BY intent.intent_ref
            """,
            (graph_id, revision),
        ).fetchall()
        return {
            "graph": _graph_projection(graph),
            "budgets": [_budget_projection(row) for row in budgets],
            "nodes": [_node_projection(row) for row in nodes],
            "attempts": [_attempt_projection(row) for row in attempts],
            "leases": [_lease_projection(row) for row in leases],
            "operations": [_operation_projection(row) for row in operations],
            "reviews": reviews,
            "effects": [dict(row) for row in effects],
            "parallel_decisions": [
                {
                    "decision_ref": str(row["decision_ref"]),
                    "graph_version_after": int(row["graph_version_after"]),
                    "payload_sha256": str(row["payload_sha256"]),
                }
                for row in parallel
            ],
            "success_decisions": [dict(row) for row in success],
            "cancellations": [dict(row) for row in cancellations],
        }

    def _classify_terminal_observation(
        self,
        conn: sqlite3.Connection,
        observation: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        graph = observation["graph"]
        status = str(graph["status"])
        all_budgets = list(observation["budgets"])
        graph_budgets = [
            row
            for row in all_budgets
            if row.get("node_id") is None and row.get("attempt_id") is None
        ]
        child_budgets = [
            row
            for row in all_budgets
            if row.get("node_id") is not None or row.get("attempt_id") is not None
        ]
        nodes = list(observation["nodes"])
        attempts = list(observation["attempts"])
        leases = list(observation["leases"])
        operations = list(observation["operations"])
        reviews = list(observation["reviews"])
        effects = list(observation["effects"])
        failure_nodes = [row for row in nodes if row["status"] in _FAILURE_NODE_STATUSES]
        if child_budgets or len(graph_budgets) != 1:
            return _quarantine(
                "TERMINAL_GRAPH_BUDGET_BINDING_AMBIGUOUS",
                selected_budget=_only_or_none(child_budgets or graph_budgets),
                selected_node=_only_or_none(failure_nodes),
                selected_attempt=None,
            )
        budgets = graph_budgets
        budget = graph_budgets[0]
        unsettled = [
            row
            for row in budgets
            if row["status"] in _UNSETTLED_BUDGET_STATUSES
        ]
        live_conflict = (
            any(row["status"] in _LIVE_ATTEMPT_STATUSES for row in attempts)
            or any(row["status"] == "ACTIVE" for row in leases)
            or any(row["status"] in _UNRESOLVED_OPERATION_STATUSES for row in operations)
            or any(row["status"] in _ACTIVE_REVIEW_STATUSES for row in reviews)
            or bool(effects)
        )
        authority_conflict = bool(
            observation["parallel_decisions"]
            or observation["success_decisions"]
            or observation["cancellations"]
        )
        parallel_decision = canonical_parallel_terminal_decision(conn, graph)
        terminal_lineage_valid = _terminal_execution_lineage_valid(
            attempts,
            leases,
        )

        if status == "BLOCKED":
            if parallel_decision is not None:
                if (
                    parallel_decision["graph_target_status"] == "BLOCKED"
                    and self._parallel_terminal_snapshot_matches(
                        parallel_decision,
                        observation,
                    )
                ):
                    return None
                return _quarantine(
                    "BLOCKED_PARALLEL_DECISION_AFTER_STATE_DRIFTED",
                    selected_budget=budget,
                    selected_node=_only_or_none(failure_nodes),
                    selected_attempt=None,
                )
            if _has_exact_review_checkpoint(nodes, reviews) and not failure_nodes:
                non_review_live_conflict = (
                    any(row["status"] in _LIVE_ATTEMPT_STATUSES for row in attempts)
                    or any(row["status"] == "ACTIVE" for row in leases)
                    or any(
                        row["status"] in _UNRESOLVED_OPERATION_STATUSES
                        for row in operations
                    )
                    or bool(effects)
                )
                if (
                    not non_review_live_conflict
                    and not authority_conflict
                    and budget["status"] == "ACTIVE"
                    and terminal_lineage_valid
                ):
                    return None
                return _quarantine(
                    "BLOCKED_REVIEW_CHECKPOINT_HAS_CONFLICTING_AUTHORITY",
                    selected_budget=budget,
                    selected_node=_only_or_none(nodes),
                    selected_attempt=None,
                )
            if failure_nodes:
                selected_node, selected_attempt = _selected_failure_lineage(
                    failure_nodes,
                    attempts,
                )
                if (
                    selected_node is not None
                    and selected_attempt is not None
                    and not live_conflict
                    and not authority_conflict
                    and terminal_lineage_valid
                ):
                    if (
                        budget["status"] == "RELEASED"
                        and self._exact_checkpoint_budget_rejection_terminal(
                            conn,
                            observation=observation,
                            selected_node=selected_node,
                            selected_attempt=selected_attempt,
                            selected_budget=budget,
                        )
                    ):
                        return None
                    if budget["status"] in {"PENDING", "CONSUMED"}:
                        return _quarantine(
                            "BLOCKED_FAILURE_BUDGET_STATE_AMBIGUOUS",
                            selected_budget=budget,
                            selected_node=selected_node,
                            selected_attempt=selected_attempt,
                        )
                    release = budget["status"] in _UNSETTLED_BUDGET_STATUSES
                    if budget["status"] not in {
                        "ACTIVE",
                        "EXCEEDED",
                        "RELEASED",
                        "CANCELLED",
                    }:
                        return _quarantine(
                            "BLOCKED_FAILURE_BUDGET_STATE_AMBIGUOUS",
                            selected_budget=budget,
                            selected_node=selected_node,
                            selected_attempt=selected_attempt,
                        )
                    return {
                        "case_kind": "BLOCKED_FAILURE_NODE",
                        "classification": "SIGNED_REPAIR_REQUIRED",
                        "reason_code": "BLOCKED_GRAPH_HAS_DURABLE_FAILURE",
                        "recommended_action": (
                            "FAIL_GRAPH_AND_RELEASE_BUDGET" if release else "FAIL_GRAPH"
                        ),
                        "selected_budget": budget,
                        "selected_node": selected_node,
                        "selected_attempt": selected_attempt,
                        "failure_bundle": None,
                    }
            return _quarantine(
                "BLOCKED_TERMINAL_AUTHORITY_AMBIGUOUS",
                selected_budget=_only_or_none(unsettled or budgets),
                selected_node=_only_or_none(failure_nodes),
                selected_attempt=None,
            )

        if status == "FAILED":
            if not unsettled:
                parallel_valid = (
                    parallel_decision is not None
                    and parallel_decision["graph_target_status"] == "FAILED"
                    and self._parallel_terminal_snapshot_matches(
                        parallel_decision,
                        observation,
                    )
                )
                healthy_authority = (
                    not observation["parallel_decisions"] or parallel_valid
                ) and not observation["success_decisions"] and not observation[
                    "cancellations"
                ]
                if (
                    live_conflict
                    or not terminal_lineage_valid
                    or not healthy_authority
                    or budget["status"] != "RELEASED"
                    or not failure_nodes
                ):
                    return _quarantine(
                        "FAILED_TERMINAL_FACTS_AMBIGUOUS",
                        selected_budget=budget,
                        selected_node=_only_or_none(failure_nodes),
                        selected_attempt=None,
                    )
                return None
            selected_budget = _only_or_none(unsettled)
            bundle = self._exact_atomic_failure_bundle(conn, observation)
            exceeded_authority_valid = True
            if selected_budget is not None and selected_budget["status"] == "EXCEEDED":
                try:
                    budget_row = conn.execute(
                        "SELECT * FROM task_runtime_budget_reservation WHERE budget_id = ?",
                        (str(selected_budget["budget_id"]),),
                    ).fetchone()
                    if budget_row is None:
                        raise TaskStoreError(
                            TASK_STORE_DURABLE_DATA_INVALID,
                            "EXCEEDED Budget disappeared during terminal discovery",
                        )
                    _assert_terminal_budget_authority(
                        conn,
                        budget_row=budget_row,
                        terminal_version=int(selected_budget["status_version"]),
                    )
                except TaskStoreError:
                    exceeded_authority_valid = False
            if (
                selected_budget is not None
                and bundle is not None
                and not live_conflict
                and terminal_lineage_valid
                and not authority_conflict
                and not observation["operations"]
                and exceeded_authority_valid
            ):
                return {
                    "case_kind": "FAILED_UNSETTLED_BUDGET",
                    "classification": "EXACT_REPAIRABLE",
                    "reason_code": "EXACT_ATOMIC_FAILURE_BUNDLE_BUDGET_UNSETTLED",
                    "recommended_action": "RELEASE_BUDGET",
                    "selected_budget": selected_budget,
                    "selected_node": bundle["node"],
                    "selected_attempt": bundle["attempt"],
                    "failure_bundle": bundle,
                }
            return _quarantine(
                "FAILED_UNSETTLED_BUDGET_EVIDENCE_AMBIGUOUS",
                selected_budget=selected_budget,
                selected_node=_only_or_none(failure_nodes),
                selected_attempt=None,
            )

        if status == "CANCELLED":
            if not unsettled:
                cancellation_rows = list(observation["cancellations"])
                cancellation_authority_valid = (
                    len(cancellation_rows) == 1
                    and str(cancellation_rows[0].get("status") or "")
                    == "FINALIZED"
                    and graph_cancellation_authority_valid(
                        self,
                        conn,
                        graph,
                        budget,
                    )
                )
                if (
                    live_conflict
                    or not terminal_lineage_valid
                    or observation["parallel_decisions"]
                    or observation["success_decisions"]
                    or not cancellation_authority_valid
                    or budget["status"] not in {"RELEASED", "CANCELLED"}
                ):
                    return _quarantine(
                        "CANCELLED_TERMINAL_FACTS_AMBIGUOUS",
                        selected_budget=budget,
                        selected_node=_only_or_none(failure_nodes),
                        selected_attempt=None,
                    )
                return None
            selected_budget = _only_or_none(unsettled)
            if (
                selected_budget is not None
                and not observation["cancellations"]
                and not live_conflict
                and terminal_lineage_valid
                and not observation["parallel_decisions"]
                and not observation["success_decisions"]
            ):
                selected_node, selected_attempt = _selected_failure_lineage(
                    failure_nodes,
                    attempts,
                )
                return {
                    "case_kind": "CANCELLED_UNSETTLED_BUDGET",
                    "classification": "SIGNED_REPAIR_REQUIRED",
                    "reason_code": "LEGACY_CANCELLED_GRAPH_BUDGET_UNSETTLED",
                    "recommended_action": "RELEASE_BUDGET",
                    "selected_budget": selected_budget,
                    "selected_node": selected_node,
                    "selected_attempt": selected_attempt,
                    "failure_bundle": None,
                }
            return _quarantine(
                "CANCELLED_TERMINAL_AUTHORITY_AMBIGUOUS",
                selected_budget=selected_budget,
                selected_node=_only_or_none(failure_nodes),
                selected_attempt=None,
            )

        if status == "COMPLETED":
            completed_live_conflict = (
                any(row["status"] in _LIVE_ATTEMPT_STATUSES for row in attempts)
                or any(row["status"] == "ACTIVE" for row in leases)
                or any(
                    row["status"] in _UNRESOLVED_OPERATION_STATUSES
                    for row in operations
                )
                or any(row["status"] in _ACTIVE_REVIEW_STATUSES for row in reviews)
            )
            consistent = (
                budget["status"] == "CONSUMED"
                and bool(nodes)
                and all(row["status"] == "ACCEPTED" for row in nodes)
                and not completed_live_conflict
                and terminal_lineage_valid
                and not observation["parallel_decisions"]
                and not observation["cancellations"]
                and parallel_success_authority_valid(self, conn, graph)
            )
            if consistent:
                return None
            return {
                "case_kind": "COMPLETED_INCONSISTENT",
                "classification": "QUARANTINED_STRUCTURAL",
                "reason_code": "COMPLETED_GRAPH_TERMINAL_FACTS_INCONSISTENT",
                "recommended_action": "NONE",
                "selected_budget": _only_or_none(budgets),
                "selected_node": None,
                "selected_attempt": None,
                "failure_bundle": None,
            }
        return None

    def _exact_checkpoint_budget_rejection_terminal(
        self,
        conn: sqlite3.Connection,
        *,
        observation: Mapping[str, Any],
        selected_node: Mapping[str, Any],
        selected_attempt: Mapping[str, Any],
        selected_budget: Mapping[str, Any],
    ) -> bool:
        """Recognize any exact atomic checkpoint Budget terminal authority."""
        try:
            resolved = self._try_resolve_checkpoint_budget_rejection_terminal_uow(
                conn,
                str(selected_attempt["attempt_id"]),
            )
            if resolved is None:
                return False
            graph = resolved["graph"]
            node = resolved["node"]
            attempt = resolved["attempt"]
            budget = resolved["budget"]
            exact = (
                str(graph["graph_id"]) == str(observation["graph"]["graph_id"])
                and int(graph["revision"])
                == int(observation["graph"]["revision"])
                and str(node["node_id"]) == str(selected_node["node_id"])
                and int(node["status_version"])
                == int(selected_node["status_version"])
                and int(attempt["status_version"])
                == int(selected_attempt["status_version"])
                and str(budget["budget_id"])
                == str(selected_budget["budget_id"])
                and int(budget["status_version"])
                == int(selected_budget["status_version"])
            )
        except TaskStoreError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "checkpoint budget terminal projection is invalid",
            ) from exc
        if not exact:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "checkpoint budget terminal reconciliation identity drifted",
            )
        return True

    @staticmethod
    def _parallel_terminal_snapshot_matches(
        decision: Mapping[str, Any],
        observation: Mapping[str, Any],
    ) -> bool:
        budgets = [
            row
            for row in observation["budgets"]
            if row.get("node_id") is None and row.get("attempt_id") is None
        ]
        if len(budgets) != 1:
            return False
        budget = budgets[0]
        budget_target = decision.get("budget_target_status")
        expected_budget_status = (
            str(budget_target)
            if budget_target is not None
            else str(decision["budget_status_before"])
        )
        expected_budget_version = int(decision["budget_status_version_before"]) + (
            1 if budget_target is not None else 0
        )
        current_nodes = {
            str(row["node_id"]): {
                "status": str(row["status"]),
                "status_version": int(row["status_version"]),
            }
            for row in observation["nodes"]
        }
        expected_nodes = {
            str(node_id): {
                "status": str(value["status"]),
                "status_version": int(value["status_version"]),
            }
            for node_id, value in dict(decision["node_states"]).items()
        }
        live_execution = (
            any(
                row["status"] in _LIVE_ATTEMPT_STATUSES
                for row in observation["attempts"]
            )
            or any(row["status"] == "ACTIVE" for row in observation["leases"])
            or any(
                row["status"] in _UNRESOLVED_OPERATION_STATUSES
                for row in observation["operations"]
            )
            or bool(observation["effects"])
        )
        decision_kind = str(decision["decision_kind"])
        if decision_kind == "REVIEW":
            reviews_valid = _has_exact_review_checkpoint(
                list(observation["nodes"]),
                list(observation["reviews"]),
            )
        else:
            reviews_valid = not any(
                row["status"] in _ACTIVE_REVIEW_STATUSES
                for row in observation["reviews"]
            )
        return bool(
            str(budget["budget_id"]) == str(decision["budget_id"])
            and str(budget["status"]) == expected_budget_status
            and int(budget["status_version"]) == expected_budget_version
            and current_nodes == expected_nodes
            and not live_execution
            and _terminal_execution_lineage_valid(
                list(observation["attempts"]),
                list(observation["leases"]),
            )
            and reviews_valid
            and not observation["success_decisions"]
            and not observation["cancellations"]
        )

    def _exact_atomic_failure_bundle(
        self,
        conn: sqlite3.Connection,
        observation: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        graph = observation["graph"]
        failures = [
            row for row in observation["nodes"] if row["status"] == "FAILED"
        ]
        selected_node, selected_attempt = _selected_failure_lineage(
            failures,
            observation["attempts"],
        )
        if selected_node is None or selected_attempt is None:
            return None
        if selected_attempt["status"] != "FAILED" or not selected_attempt.get("error_ref"):
            return None
        attempt_id = str(selected_attempt["attempt_id"])
        terminal_ts = selected_attempt.get("completed_ts_ms")
        if terminal_ts is None or any(
            int(value) != int(terminal_ts)
            for value in (
                selected_attempt["updated_ts_ms"],
                selected_node["updated_ts_ms"],
                graph["updated_ts_ms"],
            )
        ):
            return None
        matched_leases = [
            row
            for row in observation["leases"]
            if row["attempt_id"] == attempt_id
            and row["owner_id"] == selected_attempt["owner_id"]
        ]
        if len(matched_leases) != 1:
            return None
        lease = matched_leases[0]
        if (
            lease["status"] != "RELEASED"
            or lease.get("released_ts_ms") is None
            or int(lease["released_ts_ms"]) != int(terminal_ts)
        ):
            return None
        if any(row["attempt_id"] == attempt_id for row in observation["operations"]):
            return None
        expected_rows = {
            "attempt": _one_outbox(
                conn,
                aggregate_type="TASK_ATTEMPT",
                aggregate_id=attempt_id,
                aggregate_version=int(selected_attempt["status_version"]),
                event_type="TASK_ATTEMPT_FAILED",
                graph_revision=None,
            ),
            "node": _one_outbox(
                conn,
                aggregate_type="TASK_NODE",
                aggregate_id=(
                    f"{graph['graph_id']}@{graph['revision']}/{selected_node['node_id']}"
                ),
                aggregate_version=int(selected_node["status_version"]),
                event_type="TASK_NODE_FAILED",
                graph_revision=None,
            ),
            "graph": _one_outbox(
                conn,
                aggregate_type="TASK_GRAPH",
                aggregate_id=str(graph["graph_id"]),
                aggregate_version=int(graph["status_version"]),
                event_type="TASK_GRAPH_FAILED",
                graph_revision=int(graph["revision"]),
            ),
            "lease": _one_outbox(
                conn,
                aggregate_type="TASK_LEASE",
                aggregate_id=str(lease["lease_id"]),
                aggregate_version=int(lease["status_version"]),
                event_type="TASK_LEASE_RELEASED_TERMINAL",
                graph_revision=None,
            ),
        }
        if any(value is None for value in expected_rows.values()):
            return None
        outboxes = {name: _outbox_projection(row) for name, row in expected_rows.items()}
        evidence = tuple(outboxes["attempt"]["evidence_refs"])
        if (
            str(selected_attempt["error_ref"]) not in evidence
            or any(tuple(item["evidence_refs"]) != evidence for item in outboxes.values())
            or any(item["attempt_id"] != attempt_id for item in outboxes.values())
            or any(int(item["created_ts_ms"]) != int(terminal_ts) for item in outboxes.values())
            or any(
                item["payload_ref"]
                != build_outbox_payload_ref(
                    aggregate_type=item["aggregate_type"],
                    aggregate_id=item["aggregate_id"],
                    aggregate_version=item["aggregate_version"],
                    graph_revision=item["graph_revision"],
                )
                for item in outboxes.values()
            )
        ):
            return None
        deterministic = {
            "node": (
                f"attempt-terminal-node:{attempt_id}:"
                f"{graph['graph_id']}@{graph['revision']}/{selected_node['node_id']}:"
                f"{selected_node['status_version']}"
            ),
            "graph": (
                f"attempt-terminal-graph:{attempt_id}:{graph['graph_id']}:"
                f"{graph['status_version']}"
            ),
            "lease": (
                f"attempt-terminal-lease:{attempt_id}:{lease['lease_id']}:"
                f"{lease['status_version']}"
            ),
        }
        for kind, key in deterministic.items():
            if (
                outboxes[kind]["idempotency_key"] != key
                or outboxes[kind]["outbox_id"] != f"outbox::{key}"
                or outboxes[kind]["payload_ref"]
                != build_outbox_payload_ref(
                    aggregate_type=outboxes[kind]["aggregate_type"],
                    aggregate_id=outboxes[kind]["aggregate_id"],
                    aggregate_version=outboxes[kind]["aggregate_version"],
                    graph_revision=outboxes[kind]["graph_revision"],
                )
            ):
                return None
        return {
            "node": selected_node,
            "attempt": selected_attempt,
            "lease": lease,
            "outboxes": outboxes,
            "evidence_refs": list(evidence),
            "bundle_digest": _value_digest(outboxes),
        }

    @staticmethod
    def _build_terminal_case(
        observation: Mapping[str, Any],
        *,
        case_kind: str,
        classification: str,
        reason_code: str,
        recommended_action: str,
        selected_budget: Mapping[str, Any] | None,
        selected_node: Mapping[str, Any] | None,
        selected_attempt: Mapping[str, Any] | None,
        failure_bundle: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        return _build_terminal_case_projection(
            observation,
            case_kind=case_kind,
            classification=classification,
            reason_code=reason_code,
            recommended_action=recommended_action,
            selected_budget=selected_budget,
            selected_node=selected_node,
            selected_attempt=selected_attempt,
            failure_bundle=failure_bundle,
        )


__all__ = ["TaskTerminalReconciliationReadMixin"]
