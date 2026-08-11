"""Signed ingress evidence for general Acceptance Effect reconciliation."""

from __future__ import annotations

from collections.abc import Mapping
import json
import sqlite3
from typing import Any

from host_brain.contracts.agent_runtime.acceptance_effect_reconciliation import (
    ACCEPTANCE_EFFECT_RECONCILIATION_EVIDENCE_SCHEMA_VERSION,
    ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE,
    build_acceptance_effect_reconciliation_binding,
    build_acceptance_effect_reconciliation_identity,
)
from host_brain.persistence.reconciliation_proof_verifier import (
    TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
    TASK_STORE_RECONCILIATION_PROOF_INVALID,
)
from host_brain.persistence.task_acceptance_effect_reconciliation_store import (
    ACCEPTANCE_EFFECT_RECONCILIATION_AGGREGATE_TYPE,
    ACCEPTANCE_EFFECT_RECONCILIATION_EVENT_TYPE,
    _CASE_TABLE,
    _EVIDENCE_TABLE,
    _PROOF_TABLE,
    _SIGNATURE_PATTERN,
    _canonical_json,
    _digest,
    _durable_ref,
    _optional_durable_ref,
    _required_text,
    _sha256_text,
)
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_NOT_FOUND,
    TASK_STORE_OUTBOX_CONFLICT,
    TaskStoreError,
    build_outbox_payload_ref,
)


