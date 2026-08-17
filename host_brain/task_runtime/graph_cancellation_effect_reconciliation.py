"""为阻塞图取消的 Acceptance Effect 记录带签名、仅追加的对账证据。

当副作用是否已执行无法由正常取消路径安全判定时，本模块把人工/外部证明绑定到
精确 case 快照并写入独立 sidecar；它不重写原有取消权威或猜测副作用结果。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum
import hashlib
import json
import re
from typing import Any

from host_brain.contracts.agent_runtime.effect_reconciliation import (
    EFFECT_RECONCILIATION_BINDING_DOMAIN,
    EFFECT_RECONCILIATION_BINDING_SCHEMA_VERSION,
    EFFECT_RECONCILIATION_CASE_SCHEMA_VERSION,
    EFFECT_RECONCILIATION_PROOF_SCHEMA_VERSION,
    GraphCancellationEffectStoreRejected,
    build_graph_cancellation_effect_reconciliation_binding,
)

from .ports import (
    GraphCancellationEffectReconciliationStorePort,
    TaskOutboxFactoryPort,
)


EFFECT_RECONCILIATION_INVALID = "EFFECT_RECONCILIATION_INVALID"
EFFECT_RECONCILIATION_REJECTED = "EFFECT_RECONCILIATION_REJECTED"
EFFECT_RECONCILIATION_STATE_CONFLICT = "EFFECT_RECONCILIATION_STATE_CONFLICT"
EFFECT_RECONCILIATION_UNCERTAIN = "EFFECT_RECONCILIATION_UNCERTAIN"
EFFECT_RECONCILIATION_WAKE_FAILED = "EFFECT_RECONCILIATION_WAKE_FAILED"

_OUTBOX_AGGREGATE_TYPE = "GRAPH_CANCELLATION_EFFECT_RECONCILIATION"
_OUTBOX_EVENT_TYPE = "GRAPH_CANCELLATION_EFFECT_PROOF_RECORDED"
_IDENTITY_DOMAIN = (
    b"NATSUME_TASK_RUNTIME_ACCEPTANCE_EFFECT_RECONCILIATION_IDENTITY_V1\x00"
)
_DURABLE_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SIGNATURE_PATTERN = re.compile(r"^ed25519:[A-Za-z0-9_-]{86}==$")
_EFFECT_STATUSES = frozenset({"PENDING", "CLAIMED", "COMMITTED", "FAILED"})
_FENCE_RESOLUTIONS = frozenset(
    {
        "CANCELLED",
        "RECONCILIATION_REQUIRED",
        "COMMITTED_EVIDENCE",
        "FAILED_EVIDENCE",
    }
)


class GraphCancellationEffectReconciliationPurpose(StrEnum):
    """对账针对的取消阶段：待处理请求修复或历史终态隔离校正。"""
    PENDING_CANCELLATION = "PENDING_CANCELLATION"
    LEGACY_FINALIZED_CORRECTION = "LEGACY_FINALIZED_CORRECTION"


class GraphCancellationEffectReconciliationOutcome(StrEnum):
    """经证明的副作用结论，决定取消围栏最终如何收束。"""
    NO_SIDE_EFFECT = "NO_SIDE_EFFECT"
    SIDE_EFFECT_CONFIRMED = "SIDE_EFFECT_CONFIRMED"


_PURPOSE_CLASSIFICATION = {
    GraphCancellationEffectReconciliationPurpose.PENDING_CANCELLATION: (
        "REPAIRABLE_REQUESTED"
    ),
    GraphCancellationEffectReconciliationPurpose.LEGACY_FINALIZED_CORRECTION: (
        "QUARANTINED_FINALIZED"
    ),
}
_PURPOSE_NEW_INTENT_STATUS = {
    GraphCancellationEffectReconciliationPurpose.PENDING_CANCELLATION: "REQUESTED",
    GraphCancellationEffectReconciliationPurpose.LEGACY_FINALIZED_CORRECTION: (
        "FINALIZED"
    ),
}
_OUTCOME_TARGET = {
    GraphCancellationEffectReconciliationOutcome.NO_SIDE_EFFECT: "CANCELLED",
    GraphCancellationEffectReconciliationOutcome.SIDE_EFFECT_CONFIRMED: (
        "COMMITTED_EVIDENCE"
    ),
}
_PENDING_CASE_FENCE = {
    "RECONCILIATION_REQUIRED": "RECONCILIATION_REQUIRED",
    "LEGACY_FAILED_EVIDENCE": "FAILED_EVIDENCE",
    "LEGACY_RETRY_PENDING_CANCELLED": "CANCELLED",
}


class GraphCancellationEffectReconciliationError(RuntimeError):
    """带明确写入不确定性标记的对账失败。

    ``side_effects_may_have_run`` 让上游在异常后仍保持保守恢复，而不是把传输失败
    当作“证明未被记录”或“副作用未发生”。
    """

    def __init__(
        self,
        reason_code: str,
        detail: str,
        *,
        side_effects_may_have_run: bool,
    ) -> None:
        self.reason_code = str(reason_code or EFFECT_RECONCILIATION_REJECTED)
        self.detail = str(detail or "effect reconciliation proof failed")
        self.side_effects_may_have_run = bool(side_effects_may_have_run)
        super().__init__(f"{self.reason_code}: {self.detail}")


class GraphCancellationEffectReconciliationService:
    """记录一份带签名的证明 sidecar，不重写既有取消权威。"""

    __slots__ = ("_clock_ms", "_outbox_factory", "_store", "_wake_cancellation")

    def __init__(
        self,
        *,
        task_store: GraphCancellationEffectReconciliationStorePort,
        outbox_factory: TaskOutboxFactoryPort,
        wake_cancellation: Callable[[], None],
        clock_ms: Callable[[], int],
    ) -> None:
        if not callable(wake_cancellation):
            raise TypeError("wake_cancellation must be callable")
        if not callable(clock_ms):
            raise TypeError("clock_ms must be callable")
        self._store = task_store
        self._outbox_factory = outbox_factory
        self._wake_cancellation = wake_cancellation
        self._clock_ms = clock_ms

    def reconcile_graph_cancellation_effect(
        self,
        *,
        intent_ref: str,
        effect_id: str,
        case_id: str,
        expected_case_snapshot_sha256: str,
        expected_subject_snapshot_sha256: str,
        purpose: GraphCancellationEffectReconciliationPurpose | str,
        outcome: GraphCancellationEffectReconciliationOutcome | str,
        reconciler_actor_id: str,
        issued_ts_ms: int,
        downstream_receipt_ref: str | None = None,
        executor_quiescence_ref: str | None = None,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
    ) -> Mapping[str, Any]:
        """将对账证明原子绑定到精确快照，并处理写入结果不明时的只读确认。

        已存在的完全相同证明会以 ``replayed`` 返回；不同证明必须拒绝，避免后来
        的人工判断篡改同一副作用 case 的历史结论。
        """
        request = self._normalize_request(
            intent_ref=intent_ref,
            effect_id=effect_id,
            case_id=case_id,
            expected_case_snapshot_sha256=expected_case_snapshot_sha256,
            expected_subject_snapshot_sha256=expected_subject_snapshot_sha256,
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
        try:
            snapshot = self._read_snapshot(request)
        except GraphCancellationEffectReconciliationError:
            raise
        except Exception as exc:
            raise GraphCancellationEffectReconciliationError(
                EFFECT_RECONCILIATION_REJECTED,
                "durable Effect reconciliation prerequisites could not be read",
                side_effects_may_have_run=False,
            ) from exc

        # 先检查 append-only 证明：精确重放可成功，不同内容绝不能覆盖先前证据。
        existing_proof = snapshot.get("proof")
        if existing_proof is not None:
            try:
                artifacts = self._artifacts_from_existing_proof(
                    proof=existing_proof,
                    request=request,
                )
            except Exception:
                self._reject("persisted Effect proof binding is not exact or canonical")
            if not self._is_exact_proof(existing_proof, artifacts=artifacts, request=request):
                self._reject("Effect reconciliation case already has a different proof")
            return self._success(
                snapshot=snapshot,
                artifacts=artifacts,
                request=request,
                replayed=True,
            )

        try:
            artifacts = self._build_artifacts(snapshot=snapshot, request=request)
        except Exception as exc:
            raise GraphCancellationEffectReconciliationError(
                EFFECT_RECONCILIATION_REJECTED,
                "canonical Effect reconciliation binding could not be constructed",
                side_effects_may_have_run=False,
            ) from exc
        self._require_new_proof_candidate(snapshot=snapshot, request=request)
        try:
            outbox = self._build_outbox(
                snapshot=snapshot,
                artifacts=artifacts,
                request=request,
            )
        except Exception as exc:
            raise GraphCancellationEffectReconciliationError(
                EFFECT_RECONCILIATION_REJECTED,
                "canonical Effect reconciliation Outbox could not be constructed",
                side_effects_may_have_run=False,
            ) from exc

        effect = snapshot["effect"]
        attempt = snapshot["attempt"]
        fence = snapshot["fence"]
        intent = snapshot["intent"]
        case = snapshot["case"]
        try:
            result_raw = self._store.record_graph_cancellation_effect_reconciliation_proof(
                request["intent_ref"],
                request["effect_id"],
                purpose=request["purpose"],
                case_id=request["case_id"],
                expected_case_snapshot_sha256=request[
                    "expected_case_snapshot_sha256"
                ],
                expected_subject_snapshot_sha256=request[
                    "expected_subject_snapshot_sha256"
                ],
                expected_intent_status_version=int(intent["status_version"]),
                expected_effect_status=str(effect["status"]),
                expected_effect_status_version=int(effect["status_version"]),
                expected_effect_fencing_token=int(effect["fencing_token"]),
                expected_effect_attempt_count=int(effect["attempt_count"]),
                expected_effect_lease_owner=effect["lease_owner"],
                expected_effect_lease_expires_ts_ms=effect[
                    "lease_expires_ts_ms"
                ],
                expected_attempt_status=str(attempt["status"]),
                expected_attempt_status_version=int(attempt["status_version"]),
                expected_fence_observed_status=str(fence["observed_status"]),
                expected_fence_observed_status_version=int(
                    fence["observed_status_version"]
                ),
                expected_fence_resolution_status=str(fence["resolution_status"]),
                outcome=request["outcome"],
                target_resolution_status=request["target_resolution_status"],
                receipt_ref=request["downstream_receipt_ref"],
                executor_quiescence_ref=request["executor_quiescence_ref"],
                reconciler_actor_id=request["reconciler_actor_id"],
                attestation_ref=request["attestation_ref"],
                attestation_key_id=request["attestation_key_id"],
                attestation_signature=request["attestation_signature"],
                issued_ts_ms=request["issued_ts_ms"],
                proof_id=artifacts["proof_id"],
                outbox=outbox,
            )
        except GraphCancellationEffectStoreRejected as exc:
            raise GraphCancellationEffectReconciliationError(
                exc.reason_code,
                exc.detail,
                side_effects_may_have_run=False,
            ) from exc
        except Exception as exc:
            # 写入结果未知时只读回查同一签名证明；查不到则保守标记副作用可能已发生。
            replay = self._proof_after_unknown_outcome(request)
            if replay is not None:
                replay_snapshot, replay_artifacts = replay
                return self._success(
                    snapshot=replay_snapshot,
                    artifacts=replay_artifacts,
                    request=request,
                    replayed=True,
                )
            raise GraphCancellationEffectReconciliationError(
                EFFECT_RECONCILIATION_UNCERTAIN,
                "Effect reconciliation proof write outcome is uncertain",
                side_effects_may_have_run=True,
            ) from exc

        try:
            result = self._record(result_raw, field_name="proof Store result")
            result_case = self._record(result.get("case"), field_name="settled case")
            proof = self._record(result.get("proof"), field_name="settled proof")
            if not self._same_case(result_case, case) or not self._is_exact_proof(
                proof,
                artifacts=artifacts,
                request=request,
            ):
                raise ValueError("settled proof differs from the signed request")
            replayed = result.get("replayed")
            if type(replayed) is not bool:
                raise ValueError("proof Store replay marker is invalid")
        except Exception as exc:
            raise GraphCancellationEffectReconciliationError(
                EFFECT_RECONCILIATION_UNCERTAIN,
                "Store returned a mismatched Effect reconciliation proof",
                side_effects_may_have_run=True,
            ) from exc
        return self._success(
            snapshot=snapshot,
            artifacts=artifacts,
            request=request,
            replayed=replayed,
        )

    def _proof_after_unknown_outcome(
        self,
        request: Mapping[str, Any],
    ) -> tuple[dict[str, Any], Mapping[str, Any]] | None:
        """在写入结果未知后回读同一证明；仅精确匹配时才确认它已成功。"""
        try:
            snapshot = self._read_snapshot(request)
            proof = snapshot.get("proof")
            if proof is None:
                return None
            artifacts = self._artifacts_from_existing_proof(
                proof=proof,
                request=request,
            )
            if not self._is_exact_proof(proof, artifacts=artifacts, request=request):
                return None
            return snapshot, artifacts
        except Exception:
            return None

    def _read_snapshot(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """读取并验证对账 case 仍是请求签名时所见的那一个快照。"""
        raw = self._store.read_graph_cancellation_effect_reconciliation(
            request["intent_ref"],
            request["effect_id"],
        )
        snapshot = self._normalize_snapshot(raw)
        case = snapshot["case"]
        proof = snapshot.get("proof")
        expected_subject_digest = (
            self._text(proof.get("subject_snapshot_sha256"))
            if isinstance(proof, Mapping)
            else snapshot["subject_snapshot_sha256"]
        )
        if (
            case["case_id"] != request["case_id"]
            or case["snapshot_sha256"]
            != request["expected_case_snapshot_sha256"]
            or expected_subject_digest != request["expected_subject_snapshot_sha256"]
            or case["purpose"] != request["purpose"]
        ):
            self._reject("Effect reconciliation request does not bind the exact case")
        return snapshot

    def _artifacts_from_existing_proof(
        self,
        *,
        proof: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """从持久化证明重建并验证其规范绑定字节，供幂等重放使用。"""
        binding_json = self._required_text(
            proof.get("proof_binding_json"),
            field_name="proof.proof_binding_json",
        )
        binding = json.loads(binding_json)
        binding_bytes = build_graph_cancellation_effect_reconciliation_binding(
            binding
        )
        if binding_bytes.decode("utf-8") != binding_json:
            raise ValueError("persisted proof binding is not canonical")
        resolution = self._record(
            binding.get("resolution"),
            field_name="proof resolution binding",
        )
        authority = self._record(
            binding.get("authority"),
            field_name="proof authority binding",
        )
        case = self._record(binding.get("case"), field_name="proof case binding")
        outbox = self._record(binding.get("outbox"), field_name="proof Outbox binding")
        if (
            self._text(binding.get("subject_snapshot_sha256"))
            != request["expected_subject_snapshot_sha256"]
            or self._text(case.get("case_id")) != request["case_id"]
            or self._text(case.get("snapshot_sha256"))
            != request["expected_case_snapshot_sha256"]
            or self._text(resolution.get("purpose")).upper() != request["purpose"]
            or self._text(resolution.get("outcome")).upper() != request["outcome"]
            or self._text(resolution.get("target_resolution_status")).upper()
            != request["target_resolution_status"]
            or self._optional_text(resolution.get("receipt_ref"))
            != request["downstream_receipt_ref"]
            or self._optional_text(resolution.get("executor_quiescence_ref"))
            != request["executor_quiescence_ref"]
            or self._text(authority.get("reconciler_actor_id"))
            != request["reconciler_actor_id"]
            or self._text(authority.get("attestation_ref"))
            != request["attestation_ref"]
            or self._text(authority.get("attestation_key_id"))
            != request["attestation_key_id"]
            or self._exact_int(authority.get("issued_ts_ms"))
            != request["issued_ts_ms"]
        ):
            raise ValueError("persisted proof binding differs from the request")
        return {
            **dict(outbox),
            "binding": binding,
            "binding_bytes": binding_bytes,
            "proof_id": self._text(outbox.get("proof_id")),
            "proof_digest": "sha256:"
            + hashlib.sha256(binding_bytes).hexdigest(),
        }

    def _normalize_snapshot(self, raw: Any) -> dict[str, Any]:
        root = self._record(raw, field_name="Effect reconciliation aggregate")
        case_raw = self._record(root.get("case"), field_name="Effect reconciliation case")
        intent_raw = self._record(root.get("intent"), field_name="cancellation intent")
        effect_raw = self._record(root.get("effect"), field_name="Acceptance Effect")
        fence_raw = self._record(root.get("fence"), field_name="Effect fence")
        attempt_raw = self._record(root.get("attempt"), field_name="Effect Attempt")

        case = {
            "schema_version": self._text(case_raw.get("schema_version"))
            or EFFECT_RECONCILIATION_CASE_SCHEMA_VERSION,
            "case_id": self._durable_ref(case_raw.get("case_id"), field_name="case_id"),
            "intent_ref": self._durable_ref(
                case_raw.get("intent_ref"), field_name="case.intent_ref"
            ),
            "effect_id": self._durable_ref(
                case_raw.get("effect_id"), field_name="case.effect_id"
            ),
            "graph_id": self._required_text(
                case_raw.get("graph_id"), field_name="case.graph_id"
            ),
            "graph_revision": self._positive_int(
                case_raw.get("graph_revision"), field_name="case.graph_revision"
            ),
            "node_id": self._required_text(
                case_raw.get("node_id"), field_name="case.node_id"
            ),
            "attempt_id": self._required_text(
                case_raw.get("attempt_id"), field_name="case.attempt_id"
            ),
            "purpose": self._enum_text(
                GraphCancellationEffectReconciliationPurpose,
                case_raw.get("purpose"),
                field_name="case.purpose",
            ),
            "classification": self._required_text(
                case_raw.get("classification"), field_name="case.classification"
            ).upper(),
            "case_kind": self._required_text(
                case_raw.get("case_kind"), field_name="case.case_kind"
            ).upper(),
            "reason_code": self._required_text(
                case_raw.get("reason_code"), field_name="case.reason_code"
            ),
            "legacy_decision_ref": self._optional_durable_ref(
                case_raw.get("legacy_decision_ref"),
                field_name="case.legacy_decision_ref",
            ),
            "legacy_decision_payload_sha256": self._optional_sha256(
                case_raw.get("legacy_decision_payload_sha256"),
                field_name="case.legacy_decision_payload_sha256",
            ),
            "snapshot_sha256": self._sha256(
                case_raw.get("snapshot_sha256"), field_name="case.snapshot_sha256"
            ),
        }
        intent = {
            "intent_ref": self._durable_ref(
                intent_raw.get("intent_ref"), field_name="intent.intent_ref"
            ),
            "request_id": self._required_text(
                intent_raw.get("request_id"), field_name="intent.request_id"
            ),
            "payload_sha256": self._sha256(
                intent_raw.get("payload_sha256"), field_name="intent.payload_sha256"
            ),
            "status": self._required_text(
                intent_raw.get("status"), field_name="intent.status"
            ).upper(),
            "status_version": self._non_negative_int(
                intent_raw.get("status_version"), field_name="intent.status_version"
            ),
            "graph_id": self._required_text(
                intent_raw.get("graph_id"), field_name="intent.graph_id"
            ),
            "graph_revision": self._positive_int(
                intent_raw.get("graph_revision"), field_name="intent.graph_revision"
            ),
        }
        effect = {
            "effect_id": self._durable_ref(
                effect_raw.get("effect_id", effect_raw.get("id")),
                field_name="effect.effect_id",
            ),
            "graph_id": intent["graph_id"],
            "graph_revision": intent["graph_revision"],
            "node_id": case["node_id"],
            "attempt_id": case["attempt_id"],
            "commit_scope": self._required_text(
                effect_raw.get("commit_scope"), field_name="effect.commit_scope"
            ).upper(),
            "effect_type": self._required_text(
                effect_raw.get("effect_type", effect_raw.get("type")),
                field_name="effect.effect_type",
            ),
            "payload_ref": self._durable_ref(
                effect_raw.get("payload_ref"), field_name="effect.payload_ref"
            ),
            "payload_sha256": self._sha256(
                effect_raw.get("payload_sha256"), field_name="effect.payload_sha256"
            ),
            "idempotency_key": self._required_text(
                effect_raw.get("idempotency_key"),
                field_name="effect.idempotency_key",
            ),
            "status": self._required_text(
                effect_raw.get("status"), field_name="effect.status"
            ).upper(),
            "status_version": self._non_negative_int(
                effect_raw.get("status_version"), field_name="effect.status_version"
            ),
            "attempt_count": self._non_negative_int(
                effect_raw.get("attempt_count"), field_name="effect.attempt_count"
            ),
            "fencing_token": self._non_negative_int(
                effect_raw.get("fencing_token"), field_name="effect.fencing_token"
            ),
            "available_ts_ms": self._non_negative_int(
                effect_raw.get("available_ts_ms"), field_name="effect.available_ts_ms"
            ),
            "committed_ts_ms": self._optional_non_negative_int(
                effect_raw.get("committed_ts_ms"), field_name="effect.committed_ts_ms"
            ),
            "created_ts_ms": self._non_negative_int(
                effect_raw.get("created_ts_ms"), field_name="effect.created_ts_ms"
            ),
            "failed_ts_ms": self._optional_non_negative_int(
                effect_raw.get("failed_ts_ms"), field_name="effect.failed_ts_ms"
            ),
            "lease_owner": self._optional_text(effect_raw.get("lease_owner")),
            "lease_expires_ts_ms": self._optional_non_negative_int(
                effect_raw.get("lease_expires_ts_ms"),
                field_name="effect.lease_expires_ts_ms",
            ),
            "receipt_ref": self._optional_durable_ref(
                effect_raw.get("receipt_ref"), field_name="effect.receipt_ref"
            ),
            "reason_code": self._optional_text(effect_raw.get("reason_code")),
            "last_error_sha256": self._optional_sha256(
                effect_raw.get("last_error_sha256"),
                field_name="effect.last_error_sha256",
            ),
            "updated_ts_ms": self._non_negative_int(
                effect_raw.get("updated_ts_ms"), field_name="effect.updated_ts_ms"
            ),
        }
        fence = {
            "effect_id": self._durable_ref(
                fence_raw.get("effect_id", case["effect_id"]),
                field_name="fence.effect_id",
            ),
            "intent_ref": self._durable_ref(
                fence_raw.get("intent_ref", case["intent_ref"]),
                field_name="fence.intent_ref",
            ),
            "observed_status": self._required_text(
                fence_raw.get("observed_status"), field_name="fence.observed_status"
            ).upper(),
            "observed_status_version": self._non_negative_int(
                fence_raw.get("observed_status_version"),
                field_name="fence.observed_status_version",
            ),
            "resolution_status": self._required_text(
                fence_raw.get("resolution_status"),
                field_name="fence.resolution_status",
            ).upper(),
        }
        attempt = {
            "attempt_id": self._required_text(
                attempt_raw.get("attempt_id"), field_name="attempt.attempt_id"
            ),
            "graph_id": self._required_text(
                attempt_raw.get("graph_id", intent["graph_id"]),
                field_name="attempt.graph_id",
            ),
            "graph_revision": self._positive_int(
                attempt_raw.get("graph_revision", intent["graph_revision"]),
                field_name="attempt.graph_revision",
            ),
            "node_id": self._required_text(
                attempt_raw.get("node_id", case_raw.get("node_id")),
                field_name="attempt.node_id",
            ),
            "status": self._required_text(
                attempt_raw.get("status"), field_name="attempt.status"
            ).upper(),
            "status_version": self._non_negative_int(
                attempt_raw.get("status_version"), field_name="attempt.status_version"
            ),
        }
        if (
            case["schema_version"] != EFFECT_RECONCILIATION_CASE_SCHEMA_VERSION
            or intent["status"] not in {"REQUESTED", "FINALIZED"}
            or effect["status"] not in _EFFECT_STATUSES
            or effect["commit_scope"] not in {"NODE", "GRAPH"}
            or fence["observed_status"] not in _EFFECT_STATUSES
            or fence["resolution_status"] not in _FENCE_RESOLUTIONS
            or case["intent_ref"] != intent["intent_ref"]
            or case["effect_id"] != effect["effect_id"]
            or case["graph_id"] != intent["graph_id"]
            or case["graph_revision"] != intent["graph_revision"]
            or case["node_id"] != effect["node_id"]
            or case["attempt_id"] != effect["attempt_id"]
            or fence["intent_ref"] != intent["intent_ref"]
            or fence["effect_id"] != effect["effect_id"]
            or effect["graph_id"] != intent["graph_id"]
            or effect["graph_revision"] != intent["graph_revision"]
            or attempt["graph_id"] != intent["graph_id"]
            or attempt["graph_revision"] != intent["graph_revision"]
            or attempt["attempt_id"] != effect["attempt_id"]
            or attempt["node_id"] != effect["node_id"]
        ):
            self._reject(
                "Effect case, intent, fence, Attempt, or Graph revision lineage drifted"
            )
        proof_raw = root.get("proof")
        proof = None if proof_raw is None else self._record(
            proof_raw,
            field_name="Effect reconciliation proof",
        )
        return {
            "case": case,
            "intent": intent,
            "effect": effect,
            "fence": fence,
            "attempt": attempt,
            "proof": proof,
            "subject_snapshot_sha256": self._sha256(
                root.get("subject_snapshot_sha256"),
                field_name="subject_snapshot_sha256",
            ),
        }

    def _require_new_proof_candidate(
        self,
        *,
        snapshot: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> None:
        purpose = GraphCancellationEffectReconciliationPurpose(request["purpose"])
        case = snapshot["case"]
        intent = snapshot["intent"]
        effect = snapshot["effect"]
        fence = snapshot["fence"]
        if case["classification"] != _PURPOSE_CLASSIFICATION[purpose]:
            self._reject("Effect reconciliation case classification is not repairable")
        if intent["status"] != _PURPOSE_NEW_INTENT_STATUS[purpose]:
            self._reject("new Effect proof is forbidden for this cancellation epoch")
        if purpose is GraphCancellationEffectReconciliationPurpose.PENDING_CANCELLATION:
            if (
                case["legacy_decision_ref"] is not None
                or case["legacy_decision_payload_sha256"] is not None
            ):
                self._reject("pending cancellation case cannot bind a legacy decision")
        elif (
            case["legacy_decision_ref"] is None
            or case["legacy_decision_payload_sha256"] is None
        ):
            self._reject("finalized correction must bind the immutable legacy decision")
        if (
            purpose
            is GraphCancellationEffectReconciliationPurpose.PENDING_CANCELLATION
            and (
                case["case_kind"] not in _PENDING_CASE_FENCE
                or fence["resolution_status"]
                != _PENDING_CASE_FENCE[case["case_kind"]]
            )
        ):
            self._reject("pending cancellation case_kind does not match its Effect fence")
        if (
            case["case_kind"] == "LEGACY_RETRY_PENDING_CANCELLED"
            and not (
                fence["observed_status"] == "PENDING"
                and effect["status"] == "PENDING"
                and effect["status_version"] >= 2
                and effect["attempt_count"] >= 1
                and effect["fencing_token"] >= 1
            )
        ):
            self._reject("legacy retry-PENDING case does not bind a retry tuple")
        if effect["status"] == "COMMITTED":
            if (
                purpose
                is not GraphCancellationEffectReconciliationPurpose.LEGACY_FINALIZED_CORRECTION
                or request["outcome"] != "SIDE_EFFECT_CONFIRMED"
                or effect["receipt_ref"] != request["downstream_receipt_ref"]
            ):
                self._reject("COMMITTED Effect permits only exact legacy confirmation")
        elif effect["receipt_ref"] is not None:
            self._reject("non-COMMITTED Effect carries an invalid durable receipt")
        if effect["status"] == "PENDING" and not (
            effect["status_version"] >= 2
            and effect["attempt_count"] >= 1
            and effect["fencing_token"] >= 1
            and effect["lease_owner"] is None
            and effect["lease_expires_ts_ms"] is None
        ):
            self._reject("virgin or incoherent PENDING Effect is not a proof candidate")
        if effect["status"] == "CLAIMED":
            lease_owner = effect["lease_owner"]
            lease_expiry = effect["lease_expires_ts_ms"]
            if not lease_owner or lease_expiry is None:
                self._reject("CLAIMED Effect has an incoherent lease snapshot")
            now_ms = self._trusted_now_ms()
            if int(lease_expiry) > now_ms:
                self._reject("live CLAIMED Effect cannot receive reconciliation proof")
            if request["executor_quiescence_ref"] is None:
                self._reject("expired CLAIMED Effect requires executor quiescence evidence")
        elif effect["lease_expires_ts_ms"] is not None:
            self._reject("non-CLAIMED Effect carries a live lease expiry")

    def _build_artifacts(
        self,
        *,
        snapshot: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        identity_value = {
            "schema_version": EFFECT_RECONCILIATION_BINDING_SCHEMA_VERSION,
            "domain": EFFECT_RECONCILIATION_BINDING_DOMAIN,
            "case_id": request["case_id"],
            "case_snapshot_sha256": request["expected_case_snapshot_sha256"],
            "subject_snapshot_sha256": request[
                "expected_subject_snapshot_sha256"
            ],
            "intent_ref": request["intent_ref"],
            "effect_id": request["effect_id"],
            "purpose": request["purpose"],
            "outcome": request["outcome"],
            "target_resolution_status": request["target_resolution_status"],
            "receipt_ref": request["downstream_receipt_ref"],
            "executor_quiescence_ref": request["executor_quiescence_ref"],
            "reconciler_actor_id": request["reconciler_actor_id"],
            "attestation_ref": request["attestation_ref"],
            "attestation_key_id": request["attestation_key_id"],
            "issued_ts_ms": request["issued_ts_ms"],
        }
        identity_bytes = _IDENTITY_DOMAIN + self._canonical_json(identity_value)
        identity_digest = hashlib.sha256(identity_bytes).hexdigest()
        proof_id = f"effect-reconciliation-proof://sha256/{identity_digest}"
        outbox_id = f"outbox::effect-reconciliation::{identity_digest}"
        idempotency_key = f"effect-reconciliation:{identity_digest}"
        payload_ref = (
            f"taskstore://event/{_OUTBOX_AGGREGATE_TYPE}/{request['case_id']}"
            f"@{snapshot['intent']['graph_revision']}/0"
        )
        evidence_refs = sorted(
            {
                str(item)
                for item in (
                    proof_id,
                    request["case_id"],
                    request["intent_ref"],
                    request["effect_id"],
                    request["attestation_ref"],
                    request["executor_quiescence_ref"],
                    request["downstream_receipt_ref"],
                )
                if item
            }
        )
        outbox_binding = {
            "proof_id": proof_id,
            "aggregate_type": _OUTBOX_AGGREGATE_TYPE,
            "aggregate_id": request["case_id"],
            "aggregate_version": 0,
            "graph_revision": snapshot["intent"]["graph_revision"],
            "attempt_id": snapshot["effect"]["attempt_id"],
            "evidence_refs": evidence_refs,
            "event_type": _OUTBOX_EVENT_TYPE,
            "payload_ref": payload_ref,
            "idempotency_key": idempotency_key,
            "outbox_id": outbox_id,
        }
        binding = {
            "schema_version": EFFECT_RECONCILIATION_BINDING_SCHEMA_VERSION,
            "domain": EFFECT_RECONCILIATION_BINDING_DOMAIN,
            "subject_snapshot_sha256": request[
                "expected_subject_snapshot_sha256"
            ],
            "case": dict(snapshot["case"]),
            "intent": dict(snapshot["intent"]),
            "effect": dict(snapshot["effect"]),
            "fence": dict(snapshot["fence"]),
            "attempt": dict(snapshot["attempt"]),
            "resolution": {
                "purpose": request["purpose"],
                "outcome": request["outcome"],
                "target_resolution_status": request["target_resolution_status"],
                "receipt_ref": request["downstream_receipt_ref"],
                "executor_quiescence_ref": request["executor_quiescence_ref"],
            },
            "authority": {
                "reconciler_actor_id": request["reconciler_actor_id"],
                "attestation_ref": request["attestation_ref"],
                "attestation_key_id": request["attestation_key_id"],
                "issued_ts_ms": request["issued_ts_ms"],
            },
            "outbox": outbox_binding,
        }
        binding_bytes = build_graph_cancellation_effect_reconciliation_binding(
            binding
        )
        return {
            **outbox_binding,
            "binding": binding,
            "binding_bytes": binding_bytes,
            "proof_digest": "sha256:"
            + hashlib.sha256(binding_bytes).hexdigest(),
        }

    def _build_outbox(
        self,
        *,
        snapshot: Mapping[str, Any],
        artifacts: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> Any:
        return self._outbox_factory.create(
            aggregate_type=artifacts["aggregate_type"],
            aggregate_id=artifacts["aggregate_id"],
            aggregate_version=artifacts["aggregate_version"],
            event_type=artifacts["event_type"],
            payload_ref=artifacts["payload_ref"],
            idempotency_key=artifacts["idempotency_key"],
            graph_revision=snapshot["intent"]["graph_revision"],
            attempt_id=artifacts["attempt_id"],
            evidence_refs=tuple(artifacts["evidence_refs"]),
            outbox_id=artifacts["outbox_id"],
        )

    def _is_exact_proof(
        self,
        proof: Any,
        *,
        artifacts: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> bool:
        if not isinstance(proof, Mapping):
            return False
        return (
            self._text(proof.get("schema_version"))
            == EFFECT_RECONCILIATION_PROOF_SCHEMA_VERSION
            and self._text(proof.get("proof_id")) == artifacts["proof_id"]
            and self._text(proof.get("case_id")) == request["case_id"]
            and self._text(proof.get("intent_ref")) == request["intent_ref"]
            and self._text(proof.get("effect_id")) == request["effect_id"]
            and self._text(proof.get("purpose")).upper() == request["purpose"]
            and self._text(proof.get("outcome")).upper() == request["outcome"]
            and self._text(proof.get("target_resolution_status")).upper()
            == request["target_resolution_status"]
            and self._optional_text(proof.get("receipt_ref"))
            == request["downstream_receipt_ref"]
            and self._optional_text(proof.get("executor_quiescence_ref"))
            == request["executor_quiescence_ref"]
            and self._text(proof.get("reconciler_actor_id"))
            == request["reconciler_actor_id"]
            and self._text(proof.get("attestation_ref")) == request["attestation_ref"]
            and self._text(proof.get("attestation_key_id"))
            == request["attestation_key_id"]
            and self._text(proof.get("attestation_signature"))
            == request["attestation_signature"]
            and self._exact_int(proof.get("issued_ts_ms")) == request["issued_ts_ms"]
            and self._text(proof.get("case_snapshot_sha256"))
            == request["expected_case_snapshot_sha256"]
            and self._text(proof.get("subject_snapshot_sha256"))
            == request["expected_subject_snapshot_sha256"]
            and self._text(proof.get("proof_digest")) == artifacts["proof_digest"]
            and self._text(proof.get("outbox_id")) == artifacts["outbox_id"]
        )

    def _success(
        self,
        *,
        snapshot: Mapping[str, Any],
        artifacts: Mapping[str, Any],
        request: Mapping[str, Any],
        replayed: bool,
    ) -> Mapping[str, Any]:
        purpose = GraphCancellationEffectReconciliationPurpose(request["purpose"])
        if purpose is GraphCancellationEffectReconciliationPurpose.PENDING_CANCELLATION:
            try:
                self._wake_cancellation()
            except Exception as exc:
                raise GraphCancellationEffectReconciliationError(
                    EFFECT_RECONCILIATION_WAKE_FAILED,
                    "Effect proof was recorded but cancellation continuation was not woken",
                    side_effects_may_have_run=True,
                ) from exc
        return {
            "status": (
                "PROOF_RECORDED"
                if purpose
                is GraphCancellationEffectReconciliationPurpose.PENDING_CANCELLATION
                else "CORRECTION_PROOF_RECORDED"
            ),
            "intent_ref": snapshot["intent"]["intent_ref"],
            "graph_id": snapshot["intent"]["graph_id"],
            "graph_revision": snapshot["intent"]["graph_revision"],
            "case": {
                "case_id": snapshot["case"]["case_id"],
                "purpose": snapshot["case"]["purpose"],
                "classification": snapshot["case"]["classification"],
                "case_kind": snapshot["case"]["case_kind"],
            },
            "effect": {
                "effect_id": snapshot["effect"]["effect_id"],
                "node_id": snapshot["effect"]["node_id"],
                "attempt_id": snapshot["effect"]["attempt_id"],
                "status": snapshot["effect"]["status"],
                "status_version": snapshot["effect"]["status_version"],
                "attempt_count": snapshot["effect"]["attempt_count"],
                "fencing_token": snapshot["effect"]["fencing_token"],
            },
            "proof": {
                "proof_id": artifacts["proof_id"],
                "purpose": request["purpose"],
                "outcome": request["outcome"],
                "target_resolution_status": request["target_resolution_status"],
                "receipt_ref": request["downstream_receipt_ref"],
                "executor_quiescence_ref": request["executor_quiescence_ref"],
            },
            "replayed": bool(replayed),
        }

    @classmethod
    def _normalize_request(cls, **raw: Any) -> dict[str, Any]:
        try:
            purpose = GraphCancellationEffectReconciliationPurpose(
                cls._text(raw.get("purpose")).upper()
            )
            outcome = GraphCancellationEffectReconciliationOutcome(
                cls._text(raw.get("outcome")).upper()
            )
        except ValueError as exc:
            raise GraphCancellationEffectReconciliationError(
                EFFECT_RECONCILIATION_INVALID,
                "Effect reconciliation purpose or outcome is invalid",
                side_effects_may_have_run=False,
            ) from exc
        receipt = cls._optional_text(raw.get("downstream_receipt_ref"))
        quiescence = cls._optional_text(raw.get("executor_quiescence_ref"))
        signature = cls._text(raw.get("attestation_signature"))
        issued_ts_ms = raw.get("issued_ts_ms")
        fields = {
            "intent_ref": cls._text(raw.get("intent_ref")),
            "effect_id": cls._text(raw.get("effect_id")),
            "case_id": cls._text(raw.get("case_id")),
            "expected_case_snapshot_sha256": cls._text(
                raw.get("expected_case_snapshot_sha256")
            ),
            "expected_subject_snapshot_sha256": cls._text(
                raw.get("expected_subject_snapshot_sha256")
            ),
            "purpose": purpose.value,
            "outcome": outcome.value,
            "target_resolution_status": _OUTCOME_TARGET[outcome],
            "reconciler_actor_id": cls._text(raw.get("reconciler_actor_id")),
            "issued_ts_ms": issued_ts_ms,
            "downstream_receipt_ref": receipt,
            "executor_quiescence_ref": quiescence,
            "attestation_ref": cls._text(raw.get("attestation_ref")),
            "attestation_key_id": cls._text(raw.get("attestation_key_id")),
            "attestation_signature": signature,
        }
        durable_values = (
            fields["intent_ref"],
            fields["effect_id"],
            fields["case_id"],
            fields["attestation_ref"],
        )
        if (
            any(_DURABLE_REFERENCE_PATTERN.fullmatch(value) is None for value in durable_values)
            or not _SHA256_PATTERN.fullmatch(fields["expected_case_snapshot_sha256"])
            or not _SHA256_PATTERN.fullmatch(
                fields["expected_subject_snapshot_sha256"]
            )
            or not fields["reconciler_actor_id"]
            or not fields["attestation_key_id"]
            or not _SIGNATURE_PATTERN.fullmatch(signature)
            or type(issued_ts_ms) is not int
            or issued_ts_ms < 0
            or (
                receipt is not None
                and _DURABLE_REFERENCE_PATTERN.fullmatch(receipt) is None
            )
            or (
                quiescence is not None
                and _DURABLE_REFERENCE_PATTERN.fullmatch(quiescence) is None
            )
            or (
                outcome
                is GraphCancellationEffectReconciliationOutcome.SIDE_EFFECT_CONFIRMED
                and receipt is None
            )
            or (
                outcome
                is GraphCancellationEffectReconciliationOutcome.NO_SIDE_EFFECT
                and receipt is not None
            )
        ):
            raise GraphCancellationEffectReconciliationError(
                EFFECT_RECONCILIATION_INVALID,
                "Effect reconciliation request or receipt matrix is invalid",
                side_effects_may_have_run=False,
            )
        return fields

    def _trusted_now_ms(self) -> int:
        try:
            value = self._clock_ms()
        except Exception as exc:
            raise GraphCancellationEffectReconciliationError(
                EFFECT_RECONCILIATION_REJECTED,
                "trusted reconciliation clock is unavailable",
                side_effects_may_have_run=False,
            ) from exc
        if type(value) is not int or value < 0:
            raise GraphCancellationEffectReconciliationError(
                EFFECT_RECONCILIATION_REJECTED,
                "trusted reconciliation clock returned an invalid timestamp",
                side_effects_may_have_run=False,
            )
        return value

    @staticmethod
    def _same_case(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
        return all(
            str(left.get(key) or "").strip() == str(right.get(key) or "").strip()
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
        )

    @staticmethod
    def _canonical_json(value: Mapping[str, Any]) -> bytes:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @staticmethod
    def _record(value: Any, *, field_name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{field_name} must be a mapping")
        return value

    @staticmethod
    def _text(value: Any) -> str:
        return str(getattr(value, "value", value) or "").strip()

    @classmethod
    def _required_text(cls, value: Any, *, field_name: str) -> str:
        result = cls._text(value)
        if not result:
            raise ValueError(f"{field_name} is required")
        return result

    @classmethod
    def _optional_text(cls, value: Any) -> str | None:
        return cls._text(value) or None

    @classmethod
    def _durable_ref(cls, value: Any, *, field_name: str) -> str:
        result = cls._required_text(value, field_name=field_name)
        if _DURABLE_REFERENCE_PATTERN.fullmatch(result) is None:
            raise ValueError(f"{field_name} must be a durable reference")
        return result

    @classmethod
    def _optional_durable_ref(cls, value: Any, *, field_name: str) -> str | None:
        result = cls._optional_text(value)
        if result is not None and _DURABLE_REFERENCE_PATTERN.fullmatch(result) is None:
            raise ValueError(f"{field_name} must be a durable reference")
        return result

    @classmethod
    def _sha256(cls, value: Any, *, field_name: str) -> str:
        result = cls._required_text(value, field_name=field_name)
        if _SHA256_PATTERN.fullmatch(result) is None:
            raise ValueError(f"{field_name} must be canonical sha256")
        return result

    @classmethod
    def _optional_sha256(cls, value: Any, *, field_name: str) -> str | None:
        result = cls._optional_text(value)
        if result is not None and _SHA256_PATTERN.fullmatch(result) is None:
            raise ValueError(f"{field_name} must be canonical sha256")
        return result

    @staticmethod
    def _non_negative_int(value: Any, *, field_name: str) -> int:
        if type(value) is not int or value < 0:
            raise ValueError(f"{field_name} must be a non-negative integer")
        return value

    @classmethod
    def _optional_non_negative_int(
        cls,
        value: Any,
        *,
        field_name: str,
    ) -> int | None:
        if value is None:
            return None
        return cls._non_negative_int(value, field_name=field_name)

    @staticmethod
    def _positive_int(value: Any, *, field_name: str) -> int:
        if type(value) is not int or value < 1:
            raise ValueError(f"{field_name} must be a positive integer")
        return value

    @classmethod
    def _enum_text(cls, enum_type: type[StrEnum], value: Any, *, field_name: str) -> str:
        try:
            return enum_type(cls._text(value).upper()).value
        except ValueError as exc:
            raise ValueError(f"{field_name} is invalid") from exc

    @staticmethod
    def _exact_int(value: Any) -> int:
        return value if type(value) is int else -1

    @staticmethod
    def _reject(detail: str) -> None:
        raise GraphCancellationEffectReconciliationError(
            EFFECT_RECONCILIATION_STATE_CONFLICT,
            detail,
            side_effects_may_have_run=False,
        )


__all__ = [
    "EFFECT_RECONCILIATION_INVALID",
    "EFFECT_RECONCILIATION_REJECTED",
    "EFFECT_RECONCILIATION_STATE_CONFLICT",
    "EFFECT_RECONCILIATION_UNCERTAIN",
    "EFFECT_RECONCILIATION_WAKE_FAILED",
    "GraphCancellationEffectReconciliationError",
    "GraphCancellationEffectReconciliationOutcome",
    "GraphCancellationEffectReconciliationPurpose",
    "GraphCancellationEffectReconciliationService",
]
