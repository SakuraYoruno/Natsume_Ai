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
from host_brain.contracts.agent_runtime.terminal_reconciliation import (
    is_signed_prebudget_cancellation_classifier_correction,
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
)

from .task_terminal_reconciliation_classifier import (
    TaskTerminalReconciliationClassifierMixin,
)
from .task_terminal_reconciliation_common import (
    _canonical_digest,
    _terminal_reconciliation_case_record,
    _terminal_reconciliation_proof_record,
    _terminal_reconciliation_supersession_record,
)
from .task_terminal_reconciliation_schema import (
    assert_terminal_reconciliation_schema,
)
from .task_terminal_reconciliation_projection import (
    _attempt_projection,
    _bounded_limit,
    _build_terminal_case as _build_terminal_case_projection,
    _budget_projection,
    _graph_projection,
    _healthy_closure_if_required,
    _lease_projection,
    _node_projection,
    _operation_projection,
    _quarantine,
    _review_projection,
    _structural_terminal_observation,
)


_TERMINAL_GRAPH_STATUSES = ("BLOCKED", "COMPLETED", "FAILED", "CANCELLED")


class TaskTerminalReconciliationReadMixin(TaskTerminalReconciliationClassifierMixin):
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
                observation = _structural_terminal_observation(conn, graph)
                classified = _quarantine(
                    "TERMINAL_DURABLE_PROJECTION_INVALID",
                    selected_budget=None,
                    selected_node=None,
                    selected_attempt=None,
                )
            if classified is None:
                continue
            candidate = _build_terminal_case_projection(observation, **classified)
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
        """Return persisted signable cases with exact current subjects."""

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
            "(classification <> 'QUARANTINED_STRUCTURAL' OR ("
            "case_kind = 'TERMINAL_STRUCTURAL_ANOMALY' AND "
            "reason_code = 'TERMINAL_GRAPH_BUDGET_BINDING_AMBIGUOUS' AND "
            "graph_status = 'CANCELLED' AND budget_id IS NULL AND "
            "recommended_action = 'NONE'))",
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
                if is_signed_prebudget_cancellation_classifier_correction(case):
                    try:
                        self._assert_current_terminal_reconciliation_case(conn, case)
                    except TaskStoreError:
                        continue
                    candidates.append(self._case_aggregate(conn, row))
                    if len(candidates) >= bounded:
                        break
                    continue
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
                if (
                    case["classification"] != "QUARANTINED_STRUCTURAL"
                    or is_signed_prebudget_cancellation_classifier_correction(case)
                ):
                    self._assert_current_terminal_reconciliation_case(
                        conn,
                        case,
                    )
                subject = self._terminal_reconciliation_subject(conn, case)
            except TaskStoreError:
                if (
                    case["classification"] != "QUARANTINED_STRUCTURAL"
                    or is_signed_prebudget_cancellation_classifier_correction(case)
                ):
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
            if is_signed_prebudget_cancellation_classifier_correction(case):
                graph = conn.execute(
                    "SELECT * FROM task_runtime_graph WHERE graph_id = ? AND revision = ?",
                    (str(case["graph_id"]), int(case["graph_revision"])),
                ).fetchone()
                if graph is not None:
                    observation = self._terminal_observation(conn, graph)
                    if (
                        dict(case["snapshot"].get("observation") or {})
                        == dict(observation)
                        and self._classify_terminal_observation(conn, observation) is None
                    ):
                        return
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
        return _build_terminal_case_projection(observation, **classified)

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


__all__ = ["TaskTerminalReconciliationReadMixin"]
