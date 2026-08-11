"""Read, readiness and trusted validation for Effect reconciliation."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping
from typing import Any

from host_brain.contracts.agent_runtime.effect_reconciliation import (
    build_graph_cancellation_effect_reconciliation_binding,
)
from host_brain.persistence.task_record_codec import (
    _acceptance_effect_record,
    _dumps,
    _graph_cancellation_intent_record,
    _loads_list,
    _loads_object,
)
from host_brain.persistence.task_store_types import (
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_NOT_FOUND,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
    _DURABLE_REFERENCE_PATTERN,
)

from .task_effect_reconciliation_common import (
    EFFECT_RECONCILIATION_CASE_SCHEMA,
    EFFECT_RECONCILIATION_LEGACY_PURPOSE,
    EFFECT_RECONCILIATION_PENDING_PURPOSE,
    EFFECT_RECONCILIATION_PROOF_SCHEMA,
    _CASE_COLUMNS,
    _CASE_FOREIGN_KEYS,
    _PROOF_COLUMNS,
    _PROOF_FOREIGN_KEYS,
    _RECONCILIATION_AUTHORITY_REQUIRED,
    _REQUIRED_INDEXES,
    _REQUIRED_TRIGGERS,
    _SIGNATURE_PATTERN,
    _TRIGGER_SQL_REQUIREMENTS,
    _durable_ref,
    _effect_projection,
    _effect_reconciliation_case_record,
    _has_index,
    _require_current_legacy_decision_binding,
    _table_columns,
    _table_foreign_keys,
)


class TaskEffectReconciliationReadMixin:
    """Own 0098 readiness, inventory reads and trusted proof validation."""

    def assert_effect_reconciliation_ready(
        self,
        *,
        force_revalidate: bool = False,
    ) -> None:
        if (
            not force_revalidate
            and getattr(self, "_effect_reconciliation_schema_ready", False)
        ):
            return
        self._effect_reconciliation_schema_ready = False
        conn = self._connect()
        try:
            case_columns = _table_columns(
                conn,
                "task_runtime_effect_reconciliation_case",
            )
            proof_columns = _table_columns(
                conn,
                "task_runtime_effect_reconciliation_proof",
            )
            indexes = {
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'index' AND (
                        name LIKE 'idx_task_runtime_effect_reconciliation_%'
                        OR name = 'uq_task_runtime_cancellation_effect_fence_binding'
                    )
                    """
                ).fetchall()
            }
            triggers = {
                str(row[0]): str(row[1] or "")
                for row in conn.execute(
                    """
                    SELECT name, sql FROM sqlite_master
                    WHERE type = 'trigger' AND (
                        name LIKE 'trg_task_effect_reconciliation_%'
                        OR name LIKE 'trg_task_effect_failed_evidence_%'
                    )
                    """
                ).fetchall()
            }
            case_foreign_keys = _table_foreign_keys(
                conn,
                "task_runtime_effect_reconciliation_case",
            )
            proof_foreign_keys = _table_foreign_keys(
                conn,
                "task_runtime_effect_reconciliation_proof",
            )
            index_contract_ready = all(
                (
                    _has_index(
                        conn,
                        "task_runtime_graph_cancellation_effect_fence",
                        ("effect_id", "intent_ref"),
                        unique=True,
                    ),
                    _has_index(
                        conn,
                        "task_runtime_effect_reconciliation_case",
                        ("classification", "purpose", "created_ts_ms", "case_id"),
                        unique=False,
                    ),
                    _has_index(
                        conn,
                        "task_runtime_effect_reconciliation_case",
                        ("effect_id", "intent_ref", "purpose"),
                        unique=True,
                    ),
                    _has_index(
                        conn,
                        "task_runtime_effect_reconciliation_proof",
                        ("case_id", "purpose", "created_ts_ms", "proof_id"),
                        unique=False,
                    ),
                    _has_index(
                        conn,
                        "task_runtime_effect_reconciliation_proof",
                        ("effect_id", "intent_ref", "purpose"),
                        unique=True,
                    ),
                )
            )
            foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
            missing_inventory = conn.execute(
                """
                SELECT fence.effect_id
                FROM task_runtime_graph_cancellation_effect_fence AS fence
                JOIN task_runtime_acceptance_effect_intent AS effect
                  ON effect.effect_id = fence.effect_id
                LEFT JOIN task_runtime_effect_reconciliation_case AS item
                  ON item.effect_id = fence.effect_id
                 AND item.intent_ref = fence.intent_ref
                WHERE item.case_id IS NULL AND (
                    fence.resolution_status = 'FAILED_EVIDENCE'
                    OR (
                        fence.observed_status = 'PENDING'
                        AND fence.resolution_status = 'CANCELLED'
                        AND (
                            effect.attempt_count > 0
                            OR effect.fencing_token > 0
                            OR effect.status_version > 0
                            OR effect.reason_code IS NOT NULL
                            OR effect.last_error IS NOT NULL
                        )
                    )
                )
                LIMIT 1
                """
            ).fetchone() if _CASE_COLUMNS == case_columns else None
            case_rows = (
                conn.execute(
                    "SELECT * FROM task_runtime_effect_reconciliation_case ORDER BY case_id"
                ).fetchall()
                if _CASE_COLUMNS == case_columns
                else ()
            )
            proof_rows = (
                conn.execute(
                    "SELECT * FROM task_runtime_effect_reconciliation_proof ORDER BY proof_id"
                ).fetchall()
                if _PROOF_COLUMNS == proof_columns
                else ()
            )
            for row in case_rows:
                _effect_reconciliation_case_record(row)
            for row in proof_rows:
                self._validated_effect_reconciliation_proof(conn, row)
        finally:
            conn.close()
        missing_case = sorted(_CASE_COLUMNS.symmetric_difference(case_columns))
        missing_proof = sorted(_PROOF_COLUMNS.symmetric_difference(proof_columns))
        missing_indexes = sorted(_REQUIRED_INDEXES - indexes)
        missing_triggers = sorted(_REQUIRED_TRIGGERS - set(triggers))
        malformed_trigger = next(
            (
                name
                for name, fragments in _TRIGGER_SQL_REQUIREMENTS.items()
                if any(
                    fragment not in " ".join(
                        str(triggers.get(name) or "").upper().split()
                    )
                    for fragment in fragments
                )
            ),
            None,
        )
        if missing_case or missing_proof or missing_indexes or missing_triggers:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime effect reconciliation migration 0098 is not applied: "
                f"case={missing_case}, proof={missing_proof}, "
                f"indexes={missing_indexes}, triggers={missing_triggers}",
            )
        if malformed_trigger is not None:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                f"effect reconciliation trigger is malformed: {malformed_trigger}",
            )
        if (
            case_foreign_keys != _CASE_FOREIGN_KEYS
            or proof_foreign_keys != _PROOF_FOREIGN_KEYS
            or not index_contract_ready
        ):
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "effect reconciliation physical FK/index contract is incomplete",
            )
        if foreign_keys:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "effect reconciliation readiness found foreign-key violations",
            )
        if missing_inventory is not None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "legacy effect reconciliation evidence is missing immutable inventory",
            )
        self._effect_reconciliation_schema_ready = True

    def assert_effect_reconciliation_production_ready(self) -> None:
        """Gate production on fully proved legacy inventory only.

        Runtime ``RECONCILIATION_REQUIRED`` virtual candidates are deliberately
        excluded: the Operator must be able to start in order to settle them.
        Every persisted legacy/quarantine row is scanned and every proof is
        fully decoded, lineage-checked and signature-verified on one SQLite
        read snapshot.
        """

        self.assert_effect_reconciliation_ready()
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            rows = conn.execute(
                """
                SELECT * FROM task_runtime_effect_reconciliation_case
                WHERE case_kind IN (
                    'LEGACY_FAILED_EVIDENCE',
                    'LEGACY_RETRY_PENDING_CANCELLED'
                )
                   OR classification IN (
                    'QUARANTINED_FINALIZED',
                    'QUARANTINED_STRUCTURAL'
                   )
                ORDER BY created_ts_ms, case_id
                """
            ).fetchall()
            for row in rows:
                case = _effect_reconciliation_case_record(row)
                classification = case["classification"]
                if classification == "QUARANTINED_STRUCTURAL":
                    raise TaskStoreError(
                        TASK_STORE_DURABLE_DATA_INVALID,
                        "structural Effect reconciliation inventory blocks production",
                    )
                if classification == "QUARANTINED_FINALIZED":
                    _require_current_legacy_decision_binding(conn, case)
                purpose = (
                    EFFECT_RECONCILIATION_LEGACY_PURPOSE
                    if classification == "QUARANTINED_FINALIZED"
                    else EFFECT_RECONCILIATION_PENDING_PURPOSE
                )
                proof = self._read_validated_effect_reconciliation_proof_in_transaction(
                    conn,
                    intent_ref=case["intent_ref"],
                    effect_id=case["effect_id"],
                    purpose=purpose,
                )
                if proof is None:
                    raise TaskStoreError(
                        _RECONCILIATION_AUTHORITY_REQUIRED,
                        "legacy Effect reconciliation inventory lacks a trusted proof",
                    )
            conn.rollback()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_effect_reconciliation_cases(
        self,
        *,
        classification: str | None = None,
        limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        self.assert_effect_reconciliation_ready()
        clauses = ""
        parameters: list[Any] = []
        if classification is not None:
            normalized = str(classification or "").strip().upper()
            if normalized not in {
                "REPAIRABLE_REQUESTED",
                "QUARANTINED_FINALIZED",
                "QUARANTINED_STRUCTURAL",
            }:
                raise TaskStoreError(TASK_STORE_INVALID, "case classification is invalid")
            clauses = " WHERE classification = ?"
            parameters.append(normalized)
        parameters.append(min(1_000, max(1, int(limit))))
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM task_runtime_effect_reconciliation_case"
                + clauses
                + " ORDER BY created_ts_ms, case_id LIMIT ?",
                tuple(parameters),
            ).fetchall()
            return tuple(_effect_reconciliation_case_record(row) for row in rows)
        finally:
            conn.close()

    def list_effect_reconciliation_candidates(
        self,
        intent_ref: str | None = None,
        *,
        limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        """Return secret-free REQUESTED challenges for unresolved Effect fences."""

        self.assert_effect_reconciliation_ready()
        reference = (
            _durable_ref(intent_ref, "intent_ref")
            if str(intent_ref or "").strip()
            else None
        )
        if type(limit) is not int or limit < 1:
            raise TaskStoreError(TASK_STORE_INVALID, "candidate limit is invalid")
        parameters: list[Any] = []
        intent_filter = ""
        if reference is not None:
            intent_filter = " AND intent.intent_ref = ?"
            parameters.append(reference)
        parameters.append(min(1_000, limit))
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT fence.intent_ref, fence.effect_id
                FROM task_runtime_graph_cancellation_effect_fence AS fence
                JOIN task_runtime_graph_cancellation_intent AS intent
                  ON intent.intent_ref = fence.intent_ref
                WHERE intent.status = 'REQUESTED'
                  AND (
                    fence.resolution_status = 'RECONCILIATION_REQUIRED'
                    OR EXISTS (
                        SELECT 1
                        FROM task_runtime_effect_reconciliation_case AS item
                        WHERE item.intent_ref = fence.intent_ref
                          AND item.effect_id = fence.effect_id
                          AND item.classification = 'REPAIRABLE_REQUESTED'
                    )
                  )
                """
                + intent_filter
                + " ORDER BY fence.intent_ref, fence.effect_id LIMIT ?",
                tuple(parameters),
            ).fetchall()
            candidates: list[dict[str, Any]] = []
            for row in rows:
                subject = self._effect_reconciliation_subject(
                    conn,
                    intent_ref=str(row["intent_ref"]),
                    effect_id=str(row["effect_id"]),
                )
                case_row = conn.execute(
                    """
                    SELECT * FROM task_runtime_effect_reconciliation_case
                    WHERE intent_ref = ? AND effect_id = ?
                      AND purpose = 'PENDING_CANCELLATION'
                    """,
                    (str(row["intent_ref"]), str(row["effect_id"])),
                ).fetchone()
                case = (
                    _effect_reconciliation_case_record(case_row)
                    if case_row is not None
                    else self._derive_effect_reconciliation_case(
                        conn,
                        subject=subject,
                    )
                )
                if case["classification"] != "REPAIRABLE_REQUESTED":
                    continue
                proof_exists = conn.execute(
                    """
                    SELECT 1 FROM task_runtime_effect_reconciliation_proof
                    WHERE intent_ref = ? AND effect_id = ?
                      AND purpose = 'PENDING_CANCELLATION'
                    LIMIT 1
                    """,
                    (str(row["intent_ref"]), str(row["effect_id"])),
                ).fetchone() is not None
                candidates.append(
                    {
                        "case": case,
                        "intent": subject["intent"],
                        "effect": subject["effect"],
                        "fence": subject["fence"],
                        "attempt": subject["attempt"],
                        "case_snapshot_sha256": case["snapshot_sha256"],
                        "subject_snapshot_sha256": subject[
                            "subject_snapshot_sha256"
                        ],
                        "proof_exists": proof_exists,
                    }
                )
            return tuple(candidates)
        finally:
            conn.close()

    def read_graph_cancellation_effect_reconciliation(
        self,
        intent_ref: str,
        effect_id: str,
    ) -> dict[str, Any]:
        self.assert_effect_reconciliation_ready()
        intent_reference = _durable_ref(intent_ref, "intent_ref")
        effect_reference = _durable_ref(effect_id, "effect_id")
        conn = self._connect()
        try:
            subject = self._effect_reconciliation_subject(
                conn,
                intent_ref=intent_reference,
                effect_id=effect_reference,
            )
            case_row = conn.execute(
                """
                SELECT * FROM task_runtime_effect_reconciliation_case
                WHERE intent_ref = ? AND effect_id = ?
                ORDER BY CASE purpose
                    WHEN 'PENDING_CANCELLATION' THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (intent_reference, effect_reference),
            ).fetchone()
            case = (
                _effect_reconciliation_case_record(case_row)
                if case_row is not None
                else self._derive_effect_reconciliation_case(
                    conn,
                    subject=subject,
                )
            )
            proof_row = conn.execute(
                """
                SELECT * FROM task_runtime_effect_reconciliation_proof
                WHERE case_id = ?
                """,
                (case["case_id"],),
            ).fetchone()
            proof = (
                self._validated_effect_reconciliation_proof(conn, proof_row)
                if proof_row is not None
                else None
            )
            return {"case": case, **subject, "proof": proof}
        finally:
            conn.close()

    def _read_validated_effect_reconciliation_proof_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        intent_ref: str,
        effect_id: str,
        purpose: str,
    ) -> dict[str, Any] | None:
        """Read one exact proof without opening a connection or transaction."""

        row = conn.execute(
            """
            SELECT * FROM task_runtime_effect_reconciliation_proof
            WHERE intent_ref = ? AND effect_id = ? AND purpose = ?
            """,
            (str(intent_ref), str(effect_id), str(purpose)),
        ).fetchone()
        return (
            self._validated_effect_reconciliation_proof(conn, row)
            if row is not None
            else None
        )

    def _list_effect_reconciliation_cases_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        intent_ref: str,
    ) -> tuple[dict[str, Any], ...]:
        """Read immutable unresolved/quarantine inventory on the caller connection."""

        rows = conn.execute(
            """
            SELECT * FROM task_runtime_effect_reconciliation_case
            WHERE intent_ref = ?
              AND classification IN (
                'REPAIRABLE_REQUESTED',
                'QUARANTINED_FINALIZED',
                'QUARANTINED_STRUCTURAL'
              )
            ORDER BY created_ts_ms, case_id
            """,
            (str(intent_ref),),
        ).fetchall()
        return tuple(_effect_reconciliation_case_record(row) for row in rows)

    def _effect_has_reconciliation_proof_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        effect_id: str,
    ) -> bool:
        """Fail closed on corrupt evidence while answering the late-write guard."""

        rows = conn.execute(
            """
            SELECT * FROM task_runtime_effect_reconciliation_proof
            WHERE effect_id = ? ORDER BY proof_id
            """,
            (str(effect_id),),
        ).fetchall()
        for row in rows:
            self._validated_effect_reconciliation_proof(conn, row)
        return bool(rows)

    def _derive_effect_reconciliation_case(
        self,
        conn: sqlite3.Connection,
        *,
        subject: Mapping[str, Any],
    ) -> dict[str, Any]:
        intent = subject["intent"]
        effect = subject["effect"]
        fence = subject["fence"]
        attempt = subject["attempt"]
        resolution = str(fence["resolution_status"])
        if resolution == "RECONCILIATION_REQUIRED":
            case_kind = "RECONCILIATION_REQUIRED"
        elif resolution == "FAILED_EVIDENCE":
            case_kind = "LEGACY_FAILED_EVIDENCE"
        elif (
            resolution == "CANCELLED"
            and str(fence["observed_status"]) == "PENDING"
            and str(effect["status"]) == "PENDING"
            and int(effect["attempt_count"]) >= 1
            and int(effect["fencing_token"]) >= 1
            and int(effect["status_version"]) >= 2
        ):
            case_kind = "LEGACY_RETRY_PENDING_CANCELLED"
        else:
            raise TaskStoreError(
                TASK_STORE_NOT_FOUND,
                "effect fence is not a reconciliation case",
            )
        purpose = (
            EFFECT_RECONCILIATION_PENDING_PURPOSE
            if intent["status"] == "REQUESTED"
            else EFFECT_RECONCILIATION_LEGACY_PURPOSE
        )
        decision = conn.execute(
            """
            SELECT decision_ref, payload_sha256
            FROM task_runtime_graph_cancellation_decision
            WHERE intent_ref = ?
            """,
            (str(intent["intent_ref"]),),
        ).fetchone()
        structural = (
            case_kind == "RECONCILIATION_REQUIRED"
            or (
                case_kind == "LEGACY_FAILED_EVIDENCE"
                and str(effect["status"]) == "FAILED"
            )
            or case_kind == "LEGACY_RETRY_PENDING_CANCELLED"
        )
        if (
            intent["status"] == "FINALIZED"
            and decision is not None
            and structural
        ):
            classification = "QUARANTINED_FINALIZED"
            reason_code = "LEGACY_FINALIZED_IMMUTABLE_DECISION"
        elif (
            intent["status"] == "REQUESTED"
            and decision is None
            and structural
        ):
            classification = "REPAIRABLE_REQUESTED"
            reason_code = {
                "RECONCILIATION_REQUIRED": "REQUESTED_EFFECT_RECONCILIATION_REQUIRED",
                "LEGACY_FAILED_EVIDENCE": "REQUESTED_LEGACY_FAILED_EVIDENCE",
                "LEGACY_RETRY_PENDING_CANCELLED": "REQUESTED_RETRY_PENDING_CANCELLED",
            }[case_kind]
        else:
            classification = "QUARANTINED_STRUCTURAL"
            reason_code = "EFFECT_RECONCILIATION_STRUCTURAL_ANOMALY"
        raw_effect = conn.execute(
            "SELECT * FROM task_runtime_acceptance_effect_intent WHERE effect_id = ?",
            (str(effect["effect_id"]),),
        ).fetchone()
        raw_fence = conn.execute(
            "SELECT * FROM task_runtime_graph_cancellation_effect_fence WHERE effect_id = ?",
            (str(effect["effect_id"]),),
        ).fetchone()
        snapshot = {
            "attempt_id": attempt["attempt_id"],
            "attempt_status": attempt["status"],
            "attempt_status_version": attempt["status_version"],
            "case_kind": case_kind,
            "classification": classification,
            "effect_attempt_count": effect["attempt_count"],
            "effect_available_ts_ms": effect["available_ts_ms"],
            "effect_commit_scope": effect["commit_scope"],
            "effect_committed_ts_ms": raw_effect["committed_ts_ms"],
            "effect_created_ts_ms": raw_effect["created_ts_ms"],
            "effect_failed_ts_ms": raw_effect["failed_ts_ms"],
            "effect_fencing_token": effect["fencing_token"],
            "effect_id": effect["effect_id"],
            "effect_idempotency_key": effect["idempotency_key"],
            "effect_last_error_sha256": effect["last_error_sha256"],
            "effect_lease_expires_ts_ms": effect["lease_expires_ts_ms"],
            "effect_lease_owner": effect["lease_owner"],
            "effect_payload_ref": effect["payload_ref"],
            "effect_payload_sha256": effect["payload_sha256"],
            "effect_reason_code": effect["reason_code"],
            "effect_receipt_ref": effect["receipt_ref"],
            "effect_status": effect["status"],
            "effect_status_version": effect["status_version"],
            "effect_type": effect["effect_type"],
            "effect_updated_ts_ms": raw_effect["updated_ts_ms"],
            "fence_observed_status": fence["observed_status"],
            "fence_observed_status_version": fence["observed_status_version"],
            "fence_resolution_status": fence["resolution_status"],
            "graph_id": effect["graph_id"],
            "graph_revision": effect["graph_revision"],
            "intent_payload_sha256": intent["payload_sha256"],
            "intent_ref": intent["intent_ref"],
            "intent_request_id": intent["request_id"],
            "intent_status": intent["status"],
            "intent_status_version": intent["status_version"],
            "inventory_reason": reason_code,
            "legacy_decision_payload_sha256": (
                str(decision["payload_sha256"]) if decision is not None else None
            ),
            "legacy_decision_ref": (
                str(decision["decision_ref"]) if decision is not None else None
            ),
            "node_id": effect["node_id"],
            "purpose": purpose,
        }
        snapshot_json = _dumps(snapshot)
        snapshot_digest = "sha256:" + hashlib.sha256(
            snapshot_json.encode("utf-8")
        ).hexdigest()
        case_id = "effect-reconciliation-case://sha256/" + hashlib.sha256(
            f"{purpose}\n{intent['intent_ref']}\n{effect['effect_id']}".encode("utf-8")
        ).hexdigest()
        return {
            "schema_version": EFFECT_RECONCILIATION_CASE_SCHEMA,
            "case_id": case_id,
            "purpose": purpose,
            "case_kind": case_kind,
            "classification": classification,
            "reason_code": reason_code,
            "intent_ref": intent["intent_ref"],
            "effect_id": effect["effect_id"],
            "graph_id": effect["graph_id"],
            "graph_revision": effect["graph_revision"],
            "node_id": effect["node_id"],
            "attempt_id": effect["attempt_id"],
            "legacy_decision_ref": snapshot["legacy_decision_ref"],
            "legacy_decision_payload_sha256": snapshot[
                "legacy_decision_payload_sha256"
            ],
            "intent_status": intent["status"],
            "intent_status_version": intent["status_version"],
            "effect_status": effect["status"],
            "effect_status_version": effect["status_version"],
            "fence_observed_status": fence["observed_status"],
            "fence_observed_status_version": fence["observed_status_version"],
            "fence_resolution_status": fence["resolution_status"],
            "snapshot": snapshot,
            "snapshot_json": snapshot_json,
            "snapshot_sha256": snapshot_digest,
            "created_ts_ms": int(raw_fence["created_ts_ms"]),
        }

    def _effect_reconciliation_subject(
        self,
        conn: sqlite3.Connection,
        *,
        intent_ref: str,
        effect_id: str,
    ) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT effect.*, intent.status AS intent_status,
                   intent.status_version AS intent_status_version,
                   intent.payload_sha256 AS intent_payload_sha256,
                   intent.request_id AS intent_request_id,
                   fence.observed_status AS fence_observed_status,
                   fence.observed_status_version AS fence_observed_status_version,
                   fence.resolution_status AS fence_resolution_status,
                   attempt.status AS attempt_status,
                   attempt.status_version AS attempt_status_version
            FROM task_runtime_acceptance_effect_intent AS effect
            JOIN task_runtime_graph_cancellation_effect_fence AS fence
              ON fence.effect_id = effect.effect_id
            JOIN task_runtime_graph_cancellation_intent AS intent
              ON intent.intent_ref = fence.intent_ref
            JOIN task_runtime_attempt AS attempt
              ON attempt.attempt_id = effect.attempt_id
             AND attempt.graph_id = effect.graph_id
             AND attempt.graph_revision = effect.graph_revision
             AND attempt.node_id = effect.node_id
            WHERE intent.intent_ref = ? AND effect.effect_id = ?
            """,
            (intent_ref, effect_id),
        ).fetchone()
        if row is None:
            raise TaskStoreError(TASK_STORE_NOT_FOUND, "effect reconciliation subject not found")
        effect = _acceptance_effect_record(row)
        intent_row = conn.execute(
            "SELECT * FROM task_runtime_graph_cancellation_intent WHERE intent_ref = ?",
            (intent_ref,),
        ).fetchone()
        intent = _graph_cancellation_intent_record(intent_row)
        fence = {
            "observed_status": str(row["fence_observed_status"]),
            "observed_status_version": int(row["fence_observed_status_version"]),
            "resolution_status": str(row["fence_resolution_status"]),
        }
        attempt = {
            "attempt_id": str(effect["attempt_id"]),
            "graph_id": str(effect["graph_id"]),
            "graph_revision": int(effect["graph_revision"]),
            "node_id": str(effect["node_id"]),
            "status": str(row["attempt_status"]),
            "status_version": int(row["attempt_status_version"]),
        }
        intent_projection = {
            "intent_ref": str(intent["intent_ref"]),
            "status": str(intent["status"]),
            "status_version": int(intent["status_version"]),
            "graph_id": str(intent["graph_id"]),
            "graph_revision": int(intent["graph_revision"]),
            "payload_sha256": str(intent["payload_sha256"]),
            "request_id": str(intent["request_id"]),
        }
        effect_projection = _effect_projection(effect)
        fence_projection = {
            "effect_id": str(effect["effect_id"]),
            "intent_ref": str(intent["intent_ref"]),
            **fence,
        }
        current_snapshot = {
            "attempt": attempt,
            "effect": effect_projection,
            "fence": fence_projection,
            "intent": intent_projection,
        }
        snapshot_json = _dumps(current_snapshot)
        return {
            "intent": intent_projection,
            "effect": effect_projection,
            "fence": fence_projection,
            "attempt": attempt,
            "subject_snapshot_json": snapshot_json,
            "subject_snapshot_sha256": "sha256:"
            + hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
        }

    def _validated_effect_reconciliation_proof(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row | Mapping[str, Any],
    ) -> dict[str, Any]:
        if row is None:
            raise TaskStoreError(TASK_STORE_DURABLE_DATA_INVALID, "proof row is missing")
        record = dict(row)
        try:
            binding = _loads_object(
                str(record["proof_binding_json"]),
                field_name="effect_reconciliation.proof_binding_json",
            )
            binding_bytes = build_graph_cancellation_effect_reconciliation_binding(
                binding
            )
            subject_snapshot = _loads_object(
                str(record["subject_snapshot_json"]),
                field_name="effect_reconciliation.subject_snapshot_json",
            )
        except (KeyError, TaskStoreError, ValueError) as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "persisted effect reconciliation proof binding is invalid",
            ) from exc

        canonical = binding_bytes.decode("utf-8")
        digest = "sha256:" + hashlib.sha256(binding_bytes).hexdigest()
        canonical_subject = _dumps(subject_snapshot)
        subject_digest = "sha256:" + hashlib.sha256(
            canonical_subject.encode("utf-8")
        ).hexdigest()
        case_binding = dict(binding["case"])
        intent_binding = dict(binding["intent"])
        effect_binding = dict(binding["effect"])
        fence_binding = dict(binding["fence"])
        attempt_binding = dict(binding["attempt"])
        resolution_binding = dict(binding["resolution"])
        authority_binding = dict(binding["authority"])
        outbox_binding = dict(binding["outbox"])
        expected_subject = {
            "attempt": attempt_binding,
            "effect": effect_binding,
            "fence": fence_binding,
            "intent": intent_binding,
        }
        if (
            canonical != str(record.get("proof_binding_json") or "")
            or digest != str(record.get("proof_digest") or "")
            or canonical_subject != str(record.get("subject_snapshot_json") or "")
            or subject_digest != str(record.get("subject_snapshot_sha256") or "")
            or binding.get("subject_snapshot_sha256") != subject_digest
            or subject_snapshot != expected_subject
            or str(record.get("schema_version") or "")
            != EFFECT_RECONCILIATION_PROOF_SCHEMA
            or str(binding.get("schema_version") or "")
            != EFFECT_RECONCILIATION_PROOF_SCHEMA
            or str(outbox_binding.get("proof_id") or "")
            != str(record.get("proof_id") or "")
            or str(case_binding.get("case_id") or "")
            != str(record.get("case_id") or "")
            or str(case_binding.get("intent_ref") or "")
            != str(record.get("intent_ref") or "")
            or str(case_binding.get("effect_id") or "")
            != str(record.get("effect_id") or "")
            or str(case_binding.get("graph_id") or "")
            != str(record.get("graph_id") or "")
            or int(case_binding.get("graph_revision", -1))
            != int(record.get("graph_revision", -2))
            or str(case_binding.get("node_id") or "")
            != str(record.get("node_id") or "")
            or str(case_binding.get("attempt_id") or "")
            != str(record.get("attempt_id") or "")
            or str(resolution_binding.get("purpose") or "")
            != str(record.get("purpose") or "")
            or str(case_binding.get("purpose") or "")
            != str(record.get("purpose") or "")
            or str(case_binding.get("snapshot_sha256") or "")
            != str(record.get("case_snapshot_sha256") or "")
            or str(resolution_binding.get("outcome") or "")
            != str(record.get("outcome") or "")
            or str(resolution_binding.get("target_resolution_status") or "")
            != str(record.get("target_resolution_status") or "")
            or (str(resolution_binding.get("receipt_ref") or "").strip() or None)
            != (str(record.get("receipt_ref") or "").strip() or None)
            or (
                str(
                    resolution_binding.get("executor_quiescence_ref") or ""
                ).strip()
                or None
            )
            != (str(record.get("executor_quiescence_ref") or "").strip() or None)
            or str(authority_binding.get("reconciler_actor_id") or "")
            != str(record.get("reconciler_actor_id") or "")
            or str(authority_binding.get("attestation_ref") or "")
            != str(record.get("attestation_ref") or "")
            or str(authority_binding.get("attestation_key_id") or "")
            != str(record.get("attestation_key_id") or "")
            or int(authority_binding.get("issued_ts_ms", -1))
            != int(record.get("issued_ts_ms", -2))
            or str(outbox_binding.get("outbox_id") or "")
            != str(record.get("outbox_id") or "")
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "persisted effect reconciliation proof columns drifted",
            )

        case_row = conn.execute(
            "SELECT * FROM task_runtime_effect_reconciliation_case WHERE case_id = ?",
            (str(record["case_id"]),),
        ).fetchone()
        if case_row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "persisted effect reconciliation proof case is missing",
            )
        case = _effect_reconciliation_case_record(case_row)
        expected_case_binding = {
            key: case[key]
            for key in (
                "schema_version",
                "case_id",
                "intent_ref",
                "effect_id",
                "graph_id",
                "graph_revision",
                "node_id",
                "attempt_id",
                "purpose",
                "classification",
                "case_kind",
                "reason_code",
                "legacy_decision_ref",
                "legacy_decision_payload_sha256",
                "snapshot_sha256",
            )
        }
        if case_binding != expected_case_binding:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "persisted effect reconciliation proof case binding drifted",
            )
        if case["purpose"] == EFFECT_RECONCILIATION_LEGACY_PURPOSE:
            _require_current_legacy_decision_binding(conn, case)

        outcome = str(record["outcome"])
        target = str(record["target_resolution_status"])
        receipt = str(record["receipt_ref"] or "").strip() or None
        if not (
            (outcome == "NO_SIDE_EFFECT" and target == "CANCELLED" and receipt is None)
            or (
                outcome == "SIDE_EFFECT_CONFIRMED"
                and target == "COMMITTED_EVIDENCE"
                and receipt is not None
                and _DURABLE_REFERENCE_PATTERN.fullmatch(receipt) is not None
            )
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "persisted effect reconciliation proof resolution is invalid",
            )

        signature = str(record.get("attestation_signature") or "")
        key_id = str(record.get("attestation_key_id") or "")
        verifier = getattr(self, "_reconciliation_proof_verifier", None)
        if verifier is None:
            raise TaskStoreError(
                _RECONCILIATION_AUTHORITY_REQUIRED,
                "persisted effect proof requires a configured verifier",
            )
        if not verifier.has_key(key_id):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"persisted effect proof has unknown key_id: {key_id}",
            )
        if not _SIGNATURE_PATTERN.fullmatch(signature) or not verifier.verify(
            key_id=key_id,
            message=binding_bytes,
            signature=signature,
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "persisted effect reconciliation proof signature is invalid",
            )
        outbox_row = conn.execute(
            "SELECT * FROM task_runtime_outbox WHERE outbox_id = ?",
            (str(record["outbox_id"]),),
        ).fetchone()
        try:
            outbox_evidence = _loads_list(
                str(outbox_row["evidence_refs_json"]) if outbox_row is not None else "",
                field_name="effect_reconciliation.outbox_evidence_refs_json",
            )
        except TaskStoreError as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "persisted effect reconciliation proof Outbox evidence is invalid",
            ) from exc
        if (
            outbox_row is None
            or str(outbox_row["aggregate_type"]) != str(outbox_binding.get("aggregate_type") or "")
            or str(outbox_row["aggregate_id"]) != str(outbox_binding.get("aggregate_id") or "")
            or int(outbox_row["aggregate_version"]) != int(outbox_binding.get("aggregate_version", -1))
            or str(outbox_row["event_type"]) != str(outbox_binding.get("event_type") or "")
            or str(outbox_row["payload_ref"]) != str(outbox_binding.get("payload_ref") or "")
            or str(outbox_row["idempotency_key"]) != str(outbox_binding.get("idempotency_key") or "")
            or str(outbox_row["outbox_id"]) != str(outbox_binding.get("outbox_id") or "")
            or int(outbox_row["graph_revision"] or 0)
            != int(outbox_binding.get("graph_revision", -1))
            or int(outbox_row["graph_revision"] or 0)
            != int(case["graph_revision"])
            or (str(outbox_row["attempt_id"] or "").strip() or None)
            != (str(outbox_binding.get("attempt_id") or "").strip() or None)
            or outbox_evidence != list(outbox_binding.get("evidence_refs") or ())
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "persisted effect reconciliation proof Outbox drifted",
            )

        current_subject = self._effect_reconciliation_subject(
            conn,
            intent_ref=str(record["intent_ref"]),
            effect_id=str(record["effect_id"]),
        )
        current_projection = {
            "attempt": current_subject["attempt"],
            "effect": current_subject["effect"],
            "fence": current_subject["fence"],
            "intent": current_subject["intent"],
        }
        if current_projection != subject_snapshot:
            current_intent = dict(current_projection["intent"])
            signed_intent = dict(subject_snapshot["intent"])
            current_identity = dict(current_intent)
            signed_identity = dict(signed_intent)
            current_status = current_identity.pop("status", None)
            signed_status = signed_identity.pop("status", None)
            current_version = current_identity.pop("status_version", None)
            signed_version = signed_identity.pop("status_version", None)
            allowed_finalization = (
                str(record["purpose"]) == EFFECT_RECONCILIATION_PENDING_PURPOSE
                and signed_status == "REQUESTED"
                and current_status == "FINALIZED"
                and type(signed_version) is int
                and type(current_version) is int
                and current_version == signed_version + 1
                and current_identity == signed_identity
                and current_projection["attempt"] == subject_snapshot["attempt"]
                and current_projection["effect"] == subject_snapshot["effect"]
                and current_projection["fence"] == subject_snapshot["fence"]
            )
            if not allowed_finalization:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "persisted effect reconciliation proof subject drifted",
                )
        return {
            "schema_version": EFFECT_RECONCILIATION_PROOF_SCHEMA,
            "proof_id": str(record["proof_id"]),
            "purpose": str(record["purpose"]),
            "case_id": str(record["case_id"]),
            "intent_ref": str(record["intent_ref"]),
            "effect_id": str(record["effect_id"]),
            "graph_revision": int(record["graph_revision"]),
            "outcome": str(record["outcome"]),
            "target_resolution_status": str(record["target_resolution_status"]),
            "receipt_ref": receipt,
            "executor_quiescence_ref": str(
                record["executor_quiescence_ref"] or ""
            ).strip()
            or None,
            "reconciler_actor_id": str(record["reconciler_actor_id"]),
            "attestation_ref": str(record["attestation_ref"]),
            "attestation_key_id": key_id,
            "attestation_signature": signature,
            "case_snapshot_sha256": str(record["case_snapshot_sha256"]),
            "subject_snapshot_sha256": str(record["subject_snapshot_sha256"]),
            "proof_digest": str(record["proof_digest"]),
            "outbox_id": str(record["outbox_id"]),
            "issued_ts_ms": int(record["issued_ts_ms"]),
            "created_ts_ms": int(record["created_ts_ms"]),
            "proof_binding_json": canonical,
        }


__all__ = ["TaskEffectReconciliationReadMixin"]