class TaskAcceptanceEffectReconciliationEvidenceStoreMixin:
    """Stage and reopen signed evidence without owning proof settlement."""

    def stage_acceptance_effect_reconciliation_evidence(
        self,
        *,
        request_id: str,
        ingress_audit_ref: str,
        body_sha256: str,
        effect_id: str,
        case_id: str,
        expected_case_snapshot_sha256: str,
        expected_subject_snapshot_sha256: str,
        purpose: str,
        outcome: str,
        reconciler_actor_id: str,
        issued_ts_ms: int,
        downstream_receipt_ref: str | None,
        executor_quiescence_ref: str | None,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
    ) -> dict[str, Any]:
        """Verify and isolate one signed ingress body before Task admission."""

        self.assert_acceptance_effect_reconciliation_ready()
        request = self._normalize_acceptance_effect_reconciliation_evidence(
            request_id=request_id,
            ingress_audit_ref=ingress_audit_ref,
            body_sha256=body_sha256,
            effect_id=effect_id,
            case_id=case_id,
            expected_case_snapshot_sha256=(
                expected_case_snapshot_sha256
            ),
            expected_subject_snapshot_sha256=(
                expected_subject_snapshot_sha256
            ),
            purpose=purpose,
            outcome=outcome,
            reconciler_actor_id=reconciler_actor_id,
            issued_ts_ms=issued_ts_ms,
            downstream_receipt_ref=downstream_receipt_ref,
            executor_quiescence_ref=executor_quiescence_ref,
            attestation_ref=attestation_ref,
            attestation_key_id=attestation_key_id,
            attestation_signature=attestation_signature,
        )
        with self._transaction() as conn:
            existing = conn.execute(
                f"SELECT * FROM {_EVIDENCE_TABLE} WHERE request_id = ?",
                (request["request_id"],),
            ).fetchone()
            if existing is not None:
                persisted = (
                    self._validated_acceptance_effect_reconciliation_evidence(
                        conn,
                        existing,
                    )
                )
                if (
                    self._acceptance_effect_reconciliation_evidence_fields(
                        persisted
                    )
                    != request
                ):
                    raise TaskStoreError(
                        TASK_STORE_CAS_CONFLICT,
                        "Acceptance Effect reconciliation request_id was "
                        "reused with drift",
                    )
                return {**persisted, "exact_replay": True}

            subject = self._derive_acceptance_effect_reconciliation_subject(
                conn,
                effect_id=request["effect_id"],
                ts_ms=self._now(),
                require_candidate=True,
            )
            case = dict(subject["case"])
            if (
                case["case_id"] != request["case_id"]
                or case["snapshot_sha256"]
                != request["expected_case_snapshot_sha256"]
                or subject["subject_snapshot_sha256"]
                != request["expected_subject_snapshot_sha256"]
            ):
                raise TaskStoreError(
                    TASK_STORE_OUTBOX_CONFLICT,
                    "Acceptance Effect reconciliation evidence subject drifted",
                )
            artifacts = (
                self._build_acceptance_effect_reconciliation_artifacts(
                    subject=subject,
                    request=request,
                )
            )
            self._verify_acceptance_effect_reconciliation_signature(
                key_id=request["attestation_key_id"],
                signature=request["attestation_signature"],
                binding=artifacts["binding_bytes"],
                persisted=False,
            )
            binding_json = artifacts["binding_bytes"].decode("utf-8")
            proof_digest = _sha256_text(binding_json)
            evidence_payload = {
                "schema_version": (
                    ACCEPTANCE_EFFECT_RECONCILIATION_EVIDENCE_SCHEMA_VERSION
                ),
                **request,
                "target_effect_status": request["target_effect_status"],
                "proof_id": artifacts["proof_id"],
                "outbox_id": artifacts["outbox"].outbox_id,
                "proof_digest": proof_digest,
            }
            evidence_payload_json = _canonical_json(evidence_payload)
            evidence_digest = _sha256_text(evidence_payload_json)
            evidence_ref = (
                "effect-reconcile-evidence://sha256/"
                + evidence_digest.removeprefix("sha256:")
            )
            try:
                conn.execute(
                    f"""
                    INSERT INTO {_EVIDENCE_TABLE}(
                        evidence_ref, schema_version, request_id,
                        ingress_audit_ref, body_sha256, effect_id, case_id,
                        expected_case_snapshot_sha256,
                        expected_subject_snapshot_sha256, purpose, outcome,
                        target_effect_status, receipt_ref,
                        executor_quiescence_ref, reconciler_actor_id,
                        issued_ts_ms, attestation_ref, attestation_key_id,
                        attestation_signature, proof_id, outbox_id,
                        proof_binding_json, proof_digest,
                        evidence_payload_json, evidence_digest, created_ts_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence_ref,
                        ACCEPTANCE_EFFECT_RECONCILIATION_EVIDENCE_SCHEMA_VERSION,
                        request["request_id"],
                        request["ingress_audit_ref"],
                        request["body_sha256"],
                        request["effect_id"],
                        request["case_id"],
                        request["expected_case_snapshot_sha256"],
                        request["expected_subject_snapshot_sha256"],
                        request["purpose"],
                        request["outcome"],
                        request["target_effect_status"],
                        request["downstream_receipt_ref"],
                        request["executor_quiescence_ref"],
                        request["reconciler_actor_id"],
                        request["issued_ts_ms"],
                        request["attestation_ref"],
                        request["attestation_key_id"],
                        request["attestation_signature"],
                        artifacts["proof_id"],
                        artifacts["outbox"].outbox_id,
                        binding_json,
                        proof_digest,
                        evidence_payload_json,
                        evidence_digest,
                        self._now(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    "Acceptance Effect reconciliation evidence identity "
                    "conflicted",
                ) from exc
            stored = conn.execute(
                f"SELECT * FROM {_EVIDENCE_TABLE} WHERE evidence_ref = ?",
                (evidence_ref,),
            ).fetchone()
            return {
                **self._validated_acceptance_effect_reconciliation_evidence(
                    conn,
                    stored,
                ),
                "exact_replay": False,
            }

    def get_acceptance_effect_reconciliation_evidence(
        self,
        evidence_ref: str,
    ) -> dict[str, Any]:
        self.assert_acceptance_effect_reconciliation_ready()
        reference = _durable_ref(evidence_ref, "evidence_ref")
        with self._transaction() as conn:
            row = conn.execute(
                f"SELECT * FROM {_EVIDENCE_TABLE} WHERE evidence_ref = ?",
                (reference,),
            ).fetchone()
            if row is None:
                raise TaskStoreError(
                    TASK_STORE_NOT_FOUND,
                    "Acceptance Effect reconciliation evidence was not found",
                )
            return self._validated_acceptance_effect_reconciliation_evidence(
                conn,
                row,
            )

    def _read_acceptance_effect_reconciliation_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        effect_id: str,
        case_id: str | None,
        require_candidate: bool,
        ts_ms: int,
    ) -> dict[str, Any]:
        if case_id is not None:
            persisted_case = conn.execute(
                f"SELECT * FROM {_CASE_TABLE} WHERE case_id = ? AND effect_id = ?",
                (case_id, effect_id),
            ).fetchone()
            if persisted_case is not None:
                case = self._validated_acceptance_effect_reconciliation_case(
                    persisted_case
                )
                proof_row = conn.execute(
                    f"SELECT * FROM {_PROOF_TABLE} WHERE case_id = ?",
                    (case_id,),
                ).fetchone()
                if proof_row is not None:
                    proof = (
                        self._validated_acceptance_effect_reconciliation_proof(
                            conn,
                            proof_row,
                        )
                    )
                    subject = json.loads(str(proof["subject_snapshot_json"]))
                    current = self._current_acceptance_effect(effect_id, conn=conn)
                    return {
                        **subject,
                        "proof": proof,
                        "current_effect": current,
                    }
        subject = self._derive_acceptance_effect_reconciliation_subject(
            conn,
            effect_id=effect_id,
            ts_ms=ts_ms,
            require_candidate=require_candidate,
        )
        if case_id is not None and subject["case"]["case_id"] != case_id:
            raise TaskStoreError(
                TASK_STORE_OUTBOX_CONFLICT,
                "Acceptance Effect reconciliation case identity drifted",
            )
        proof_row = conn.execute(
            f"SELECT * FROM {_PROOF_TABLE} WHERE case_id = ?",
            (subject["case"]["case_id"],),
        ).fetchone()
        return {
            **subject,
            "proof": (
                self._validated_acceptance_effect_reconciliation_proof(
                    conn,
                    proof_row,
                )
                if proof_row is not None
                else None
            ),
            "current_effect": dict(subject["effect"]),
        }

    def _validated_acceptance_effect_reconciliation_evidence(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row | Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Acceptance Effect reconciliation evidence is missing",
            )
        record = dict(row)
        request = self._normalize_acceptance_effect_reconciliation_evidence(
            request_id=record.get("request_id"),
            ingress_audit_ref=record.get("ingress_audit_ref"),
            body_sha256=record.get("body_sha256"),
            effect_id=record.get("effect_id"),
            case_id=record.get("case_id"),
            expected_case_snapshot_sha256=record.get(
                "expected_case_snapshot_sha256"
            ),
            expected_subject_snapshot_sha256=record.get(
                "expected_subject_snapshot_sha256"
            ),
            purpose=record.get("purpose"),
            outcome=record.get("outcome"),
            reconciler_actor_id=record.get("reconciler_actor_id"),
            issued_ts_ms=record.get("issued_ts_ms"),
            downstream_receipt_ref=record.get("receipt_ref"),
            executor_quiescence_ref=record.get(
                "executor_quiescence_ref"
            ),
            attestation_ref=record.get("attestation_ref"),
            attestation_key_id=record.get("attestation_key_id"),
            attestation_signature=record.get("attestation_signature"),
        )
        try:
            binding_payload = json.loads(
                str(record["proof_binding_json"])
            )
            binding_bytes = build_acceptance_effect_reconciliation_binding(
                binding_payload
            )
            evidence_payload = json.loads(
                str(record["evidence_payload_json"])
            )
            canonical_evidence = _canonical_json(evidence_payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Acceptance Effect reconciliation evidence is invalid",
            ) from exc

        proof_row = conn.execute(
            f"SELECT * FROM {_PROOF_TABLE} WHERE case_id = ?",
            (request["case_id"],),
        ).fetchone()
        if proof_row is not None:
            proof = self._validated_acceptance_effect_reconciliation_proof(
                conn,
                proof_row,
            )
            try:
                subject = json.loads(str(proof["subject_snapshot_json"]))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "Acceptance Effect reconciliation evidence subject is "
                    "invalid",
                ) from exc
        else:
            subject = self._derive_acceptance_effect_reconciliation_subject(
                conn,
                effect_id=request["effect_id"],
                ts_ms=self._now(),
                require_candidate=True,
            )
        case = dict(subject.get("case") or {})
        if (
            case.get("case_id") != request["case_id"]
            or case.get("snapshot_sha256")
            != request["expected_case_snapshot_sha256"]
            or subject.get("subject_snapshot_sha256")
            != request["expected_subject_snapshot_sha256"]
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Acceptance Effect reconciliation evidence subject drifted",
            )
        artifacts = self._build_acceptance_effect_reconciliation_artifacts(
            subject=subject,
            request=request,
        )
        expected_binding_json = artifacts["binding_bytes"].decode("utf-8")
        proof_digest = _sha256_text(expected_binding_json)
        expected_payload = {
            "schema_version": (
                ACCEPTANCE_EFFECT_RECONCILIATION_EVIDENCE_SCHEMA_VERSION
            ),
            **request,
            "target_effect_status": request["target_effect_status"],
            "proof_id": artifacts["proof_id"],
            "outbox_id": artifacts["outbox"].outbox_id,
            "proof_digest": proof_digest,
        }
        expected_payload_json = _canonical_json(expected_payload)
        evidence_digest = _sha256_text(expected_payload_json)
        evidence_ref = (
            "effect-reconcile-evidence://sha256/"
            + evidence_digest.removeprefix("sha256:")
        )
        if (
            record.get("schema_version")
            != ACCEPTANCE_EFFECT_RECONCILIATION_EVIDENCE_SCHEMA_VERSION
            or str(record.get("target_effect_status") or "")
            != request["target_effect_status"]
            or str(record.get("proof_id") or "") != artifacts["proof_id"]
            or str(record.get("outbox_id") or "")
            != artifacts["outbox"].outbox_id
            or binding_bytes.decode("utf-8") != expected_binding_json
            or str(record.get("proof_binding_json") or "")
            != expected_binding_json
            or str(record.get("proof_digest") or "") != proof_digest
            or evidence_payload != expected_payload
            or canonical_evidence != expected_payload_json
            or str(record.get("evidence_payload_json") or "")
            != expected_payload_json
            or str(record.get("evidence_digest") or "") != evidence_digest
            or str(record.get("evidence_ref") or "") != evidence_ref
            or type(record.get("created_ts_ms")) is not int
            or int(record["created_ts_ms"]) < 0
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Acceptance Effect reconciliation evidence binding drifted",
            )
        if proof_row is not None and (
            str(proof.get("proof_id") or "") != artifacts["proof_id"]
            or str(proof.get("outbox_id") or "")
            != artifacts["outbox"].outbox_id
            or str(proof.get("proof_binding_json") or "")
            != expected_binding_json
            or str(proof.get("attestation_signature") or "")
            != request["attestation_signature"]
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Acceptance Effect reconciliation evidence/proof drifted",
            )
        self._verify_acceptance_effect_reconciliation_signature(
            key_id=request["attestation_key_id"],
            signature=request["attestation_signature"],
            binding=artifacts["binding_bytes"],
            persisted=True,
        )
        return {
            **request,
            "evidence_ref": evidence_ref,
            "evidence_digest": evidence_digest,
            "proof_id": artifacts["proof_id"],
            "outbox_id": artifacts["outbox"].outbox_id,
            "proof_digest": proof_digest,
            "created_ts_ms": int(record["created_ts_ms"]),
        }

    def _build_acceptance_effect_reconciliation_artifacts(
        self,
        *,
        subject: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        identity = build_acceptance_effect_reconciliation_identity(
            case_id=request["case_id"],
            subject_snapshot_sha256=request[
                "expected_subject_snapshot_sha256"
            ],
            outcome=request["outcome"],
            target_effect_status=request["target_effect_status"],
            downstream_receipt_ref=request["downstream_receipt_ref"],
            executor_quiescence_ref=request["executor_quiescence_ref"],
            reconciler_actor_id=request["reconciler_actor_id"],
            attestation_ref=request["attestation_ref"],
            attestation_key_id=request["attestation_key_id"],
            issued_ts_ms=request["issued_ts_ms"],
        )
        evidence_refs = {
            identity["proof_id"],
            request["case_id"],
            request["effect_id"],
            request["attestation_ref"],
        }
        if request["downstream_receipt_ref"] is not None:
            evidence_refs.add(request["downstream_receipt_ref"])
        if request["executor_quiescence_ref"] is not None:
            evidence_refs.add(request["executor_quiescence_ref"])
        outbox = OutboxMessage.create(
            aggregate_type=ACCEPTANCE_EFFECT_RECONCILIATION_AGGREGATE_TYPE,
            aggregate_id=request["case_id"],
            aggregate_version=0,
            graph_revision=None,
            event_type=ACCEPTANCE_EFFECT_RECONCILIATION_EVENT_TYPE,
            payload_ref=build_outbox_payload_ref(
                aggregate_type=(
                    ACCEPTANCE_EFFECT_RECONCILIATION_AGGREGATE_TYPE
                ),
                aggregate_id=request["case_id"],
                aggregate_version=0,
                graph_revision=None,
            ),
            idempotency_key=identity["idempotency_key"],
            outbox_id=identity["outbox_id"],
            attempt_id=str(subject["case"]["attempt_id"]),
            evidence_refs=tuple(sorted(evidence_refs)),
        )
        binding_payload = self._acceptance_effect_reconciliation_binding(
            subject=subject,
            proof_id=identity["proof_id"],
            outcome=request["outcome"],
            target_effect_status=request["target_effect_status"],
            receipt_ref=request["downstream_receipt_ref"],
            executor_quiescence_ref=request["executor_quiescence_ref"],
            reconciler_actor_id=request["reconciler_actor_id"],
            attestation_ref=request["attestation_ref"],
            attestation_key_id=request["attestation_key_id"],
            issued_ts_ms=request["issued_ts_ms"],
            outbox=outbox,
        )
        return {
            "proof_id": identity["proof_id"],
            "outbox": outbox,
            "binding_bytes": build_acceptance_effect_reconciliation_binding(
                binding_payload
            ),
        }

    def _verify_acceptance_effect_reconciliation_signature(
        self,
        *,
        key_id: str,
        signature: str,
        binding: bytes,
        persisted: bool,
    ) -> None:
        verifier = getattr(self, "_reconciliation_proof_verifier", None)
        if verifier is None:
            raise TaskStoreError(
                TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
                "Acceptance Effect reconciliation verifier is unavailable",
            )
        if (
            not verifier.has_key(key_id)
            or _SIGNATURE_PATTERN.fullmatch(signature) is None
            or not verifier.verify(
                key_id=key_id,
                message=binding,
                signature=signature,
            )
        ):
            raise TaskStoreError(
                (
                    TASK_STORE_DURABLE_DATA_INVALID
                    if persisted
                    else TASK_STORE_RECONCILIATION_PROOF_INVALID
                ),
                (
                    "persisted Acceptance Effect reconciliation signature "
                    "is invalid"
                    if persisted
                    else "Acceptance Effect reconciliation signature is invalid"
                ),
            )

    @staticmethod
    def _acceptance_effect_reconciliation_evidence_fields(
        value: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            key: value.get(key)
            for key in (
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
                "downstream_receipt_ref",
                "executor_quiescence_ref",
                "reconciler_actor_id",
                "issued_ts_ms",
                "attestation_ref",
                "attestation_key_id",
                "attestation_signature",
            )
        }

    @staticmethod
    def _normalize_acceptance_effect_reconciliation_evidence(
        *,
        request_id: Any,
        ingress_audit_ref: Any,
        body_sha256: Any,
        effect_id: Any,
        case_id: Any,
        expected_case_snapshot_sha256: Any,
        expected_subject_snapshot_sha256: Any,
        purpose: Any,
        outcome: Any,
        reconciler_actor_id: Any,
        issued_ts_ms: Any,
        downstream_receipt_ref: Any,
        executor_quiescence_ref: Any,
        attestation_ref: Any,
        attestation_key_id: Any,
        attestation_signature: Any,
    ) -> dict[str, Any]:
        normalized_outcome = str(outcome or "").strip().upper()
        target = {
            "NO_SIDE_EFFECT": "PENDING",
            "SIDE_EFFECT_CONFIRMED": "COMMITTED",
        }.get(normalized_outcome)
        body_digest = str(body_sha256 or "").strip().lower()
        if not body_digest.startswith("sha256:"):
            body_digest = "sha256:" + body_digest
        receipt = _optional_durable_ref(
            downstream_receipt_ref,
            "downstream_receipt_ref",
        )
        quiescence = _optional_durable_ref(
            executor_quiescence_ref,
            "executor_quiescence_ref",
        )
        request = {
            "request_id": _required_text(request_id, "request_id"),
            "ingress_audit_ref": _durable_ref(
                ingress_audit_ref,
                "ingress_audit_ref",
            ),
            "body_sha256": _digest(body_digest, "body_sha256"),
            "effect_id": _durable_ref(effect_id, "effect_id"),
            "case_id": _durable_ref(case_id, "case_id"),
            "expected_case_snapshot_sha256": _digest(
                expected_case_snapshot_sha256,
                "expected_case_snapshot_sha256",
            ),
            "expected_subject_snapshot_sha256": _digest(
                expected_subject_snapshot_sha256,
                "expected_subject_snapshot_sha256",
            ),
            "purpose": str(purpose or "").strip().upper(),
            "outcome": normalized_outcome,
            "target_effect_status": target,
            "downstream_receipt_ref": receipt,
            "executor_quiescence_ref": quiescence,
            "reconciler_actor_id": _required_text(
                reconciler_actor_id,
                "reconciler_actor_id",
            ),
            "issued_ts_ms": issued_ts_ms,
            "attestation_ref": _durable_ref(
                attestation_ref,
                "attestation_ref",
            ),
            "attestation_key_id": _required_text(
                attestation_key_id,
                "attestation_key_id",
                maximum=255,
            ),
            "attestation_signature": str(
                attestation_signature or ""
            ).strip(),
        }
        if (
            request["purpose"]
            != ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE
            or target is None
            or type(issued_ts_ms) is not int
            or issued_ts_ms < 0
            or _SIGNATURE_PATTERN.fullmatch(
                request["attestation_signature"]
            )
            is None
            or (
                normalized_outcome == "NO_SIDE_EFFECT"
                and (receipt is not None or quiescence is None)
            )
            or (
                normalized_outcome == "SIDE_EFFECT_CONFIRMED"
                and receipt is None
            )
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Acceptance Effect reconciliation evidence fields are invalid",
            )
        return request

__all__ = ["TaskAcceptanceEffectReconciliationEvidenceStoreMixin"]
