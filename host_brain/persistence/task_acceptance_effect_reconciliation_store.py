"""Durable authority for cancellation-independent Acceptance Effect recovery."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
import sqlite3
from typing import Any

from host_brain.contracts.agent_runtime.acceptance_effect_reconciliation import (
    ACCEPTANCE_EFFECT_RECONCILIATION_BINDING_DOMAIN,
    ACCEPTANCE_EFFECT_RECONCILIATION_BINDING_SCHEMA_VERSION,
    ACCEPTANCE_EFFECT_RECONCILIATION_CASE_SCHEMA_VERSION,
    ACCEPTANCE_EFFECT_RECONCILIATION_PROOF_SCHEMA_VERSION,
    ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE,
    AcceptanceEffectReconciliationStoreRejected,
    build_acceptance_effect_reconciliation_binding,
)
from host_brain.persistence.task_record_codec import (
    _acceptance_effect_record,
)
from host_brain.persistence.reconciliation_proof_verifier import (
    TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
    TASK_STORE_RECONCILIATION_PROOF_INVALID,
)
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_NOT_FOUND,
    TASK_STORE_OUTBOX_CONFLICT,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
    _DURABLE_REFERENCE_PATTERN,
)


ACCEPTANCE_EFFECT_RECONCILIATION_AGGREGATE_TYPE = (
    "ACCEPTANCE_EFFECT_RECONCILIATION"
)
ACCEPTANCE_EFFECT_RECONCILIATION_EVENT_TYPE = (
    "ACCEPTANCE_EFFECT_RECONCILIATION_PROOF_RECORDED"
)
ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED = (
    "ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED"
)
ACCEPTANCE_EFFECT_RECONCILED_NO_SIDE_EFFECT = (
    "ACCEPTANCE_EFFECT_RECONCILED_NO_SIDE_EFFECT"
)

_CASE_TABLE = "task_runtime_acceptance_effect_reconciliation_case"
_EVIDENCE_TABLE = "task_runtime_acceptance_effect_reconciliation_evidence"
_PROOF_TABLE = "task_runtime_acceptance_effect_reconciliation_proof"
_SIGNATURE_PATTERN = re.compile(r"^ed25519:[A-Za-z0-9_-]{86}==$")
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUIRED_TABLE_COLUMNS = {
    _EVIDENCE_TABLE: frozenset(
        {
            "evidence_ref",
            "schema_version",
            "request_id",
            "ingress_audit_ref",
            "body_sha256",
            "effect_id",
            "case_id",
            "expected_case_snapshot_sha256",
            "expected_subject_snapshot_sha256",
            "purpose",
            "outcome",
            "target_effect_status",
            "receipt_ref",
            "executor_quiescence_ref",
            "reconciler_actor_id",
            "issued_ts_ms",
            "attestation_ref",
            "attestation_key_id",
            "attestation_signature",
            "proof_id",
            "outbox_id",
            "proof_binding_json",
            "proof_digest",
            "evidence_payload_json",
            "evidence_digest",
            "created_ts_ms",
        }
    ),
    _CASE_TABLE: frozenset(
        {
            "case_id",
            "schema_version",
            "purpose",
            "case_kind",
            "classification",
            "effect_id",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "snapshot_json",
            "snapshot_sha256",
            "created_ts_ms",
        }
    ),
    _PROOF_TABLE: frozenset(
        {
            "proof_id",
            "schema_version",
            "purpose",
            "case_id",
            "effect_id",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "case_snapshot_sha256",
            "subject_snapshot_json",
            "subject_snapshot_sha256",
            "outcome",
            "target_effect_status",
            "receipt_ref",
            "executor_quiescence_ref",
            "reconciler_actor_id",
            "attestation_ref",
            "attestation_key_id",
            "attestation_signature",
            "issued_ts_ms",
            "proof_binding_json",
            "proof_digest",
            "outbox_id",
            "created_ts_ms",
        }
    ),
}
_REQUIRED_TRIGGERS = frozenset(
    {
        "trg_task_acceptance_effect_reconciliation_evidence_no_update",
        "trg_task_acceptance_effect_reconciliation_evidence_no_delete",
        "trg_task_acceptance_effect_reconciliation_case_no_update",
        "trg_task_acceptance_effect_reconciliation_case_no_delete",
        "trg_task_acceptance_effect_reconciliation_proof_no_update",
        "trg_task_acceptance_effect_reconciliation_proof_no_delete",
        "trg_task_acceptance_effect_reconciled_retry_requires_proof",
        "trg_task_acceptance_effect_confirmed_proof_terminal",
    }
)
_REQUIRED_INDEXES = frozenset(
    {
        "idx_task_runtime_acceptance_effect_recon_evidence_effect",
        "idx_task_runtime_acceptance_effect_recon_case_effect",
        "idx_task_runtime_acceptance_effect_recon_proof_effect",
    }
)
_TRIGGER_SQL_REQUIREMENTS = {
    "trg_task_acceptance_effect_reconciliation_evidence_no_update": (
        "BEFORE UPDATE ON TASK_RUNTIME_ACCEPTANCE_EFFECT_RECONCILIATION_EVIDENCE",
        "ACCEPTANCE EFFECT RECONCILIATION EVIDENCE IS APPEND-ONLY",
    ),
    "trg_task_acceptance_effect_reconciliation_evidence_no_delete": (
        "BEFORE DELETE ON TASK_RUNTIME_ACCEPTANCE_EFFECT_RECONCILIATION_EVIDENCE",
        "ACCEPTANCE EFFECT RECONCILIATION EVIDENCE IS APPEND-ONLY",
    ),
    "trg_task_acceptance_effect_reconciliation_case_no_update": (
        "BEFORE UPDATE ON TASK_RUNTIME_ACCEPTANCE_EFFECT_RECONCILIATION_CASE",
        "ACCEPTANCE EFFECT RECONCILIATION CASE IS IMMUTABLE",
    ),
    "trg_task_acceptance_effect_reconciliation_case_no_delete": (
        "BEFORE DELETE ON TASK_RUNTIME_ACCEPTANCE_EFFECT_RECONCILIATION_CASE",
        "ACCEPTANCE EFFECT RECONCILIATION CASE IS IMMUTABLE",
    ),
    "trg_task_acceptance_effect_reconciliation_proof_no_update": (
        "BEFORE UPDATE ON TASK_RUNTIME_ACCEPTANCE_EFFECT_RECONCILIATION_PROOF",
        "ACCEPTANCE EFFECT RECONCILIATION PROOF IS APPEND-ONLY",
    ),
    "trg_task_acceptance_effect_reconciliation_proof_no_delete": (
        "BEFORE DELETE ON TASK_RUNTIME_ACCEPTANCE_EFFECT_RECONCILIATION_PROOF",
        "ACCEPTANCE EFFECT RECONCILIATION PROOF IS APPEND-ONLY",
    ),
    "trg_task_acceptance_effect_reconciled_retry_requires_proof": (
        "NEW.REASON_CODE = 'ACCEPTANCE_EFFECT_RECONCILED_NO_SIDE_EFFECT'",
        "PROOF.OUTCOME = 'NO_SIDE_EFFECT'",
        "PROOF.TARGET_EFFECT_STATUS = 'PENDING'",
    ),
    "trg_task_acceptance_effect_confirmed_proof_terminal": (
        "PROOF.OUTCOME = 'SIDE_EFFECT_CONFIRMED'",
        "NEW.STATUS <> 'COMMITTED'",
        "NEW.RECEIPT_REF <> PROOF.RECEIPT_REF",
    ),
}


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _required_text(value: Any, field_name: str, *, maximum: int = 512) -> str:
    text = str(value or "")
    if not text or text != text.strip() or len(text) > maximum:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"{field_name} must be canonical non-empty text",
        )
    return text


def _durable_ref(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    if _DURABLE_REFERENCE_PATTERN.fullmatch(text) is None:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"{field_name} must be a durable reference",
        )
    return text


def _optional_durable_ref(value: Any, field_name: str) -> str | None:
    if value is None or value == "":
        return None
    return _durable_ref(value, field_name)


def _digest(value: Any, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if _SHA256_PATTERN.fullmatch(text) is None:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"{field_name} must be a sha256 digest",
        )
    return text


class TaskAcceptanceEffectReconciliationStoreMixin:
    """Freeze, verify and settle one exact unknown Effect in one SQLite UoW."""

    def assert_acceptance_effect_reconciliation_ready(
        self,
        *,
        force_revalidate: bool = False,
    ) -> None:
        if (
            not force_revalidate
            and getattr(
                self,
                "_acceptance_effect_reconciliation_schema_ready",
                False,
            )
        ):
            return
        self._acceptance_effect_reconciliation_schema_ready = False
        conn = self._connect()
        try:
            for table_name, expected_columns in _REQUIRED_TABLE_COLUMNS.items():
                columns = {
                    str(row["name"])
                    for row in conn.execute(
                        f"PRAGMA table_info({table_name})"
                    ).fetchall()
                }
                if columns != expected_columns:
                    raise TaskStoreError(
                        TASK_STORE_SCHEMA_NOT_READY,
                        "Acceptance Effect reconciliation schema is not ready: "
                        f"{table_name}",
                    )
            indexes = {
                str(row["name"])
                for row in conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'index'
                      AND name LIKE
                        'idx_task_runtime_acceptance_effect_recon_%'
                    """
                ).fetchall()
            }
            triggers = {
                str(row["name"]): " ".join(
                    str(row["sql"] or "").upper().split()
                )
                for row in conn.execute(
                    """
                    SELECT name, sql FROM sqlite_master
                    WHERE type = 'trigger'
                      AND name LIKE 'trg_task_acceptance_effect_%'
                    """
                ).fetchall()
            }
            malformed_trigger = next(
                (
                    name
                    for name, fragments in _TRIGGER_SQL_REQUIREMENTS.items()
                    if any(
                        fragment not in triggers.get(name, "")
                        for fragment in fragments
                    )
                ),
                None,
            )
            if (
                not _REQUIRED_INDEXES.issubset(indexes)
                or not _REQUIRED_TRIGGERS.issubset(triggers)
                or malformed_trigger is not None
            ):
                raise TaskStoreError(
                    TASK_STORE_SCHEMA_NOT_READY,
                    "Acceptance Effect reconciliation physical schema is not ready",
                )
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "Acceptance Effect reconciliation foreign keys drifted",
                )
        finally:
            conn.close()
        self._acceptance_effect_reconciliation_schema_ready = True

    def list_acceptance_effect_reconciliation_candidates(
        self,
        *,
        limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        self.assert_acceptance_effect_reconciliation_ready()
        bounded = min(500, max(1, int(limit)))
        conn = self._connect()
        try:
            ts_ms = self._now()
            rows = conn.execute(
                """
                SELECT effect.effect_id
                FROM task_runtime_acceptance_effect_intent AS effect
                JOIN task_runtime_attempt AS attempt
                  ON attempt.attempt_id = effect.attempt_id
                 AND attempt.graph_id = effect.graph_id
                 AND attempt.graph_revision = effect.graph_revision
                 AND attempt.node_id = effect.node_id
                JOIN task_runtime_node AS node
                  ON node.graph_id = effect.graph_id
                 AND node.graph_revision = effect.graph_revision
                 AND node.node_id = effect.node_id
                JOIN task_runtime_graph AS graph_row
                  ON graph_row.graph_id = effect.graph_id
                 AND graph_row.revision = effect.graph_revision
                WHERE attempt.status = 'SUCCEEDED'
                  AND node.status = 'ACCEPTED'
                  AND graph_row.status IN ('ACTIVE', 'COMPLETED')
                  AND effect.receipt_ref IS NULL
                  AND (
                    (
                        effect.status = 'PENDING'
                        AND effect.reason_code =
                            'ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED'
                    )
                    OR effect.status = 'FAILED'
                    OR (
                        effect.status = 'CLAIMED'
                        AND effect.lease_expires_ts_ms <= ?
                    )
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM task_runtime_graph_cancellation_effect_fence AS fence
                    WHERE fence.effect_id = effect.effect_id
                  )
                ORDER BY effect.updated_ts_ms, effect.effect_id
                LIMIT ?
                """,
                (ts_ms, bounded),
            ).fetchall()
            results: list[dict[str, Any]] = []
            for row in rows:
                try:
                    results.append(
                        self._read_acceptance_effect_reconciliation_in_transaction(
                            conn,
                            effect_id=str(row["effect_id"]),
                            case_id=None,
                            require_candidate=True,
                            ts_ms=ts_ms,
                        )
                    )
                except TaskStoreError:
                    continue
            return tuple(results)
        finally:
            conn.close()

    def read_acceptance_effect_reconciliation(
        self,
        effect_id: str,
        *,
        case_id: str | None = None,
    ) -> dict[str, Any]:
        self.assert_acceptance_effect_reconciliation_ready()
        effect_ref = _durable_ref(effect_id, "effect_id")
        case_ref = (
            _durable_ref(case_id, "case_id") if case_id is not None else None
        )
        conn = self._connect()
        try:
            return self._read_acceptance_effect_reconciliation_in_transaction(
                conn,
                effect_id=effect_ref,
                case_id=case_ref,
                require_candidate=case_ref is None,
                ts_ms=self._now(),
            )
        finally:
            conn.close()


    def record_acceptance_effect_reconciliation_proof(
        self,
        effect_id: str,
        *,
        case_id: str,
        expected_case_snapshot_sha256: str,
        expected_subject_snapshot_sha256: str,
        expected_effect_status: str,
        expected_effect_status_version: int,
        expected_effect_fencing_token: int,
        expected_effect_attempt_count: int,
        outcome: str,
        target_effect_status: str,
        receipt_ref: str | None,
        executor_quiescence_ref: str | None,
        reconciler_actor_id: str,
        issued_ts_ms: int,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
        proof_id: str,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        try:
            return self._record_acceptance_effect_reconciliation_proof(
                effect_id,
                case_id=case_id,
                expected_case_snapshot_sha256=(
                    expected_case_snapshot_sha256
                ),
                expected_subject_snapshot_sha256=(
                    expected_subject_snapshot_sha256
                ),
                expected_effect_status=expected_effect_status,
                expected_effect_status_version=expected_effect_status_version,
                expected_effect_fencing_token=expected_effect_fencing_token,
                expected_effect_attempt_count=expected_effect_attempt_count,
                outcome=outcome,
                target_effect_status=target_effect_status,
                receipt_ref=receipt_ref,
                executor_quiescence_ref=executor_quiescence_ref,
                reconciler_actor_id=reconciler_actor_id,
                issued_ts_ms=issued_ts_ms,
                attestation_ref=attestation_ref,
                attestation_key_id=attestation_key_id,
                attestation_signature=attestation_signature,
                proof_id=proof_id,
                outbox=outbox,
            )
        except TaskStoreError as exc:
            raise AcceptanceEffectReconciliationStoreRejected(
                exc.reason_code,
                exc.detail,
            ) from exc

    def _record_acceptance_effect_reconciliation_proof(
        self,
        effect_id: str,
        *,
        case_id: str,
        expected_case_snapshot_sha256: str,
        expected_subject_snapshot_sha256: str,
        expected_effect_status: str,
        expected_effect_status_version: int,
        expected_effect_fencing_token: int,
        expected_effect_attempt_count: int,
        outcome: str,
        target_effect_status: str,
        receipt_ref: str | None,
        executor_quiescence_ref: str | None,
        reconciler_actor_id: str,
        issued_ts_ms: int,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
        proof_id: str,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        self.assert_acceptance_effect_reconciliation_ready()
        effect_ref = _durable_ref(effect_id, "effect_id")
        case_ref = _durable_ref(case_id, "case_id")
        proof_ref = _durable_ref(proof_id, "proof_id")
        case_digest = _digest(
            expected_case_snapshot_sha256,
            "expected_case_snapshot_sha256",
        )
        subject_digest = _digest(
            expected_subject_snapshot_sha256,
            "expected_subject_snapshot_sha256",
        )
        normalized_outcome = str(outcome or "").strip().upper()
        normalized_target = str(target_effect_status or "").strip().upper()
        receipt = _optional_durable_ref(receipt_ref, "receipt_ref")
        quiescence = _optional_durable_ref(
            executor_quiescence_ref,
            "executor_quiescence_ref",
        )
        actor_id = _required_text(
            reconciler_actor_id,
            "reconciler_actor_id",
        )
        attestation_reference = _durable_ref(
            attestation_ref,
            "attestation_ref",
        )
        key_id = _required_text(
            attestation_key_id,
            "attestation_key_id",
            maximum=255,
        )
        signature = str(attestation_signature or "").strip()
        if (
            type(expected_effect_status_version) is not int
            or expected_effect_status_version < 0
            or type(expected_effect_fencing_token) is not int
            or expected_effect_fencing_token < 1
            or type(expected_effect_attempt_count) is not int
            or expected_effect_attempt_count < 1
            or type(issued_ts_ms) is not int
            or issued_ts_ms < 0
            or _SIGNATURE_PATTERN.fullmatch(signature) is None
            or not (
                (
                    normalized_outcome == "NO_SIDE_EFFECT"
                    and normalized_target == "PENDING"
                    and receipt is None
                    and quiescence is not None
                )
                or (
                    normalized_outcome == "SIDE_EFFECT_CONFIRMED"
                    and normalized_target == "COMMITTED"
                    and receipt is not None
                )
            )
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Acceptance Effect reconciliation request matrix is invalid",
            )
        if not isinstance(outbox, OutboxMessage):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Acceptance Effect reconciliation Outbox is invalid",
            )
        ts_ms = self._now()
        with self._transaction() as conn:
            existing = conn.execute(
                f"SELECT * FROM {_PROOF_TABLE} WHERE case_id = ?",
                (case_ref,),
            ).fetchone()
            if existing is not None:
                proof = (
                    self._validated_acceptance_effect_reconciliation_proof(
                        conn,
                        existing,
                    )
                )
                if not self._proof_matches_request(
                    proof,
                    proof_id=proof_ref,
                    effect_id=effect_ref,
                    case_id=case_ref,
                    outcome=normalized_outcome,
                    target_effect_status=normalized_target,
                    receipt_ref=receipt,
                    executor_quiescence_ref=quiescence,
                    reconciler_actor_id=actor_id,
                    attestation_ref=attestation_reference,
                    attestation_key_id=key_id,
                    attestation_signature=signature,
                    issued_ts_ms=issued_ts_ms,
                    outbox_id=outbox.outbox_id,
                ):
                    raise TaskStoreError(
                        TASK_STORE_OUTBOX_CONFLICT,
                        "Acceptance Effect case already has a different proof",
                    )
                case = self._validated_acceptance_effect_reconciliation_case(
                    conn.execute(
                        f"SELECT * FROM {_CASE_TABLE} WHERE case_id = ?",
                        (case_ref,),
                    ).fetchone()
                )
                return {
                    "case": case,
                    "proof": proof,
                    "effect": self._current_acceptance_effect(
                        effect_ref,
                        conn=conn,
                    ),
                    "replayed": True,
                }

            subject = self._derive_acceptance_effect_reconciliation_subject(
                conn,
                effect_id=effect_ref,
                ts_ms=ts_ms,
                require_candidate=True,
            )
            case = dict(subject["case"])
            effect = dict(subject["effect"])
            if (
                case["case_id"] != case_ref
                or case["snapshot_sha256"] != case_digest
                or subject["subject_snapshot_sha256"] != subject_digest
                or effect["status"] != str(expected_effect_status or "").upper()
                or effect["status_version"] != expected_effect_status_version
                or effect["fencing_token"] != expected_effect_fencing_token
                or effect["attempt_count"] != expected_effect_attempt_count
            ):
                raise TaskStoreError(
                    TASK_STORE_OUTBOX_CONFLICT,
                    "Acceptance Effect reconciliation subject drifted",
                )
            if effect["status"] == "CLAIMED":
                if (
                    effect["lease_expires_ts_ms"] is None
                    or int(effect["lease_expires_ts_ms"]) > ts_ms
                    or quiescence is None
                ):
                    raise TaskStoreError(
                        TASK_STORE_INVALID,
                        "CLAIMED Effect requires expired lease and quiescence",
                    )
            expected_evidence = {
                proof_ref,
                case_ref,
                effect_ref,
                attestation_reference,
            }
            if receipt is not None:
                expected_evidence.add(receipt)
            if quiescence is not None:
                expected_evidence.add(quiescence)
            if (
                outbox.aggregate_type
                != ACCEPTANCE_EFFECT_RECONCILIATION_AGGREGATE_TYPE
                or outbox.aggregate_id != case_ref
                or outbox.aggregate_version != 0
                or outbox.graph_revision is not None
                or outbox.event_type
                != ACCEPTANCE_EFFECT_RECONCILIATION_EVENT_TYPE
                or outbox.attempt_id != case["attempt_id"]
                or set(outbox.evidence_refs) != expected_evidence
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "Acceptance Effect reconciliation Outbox binding is invalid",
                )
            binding_payload = self._acceptance_effect_reconciliation_binding(
                subject=subject,
                proof_id=proof_ref,
                outcome=normalized_outcome,
                target_effect_status=normalized_target,
                receipt_ref=receipt,
                executor_quiescence_ref=quiescence,
                reconciler_actor_id=actor_id,
                attestation_ref=attestation_reference,
                attestation_key_id=key_id,
                issued_ts_ms=issued_ts_ms,
                outbox=outbox,
            )
            binding = build_acceptance_effect_reconciliation_binding(
                binding_payload
            )
            verifier = getattr(self, "_reconciliation_proof_verifier", None)
            if verifier is None:
                raise TaskStoreError(
                    TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
                    "no Acceptance Effect reconciliation verifier is configured",
                )
            if not verifier.has_key(key_id) or not verifier.verify(
                key_id=key_id,
                message=binding,
                signature=signature,
            ):
                raise TaskStoreError(
                    TASK_STORE_RECONCILIATION_PROOF_INVALID,
                    "Acceptance Effect reconciliation signature is invalid",
                )
            conn.execute(
                f"""
                INSERT INTO {_CASE_TABLE}(
                    case_id, schema_version, purpose, case_kind,
                    classification, effect_id, graph_id, graph_revision,
                    node_id, attempt_id, snapshot_json, snapshot_sha256,
                    created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_ref,
                    ACCEPTANCE_EFFECT_RECONCILIATION_CASE_SCHEMA_VERSION,
                    ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE,
                    "GENERAL_UNKNOWN_EFFECT",
                    "REPAIRABLE_SIGNED",
                    effect_ref,
                    case["graph_id"],
                    case["graph_revision"],
                    case["node_id"],
                    case["attempt_id"],
                    case["snapshot_json"],
                    case_digest,
                    ts_ms,
                ),
            )
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    ACCEPTANCE_EFFECT_RECONCILIATION_AGGREGATE_TYPE,
                    case_ref,
                    0,
                ),
                expected_event_type=(
                    ACCEPTANCE_EFFECT_RECONCILIATION_EVENT_TYPE
                ),
                expected_attempt_id=case["attempt_id"],
                expected_graph_revision=None,
            )
            binding_json = binding.decode("utf-8")
            proof_digest = _sha256_text(binding_json)
            subject_json = _canonical_json(
                {
                    "case": {
                        key: value
                        for key, value in case.items()
                        if key != "snapshot_json"
                    },
                    "effect": subject["effect"],
                    "task": subject["task"],
                    "subject_snapshot_sha256": subject_digest,
                }
            )
            conn.execute(
                f"""
                INSERT INTO {_PROOF_TABLE}(
                    proof_id, schema_version, purpose, case_id, effect_id,
                    graph_id, graph_revision, node_id, attempt_id,
                    case_snapshot_sha256, subject_snapshot_json,
                    subject_snapshot_sha256, outcome, target_effect_status,
                    receipt_ref, executor_quiescence_ref,
                    reconciler_actor_id, attestation_ref, attestation_key_id,
                    attestation_signature, issued_ts_ms, proof_binding_json,
                    proof_digest, outbox_id, created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proof_ref,
                    ACCEPTANCE_EFFECT_RECONCILIATION_PROOF_SCHEMA_VERSION,
                    ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE,
                    case_ref,
                    effect_ref,
                    case["graph_id"],
                    case["graph_revision"],
                    case["node_id"],
                    case["attempt_id"],
                    case_digest,
                    subject_json,
                    subject_digest,
                    normalized_outcome,
                    normalized_target,
                    receipt,
                    quiescence,
                    actor_id,
                    attestation_reference,
                    key_id,
                    signature,
                    issued_ts_ms,
                    binding_json,
                    proof_digest,
                    outbox.outbox_id,
                    ts_ms,
                ),
            )
            if normalized_outcome == "NO_SIDE_EFFECT":
                error_marker = f"signed-reconciliation:{proof_digest}"
                cursor = conn.execute(
                    """
                    UPDATE task_runtime_acceptance_effect_intent
                    SET status = 'PENDING', available_ts_ms = ?,
                        lease_owner = NULL, lease_expires_ts_ms = NULL,
                        receipt_ref = NULL,
                        reason_code =
                            'ACCEPTANCE_EFFECT_RECONCILED_NO_SIDE_EFFECT',
                        last_error = ?, status_version = status_version + 1,
                        updated_ts_ms = ?, committed_ts_ms = NULL,
                        failed_ts_ms = NULL
                    WHERE effect_id = ? AND status = ?
                      AND status_version = ? AND fencing_token = ?
                      AND attempt_count = ? AND receipt_ref IS NULL
                    """,
                    (
                        ts_ms,
                        error_marker,
                        ts_ms,
                        effect_ref,
                        effect["status"],
                        effect["status_version"],
                        effect["fencing_token"],
                        effect["attempt_count"],
                    ),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE task_runtime_acceptance_effect_intent
                    SET status = 'COMMITTED',
                        lease_owner = COALESCE(lease_owner, ?),
                        lease_expires_ts_ms = NULL,
                        receipt_ref = ?, reason_code = NULL,
                        last_error = NULL,
                        status_version = status_version + 1,
                        updated_ts_ms = ?, committed_ts_ms = ?,
                        failed_ts_ms = NULL
                    WHERE effect_id = ? AND status = ?
                      AND status_version = ? AND fencing_token = ?
                      AND attempt_count = ? AND receipt_ref IS NULL
                    """,
                    (
                        actor_id,
                        receipt,
                        ts_ms,
                        ts_ms,
                        effect_ref,
                        effect["status"],
                        effect["status_version"],
                        effect["fencing_token"],
                        effect["attempt_count"],
                    ),
                )
            if cursor.rowcount != 1:
                raise TaskStoreError(
                    TASK_STORE_OUTBOX_CONFLICT,
                    "Acceptance Effect reconciliation lost its subject CAS",
                )
            stored_case = self._validated_acceptance_effect_reconciliation_case(
                conn.execute(
                    f"SELECT * FROM {_CASE_TABLE} WHERE case_id = ?",
                    (case_ref,),
                ).fetchone()
            )
            stored_proof = (
                self._validated_acceptance_effect_reconciliation_proof(
                    conn,
                    conn.execute(
                        f"SELECT * FROM {_PROOF_TABLE} WHERE proof_id = ?",
                        (proof_ref,),
                    ).fetchone(),
                )
            )
            return {
                "case": stored_case,
                "proof": stored_proof,
                "effect": self._current_acceptance_effect(
                    effect_ref,
                    conn=conn,
                ),
                "replayed": False,
            }

    def _derive_acceptance_effect_reconciliation_subject(
        self,
        conn: sqlite3.Connection,
        *,
        effect_id: str,
        ts_ms: int,
        require_candidate: bool,
    ) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT * FROM task_runtime_acceptance_effect_intent
            WHERE effect_id = ?
            """,
            (effect_id,),
        ).fetchone()
        if row is None:
            raise TaskStoreError(
                TASK_STORE_NOT_FOUND,
                f"Acceptance Effect not found: {effect_id}",
            )
        effect_record = _acceptance_effect_record(row)
        graph_row = conn.execute(
            """
            SELECT * FROM task_runtime_graph
            WHERE graph_id = ? AND revision = ?
            """,
            (
                str(effect_record["graph_id"]),
                int(effect_record["graph_revision"]),
            ),
        ).fetchone()
        node_row = conn.execute(
            """
            SELECT * FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
            """,
            (
                str(effect_record["graph_id"]),
                int(effect_record["graph_revision"]),
                str(effect_record["node_id"]),
            ),
        ).fetchone()
        attempt_row = conn.execute(
            """
            SELECT * FROM task_runtime_attempt WHERE attempt_id = ?
            """,
            (str(effect_record["attempt_id"]),),
        ).fetchone()
        if graph_row is None or node_row is None or attempt_row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Acceptance Effect Task lineage is incomplete",
            )
        cancellation = conn.execute(
            """
            SELECT intent_ref FROM task_runtime_graph_cancellation_effect_fence
            WHERE effect_id = ?
            """,
            (effect_id,),
        ).fetchone()
        status = str(effect_record["status"])
        candidate = (
            (
                status == "PENDING"
                and str(effect_record.get("reason_code") or "")
                == ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED
                and effect_record.get("lease_owner") is None
                and effect_record.get("lease_expires_ts_ms") is None
            )
            or status == "FAILED"
            or (
                status == "CLAIMED"
                and effect_record.get("lease_expires_ts_ms") is not None
                and int(effect_record["lease_expires_ts_ms"]) <= ts_ms
            )
        )
        if require_candidate and (
            not candidate
            or cancellation is not None
            or effect_record.get("receipt_ref") is not None
            or str(graph_row["status"]) not in {"ACTIVE", "COMPLETED"}
            or str(node_row["status"]) != "ACCEPTED"
            or str(attempt_row["status"]) != "SUCCEEDED"
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Acceptance Effect is not a general reconciliation candidate",
            )
        effect = {
            "effect_id": str(effect_record["effect_id"]),
            "commit_scope": str(effect_record["commit_scope"]),
            "effect_type": str(effect_record["effect_type"]),
            "payload_ref": str(effect_record["payload_ref"]),
            "payload_sha256": str(effect_record["payload_sha256"]),
            "idempotency_key": str(effect_record["idempotency_key"]),
            "status": status,
            "status_version": int(effect_record["status_version"]),
            "available_ts_ms": int(effect_record["available_ts_ms"]),
            "lease_owner": effect_record.get("lease_owner"),
            "lease_expires_ts_ms": effect_record.get("lease_expires_ts_ms"),
            "fencing_token": int(effect_record["fencing_token"]),
            "attempt_count": int(effect_record["attempt_count"]),
            "receipt_ref": effect_record.get("receipt_ref"),
            "reason_code": effect_record.get("reason_code"),
            "last_error_sha256": (
                _sha256_text(str(effect_record["last_error"]))
                if effect_record.get("last_error") is not None
                else None
            ),
            "created_ts_ms": int(effect_record["created_ts_ms"]),
            "updated_ts_ms": int(effect_record["updated_ts_ms"]),
            "committed_ts_ms": effect_record.get("committed_ts_ms"),
            "failed_ts_ms": effect_record.get("failed_ts_ms"),
        }
        task = {
            "graph_id": str(graph_row["graph_id"]),
            "graph_revision": int(graph_row["revision"]),
            "graph_status": str(graph_row["status"]),
            "graph_status_version": int(graph_row["status_version"]),
            "node_id": str(node_row["node_id"]),
            "node_status": str(node_row["status"]),
            "node_status_version": int(node_row["status_version"]),
            "attempt_id": str(attempt_row["attempt_id"]),
            "attempt_status": str(attempt_row["status"]),
            "attempt_status_version": int(attempt_row["status_version"]),
        }
        snapshot_json = _canonical_json({"effect": effect, "task": task})
        snapshot_digest = _sha256_text(snapshot_json)
        case_id = (
            "effect-recon-case://sha256/"
            + hashlib.sha256(
                (
                    "GENERAL_UNKNOWN_EFFECT\n"
                    f"{effect_id}\n{snapshot_digest}"
                ).encode("utf-8")
            ).hexdigest()
        )
        case = {
            "schema_version": (
                ACCEPTANCE_EFFECT_RECONCILIATION_CASE_SCHEMA_VERSION
            ),
            "case_id": case_id,
            "purpose": ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE,
            "case_kind": "GENERAL_UNKNOWN_EFFECT",
            "classification": "REPAIRABLE_SIGNED",
            "effect_id": effect_id,
            "graph_id": task["graph_id"],
            "graph_revision": task["graph_revision"],
            "node_id": task["node_id"],
            "attempt_id": task["attempt_id"],
            "snapshot_json": snapshot_json,
            "snapshot_sha256": snapshot_digest,
        }
        public_case = {
            key: value for key, value in case.items() if key != "snapshot_json"
        }
        subject_material = {
            "case": public_case,
            "effect": effect,
            "task": task,
        }
        subject_digest = _sha256_text(_canonical_json(subject_material))
        return {
            **subject_material,
            "case": case,
            "subject_snapshot_sha256": subject_digest,
        }

    @staticmethod
    def _acceptance_effect_reconciliation_binding(
        *,
        subject: Mapping[str, Any],
        proof_id: str,
        outcome: str,
        target_effect_status: str,
        receipt_ref: str | None,
        executor_quiescence_ref: str | None,
        reconciler_actor_id: str,
        attestation_ref: str,
        attestation_key_id: str,
        issued_ts_ms: int,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        case = dict(subject["case"])
        case.pop("snapshot_json", None)
        return {
            "schema_version": (
                ACCEPTANCE_EFFECT_RECONCILIATION_BINDING_SCHEMA_VERSION
            ),
            "domain": ACCEPTANCE_EFFECT_RECONCILIATION_BINDING_DOMAIN,
            "subject_snapshot_sha256": subject[
                "subject_snapshot_sha256"
            ],
            "case": case,
            "effect": dict(subject["effect"]),
            "task": dict(subject["task"]),
            "resolution": {
                "purpose": ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE,
                "outcome": outcome,
                "target_effect_status": target_effect_status,
                "receipt_ref": receipt_ref,
                "executor_quiescence_ref": executor_quiescence_ref,
            },
            "authority": {
                "reconciler_actor_id": reconciler_actor_id,
                "attestation_ref": attestation_ref,
                "attestation_key_id": attestation_key_id,
                "issued_ts_ms": issued_ts_ms,
            },
            "outbox": {
                "proof_id": proof_id,
                "aggregate_type": outbox.aggregate_type,
                "aggregate_id": outbox.aggregate_id,
                "aggregate_version": outbox.aggregate_version,
                "event_type": outbox.event_type,
                "payload_ref": outbox.payload_ref,
                "idempotency_key": outbox.idempotency_key,
                "outbox_id": outbox.outbox_id,
                "attempt_id": outbox.attempt_id,
                "evidence_refs": list(outbox.evidence_refs),
            },
        }

    @staticmethod
    def _proof_matches_request(
        proof: Mapping[str, Any],
        **expected: Any,
    ) -> bool:
        return all(proof.get(key) == value for key, value in expected.items())

    @staticmethod
    def _validated_acceptance_effect_reconciliation_case(
        row: sqlite3.Row | Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Acceptance Effect reconciliation case is missing",
            )
        record = dict(row)
        try:
            snapshot = json.loads(str(record["snapshot_json"]))
            canonical = _canonical_json(snapshot)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Acceptance Effect reconciliation case is invalid",
            ) from exc
        digest = _sha256_text(canonical)
        expected_case_id = (
            "effect-recon-case://sha256/"
            + hashlib.sha256(
                (
                    "GENERAL_UNKNOWN_EFFECT\n"
                    f"{record.get('effect_id')}\n{digest}"
                ).encode("utf-8")
            ).hexdigest()
        )
        if (
            record.get("schema_version")
            != ACCEPTANCE_EFFECT_RECONCILIATION_CASE_SCHEMA_VERSION
            or record.get("purpose")
            != ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE
            or record.get("case_kind") != "GENERAL_UNKNOWN_EFFECT"
            or record.get("classification") != "REPAIRABLE_SIGNED"
            or canonical != str(record.get("snapshot_json") or "")
            or digest != str(record.get("snapshot_sha256") or "")
            or expected_case_id != str(record.get("case_id") or "")
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Acceptance Effect reconciliation case binding drifted",
            )
        return record

    def _validated_acceptance_effect_reconciliation_proof(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row | Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Acceptance Effect reconciliation proof is missing",
            )
        record = dict(row)
        try:
            subject = json.loads(str(record["subject_snapshot_json"]))
            binding = json.loads(str(record["proof_binding_json"]))
            binding_bytes = (
                build_acceptance_effect_reconciliation_binding(binding)
            )
            canonical_subject = _canonical_json(subject)
            canonical_binding = binding_bytes.decode("utf-8")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Acceptance Effect reconciliation proof is invalid",
            ) from exc
        try:
            case_binding = dict(binding["case"])
            effect_binding = dict(binding["effect"])
            task_binding = dict(binding["task"])
            resolution_binding = dict(binding["resolution"])
            authority_binding = dict(binding["authority"])
            outbox_binding = dict(binding["outbox"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Acceptance Effect reconciliation proof fields are invalid",
            ) from exc
        subject_material = {
            "case": subject.get("case"),
            "effect": subject.get("effect"),
            "task": subject.get("task"),
        }
        subject_digest = _sha256_text(_canonical_json(subject_material))
        expected_subject = {
            **subject_material,
            "subject_snapshot_sha256": subject_digest,
        }
        if (
            record.get("schema_version")
            != ACCEPTANCE_EFFECT_RECONCILIATION_PROOF_SCHEMA_VERSION
            or record.get("purpose")
            != ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE
            or canonical_subject
            != str(record.get("subject_snapshot_json") or "")
            or subject != expected_subject
            or subject_digest
            != str(record.get("subject_snapshot_sha256") or "")
            or canonical_binding != str(record.get("proof_binding_json") or "")
            or _sha256_text(canonical_binding)
            != str(record.get("proof_digest") or "")
            or binding.get("subject_snapshot_sha256") != subject_digest
            or case_binding != subject.get("case")
            or effect_binding != subject.get("effect")
            or task_binding != subject.get("task")
            or outbox_binding.get("proof_id") != record.get("proof_id")
            or case_binding.get("case_id") != record.get("case_id")
            or case_binding.get("effect_id") != record.get("effect_id")
            or case_binding.get("graph_id") != record.get("graph_id")
            or case_binding.get("graph_revision")
            != record.get("graph_revision")
            or case_binding.get("node_id") != record.get("node_id")
            or case_binding.get("attempt_id") != record.get("attempt_id")
            or case_binding.get("snapshot_sha256")
            != record.get("case_snapshot_sha256")
            or case_binding.get("purpose") != record.get("purpose")
            or resolution_binding.get("purpose") != record.get("purpose")
            or resolution_binding.get("outcome") != record.get("outcome")
            or resolution_binding.get("target_effect_status")
            != record.get("target_effect_status")
            or resolution_binding.get("receipt_ref")
            != record.get("receipt_ref")
            or resolution_binding.get("executor_quiescence_ref")
            != record.get("executor_quiescence_ref")
            or authority_binding.get("reconciler_actor_id")
            != record.get("reconciler_actor_id")
            or authority_binding.get("attestation_ref")
            != record.get("attestation_ref")
            or authority_binding.get("attestation_key_id")
            != record.get("attestation_key_id")
            or authority_binding.get("issued_ts_ms")
            != record.get("issued_ts_ms")
            or outbox_binding.get("outbox_id") != record.get("outbox_id")
            or (
                record.get("outcome") == "NO_SIDE_EFFECT"
                and (
                    record.get("target_effect_status") != "PENDING"
                    or record.get("receipt_ref") is not None
                    or record.get("executor_quiescence_ref") is None
                )
            )
            or (
                record.get("outcome") == "SIDE_EFFECT_CONFIRMED"
                and (
                    record.get("target_effect_status") != "COMMITTED"
                    or record.get("receipt_ref") is None
                )
            )
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Acceptance Effect reconciliation proof binding drifted",
            )
        case_row = conn.execute(
            f"SELECT * FROM {_CASE_TABLE} WHERE case_id = ?",
            (str(record["case_id"]),),
        ).fetchone()
        case = self._validated_acceptance_effect_reconciliation_case(case_row)
        public_case = {
            key: value
            for key, value in case.items()
            if key not in {"snapshot_json", "created_ts_ms"}
        }
        if (
            case_binding != public_case
            or case.get("snapshot_sha256")
            != record.get("case_snapshot_sha256")
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Acceptance Effect reconciliation proof case drifted",
            )

        outbox_row = conn.execute(
            """
            SELECT * FROM task_runtime_outbox
            WHERE outbox_id = ?
            """,
            (str(record["outbox_id"]),),
        ).fetchone()
        try:
            evidence_refs = json.loads(
                str(outbox_row["evidence_refs_json"])
                if outbox_row is not None
                else ""
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Acceptance Effect reconciliation Outbox evidence is invalid",
            ) from exc
        if (
            outbox_row is None
            or not isinstance(evidence_refs, list)
            or any(not isinstance(value, str) for value in evidence_refs)
            or str(outbox_row["aggregate_type"])
            != str(outbox_binding.get("aggregate_type") or "")
            or str(outbox_row["aggregate_id"])
            != str(outbox_binding.get("aggregate_id") or "")
            or int(outbox_row["aggregate_version"])
            != int(outbox_binding.get("aggregate_version", -1))
            or str(outbox_row["event_type"])
            != str(outbox_binding.get("event_type") or "")
            or str(outbox_row["payload_ref"])
            != str(outbox_binding.get("payload_ref") or "")
            or str(outbox_row["idempotency_key"])
            != str(outbox_binding.get("idempotency_key") or "")
            or str(outbox_row["outbox_id"])
            != str(outbox_binding.get("outbox_id") or "")
            or outbox_row["graph_revision"] is not None
            or (str(outbox_row["attempt_id"] or "").strip() or None)
            != (str(outbox_binding.get("attempt_id") or "").strip() or None)
            or evidence_refs != list(outbox_binding.get("evidence_refs") or ())
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Acceptance Effect reconciliation proof Outbox drifted",
            )

        signature = str(record.get("attestation_signature") or "")
        key_id = str(record.get("attestation_key_id") or "")
        verifier = getattr(self, "_reconciliation_proof_verifier", None)
        if verifier is None:
            raise TaskStoreError(
                TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
                "persisted Acceptance Effect proof requires a verifier",
            )
        if (
            not verifier.has_key(key_id)
            or _SIGNATURE_PATTERN.fullmatch(signature) is None
            or not verifier.verify(
                key_id=key_id,
                message=binding_bytes,
                signature=signature,
            )
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "persisted Acceptance Effect proof signature is invalid",
            )

        current_effect = self._current_acceptance_effect(
            str(record["effect_id"]),
            conn=conn,
        )
        immutable_effect_fields = (
            "effect_id",
            "commit_scope",
            "effect_type",
            "payload_ref",
            "payload_sha256",
            "idempotency_key",
            "created_ts_ms",
        )
        if any(
            current_effect.get(field_name)
            != effect_binding.get(field_name)
            for field_name in immutable_effect_fields
        ) or (
            record.get("outcome") == "SIDE_EFFECT_CONFIRMED"
            and (
                current_effect.get("status") != "COMMITTED"
                or current_effect.get("receipt_ref")
                != record.get("receipt_ref")
            )
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "persisted Acceptance Effect proof subject drifted",
            )
        return record

    def _current_acceptance_effect(
        self,
        effect_id: str,
        *,
        conn: sqlite3.Connection,
    ) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT * FROM task_runtime_acceptance_effect_intent
            WHERE effect_id = ?
            """,
            (effect_id,),
        ).fetchone()
        if row is None:
            raise TaskStoreError(
                TASK_STORE_NOT_FOUND,
                f"Acceptance Effect not found: {effect_id}",
            )
        record = _acceptance_effect_record(row)
        record.pop("payload", None)
        if record.get("last_error") is not None:
            record["last_error_sha256"] = _sha256_text(
                str(record.pop("last_error"))
            )
        return record


__all__ = [
    "ACCEPTANCE_EFFECT_RECONCILIATION_AGGREGATE_TYPE",
    "ACCEPTANCE_EFFECT_RECONCILIATION_EVENT_TYPE",
    "ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED",
    "ACCEPTANCE_EFFECT_RECONCILED_NO_SIDE_EFFECT",
    "TaskAcceptanceEffectReconciliationStoreMixin",
]
