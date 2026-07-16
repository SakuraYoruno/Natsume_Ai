"""Transactional proof append authority for Effect reconciliation."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping
from typing import Any

from host_brain.contracts.agent_runtime.effect_reconciliation import (
    GraphCancellationEffectStoreRejected,
)
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_OUTBOX_CONFLICT,
    TaskStoreError,
    _DURABLE_REFERENCE_PATTERN,
)

from .task_effect_reconciliation_common import (
    EFFECT_RECONCILIATION_AGGREGATE_TYPE,
    EFFECT_RECONCILIATION_EVENT_TYPE,
    EFFECT_RECONCILIATION_LEGACY_PURPOSE,
    EFFECT_RECONCILIATION_PENDING_PURPOSE,
    EFFECT_RECONCILIATION_PROOF_SCHEMA,
    _RECONCILIATION_AUTHORITY_REQUIRED,
    _RECONCILIATION_PROOF_INVALID,
    _SIGNATURE_PATTERN,
    _digest,
    _durable_ref,
    _effect_reconciliation_case_record,
    _proof_binding_payload,
    _required_text,
    _require_current_legacy_decision_binding,
    build_effect_reconciliation_attestation_binding,
)


class TaskEffectReconciliationMutationMixin:
    """Append one exact proof and Outbox in the owning TaskStore UoW."""


    @staticmethod
    def _insert_effect_reconciliation_case(
        conn: sqlite3.Connection,
        case: Mapping[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO task_runtime_effect_reconciliation_case(
                case_id, schema_version, purpose, case_kind, classification,
                reason_code, intent_ref, effect_id, graph_id, graph_revision,
                node_id, attempt_id, legacy_decision_ref,
                legacy_decision_payload_sha256, intent_status,
                intent_status_version, effect_status, effect_status_version,
                fence_observed_status, fence_observed_status_version,
                fence_resolution_status, snapshot_json, snapshot_sha256,
                created_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?)
            """,
            (
                case["case_id"], case["schema_version"], case["purpose"],
                case["case_kind"], case["classification"], case["reason_code"],
                case["intent_ref"], case["effect_id"], case["graph_id"],
                case["graph_revision"], case["node_id"], case["attempt_id"],
                case["legacy_decision_ref"],
                case["legacy_decision_payload_sha256"], case["intent_status"],
                case["intent_status_version"], case["effect_status"],
                case["effect_status_version"], case["fence_observed_status"],
                case["fence_observed_status_version"],
                case["fence_resolution_status"], case["snapshot_json"],
                case["snapshot_sha256"], case["created_ts_ms"],
            ),
        )
    def record_graph_cancellation_effect_reconciliation_proof(
        self,
        intent_ref: str,
        effect_id: str,
        *,
        purpose: str,
        case_id: str,
        expected_case_snapshot_sha256: str,
        expected_subject_snapshot_sha256: str,
        expected_intent_status_version: int,
        expected_effect_status: str,
        expected_effect_status_version: int,
        expected_effect_fencing_token: int,
        expected_effect_attempt_count: int,
        expected_effect_lease_owner: str | None,
        expected_effect_lease_expires_ts_ms: int | None,
        expected_attempt_status: str,
        expected_attempt_status_version: int,
        expected_fence_observed_status: str,
        expected_fence_observed_status_version: int,
        expected_fence_resolution_status: str,
        outcome: str,
        target_resolution_status: str,
        receipt_ref: str | None,
        executor_quiescence_ref: str | None,
        reconciler_actor_id: str,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
        issued_ts_ms: int,
        proof_id: str,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        """Append one exact proof, translating only known no-commit rejections."""

        try:
            return self._record_graph_cancellation_effect_reconciliation_proof(
                intent_ref,
                effect_id,
                purpose=purpose,
                case_id=case_id,
                expected_case_snapshot_sha256=expected_case_snapshot_sha256,
                expected_subject_snapshot_sha256=expected_subject_snapshot_sha256,
                expected_intent_status_version=expected_intent_status_version,
                expected_effect_status=expected_effect_status,
                expected_effect_status_version=expected_effect_status_version,
                expected_effect_fencing_token=expected_effect_fencing_token,
                expected_effect_attempt_count=expected_effect_attempt_count,
                expected_effect_lease_owner=expected_effect_lease_owner,
                expected_effect_lease_expires_ts_ms=expected_effect_lease_expires_ts_ms,
                expected_attempt_status=expected_attempt_status,
                expected_attempt_status_version=expected_attempt_status_version,
                expected_fence_observed_status=expected_fence_observed_status,
                expected_fence_observed_status_version=expected_fence_observed_status_version,
                expected_fence_resolution_status=expected_fence_resolution_status,
                outcome=outcome,
                target_resolution_status=target_resolution_status,
                receipt_ref=receipt_ref,
                executor_quiescence_ref=executor_quiescence_ref,
                reconciler_actor_id=reconciler_actor_id,
                attestation_ref=attestation_ref,
                attestation_key_id=attestation_key_id,
                attestation_signature=attestation_signature,
                issued_ts_ms=issued_ts_ms,
                proof_id=proof_id,
                outbox=outbox,
            )
        except TaskStoreError as exc:
            raise GraphCancellationEffectStoreRejected(
                exc.reason_code,
                exc.detail,
            ) from exc

    def _record_graph_cancellation_effect_reconciliation_proof(
        self,
        intent_ref: str,
        effect_id: str,
        *,
        purpose: str,
        case_id: str,
        expected_case_snapshot_sha256: str,
        expected_subject_snapshot_sha256: str,
        expected_intent_status_version: int,
        expected_effect_status: str,
        expected_effect_status_version: int,
        expected_effect_fencing_token: int,
        expected_effect_attempt_count: int,
        expected_effect_lease_owner: str | None,
        expected_effect_lease_expires_ts_ms: int | None,
        expected_attempt_status: str,
        expected_attempt_status_version: int,
        expected_fence_observed_status: str,
        expected_fence_observed_status_version: int,
        expected_fence_resolution_status: str,
        outcome: str,
        target_resolution_status: str,
        receipt_ref: str | None,
        executor_quiescence_ref: str | None,
        reconciler_actor_id: str,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
        proof_id: str,
        issued_ts_ms: int,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        self.assert_effect_reconciliation_ready()
        intent_reference = _durable_ref(intent_ref, "intent_ref")
        effect_reference = _durable_ref(effect_id, "effect_id")
        case_reference = _durable_ref(case_id, "case_id")
        proof_reference = _durable_ref(proof_id, "proof_id")
        normalized_purpose = str(purpose or "").strip().upper()
        normalized_outcome = str(outcome or "").strip().upper()
        normalized_target = str(target_resolution_status or "").strip().upper()
        normalized_receipt = str(receipt_ref or "").strip() or None
        actor_id = _required_text(reconciler_actor_id, "reconciler_actor_id")
        attestation_reference = _durable_ref(attestation_ref, "attestation_ref")
        key_id = _required_text(attestation_key_id, "attestation_key_id")
        if (
            normalized_purpose
            not in {
                EFFECT_RECONCILIATION_PENDING_PURPOSE,
                EFFECT_RECONCILIATION_LEGACY_PURPOSE,
            }
            or len(key_id) > 255
            or type(issued_ts_ms) is not int
            or issued_ts_ms < 0
            or not (
                normalized_outcome == "NO_SIDE_EFFECT"
                and normalized_target == "CANCELLED"
                and normalized_receipt is None
                or normalized_outcome == "SIDE_EFFECT_CONFIRMED"
                and normalized_target == "COMMITTED_EVIDENCE"
                and normalized_receipt is not None
                and _DURABLE_REFERENCE_PATTERN.fullmatch(normalized_receipt)
                is not None
            )
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "effect reconciliation purpose, outcome, or authority is invalid",
            )
        signature = str(attestation_signature or "").strip()
        if not _SIGNATURE_PATTERN.fullmatch(signature):
            raise TaskStoreError(
                _RECONCILIATION_PROOF_INVALID,
                "effect reconciliation requires an Ed25519 signature",
            )
        with self._transaction() as conn:
            ts_ms = self._now()
            case_row = conn.execute(
                """
                SELECT * FROM task_runtime_effect_reconciliation_case
                WHERE case_id = ? AND intent_ref = ? AND effect_id = ? AND purpose = ?
                """,
                (
                    case_reference,
                    intent_reference,
                    effect_reference,
                    normalized_purpose,
                ),
            ).fetchone()
            if case_row is None:
                initial_subject = self._effect_reconciliation_subject(
                    conn,
                    intent_ref=intent_reference,
                    effect_id=effect_reference,
                )
                case = self._derive_effect_reconciliation_case(
                    conn,
                    subject=initial_subject,
                )
                if (
                    case["case_id"] != case_reference
                    or case["snapshot_sha256"]
                    != expected_case_snapshot_sha256
                ):
                    raise TaskStoreError(
                        TASK_STORE_OUTBOX_CONFLICT,
                        "derived effect reconciliation case drifted",
                    )
                self._insert_effect_reconciliation_case(conn, case)
                case_row = conn.execute(
                    "SELECT * FROM task_runtime_effect_reconciliation_case WHERE case_id = ?",
                    (case_reference,),
                ).fetchone()
                case = _effect_reconciliation_case_record(case_row)
            else:
                case = _effect_reconciliation_case_record(case_row)
            if case["snapshot_sha256"] != _digest(
                expected_case_snapshot_sha256,
                "expected_case_snapshot_sha256",
            ):
                raise TaskStoreError(TASK_STORE_OUTBOX_CONFLICT, "case snapshot drifted")

            existing = conn.execute(
                """
                SELECT * FROM task_runtime_effect_reconciliation_proof
                WHERE proof_id = ? OR (effect_id = ? AND intent_ref = ? AND purpose = ?)
                ORDER BY proof_id LIMIT 1
                """,
                (
                    proof_reference,
                    effect_reference,
                    intent_reference,
                    normalized_purpose,
                ),
            ).fetchone()
            if existing is not None:
                persisted = self._validated_effect_reconciliation_proof(conn, existing)
                if (
                    persisted["proof_id"] != proof_reference
                    or persisted["purpose"] != normalized_purpose
                    or persisted["case_id"] != case_reference
                    or persisted["intent_ref"] != intent_reference
                    or persisted["effect_id"] != effect_reference
                    or persisted["case_snapshot_sha256"]
                    != expected_case_snapshot_sha256
                    or persisted["subject_snapshot_sha256"]
                    != expected_subject_snapshot_sha256
                    or persisted["outcome"] != normalized_outcome
                    or persisted["target_resolution_status"]
                    != normalized_target
                    or persisted["receipt_ref"]
                    != normalized_receipt
                    or persisted["executor_quiescence_ref"]
                    != (str(executor_quiescence_ref or "").strip() or None)
                    or persisted["reconciler_actor_id"]
                    != actor_id
                    or persisted["attestation_ref"]
                    != attestation_reference
                    or persisted["attestation_key_id"]
                    != key_id
                    or persisted["issued_ts_ms"] != int(issued_ts_ms)
                    or persisted["outbox_id"] != outbox.outbox_id
                    or str(existing["attestation_signature"]) != signature
                ):
                    raise TaskStoreError(
                        TASK_STORE_OUTBOX_CONFLICT,
                        "effect reconciliation proof replay drifted",
                    )
                self._assert_mutation_outbox_replay(
                    conn,
                    outbox,
                    expected_aggregate=(
                        EFFECT_RECONCILIATION_AGGREGATE_TYPE,
                        case_reference,
                        0,
                    ),
                    expected_event_type=EFFECT_RECONCILIATION_EVENT_TYPE,
                    expected_attempt_id=case["attempt_id"],
                    expected_graph_revision=case["graph_revision"],
                )
                return {"case": case, "proof": persisted, "replayed": True}

            if case["classification"] == "QUARANTINED_STRUCTURAL":
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "structurally anomalous effect case cannot receive proof",
                )
            if (
                normalized_purpose == EFFECT_RECONCILIATION_PENDING_PURPOSE
                and case["classification"] != "REPAIRABLE_REQUESTED"
            ) or (
                normalized_purpose == EFFECT_RECONCILIATION_LEGACY_PURPOSE
                and case["classification"] != "QUARANTINED_FINALIZED"
            ):
                raise TaskStoreError(TASK_STORE_INVALID, "case purpose/classification drifted")

            subject = self._effect_reconciliation_subject(
                conn,
                intent_ref=intent_reference,
                effect_id=effect_reference,
            )
            intent = subject["intent"]
            effect = subject["effect"]
            fence = subject["fence"]
            attempt = subject["attempt"]
            if normalized_purpose == EFFECT_RECONCILIATION_PENDING_PURPOSE:
                decision = conn.execute(
                    "SELECT decision_ref FROM task_runtime_graph_cancellation_decision WHERE intent_ref = ?",
                    (intent_reference,),
                ).fetchone()
                if intent["status"] != "REQUESTED" or decision is not None:
                    raise TaskStoreError(
                        TASK_STORE_INVALID,
                        "first pending-cancellation proof requires REQUESTED without decision",
                    )
            else:
                if intent["status"] != "FINALIZED":
                    raise TaskStoreError(
                        TASK_STORE_INVALID,
                        "legacy-finalized correction requires FINALIZED intent",
                    )
                _require_current_legacy_decision_binding(conn, case)
            expected_values = {
                "intent_status_version": int(expected_intent_status_version),
                "effect_status": str(expected_effect_status or "").strip().upper(),
                "effect_status_version": int(expected_effect_status_version),
                "effect_fencing_token": int(expected_effect_fencing_token),
                "effect_attempt_count": int(expected_effect_attempt_count),
                "effect_lease_owner": str(expected_effect_lease_owner or "").strip() or None,
                "effect_lease_expires_ts_ms": expected_effect_lease_expires_ts_ms,
                "attempt_status": str(expected_attempt_status or "").strip().upper(),
                "attempt_status_version": int(expected_attempt_status_version),
                "fence_observed_status": str(expected_fence_observed_status or "").strip().upper(),
                "fence_observed_status_version": int(expected_fence_observed_status_version),
                "fence_resolution_status": str(expected_fence_resolution_status or "").strip().upper(),
            }
            actual_values = {
                "intent_status_version": int(intent["status_version"]),
                "effect_status": effect["status"],
                "effect_status_version": int(effect["status_version"]),
                "effect_fencing_token": int(effect["fencing_token"]),
                "effect_attempt_count": int(effect["attempt_count"]),
                "effect_lease_owner": effect["lease_owner"],
                "effect_lease_expires_ts_ms": effect["lease_expires_ts_ms"],
                "attempt_status": attempt["status"],
                "attempt_status_version": int(attempt["status_version"]),
                "fence_observed_status": fence["observed_status"],
                "fence_observed_status_version": int(fence["observed_status_version"]),
                "fence_resolution_status": fence["resolution_status"],
            }
            if expected_values != actual_values:
                raise TaskStoreError(TASK_STORE_OUTBOX_CONFLICT, "effect subject snapshot drifted")
            subject_digest = _digest(
                expected_subject_snapshot_sha256,
                "expected_subject_snapshot_sha256",
            )
            if subject["subject_snapshot_sha256"] != subject_digest:
                raise TaskStoreError(TASK_STORE_OUTBOX_CONFLICT, "effect subject digest drifted")
            if (
                effect["status"] == "CLAIMED"
                and (
                    effect["lease_expires_ts_ms"] is None
                    or int(effect["lease_expires_ts_ms"]) > ts_ms
                )
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "CLAIMED effect proof requires an expired lease",
                )
            quiescence = (
                _durable_ref(executor_quiescence_ref, "executor_quiescence_ref")
                if str(executor_quiescence_ref or "").strip()
                else None
            )
            if effect["status"] == "CLAIMED" and quiescence is None:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "CLAIMED effect proof requires executor quiescence evidence",
                )
            expected_evidence = {
                proof_reference,
                case_reference,
                intent_reference,
                effect_reference,
                attestation_reference,
            }
            if quiescence is not None:
                expected_evidence.add(quiescence)
            if normalized_receipt is not None:
                expected_evidence.add(normalized_receipt)
            if (
                outbox.aggregate_type != EFFECT_RECONCILIATION_AGGREGATE_TYPE
                or outbox.aggregate_id != case_reference
                or outbox.aggregate_version != 0
                or outbox.graph_revision != case["graph_revision"]
                or outbox.event_type != EFFECT_RECONCILIATION_EVENT_TYPE
                or outbox.attempt_id != case["attempt_id"]
                or set(outbox.evidence_refs) != expected_evidence
            ):
                raise TaskStoreError(TASK_STORE_INVALID, "effect proof Outbox binding is invalid")
            binding_payload = _proof_binding_payload(
                case=case,
                subject=subject,
                proof_id=proof_reference,
                purpose=normalized_purpose,
                outcome=normalized_outcome,
                target_resolution_status=normalized_target,
                receipt_ref=normalized_receipt,
                executor_quiescence_ref=quiescence,
                reconciler_actor_id=actor_id,
                attestation_ref=attestation_reference,
                attestation_key_id=key_id,
                issued_ts_ms=issued_ts_ms,
                outbox=outbox,
            )
            binding = build_effect_reconciliation_attestation_binding(
                binding_payload
            )
            verifier = getattr(self, "_reconciliation_proof_verifier", None)
            if verifier is None:
                raise TaskStoreError(
                    _RECONCILIATION_AUTHORITY_REQUIRED,
                    "no effect reconciliation proof verifier is configured",
                )
            if not verifier.has_key(key_id) or not verifier.verify(
                key_id=key_id,
                message=binding,
                signature=signature,
            ):
                raise TaskStoreError(
                    _RECONCILIATION_PROOF_INVALID,
                    "effect reconciliation signature is invalid or unknown",
                )
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    EFFECT_RECONCILIATION_AGGREGATE_TYPE,
                    case_reference,
                    0,
                ),
                expected_event_type=EFFECT_RECONCILIATION_EVENT_TYPE,
                expected_attempt_id=case["attempt_id"],
                expected_graph_revision=case["graph_revision"],
            )
            binding_json = binding.decode("utf-8")
            proof_digest = "sha256:" + hashlib.sha256(binding).hexdigest()
            conn.execute(
                """
                INSERT INTO task_runtime_effect_reconciliation_proof(
                    proof_id, schema_version, purpose, case_id, intent_ref,
                    effect_id, graph_id, graph_revision, node_id, attempt_id,
                    case_snapshot_sha256, subject_snapshot_json,
                    subject_snapshot_sha256, outcome, target_resolution_status,
                    receipt_ref, executor_quiescence_ref, reconciler_actor_id,
                    attestation_ref, attestation_key_id, attestation_signature,
                    issued_ts_ms, proof_binding_json, proof_digest, outbox_id,
                    created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proof_reference,
                    EFFECT_RECONCILIATION_PROOF_SCHEMA,
                    normalized_purpose,
                    case_reference,
                    intent_reference,
                    effect_reference,
                    case["graph_id"],
                    case["graph_revision"],
                    case["node_id"],
                    case["attempt_id"],
                    case["snapshot_sha256"],
                    subject["subject_snapshot_json"],
                    subject_digest,
                    normalized_outcome,
                    normalized_target,
                    normalized_receipt,
                    quiescence,
                    actor_id,
                    attestation_reference,
                    key_id,
                    signature,
                    int(issued_ts_ms),
                    binding_json,
                    proof_digest,
                    outbox.outbox_id,
                    ts_ms,
                ),
            )
            stored = self._validated_effect_reconciliation_proof(
                conn,
                conn.execute(
                    "SELECT * FROM task_runtime_effect_reconciliation_proof WHERE proof_id = ?",
                    (proof_reference,),
                ).fetchone(),
            )
            return {"case": case, "proof": stored, "replayed": False}


__all__ = ["TaskEffectReconciliationMutationMixin"]
