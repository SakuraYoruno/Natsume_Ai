"""Append-only signed evidence intake for governed Operation reconciliation."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
import sqlite3
from typing import Any

from host_brain.contracts.agent_runtime.operation_reconciliation import (
    OperationReconciliationContractError,
    build_reconciliation_attestation_binding,
)

from .task_operation_recovery_hold_current import hold_is_current
from .task_store_types import (
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
    _DURABLE_REFERENCE_PATTERN,
)
from .reconciliation_proof_verifier import (
    TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
    TASK_STORE_RECONCILIATION_PROOF_INVALID,
)


_SIGNATURE = re.compile(r"^ed25519:[A-Za-z0-9_-]{86}==$")
_SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_TABLE = "task_runtime_operation_reconciliation_evidence"


class TaskOperationReconciliationEvidenceStoreMixin:
    """Stage verified ingress evidence without exposing it to worker input."""

    def assert_operation_reconciliation_evidence_ready(self) -> None:
        """Fail closed only when the additive 0126 capability is used."""

        conn = self._connect()
        try:
            columns = {
                str(row["name"])
                for row in conn.execute(f"PRAGMA table_info({_TABLE})")
            }
            indexes = {
                str(row["name"])
                for row in conn.execute(f"PRAGMA index_list({_TABLE})")
            }
            triggers = {
                str(row["name"])
                for row in conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'trigger' AND tbl_name = ?
                    """,
                    (_TABLE,),
                )
            }
        finally:
            conn.close()
        required_columns = {
            "evidence_ref",
            "evidence_digest",
            "request_id",
            "reservation_id",
            "expected_version",
            "action",
            "attestation_signature",
            "recovery_case_ref",
            "recovery_case_subject_sha256",
            "created_ts_ms",
        }
        required_triggers = {
            "trg_task_operation_reconciliation_evidence_no_update",
            "trg_task_operation_reconciliation_evidence_no_delete",
            "trg_task_operation_reconciliation_evidence_recovery_case",
        }
        if (
            not required_columns <= columns
            or (
                "idx_task_operation_reconciliation_evidence_subject"
                not in indexes
            )
            or (
                "idx_task_operation_reconciliation_evidence_recovery_case"
                not in indexes
            )
            or not required_triggers <= triggers
        ):
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "Operation reconciliation evidence migrations 0126/0131 "
                "are not ready",
            )

    def stage_operation_reconciliation_evidence(
        self,
        *,
        request_id: str,
        ingress_audit_ref: str,
        body_sha256: str,
        reservation_id: str,
        expected_version: int,
        action: str,
        intent_ref: str | None,
        reconciler_actor_id: str,
        downstream_receipt_ref: str | None,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
        recovery_case_ref: str | None = None,
        recovery_case_subject_sha256: str | None = None,
    ) -> dict[str, Any]:
        self.assert_operation_reconciliation_evidence_ready()
        request = self._normalize_operation_reconciliation_evidence(
            request_id=request_id,
            ingress_audit_ref=ingress_audit_ref,
            body_sha256=body_sha256,
            reservation_id=reservation_id,
            expected_version=expected_version,
            action=action,
            intent_ref=intent_ref,
            reconciler_actor_id=reconciler_actor_id,
            downstream_receipt_ref=downstream_receipt_ref,
            attestation_ref=attestation_ref,
            attestation_key_id=attestation_key_id,
            attestation_signature=attestation_signature,
            recovery_case_ref=recovery_case_ref,
            recovery_case_subject_sha256=recovery_case_subject_sha256,
        )
        with self._transaction() as conn:
            existing = conn.execute(
                f"SELECT * FROM {_TABLE} WHERE request_id = ?",
                (request["request_id"],),
            ).fetchone()
            if existing is not None:
                persisted = self._operation_reconciliation_evidence_record(
                    conn,
                    existing,
                )
                if self._evidence_request_fields(persisted) != request:
                    raise TaskStoreError(
                        TASK_STORE_CAS_CONFLICT,
                        "Operation reconciliation request_id was reused with drift",
                    )
                return {**persisted, "exact_replay": True}

            operation = self._require_operation_reservation(
                conn,
                request["reservation_id"],
            )
            operation_record = self._operation_record(operation)
            self._require_current_recovery_case(
                conn,
                request=request,
            )
            if (
                str(operation_record.get("status") or "").upper()
                != "RECOVERY_REQUIRED"
                or int(operation_record.get("status_version") or -1)
                != request["expected_version"]
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "Operation reconciliation evidence requires the exact "
                    "RECOVERY_REQUIRED version",
                )
            binding = self._verified_operation_reconciliation_binding(
                operation_record,
                request,
            )
            binding_digest = "sha256:" + hashlib.sha256(binding).hexdigest()
            material = {
                "schema_version": self._evidence_schema_version(request),
                **self._evidence_material_request(request),
                "attestation_binding_digest": binding_digest,
            }
            encoded = json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            evidence_digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
            evidence_ref = (
                "operation-reconcile-evidence://sha256/"
                + evidence_digest.removeprefix("sha256:")
            )
            try:
                conn.execute(
                    f"""
                    INSERT INTO {_TABLE}(
                        evidence_ref, evidence_digest, request_id,
                        ingress_audit_ref, body_sha256, reservation_id,
                        expected_version, action, intent_ref,
                        downstream_receipt_ref, reconciler_actor_id,
                        attestation_ref, attestation_key_id,
                        attestation_signature, attestation_binding_digest,
                        created_ts_ms, recovery_case_ref,
                        recovery_case_subject_sha256
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?
                    )
                    """,
                    (
                        evidence_ref,
                        evidence_digest,
                        request["request_id"],
                        request["ingress_audit_ref"],
                        request["body_sha256"],
                        request["reservation_id"],
                        request["expected_version"],
                        request["action"],
                        request["intent_ref"],
                        request["downstream_receipt_ref"],
                        request["reconciler_actor_id"],
                        request["attestation_ref"],
                        request["attestation_key_id"],
                        request["attestation_signature"],
                        binding_digest,
                        self._now(),
                        request["recovery_case_ref"],
                        request["recovery_case_subject_sha256"],
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    "Operation reconciliation evidence identity conflict",
                ) from exc
            row = conn.execute(
                f"SELECT * FROM {_TABLE} WHERE evidence_ref = ?",
                (evidence_ref,),
            ).fetchone()
            return {
                **self._operation_reconciliation_evidence_record(conn, row),
                "exact_replay": False,
            }

    def get_operation_reconciliation_evidence(
        self,
        evidence_ref: str,
    ) -> dict[str, Any]:
        self.assert_operation_reconciliation_evidence_ready()
        reference = str(evidence_ref or "").strip()
        if not _DURABLE_REFERENCE_PATTERN.fullmatch(reference):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Operation reconciliation evidence_ref is invalid",
            )
        with self._transaction() as conn:
            row = conn.execute(
                f"SELECT * FROM {_TABLE} WHERE evidence_ref = ?",
                (reference,),
            ).fetchone()
            if row is None:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "Operation reconciliation evidence was not found",
                )
            return self._operation_reconciliation_evidence_record(conn, row)

    def _operation_reconciliation_evidence_record(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row | None,
    ) -> dict[str, Any]:
        if row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Operation reconciliation evidence row is missing",
            )
        record = dict(row)
        request = self._normalize_operation_reconciliation_evidence(
            **self._evidence_request_fields(record),
        )
        operation = self._operation_record(
            self._require_operation_reservation(
                conn,
                request["reservation_id"],
            )
        )
        binding = self._verified_operation_reconciliation_binding(
            operation,
            request,
        )
        binding_digest = "sha256:" + hashlib.sha256(binding).hexdigest()
        material = {
            "schema_version": self._evidence_schema_version(request),
            **self._evidence_material_request(request),
            "attestation_binding_digest": binding_digest,
        }
        encoded = json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        evidence_digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
        evidence_ref = (
            "operation-reconcile-evidence://sha256/"
            + evidence_digest.removeprefix("sha256:")
        )
        if (
            str(record.get("attestation_binding_digest") or "")
            != binding_digest
            or str(record.get("evidence_digest") or "") != evidence_digest
            or str(record.get("evidence_ref") or "") != evidence_ref
            or int(record.get("created_ts_ms") or 0) <= 0
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Operation reconciliation evidence digest drifted",
            )
        return {
            **request,
            "evidence_ref": evidence_ref,
            "evidence_digest": evidence_digest,
            "attestation_binding_digest": binding_digest,
            "created_ts_ms": int(record["created_ts_ms"]),
        }

    def _verified_operation_reconciliation_binding(
        self,
        operation: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> bytes:
        outcome = (
            "SIDE_EFFECT_CONFIRMED"
            if request["action"] == "COMMIT"
            else "NO_SIDE_EFFECT"
        )
        try:
            binding = build_reconciliation_attestation_binding(
                reservation_id=request["reservation_id"],
                business_scope=str(operation.get("business_scope") or ""),
                operation_fingerprint=str(
                    operation.get("operation_fingerprint") or ""
                ),
                recovery_version=request["expected_version"],
                reconciler_actor_id=request["reconciler_actor_id"],
                attestation_ref=request["attestation_ref"],
                recovery_action=request["action"],
                reconciliation_outcome=outcome,
                receipt_ref=request["downstream_receipt_ref"],
            )
        except OperationReconciliationContractError as exc:
            raise TaskStoreError(
                TASK_STORE_RECONCILIATION_PROOF_INVALID,
                str(exc),
            ) from exc
        verifier = self._reconciliation_proof_verifier
        if verifier is None:
            raise TaskStoreError(
                TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
                "Operation reconciliation verifier is unavailable",
            )
        if (
            not verifier.has_key(request["attestation_key_id"])
            or not verifier.verify(
                key_id=request["attestation_key_id"],
                message=binding,
                signature=request["attestation_signature"],
            )
        ):
            raise TaskStoreError(
                TASK_STORE_RECONCILIATION_PROOF_INVALID,
                "Operation reconciliation evidence signature is invalid",
            )
        return binding

    @staticmethod
    def _normalize_operation_reconciliation_evidence(
        *,
        request_id: str,
        ingress_audit_ref: str,
        body_sha256: str,
        reservation_id: str,
        expected_version: int,
        action: str,
        intent_ref: str | None,
        reconciler_actor_id: str,
        downstream_receipt_ref: str | None,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
        recovery_case_ref: str | None = None,
        recovery_case_subject_sha256: str | None = None,
    ) -> dict[str, Any]:
        normalized_action = str(action or "").strip().upper()
        intent = str(intent_ref or "").strip() or None
        receipt = str(downstream_receipt_ref or "").strip() or None
        body_digest = str(body_sha256 or "").strip().lower()
        if body_digest.startswith("sha256:"):
            body_digest = body_digest.removeprefix("sha256:")
        recovery_ref = str(recovery_case_ref or "").strip() or None
        recovery_digest = (
            str(recovery_case_subject_sha256 or "").strip().lower() or None
        )
        request = {
            "request_id": str(request_id or "").strip(),
            "ingress_audit_ref": str(ingress_audit_ref or "").strip(),
            "body_sha256": body_digest,
            "reservation_id": str(reservation_id or "").strip(),
            "expected_version": expected_version,
            "action": normalized_action,
            "intent_ref": intent,
            "downstream_receipt_ref": receipt,
            "reconciler_actor_id": str(reconciler_actor_id or "").strip(),
            "attestation_ref": str(attestation_ref or "").strip(),
            "attestation_key_id": str(attestation_key_id or "").strip(),
            "attestation_signature": str(attestation_signature or "").strip(),
            "recovery_case_ref": recovery_ref,
            "recovery_case_subject_sha256": recovery_digest,
        }
        if (
            not request["request_id"]
            or not _DURABLE_REFERENCE_PATTERN.fullmatch(
                request["ingress_audit_ref"]
            )
            or not _SHA256.fullmatch(request["body_sha256"])
            or not request["reservation_id"]
            or isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 0
            or normalized_action not in {"COMMIT", "RELEASE", "REACQUIRE"}
            or not request["reconciler_actor_id"]
            or not _DURABLE_REFERENCE_PATTERN.fullmatch(
                request["attestation_ref"]
            )
            or not request["attestation_key_id"]
            or not _SIGNATURE.fullmatch(request["attestation_signature"])
            or (normalized_action == "REACQUIRE" and intent is not None)
            or (normalized_action == "REACQUIRE" and receipt is not None)
            or (
                normalized_action == "RELEASE"
                and intent is not None
                and not _DURABLE_REFERENCE_PATTERN.fullmatch(intent)
            )
            or (
                normalized_action == "COMMIT"
                and intent is not None
                and not _DURABLE_REFERENCE_PATTERN.fullmatch(intent)
            )
            or (
                normalized_action == "COMMIT"
                and intent is None
                and not (receipt or "").startswith("worker-projection://")
            )
            or (
                normalized_action == "COMMIT"
                and not _DURABLE_REFERENCE_PATTERN.fullmatch(receipt or "")
            )
            or (normalized_action == "RELEASE" and receipt is not None)
            or ((recovery_ref is None) != (recovery_digest is None))
            or (
                recovery_ref is not None
                and not re.fullmatch(
                    r"operation-recovery-hold://sha256/[0-9a-f]{64}",
                    recovery_ref,
                )
            )
            or (
                recovery_digest is not None
                and not re.fullmatch(r"sha256:[0-9a-f]{64}", recovery_digest)
            )
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Operation reconciliation evidence fields are invalid",
            )
        return request

    @staticmethod
    def _evidence_request_fields(
        value: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            key: value.get(key)
            for key in (
                "request_id",
                "ingress_audit_ref",
                "body_sha256",
                "reservation_id",
                "expected_version",
                "action",
                "intent_ref",
                "downstream_receipt_ref",
                "reconciler_actor_id",
                "attestation_ref",
                "attestation_key_id",
                "attestation_signature",
                "recovery_case_ref",
                "recovery_case_subject_sha256",
            )
        }

    def _require_current_recovery_case(
        self,
        conn: sqlite3.Connection,
        *,
        request: Mapping[str, Any],
    ) -> None:
        rows = conn.execute(
            """
            SELECT *
            FROM task_runtime_operation_recovery_hold
            WHERE reservation_id = ? AND operation_status_version = ?
            ORDER BY created_ts_ms, case_ref
            """,
            (
                request["reservation_id"],
                request["expected_version"],
            ),
        ).fetchall()
        current = []
        for row in rows:
            record = self._validated_hold_record(conn, row)
            if hold_is_current(conn, record):
                current.append(record)
        recovery_ref = request["recovery_case_ref"]
        if not current:
            if recovery_ref is not None:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "Operation reconciliation recovery case is not current",
                )
            return
        if (
            len(current) != 1
            or recovery_ref is None
            or current[0]["case_ref"] != recovery_ref
            or current[0]["subject_sha256"]
            != request["recovery_case_subject_sha256"]
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Operation reconciliation requires the one exact current "
                "recovery case",
            )
        case = current[0]
        classification = str(case.get("classification") or "")
        if classification == "QUARANTINED_CHECKPOINT_CONFLICT":
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Checkpoint conflict recovery cases remain quarantined",
            )
        if classification == "SIGNED_OPERATION_RECONCILIATION_REQUIRED":
            valid = request["intent_ref"] is None
        elif classification == "SIGNED_CANCELLATION_RECONCILIATION_REQUIRED":
            valid = (
                request["action"] in {"COMMIT", "RELEASE"}
                and request["intent_ref"] == case["cancellation_intent_ref"]
            )
        else:
            valid = False
        if not valid:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Operation reconciliation action does not match recovery "
                "case classification",
            )

    @staticmethod
    def _evidence_schema_version(request: Mapping[str, Any]) -> str:
        return (
            "Natsume.task.operation_reconciliation_evidence.v2"
            if request.get("recovery_case_ref") is not None
            else "Natsume.task.operation_reconciliation_evidence.v1"
        )

    @staticmethod
    def _evidence_material_request(
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        material = dict(request)
        if material.get("recovery_case_ref") is None:
            material.pop("recovery_case_ref", None)
            material.pop("recovery_case_subject_sha256", None)
        return material


__all__ = ["TaskOperationReconciliationEvidenceStoreMixin"]
