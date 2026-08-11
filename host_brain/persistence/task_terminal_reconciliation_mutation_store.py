"""Transactional inventory and repair for historical terminal half-states."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import Any, Mapping

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetReservationStatus,
    validate_budget_transition,
)
from host_brain.contracts.agent_runtime.task_graph import TaskGraphStatus
from host_brain.contracts.agent_runtime.terminal_reconciliation import (
    TerminalReconciliationStoreRejected,
    is_signed_prebudget_cancellation_classifier_correction,
)
from host_brain.persistence.task_record_codec import _graph_record
from host_brain.persistence.reconciliation_proof_verifier import (
    TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
    TASK_STORE_RECONCILIATION_PROOF_INVALID,
)
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_NOT_FOUND,
    TaskStoreError,
)

from .task_terminal_reconciliation_common import (
    TERMINAL_RECONCILIATION_CASE_SCHEMA,
    TERMINAL_RECONCILIATION_PROOF_SCHEMA,
    TERMINAL_RECONCILIATION_SUPERSESSION_SCHEMA,
    _canonical_digest,
    _canonical_json,
    _terminal_reconciliation_case_record,
    _terminal_reconciliation_proof_record,
    _terminal_supersession_id,
    build_terminal_reconciliation_attestation_binding,
)


_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class TaskTerminalReconciliationMutationMixin:
    """Append cases and apply one proof under the owner's SQLite UoW."""

    def inventory_terminal_reconciliation_cases(
        self,
        *,
        limit: int = 500,
    ) -> dict[str, Any]:
        self.assert_terminal_reconciliation_ready()
        size = max(1, min(int(limit), 10_000))
        ts_ms = self._now()
        with self._transaction() as conn:
            discovered = self._discover_terminal_reconciliation_cases_in_transaction(
                conn,
                size,
                exclude_persisted=True,
            )
            inserted = 0
            persisted: list[dict[str, Any]] = []
            for candidate in discovered:
                case = self._case_mapping(candidate)
                row = conn.execute(
                    """
                    SELECT * FROM task_runtime_terminal_reconciliation_case
                    WHERE case_id = ?
                    """,
                    (str(case["case_id"]),),
                ).fetchone()
                if row is None:
                    self._insert_terminal_reconciliation_case(
                        conn,
                        case,
                        created_ts_ms=ts_ms,
                    )
                    inserted += 1
                    row = conn.execute(
                        """
                        SELECT * FROM task_runtime_terminal_reconciliation_case
                        WHERE case_id = ?
                        """,
                        (str(case["case_id"]),),
                    ).fetchone()
                if row is None:
                    raise TaskStoreError(
                        TASK_STORE_DURABLE_DATA_INVALID,
                        "terminal reconciliation case insert was not durable",
                    )
                persisted.append(_terminal_reconciliation_case_record(row))
                self._append_terminal_reconciliation_supersessions(
                    conn,
                    replacement_case=_terminal_reconciliation_case_record(row),
                    created_ts_ms=ts_ms,
                )
            return {
                "status": "INVENTORIED",
                "discovered_count": len(discovered),
                "inserted_count": inserted,
                "cases": tuple(persisted),
            }

    def _append_terminal_reconciliation_supersessions(
        self,
        conn: sqlite3.Connection,
        *,
        replacement_case: Mapping[str, Any],
        created_ts_ms: int,
    ) -> None:
        prior_rows = conn.execute(
            """
            SELECT prior.*
            FROM task_runtime_terminal_reconciliation_case AS prior
            WHERE prior.graph_id = ? AND prior.graph_revision = ?
              AND prior.case_id <> ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM task_runtime_terminal_reconciliation_proof AS proof
                  WHERE proof.case_id = prior.case_id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM task_runtime_terminal_reconciliation_supersession AS supersession
                  WHERE supersession.prior_case_id = prior.case_id
              )
            ORDER BY prior.created_ts_ms, prior.case_id
            """,
            (
                str(replacement_case["graph_id"]),
                int(replacement_case["graph_revision"]),
                str(replacement_case["case_id"]),
            ),
        ).fetchall()
        for prior_row in prior_rows:
            prior = _terminal_reconciliation_case_record(prior_row)
            supersession_id = _terminal_supersession_id(
                prior_case_id=str(prior["case_id"]),
                replacement_case_id=str(replacement_case["case_id"]),
                prior_snapshot_sha256=str(prior["snapshot_sha256"]),
                replacement_snapshot_sha256=str(
                    replacement_case["snapshot_sha256"]
                ),
            )
            conn.execute(
                """
                INSERT INTO task_runtime_terminal_reconciliation_supersession(
                    supersession_id, schema_version, prior_case_id,
                    replacement_case_id, graph_id, graph_revision,
                    prior_snapshot_sha256, replacement_snapshot_sha256,
                    reason_code, created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                          'CURRENT_CASE_RECLASSIFIED', ?)
                """,
                (
                    supersession_id,
                    TERMINAL_RECONCILIATION_SUPERSESSION_SCHEMA,
                    str(prior["case_id"]),
                    str(replacement_case["case_id"]),
                    str(replacement_case["graph_id"]),
                    int(replacement_case["graph_revision"]),
                    str(prior["snapshot_sha256"]),
                    str(replacement_case["snapshot_sha256"]),
                    int(created_ts_ms),
                ),
            )

    def record_terminal_reconciliation_proof(
        self,
        case_id: str,
        *,
        expected_case_snapshot_sha256: str,
        expected_subject_snapshot_sha256: str,
        action: str,
        target_graph_status: str | None,
        target_budget_status: str | None,
        authority_kind: str,
        reconciler_actor_id: str,
        issued_ts_ms: int,
        proof_id: str,
        graph_outbox: OutboxMessage | None,
        budget_outbox: OutboxMessage | None,
        attestation_ref: str | None = None,
        attestation_key_id: str | None = None,
        attestation_signature: str | None = None,
    ) -> dict[str, Any]:
        """Commit Graph/Budget, mutation Outboxes and proof atomically."""

        try:
            return self._record_terminal_reconciliation_proof(
                case_id,
                expected_case_snapshot_sha256=expected_case_snapshot_sha256,
                expected_subject_snapshot_sha256=expected_subject_snapshot_sha256,
                action=action,
                target_graph_status=target_graph_status,
                target_budget_status=target_budget_status,
                authority_kind=authority_kind,
                reconciler_actor_id=reconciler_actor_id,
                issued_ts_ms=issued_ts_ms,
                proof_id=proof_id,
                graph_outbox=graph_outbox,
                budget_outbox=budget_outbox,
                attestation_ref=attestation_ref,
                attestation_key_id=attestation_key_id,
                attestation_signature=attestation_signature,
            )
        except TaskStoreError as exc:
            raise TerminalReconciliationStoreRejected(
                exc.reason_code,
                exc.detail,
            ) from exc

    def _record_terminal_reconciliation_proof(
        self,
        case_id: str,
        **raw: Any,
    ) -> dict[str, Any]:
        request = self._normalize_terminal_reconciliation_mutation(
            case_id=case_id,
            **raw,
        )
        ts_ms = self._now()
        with self._transaction() as conn:
            inserted_case = False
            case_row = conn.execute(
                """
                SELECT * FROM task_runtime_terminal_reconciliation_case
                WHERE case_id = ?
                """,
                (request["case_id"],),
            ).fetchone()
            if case_row is None:
                discovered = (
                    self._discover_terminal_reconciliation_cases_in_transaction(
                        conn,
                        2_000,
                    )
                )
                candidate = next(
                    (
                        self._case_mapping(item)
                        for item in discovered
                        if str(self._case_mapping(item).get("case_id") or "")
                        == request["case_id"]
                    ),
                    None,
                )
                if candidate is None:
                    raise TaskStoreError(
                        TASK_STORE_NOT_FOUND,
                        f"terminal reconciliation case not found: {request['case_id']}",
                    )
                self._insert_terminal_reconciliation_case(
                    conn,
                    candidate,
                    created_ts_ms=ts_ms,
                )
                inserted_case = True
                case_row = conn.execute(
                    """
                    SELECT * FROM task_runtime_terminal_reconciliation_case
                    WHERE case_id = ?
                    """,
                    (request["case_id"],),
                ).fetchone()
            if case_row is None:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "terminal reconciliation case insert was not durable",
                )
            case = _terminal_reconciliation_case_record(case_row)
            if inserted_case:
                self._append_terminal_reconciliation_supersessions(
                    conn,
                    replacement_case=case,
                    created_ts_ms=ts_ms,
                )
            existing = conn.execute(
                """
                SELECT * FROM task_runtime_terminal_reconciliation_proof
                WHERE case_id = ?
                """,
                (request["case_id"],),
            ).fetchone()
            if existing is not None:
                return self._replay_terminal_reconciliation_proof(
                    conn,
                    case=case,
                    proof_row=existing,
                    request=request,
                )

            # Re-run the complete classifier under the same BEGIN IMMEDIATE
            # transaction as the repair.  Late Operation/Review/Effect or
            # decision facts can change authority without changing Graph or
            # Budget versions; current subject digest alone cannot authorize
            # an action inherited from an older case classification.
            self._assert_current_terminal_reconciliation_case(conn, case)
            subject_value = self._terminal_reconciliation_subject(conn, case)
            subject, subject_digest = self._subject_snapshot(subject_value)
            self._assert_terminal_reconciliation_request(
                conn,
                case=case,
                subject=subject,
                subject_digest=subject_digest,
                request=request,
            )
            graph_outbox = request["graph_outbox"]
            budget_outbox = request["budget_outbox"]
            binding, binding_bytes = (
                build_terminal_reconciliation_attestation_binding(
                    case=case,
                    subject_snapshot_sha256=subject_digest,
                    action=request["action"],
                    target_graph_status=request["target_graph_status"],
                    target_budget_status=request["target_budget_status"],
                    authority_kind=request["authority_kind"],
                    reconciler_actor_id=request["reconciler_actor_id"],
                    attestation_ref=request["attestation_ref"],
                    attestation_key_id=request["attestation_key_id"],
                    issued_ts_ms=request["issued_ts_ms"],
                    graph_outbox=request["graph_outbox"],
                    budget_outbox=request["budget_outbox"],
                )
            )
            self._verify_terminal_reconciliation_authority(
                binding_bytes=binding_bytes,
                request=request,
            )

            graph_record = self._apply_terminal_reconciliation_graph(
                conn,
                case=case,
                request=request,
                ts_ms=ts_ms,
            )
            budget_record = self._apply_terminal_reconciliation_budget(
                conn,
                case=case,
                request=request,
                ts_ms=ts_ms,
            )
            subject_json = _canonical_json(subject)
            binding_json = binding_bytes.decode("utf-8")
            proof_digest = "sha256:" + hashlib.sha256(binding_bytes).hexdigest()
            conn.execute(
                """
                INSERT INTO task_runtime_terminal_reconciliation_proof(
                    proof_id, schema_version, case_id, case_snapshot_sha256,
                    authority_kind, action, target_graph_status,
                    target_budget_status, subject_snapshot_json,
                    subject_snapshot_sha256, reconciler_actor_id,
                    attestation_ref, attestation_key_id, attestation_signature,
                    issued_ts_ms, proof_binding_json, proof_digest,
                    graph_outbox_id, budget_outbox_id, created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request["proof_id"],
                    TERMINAL_RECONCILIATION_PROOF_SCHEMA,
                    request["case_id"],
                    request["expected_case_snapshot_sha256"],
                    request["authority_kind"],
                    request["action"],
                    request["target_graph_status"],
                    request["target_budget_status"],
                    subject_json,
                    subject_digest,
                    request["reconciler_actor_id"],
                    request["attestation_ref"],
                    request["attestation_key_id"],
                    request["attestation_signature"],
                    request["issued_ts_ms"],
                    binding_json,
                    proof_digest,
                    graph_outbox.outbox_id if graph_outbox is not None else None,
                    budget_outbox.outbox_id if budget_outbox is not None else None,
                    ts_ms,
                ),
            )
            proof_row = conn.execute(
                """
                SELECT * FROM task_runtime_terminal_reconciliation_proof
                WHERE proof_id = ?
                """,
                (request["proof_id"],),
            ).fetchone()
            if proof_row is None:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "terminal reconciliation proof insert was not durable",
                )
            proof = _terminal_reconciliation_proof_record(
                conn,
                proof_row,
                verifier=getattr(self, "_reconciliation_proof_verifier", None),
            )
            return {
                "case": case,
                "subject": {
                    "snapshot": subject,
                    "snapshot_sha256": subject_digest,
                },
                "proof": proof,
                "graph": graph_record,
                "budget": budget_record,
                "replayed": False,
            }

    def _insert_terminal_reconciliation_case(
        self,
        conn: sqlite3.Connection,
        case: Mapping[str, Any],
        *,
        created_ts_ms: int,
    ) -> None:
        snapshot = self._case_snapshot(case)
        snapshot_json, snapshot_digest = _canonical_digest(snapshot)
        if (
            str(case.get("schema_version") or "")
            != TERMINAL_RECONCILIATION_CASE_SCHEMA
            or str(case.get("snapshot_sha256") or "") != snapshot_digest
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal reconciliation candidate snapshot drifted",
            )
        conn.execute(
            """
            INSERT INTO task_runtime_terminal_reconciliation_case(
                case_id, schema_version, case_kind, classification, reason_code,
                graph_id, graph_revision, graph_status, graph_status_version,
                budget_id, budget_status, budget_status_version,
                node_id, node_status, node_status_version,
                attempt_id, attempt_status, attempt_status_version,
                recommended_action, snapshot_json, snapshot_sha256, created_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                case["case_id"],
                case["schema_version"],
                case["case_kind"],
                case["classification"],
                case["reason_code"],
                case["graph_id"],
                case["graph_revision"],
                case["graph_status"],
                case["graph_status_version"],
                case.get("budget_id"),
                case.get("budget_status"),
                case.get("budget_status_version"),
                case.get("node_id"),
                case.get("node_status"),
                case.get("node_status_version"),
                case.get("attempt_id"),
                case.get("attempt_status"),
                case.get("attempt_status_version"),
                case["recommended_action"],
                snapshot_json,
                snapshot_digest,
                int(created_ts_ms),
            ),
        )

    def _assert_terminal_reconciliation_request(
        self,
        conn: sqlite3.Connection,
        *,
        case: Mapping[str, Any],
        subject: Mapping[str, Any],
        subject_digest: str,
        request: Mapping[str, Any],
    ) -> None:
        del subject
        classification = str(case["classification"])
        authority_kind = request["authority_kind"]
        signed_classifier_correction = (
            is_signed_prebudget_cancellation_classifier_correction(case)
        )
        if (
            request["expected_case_snapshot_sha256"] != case["snapshot_sha256"]
            or request["expected_subject_snapshot_sha256"] != subject_digest
            or request["action"] != case["recommended_action"]
            or (
                classification == "QUARANTINED_STRUCTURAL"
                and not signed_classifier_correction
            )
            or (
                classification == "EXACT_REPAIRABLE"
                and authority_kind
                not in {"SYSTEM_DURABLE_EVIDENCE", "SIGNED_OPERATOR"}
            )
            or (
                classification == "SIGNED_REPAIR_REQUIRED"
                and authority_kind != "SIGNED_OPERATOR"
            )
            or (signed_classifier_correction and authority_kind != "SIGNED_OPERATOR")
        ):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "terminal reconciliation case, subject, action, or authority drifted",
            )
        expected_targets = {
            "RELEASE_BUDGET": (str(case["graph_status"]), "RELEASED"),
            "FAIL_GRAPH": ("FAILED", None),
            "FAIL_GRAPH_AND_RELEASE_BUDGET": ("FAILED", "RELEASED"),
            "NONE": (None, None),
        }[request["action"]]
        if (
            request["target_graph_status"],
            request["target_budget_status"],
        ) != expected_targets:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "terminal reconciliation target matrix is invalid",
            )
        if str(case.get("budget_status") or "") == "EXCEEDED":
            budget_row = self._require_budget(conn, str(case["budget_id"]))
            self._assert_terminal_reconciliation_graph_budget_binding(
                budget_row,
                case=case,
            )
            self._terminal_budget_authority(
                conn,
                budget_row=budget_row,
                terminal_version=int(case["budget_status_version"]),
            )

    def _apply_terminal_reconciliation_graph(
        self,
        conn: sqlite3.Connection,
        *,
        case: Mapping[str, Any],
        request: Mapping[str, Any],
        ts_ms: int,
    ) -> dict[str, Any]:
        graph_outbox = request["graph_outbox"]
        if graph_outbox is None:
            graph = self._require_graph(
                conn,
                str(case["graph_id"]),
                int(case["graph_revision"]),
            )
            if (
                str(graph["status"]) != str(case["graph_status"])
                or int(graph["status_version"]) != int(case["graph_status_version"])
            ):
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    "terminal reconciliation Graph observation drifted",
                )
            nodes = conn.execute(
                """
                SELECT * FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id
                """,
                (str(case["graph_id"]), int(case["graph_revision"])),
            ).fetchall()
            return _graph_record(graph, nodes)
        return self._transition_graph_in_transaction(
            conn,
            graph_id=str(case["graph_id"]),
            revision=int(case["graph_revision"]),
            expected_version=int(case["graph_status_version"]),
            target_status=TaskGraphStatus.FAILED,
            outbox=graph_outbox,
            ts_ms=ts_ms,
            attempt_id=None,
            terminal_reconciliation_bypass=True,
        )

    def _apply_terminal_reconciliation_budget(
        self,
        conn: sqlite3.Connection,
        *,
        case: Mapping[str, Any],
        request: Mapping[str, Any],
        ts_ms: int,
    ) -> dict[str, Any] | None:
        budget_id = str(case.get("budget_id") or "") or None
        budget_outbox = request["budget_outbox"]
        if budget_id is None:
            if budget_outbox is not None:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "terminal reconciliation has an unexpected Budget Outbox",
                )
            return None
        row = self._require_budget(conn, budget_id)
        self._assert_terminal_reconciliation_graph_budget_binding(
            row,
            case=case,
        )
        if (
            str(row["status"]) != str(case["budget_status"])
            or int(row["status_version"]) != int(case["budget_status_version"])
        ):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "terminal reconciliation Budget observation drifted",
            )
        if budget_outbox is None:
            return self._budget_record(row)
        target = validate_budget_transition(
            str(row["status"]),
            BudgetReservationStatus.RELEASED,
        )
        cursor = conn.execute(
            """
            UPDATE task_runtime_budget_reservation
            SET status = ?, status_version = status_version + 1,
                updated_ts_ms = ?
            WHERE budget_id = ? AND status = ? AND status_version = ?
            """,
            (
                target.value,
                int(ts_ms),
                budget_id,
                str(case["budget_status"]),
                int(case["budget_status_version"]),
            ),
        )
        self._require_cas(cursor, subject=f"BudgetReservation {budget_id}")
        self._insert_outbox(
            conn,
            budget_outbox,
            ts_ms=int(ts_ms),
            expected_aggregate=(
                "BUDGET_RESERVATION",
                budget_id,
                int(case["budget_status_version"]) + 1,
            ),
            expected_event_type="BUDGET_RESERVATION_RELEASED",
            expected_attempt_id=None,
        )
        return self._budget_record(self._require_budget(conn, budget_id))

    @staticmethod
    def _assert_terminal_reconciliation_graph_budget_binding(
        row: sqlite3.Row | Mapping[str, Any],
        *,
        case: Mapping[str, Any],
    ) -> None:
        if (
            str(row["graph_id"] or "") != str(case["graph_id"])
            or int(row["graph_revision"] or 0) != int(case["graph_revision"])
            or row["node_id"] is not None
            or row["attempt_id"] is not None
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal reconciliation action requires one graph-level Budget",
            )

    def _verify_terminal_reconciliation_authority(
        self,
        *,
        binding_bytes: bytes,
        request: Mapping[str, Any],
    ) -> None:
        if request["authority_kind"] == "SYSTEM_DURABLE_EVIDENCE":
            return
        verifier = getattr(self, "_reconciliation_proof_verifier", None)
        key_id = str(request["attestation_key_id"] or "")
        signature = str(request["attestation_signature"] or "")
        if verifier is None:
            raise TaskStoreError(
                TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
                "terminal reconciliation has no verification authority",
            )
        if (
            not verifier.has_key(key_id)
            or not verifier.verify(
                key_id=key_id,
                message=binding_bytes,
                signature=signature,
            )
        ):
            raise TaskStoreError(
                TASK_STORE_RECONCILIATION_PROOF_INVALID,
                "terminal reconciliation Ed25519 proof is invalid",
            )

    def _replay_terminal_reconciliation_proof(
        self,
        conn: sqlite3.Connection,
        *,
        case: Mapping[str, Any],
        proof_row: sqlite3.Row,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        proof = _terminal_reconciliation_proof_record(
            conn,
            proof_row,
            verifier=getattr(self, "_reconciliation_proof_verifier", None),
        )
        graph_outbox = request["graph_outbox"]
        budget_outbox = request["budget_outbox"]
        if (
            str(proof.get("proof_id") or "") != request["proof_id"]
            or str(proof.get("case_snapshot_sha256") or "")
            != request["expected_case_snapshot_sha256"]
            or str(proof.get("subject_snapshot_sha256") or "")
            != request["expected_subject_snapshot_sha256"]
            or str(proof.get("authority_kind") or "")
            != request["authority_kind"]
            or str(proof.get("action") or "") != request["action"]
            or self._optional_text(proof.get("target_graph_status"))
            != request["target_graph_status"]
            or self._optional_text(proof.get("target_budget_status"))
            != request["target_budget_status"]
            or str(proof.get("reconciler_actor_id") or "")
            != request["reconciler_actor_id"]
            or int(proof.get("issued_ts_ms", -1)) != request["issued_ts_ms"]
            or self._optional_text(proof.get("attestation_ref"))
            != request["attestation_ref"]
            or self._optional_text(proof.get("attestation_key_id"))
            != request["attestation_key_id"]
            or self._optional_text(proof.get("attestation_signature"))
            != request["attestation_signature"]
            or self._optional_text(proof.get("graph_outbox_id"))
            != (graph_outbox.outbox_id if graph_outbox is not None else None)
            or self._optional_text(proof.get("budget_outbox_id"))
            != (budget_outbox.outbox_id if budget_outbox is not None else None)
        ):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "terminal reconciliation proof replay drifted",
            )
        graph = self._require_graph(
            conn,
            str(case["graph_id"]),
            int(case["graph_revision"]),
        )
        graph_changed = request["action"] in {
            "FAIL_GRAPH",
            "FAIL_GRAPH_AND_RELEASE_BUDGET",
        }
        expected_graph_version = int(case["graph_status_version"]) + int(
            graph_changed
        )
        expected_graph_status = (
            str(case["graph_status"])
            if request["action"] == "NONE"
            else str(request["target_graph_status"])
        )
        if (
            str(graph["status"]) != expected_graph_status
            or int(graph["status_version"]) != expected_graph_version
        ):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "terminal reconciliation Graph replay drifted",
            )
        if graph_outbox is not None:
            self._assert_mutation_outbox_replay(
                conn,
                graph_outbox,
                expected_aggregate=(
                    "TASK_GRAPH",
                    str(case["graph_id"]),
                    expected_graph_version,
                ),
                expected_event_type="TASK_GRAPH_FAILED",
                expected_attempt_id=None,
                expected_graph_revision=int(case["graph_revision"]),
            )
        budget_record = None
        if case.get("budget_id") is not None:
            budget = self._require_budget(conn, str(case["budget_id"]))
            budget_changed = request["action"] in {
                "RELEASE_BUDGET",
                "FAIL_GRAPH_AND_RELEASE_BUDGET",
            }
            expected_budget_version = int(case["budget_status_version"]) + int(
                budget_changed
            )
            expected_budget_status = (
                "RELEASED" if budget_changed else str(case["budget_status"])
            )
            if (
                str(budget["status"]) != expected_budget_status
                or int(budget["status_version"]) != expected_budget_version
            ):
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    "terminal reconciliation Budget replay drifted",
                )
            if budget_outbox is not None:
                self._assert_mutation_outbox_replay(
                    conn,
                    budget_outbox,
                    expected_aggregate=(
                        "BUDGET_RESERVATION",
                        str(case["budget_id"]),
                        expected_budget_version,
                    ),
                    expected_event_type="BUDGET_RESERVATION_RELEASED",
                    expected_attempt_id=None,
                )
            budget_record = self._budget_record(budget)
        nodes = conn.execute(
            """
            SELECT * FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id
            """,
            (str(case["graph_id"]), int(case["graph_revision"])),
        ).fetchall()
        return {
            "case": dict(case),
            "subject": {
                "snapshot": proof["subject_snapshot"],
                "snapshot_sha256": proof["subject_snapshot_sha256"],
            },
            "proof": proof,
            "graph": _graph_record(graph, nodes),
            "budget": budget_record,
            "replayed": True,
        }

    @staticmethod
    def _subject_snapshot(value: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        if isinstance(value.get("snapshot"), Mapping):
            snapshot = dict(value["snapshot"])
            supplied_digest = str(value.get("snapshot_sha256") or "")
        else:
            snapshot = {
                key: item
                for key, item in value.items()
                if key not in {"snapshot_sha256", "snapshot_json"}
            }
            supplied_digest = str(value.get("snapshot_sha256") or "")
        _, digest = _canonical_digest(snapshot)
        if supplied_digest and supplied_digest != digest:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal reconciliation subject digest drifted",
            )
        return snapshot, digest

    @staticmethod
    def _case_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
        if isinstance(value.get("case"), Mapping):
            return value["case"]
        return value

    @staticmethod
    def _case_snapshot(case: Mapping[str, Any]) -> Mapping[str, Any]:
        snapshot = case.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "terminal reconciliation candidate lacks its canonical snapshot",
            )
        return snapshot

    @classmethod
    def _normalize_terminal_reconciliation_mutation(
        cls,
        **raw: Any,
    ) -> dict[str, Any]:
        normalized = {
            "case_id": str(raw.get("case_id") or "").strip(),
            "expected_case_snapshot_sha256": str(
                raw.get("expected_case_snapshot_sha256") or ""
            ).strip(),
            "expected_subject_snapshot_sha256": str(
                raw.get("expected_subject_snapshot_sha256") or ""
            ).strip(),
            "action": str(raw.get("action") or "").strip().upper(),
            "target_graph_status": cls._optional_text(
                raw.get("target_graph_status")
            ),
            "target_budget_status": cls._optional_text(
                raw.get("target_budget_status")
            ),
            "authority_kind": str(raw.get("authority_kind") or "").strip().upper(),
            "reconciler_actor_id": str(
                raw.get("reconciler_actor_id") or ""
            ).strip(),
            "issued_ts_ms": raw.get("issued_ts_ms"),
            "proof_id": str(raw.get("proof_id") or "").strip(),
            "graph_outbox": raw.get("graph_outbox"),
            "budget_outbox": raw.get("budget_outbox"),
            "attestation_ref": cls._optional_text(raw.get("attestation_ref")),
            "attestation_key_id": cls._optional_text(
                raw.get("attestation_key_id")
            ),
            "attestation_signature": cls._optional_text(
                raw.get("attestation_signature")
            ),
        }
        if normalized["target_graph_status"] is not None:
            normalized["target_graph_status"] = normalized[
                "target_graph_status"
            ].upper()
        if normalized["target_budget_status"] is not None:
            normalized["target_budget_status"] = normalized[
                "target_budget_status"
            ].upper()
        action = normalized["action"]
        graph_outbox = normalized["graph_outbox"]
        budget_outbox = normalized["budget_outbox"]
        signed = normalized["authority_kind"] == "SIGNED_OPERATOR"
        invalid = (
            not _REFERENCE_PATTERN.fullmatch(normalized["case_id"])
            or not _REFERENCE_PATTERN.fullmatch(normalized["proof_id"])
            or not _DIGEST_PATTERN.fullmatch(
                normalized["expected_case_snapshot_sha256"]
            )
            or not _DIGEST_PATTERN.fullmatch(
                normalized["expected_subject_snapshot_sha256"]
            )
            or action
            not in {
                "RELEASE_BUDGET",
                "FAIL_GRAPH",
                "FAIL_GRAPH_AND_RELEASE_BUDGET",
                "NONE",
            }
            or normalized["authority_kind"]
            not in {"SYSTEM_DURABLE_EVIDENCE", "SIGNED_OPERATOR"}
            or not normalized["reconciler_actor_id"]
            or type(normalized["issued_ts_ms"]) is not int
            or normalized["issued_ts_ms"] < 0
            or (action == "RELEASE_BUDGET" and (graph_outbox is not None or budget_outbox is None))
            or (action == "FAIL_GRAPH" and (graph_outbox is None or budget_outbox is not None))
            or (
                action == "FAIL_GRAPH_AND_RELEASE_BUDGET"
                and (graph_outbox is None or budget_outbox is None)
            )
            or (
                action == "NONE"
                and (graph_outbox is not None or budget_outbox is not None)
            )
            or any(
                value is not None and not isinstance(value, OutboxMessage)
                for value in (graph_outbox, budget_outbox)
            )
            or (
                signed
                and (
                    not normalized["attestation_ref"]
                    or not normalized["attestation_key_id"]
                    or not normalized["attestation_signature"]
                )
            )
            or (
                not signed
                and any(
                    normalized[field] is not None
                    for field in (
                        "attestation_ref",
                        "attestation_key_id",
                        "attestation_signature",
                    )
                )
            )
        )
        if invalid:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "terminal reconciliation mutation fields are invalid",
            )
        for outbox in (graph_outbox, budget_outbox):
            if outbox is not None:
                outbox.validate()
                required_evidence = {
                    normalized["case_id"],
                    normalized["proof_id"],
                }
                if signed:
                    required_evidence.add(str(normalized["attestation_ref"]))
                if (
                    outbox.available_ts_ms is not None
                    or outbox.attempt_id is not None
                    or not required_evidence.issubset(outbox.evidence_refs)
                ):
                    raise TaskStoreError(
                        TASK_STORE_INVALID,
                        "terminal reconciliation Outbox authority is invalid",
                    )
        return normalized

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        return str(value or "").strip() or None


__all__ = ["TaskTerminalReconciliationMutationMixin"]
