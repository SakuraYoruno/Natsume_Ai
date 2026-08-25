"""对结果未知的 Acceptance Effect 进行签名归因式恢复。

当 handler 可能已跨越外部副作用边界却无法确认提交结果时，本模块不重放
handler，而是要求认证 reconciler 对精确的 case/effect 快照和结果作签名。
Store 以比较交换写入 proof 与 Outbox，使同一归因可安全重放、不同归因则
被视为状态冲突，保留人工/下游对账空间。
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
import hashlib
import re
from typing import Any

from host_brain.contracts.agent_runtime.acceptance_effect_reconciliation import (
    ACCEPTANCE_EFFECT_RECONCILIATION_BINDING_DOMAIN,
    ACCEPTANCE_EFFECT_RECONCILIATION_BINDING_SCHEMA_VERSION,
    ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE,
    AcceptanceEffectReconciliationStoreRejected,
    build_acceptance_effect_reconciliation_binding,
    build_acceptance_effect_reconciliation_identity,
)
from host_brain.persistence.task_acceptance_effect_reconciliation_store import (
    ACCEPTANCE_EFFECT_RECONCILIATION_AGGREGATE_TYPE,
    ACCEPTANCE_EFFECT_RECONCILIATION_EVENT_TYPE,
)
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    build_outbox_payload_ref,
)
from host_brain.task_runtime.acceptance_effect_reconciliation_ports import (
    AcceptanceEffectReconciliationStorePort,
)


ACCEPTANCE_EFFECT_RECONCILIATION_INVALID = (
    "ACCEPTANCE_EFFECT_RECONCILIATION_INVALID"
)
ACCEPTANCE_EFFECT_RECONCILIATION_REJECTED = (
    "ACCEPTANCE_EFFECT_RECONCILIATION_REJECTED"
)
ACCEPTANCE_EFFECT_RECONCILIATION_STATE_CONFLICT = (
    "ACCEPTANCE_EFFECT_RECONCILIATION_STATE_CONFLICT"
)
ACCEPTANCE_EFFECT_RECONCILIATION_UNCERTAIN = (
    "ACCEPTANCE_EFFECT_RECONCILIATION_UNCERTAIN"
)

_DURABLE_REFERENCE_PATTERN = re.compile(r"^[a-z][a-z0-9+.-]{1,31}://\S+$")
_SIGNATURE_PATTERN = re.compile(r"^ed25519:[A-Za-z0-9_-]{86}==$")
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class AcceptanceEffectReconciliationError(RuntimeError):
    """对账流程的结构化失败，并声明副作用是否可能已发生。"""

    def __init__(
        self,
        reason_code: str,
        detail: str,
        *,
        side_effects_may_have_run: bool,
    ) -> None:
        self.reason_code = str(
            reason_code or ACCEPTANCE_EFFECT_RECONCILIATION_REJECTED
        )
        self.detail = str(
            detail or "Acceptance Effect reconciliation failed"
        )
        self.side_effects_may_have_run = bool(side_effects_may_have_run)
        super().__init__(f"{self.reason_code}: {self.detail}")


class AcceptanceEffectReconciliationService:
    """提交或精确重开一份已签名的通用 Effect 归因证明。

    服务本身不判断外部世界事实；它冻结候选 case、构造待签名 binding、
    验证请求身份，再让耐久 Store 原子地将 effect 置为目标状态并写 Outbox。
    因此网络超时后可通过 proof 回读确认，而非冒险再次执行效果。
    """

    __slots__ = ("_clock_ms", "_outbox_factory", "_store")

    def __init__(
        self,
        *,
        task_store: AcceptanceEffectReconciliationStorePort,
        outbox_factory: Any = OutboxMessage,
        clock_ms: Any,
    ) -> None:
        required = (
            "read_acceptance_effect_reconciliation",
            "record_acceptance_effect_reconciliation_proof",
        )
        if any(
            not callable(getattr(task_store, name, None))
            for name in required
        ):
            raise TypeError(
                "Acceptance Effect reconciliation Store is incomplete"
            )
        if not callable(getattr(outbox_factory, "create", None)):
            raise TypeError(
                "Acceptance Effect reconciliation requires Outbox factory"
            )
        if not callable(clock_ms):
            raise TypeError(
                "Acceptance Effect reconciliation clock must be callable"
            )
        self._store = task_store
        self._outbox_factory = outbox_factory
        self._clock_ms = clock_ms

    def build_operator_challenge(
        self,
        *,
        effect_id: str,
        outcome: str,
        reconciler_actor_id: str,
        issued_ts_ms: int,
        attestation_ref: str,
        attestation_key_id: str,
        case_id: str | None = None,
        downstream_receipt_ref: str | None = None,
        executor_quiescence_ref: str | None = None,
    ) -> Mapping[str, Any]:
        """构造认证 reconciler 必须签署的精确规范字节及其可审计身份。"""

        effect_ref = str(effect_id or "").strip()
        snapshot = self._read(
            {
                "effect_id": effect_ref,
                "case_id": str(case_id or "").strip() or None,
            }
        )
        case = dict(snapshot.get("case") or {})
        request = self._normalize_request(
            effect_id=effect_ref,
            case_id=case.get("case_id"),
            expected_case_snapshot_sha256=case.get("snapshot_sha256"),
            expected_subject_snapshot_sha256=snapshot.get(
                "subject_snapshot_sha256"
            ),
            purpose=ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE,
            outcome=outcome,
            reconciler_actor_id=reconciler_actor_id,
            issued_ts_ms=issued_ts_ms,
            downstream_receipt_ref=downstream_receipt_ref,
            executor_quiescence_ref=executor_quiescence_ref,
            attestation_ref=attestation_ref,
            attestation_key_id=attestation_key_id,
            attestation_signature="ed25519:" + "A" * 86 + "==",
        )
        if not isinstance(snapshot.get("proof"), Mapping):
            self._require_candidate(snapshot, request=request)
        artifacts = self._build_artifacts(
            snapshot=snapshot,
            request=request,
        )
        return {
            "schema_version": (
                "Natsume.operator."
                "acceptance_effect_reconciliation_signing_challenge.v2"
            ),
            "effect_id": request["effect_id"],
            "case_id": request["case_id"],
            "expected_case_snapshot_sha256": request[
                "expected_case_snapshot_sha256"
            ],
            "expected_subject_snapshot_sha256": request[
                "expected_subject_snapshot_sha256"
            ],
            "purpose": request["purpose"],
            "outcome": request["outcome"],
            "downstream_receipt_ref": request[
                "downstream_receipt_ref"
            ],
            "executor_quiescence_ref": request[
                "executor_quiescence_ref"
            ],
            "issued_ts_ms": request["issued_ts_ms"],
            "attestation_ref": request["attestation_ref"],
            "attestation_key_id": request["attestation_key_id"],
            "proof_id": artifacts["proof_id"],
            "outbox_id": artifacts["outbox_id"],
            "signing_material": {
                "encoding": "base64url-canonical-json",
                "payload_b64": base64.urlsafe_b64encode(
                    artifacts["binding_bytes"]
                ).decode("ascii"),
                "payload_sha256": (
                    "sha256:"
                    + hashlib.sha256(
                        artifacts["binding_bytes"]
                    ).hexdigest()
                ),
            },
        }

    def reconcile_acceptance_effect(
        self,
        *,
        effect_id: str,
        case_id: str,
        expected_case_snapshot_sha256: str,
        expected_subject_snapshot_sha256: str,
        purpose: str,
        outcome: str,
        reconciler_actor_id: str,
        issued_ts_ms: int,
        downstream_receipt_ref: str | None = None,
        executor_quiescence_ref: str | None = None,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
    ) -> Mapping[str, Any]:
        """以签名证明结算一个未知效果，或精确重放同一既存证明。

        若已有 proof，所有绑定字段必须完全一致才返回 replay；若没有，先
        确认候选仍是已接受图上的未结算 effect，再 CAS 写入目标状态。写入
        结果不确定时会回读 proof，仍无法归因才报告 may-have-run，而不是
        擅自将效果标失败或重新调用下游。
        """
        request = self._normalize_request(
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
        snapshot = self._read(request)
        existing = snapshot.get("proof")
        if isinstance(existing, Mapping):
            artifacts = self._artifacts_from_existing_proof(
                proof=existing,
                snapshot=snapshot,
                request=request,
            )
            if not self._proof_matches(
                existing,
                artifacts=artifacts,
                request=request,
            ):
                self._reject(
                    ACCEPTANCE_EFFECT_RECONCILIATION_STATE_CONFLICT,
                    "Acceptance Effect case already has a different proof",
                )
            return self._success(
                snapshot=snapshot,
                proof=existing,
                request=request,
                artifacts=artifacts,
                replayed=True,
            )
        self._require_candidate(snapshot, request=request)
        artifacts = self._build_artifacts(
            snapshot=snapshot,
            request=request,
        )
        outbox = self._build_outbox(
            snapshot=snapshot,
            request=request,
            artifacts=artifacts,
        )
        effect = dict(snapshot["effect"])
        try:
            result_raw = (
                self._store.record_acceptance_effect_reconciliation_proof(
                    request["effect_id"],
                    case_id=request["case_id"],
                    expected_case_snapshot_sha256=request[
                        "expected_case_snapshot_sha256"
                    ],
                    expected_subject_snapshot_sha256=request[
                        "expected_subject_snapshot_sha256"
                    ],
                    expected_effect_status=str(effect["status"]),
                    expected_effect_status_version=int(
                        effect["status_version"]
                    ),
                    expected_effect_fencing_token=int(
                        effect["fencing_token"]
                    ),
                    expected_effect_attempt_count=int(
                        effect["attempt_count"]
                    ),
                    outcome=request["outcome"],
                    target_effect_status=request["target_effect_status"],
                    receipt_ref=request["downstream_receipt_ref"],
                    executor_quiescence_ref=request[
                        "executor_quiescence_ref"
                    ],
                    reconciler_actor_id=request["reconciler_actor_id"],
                    issued_ts_ms=request["issued_ts_ms"],
                    attestation_ref=request["attestation_ref"],
                    attestation_key_id=request["attestation_key_id"],
                    attestation_signature=request[
                        "attestation_signature"
                    ],
                    proof_id=artifacts["proof_id"],
                    outbox=outbox,
                )
            )
        except AcceptanceEffectReconciliationStoreRejected as exc:
            raise AcceptanceEffectReconciliationError(
                exc.reason_code,
                exc.detail,
                side_effects_may_have_run=False,
            ) from exc
        except Exception as exc:
            replay = self._proof_after_unknown_outcome(request)
            if replay is not None:
                replay_snapshot, replay_artifacts = replay
                return self._success(
                    snapshot=replay_snapshot,
                    proof=replay_snapshot["proof"],
                    request=request,
                    artifacts=replay_artifacts,
                    replayed=True,
                )
            raise AcceptanceEffectReconciliationError(
                ACCEPTANCE_EFFECT_RECONCILIATION_UNCERTAIN,
                "Acceptance Effect reconciliation write outcome is uncertain",
                side_effects_may_have_run=True,
            ) from exc
        if not isinstance(result_raw, Mapping):
            raise AcceptanceEffectReconciliationError(
                ACCEPTANCE_EFFECT_RECONCILIATION_UNCERTAIN,
                "Acceptance Effect reconciliation Store result is invalid",
                side_effects_may_have_run=True,
            )
        result = dict(result_raw)
        proof = result.get("proof")
        settled_effect = result.get("effect")
        replayed = result.get("replayed")
        if (
            not isinstance(proof, Mapping)
            or not isinstance(settled_effect, Mapping)
            or type(replayed) is not bool
            or not self._proof_matches(
                proof,
                artifacts=artifacts,
                request=request,
            )
            or str(settled_effect.get("status") or "")
            != request["target_effect_status"]
            or (
                request["outcome"] == "SIDE_EFFECT_CONFIRMED"
                and settled_effect.get("receipt_ref")
                != request["downstream_receipt_ref"]
            )
        ):
            raise AcceptanceEffectReconciliationError(
                ACCEPTANCE_EFFECT_RECONCILIATION_UNCERTAIN,
                "Acceptance Effect reconciliation Store result drifted",
                side_effects_may_have_run=True,
            )
        settled_snapshot = {
            **snapshot,
            "proof": dict(proof),
            "current_effect": dict(settled_effect),
        }
        return self._success(
            snapshot=settled_snapshot,
            proof=proof,
            request=request,
            artifacts=artifacts,
            replayed=replayed,
        )

    def _read(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """读取带 case 绑定的对账主体；读取失败不推断副作用状态。"""
        try:
            raw = self._store.read_acceptance_effect_reconciliation(
                request["effect_id"],
                case_id=request["case_id"],
            )
        except Exception as exc:
            raise AcceptanceEffectReconciliationError(
                ACCEPTANCE_EFFECT_RECONCILIATION_REJECTED,
                "Acceptance Effect reconciliation subject could not be read",
                side_effects_may_have_run=False,
            ) from exc
        if not isinstance(raw, Mapping):
            self._reject(
                ACCEPTANCE_EFFECT_RECONCILIATION_REJECTED,
                "Acceptance Effect reconciliation subject is invalid",
            )
        return dict(raw)

    def _proof_after_unknown_outcome(
        self,
        request: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """写入超时后回读并核对 proof，识别其实已成功的原子提交。"""
        try:
            snapshot = self._read(request)
            proof = snapshot.get("proof")
            if not isinstance(proof, Mapping):
                return None
            artifacts = self._artifacts_from_existing_proof(
                proof=proof,
                snapshot=snapshot,
                request=request,
            )
            if not self._proof_matches(
                proof,
                artifacts=artifacts,
                request=request,
            ):
                return None
            return snapshot, artifacts
        except Exception:
            return None

    def _build_artifacts(
        self,
        *,
        snapshot: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """从不可变快照导出 proof/outbox/幂等键及待签名 binding。"""
        identity = build_acceptance_effect_reconciliation_identity(
            case_id=request["case_id"],
            subject_snapshot_sha256=request[
                "expected_subject_snapshot_sha256"
            ],
            outcome=request["outcome"],
            target_effect_status=request["target_effect_status"],
            downstream_receipt_ref=request["downstream_receipt_ref"],
            executor_quiescence_ref=request[
                "executor_quiescence_ref"
            ],
            reconciler_actor_id=request["reconciler_actor_id"],
            attestation_ref=request["attestation_ref"],
            attestation_key_id=request["attestation_key_id"],
            issued_ts_ms=request["issued_ts_ms"],
        )
        proof_id = identity["proof_id"]
        outbox_id = identity["outbox_id"]
        idempotency_key = identity["idempotency_key"]
        evidence_refs = {
            proof_id,
            request["case_id"],
            request["effect_id"],
            request["attestation_ref"],
        }
        if request["downstream_receipt_ref"] is not None:
            evidence_refs.add(request["downstream_receipt_ref"])
        if request["executor_quiescence_ref"] is not None:
            evidence_refs.add(request["executor_quiescence_ref"])
        outbox_binding = {
            "proof_id": proof_id,
            "aggregate_type": (
                ACCEPTANCE_EFFECT_RECONCILIATION_AGGREGATE_TYPE
            ),
            "aggregate_id": request["case_id"],
            "aggregate_version": 0,
            "event_type": ACCEPTANCE_EFFECT_RECONCILIATION_EVENT_TYPE,
            "payload_ref": build_outbox_payload_ref(
                aggregate_type=(
                    ACCEPTANCE_EFFECT_RECONCILIATION_AGGREGATE_TYPE
                ),
                aggregate_id=request["case_id"],
                aggregate_version=0,
                graph_revision=None,
            ),
            "idempotency_key": idempotency_key,
            "outbox_id": outbox_id,
            "attempt_id": str(snapshot["case"]["attempt_id"]),
            "evidence_refs": sorted(evidence_refs),
        }
        binding_payload = self._binding_payload(
            snapshot=snapshot,
            request=request,
            outbox_binding=outbox_binding,
        )
        return {
            "proof_id": proof_id,
            "outbox_id": outbox_id,
            "idempotency_key": idempotency_key,
            "evidence_refs": tuple(sorted(evidence_refs)),
            "outbox_binding": outbox_binding,
            "binding_payload": binding_payload,
            "binding_bytes": (
                build_acceptance_effect_reconciliation_binding(
                    binding_payload
                )
            ),
        }

    def _artifacts_from_existing_proof(
        self,
        *,
        proof: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """重建既存 proof 的确定性身份，拒绝存储内容与当前快照漂移。"""
        artifacts = self._build_artifacts(
            snapshot=snapshot,
            request=request,
        )
        if (
            proof.get("proof_id") != artifacts["proof_id"]
            or proof.get("outbox_id") != artifacts["outbox_id"]
        ):
            self._reject(
                ACCEPTANCE_EFFECT_RECONCILIATION_STATE_CONFLICT,
                "persisted Acceptance Effect proof identity drifted",
            )
        return artifacts

    @staticmethod
    def _binding_payload(
        *,
        snapshot: Mapping[str, Any],
        request: Mapping[str, Any],
        outbox_binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        """组装签名域内的最小事实：快照、归因、认证者和 Outbox 身份。"""
        case = dict(snapshot["case"])
        case.pop("snapshot_json", None)
        return {
            "schema_version": (
                ACCEPTANCE_EFFECT_RECONCILIATION_BINDING_SCHEMA_VERSION
            ),
            "domain": ACCEPTANCE_EFFECT_RECONCILIATION_BINDING_DOMAIN,
            "subject_snapshot_sha256": request[
                "expected_subject_snapshot_sha256"
            ],
            "case": case,
            "effect": dict(snapshot["effect"]),
            "task": dict(snapshot["task"]),
            "resolution": {
                "purpose": ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE,
                "outcome": request["outcome"],
                "target_effect_status": request["target_effect_status"],
                "receipt_ref": request["downstream_receipt_ref"],
                "executor_quiescence_ref": request[
                    "executor_quiescence_ref"
                ],
            },
            "authority": {
                "reconciler_actor_id": request["reconciler_actor_id"],
                "attestation_ref": request["attestation_ref"],
                "attestation_key_id": request["attestation_key_id"],
                "issued_ts_ms": request["issued_ts_ms"],
            },
            "outbox": dict(outbox_binding),
        }

    def _build_outbox(
        self,
        *,
        snapshot: Mapping[str, Any],
        request: Mapping[str, Any],
        artifacts: Mapping[str, Any],
    ) -> OutboxMessage:
        """创建与 proof 同身份的 Outbox 事件，供后续审计/投递可靠重放。"""
        return self._outbox_factory.create(
            aggregate_type=(
                ACCEPTANCE_EFFECT_RECONCILIATION_AGGREGATE_TYPE
            ),
            aggregate_id=request["case_id"],
            aggregate_version=0,
            graph_revision=None,
            event_type=ACCEPTANCE_EFFECT_RECONCILIATION_EVENT_TYPE,
            payload_ref=artifacts["outbox_binding"]["payload_ref"],
            idempotency_key=artifacts["idempotency_key"],
            outbox_id=artifacts["outbox_id"],
            attempt_id=str(snapshot["case"]["attempt_id"]),
            evidence_refs=artifacts["evidence_refs"],
        )

    def _require_candidate(
        self,
        snapshot: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
    ) -> None:
        """确认 case 仍对应已接受节点上的未结算 effect，防止过期签名落库。"""
        case = snapshot.get("case")
        effect = snapshot.get("effect")
        task = snapshot.get("task")
        if (
            not isinstance(case, Mapping)
            or not isinstance(effect, Mapping)
            or not isinstance(task, Mapping)
            or case.get("case_id") != request["case_id"]
            or case.get("snapshot_sha256")
            != request["expected_case_snapshot_sha256"]
            or snapshot.get("subject_snapshot_sha256")
            != request["expected_subject_snapshot_sha256"]
            or case.get("purpose")
            != ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE
            or effect.get("effect_id") != request["effect_id"]
            or str(effect.get("status") or "")
            not in {"PENDING", "CLAIMED", "FAILED"}
            or effect.get("receipt_ref") is not None
            or str(task.get("attempt_status") or "") != "SUCCEEDED"
            or str(task.get("node_status") or "") != "ACCEPTED"
            or str(task.get("graph_status") or "")
            not in {"ACTIVE", "COMPLETED"}
        ):
            self._reject(
                ACCEPTANCE_EFFECT_RECONCILIATION_STATE_CONFLICT,
                "Acceptance Effect reconciliation subject is stale",
            )

    @staticmethod
    def _proof_matches(
        proof: Mapping[str, Any],
        *,
        artifacts: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> bool:
        """逐字段比较 proof 与当前签名请求，避免“相同 effect 不同结论”混用。"""
        return (
            proof.get("proof_id") == artifacts["proof_id"]
            and proof.get("case_id") == request["case_id"]
            and proof.get("effect_id") == request["effect_id"]
            and proof.get("outcome") == request["outcome"]
            and proof.get("target_effect_status")
            == request["target_effect_status"]
            and proof.get("receipt_ref")
            == request["downstream_receipt_ref"]
            and proof.get("executor_quiescence_ref")
            == request["executor_quiescence_ref"]
            and proof.get("reconciler_actor_id")
            == request["reconciler_actor_id"]
            and proof.get("attestation_ref") == request["attestation_ref"]
            and proof.get("attestation_key_id")
            == request["attestation_key_id"]
            and proof.get("attestation_signature")
            == request["attestation_signature"]
            and proof.get("issued_ts_ms") == request["issued_ts_ms"]
            and proof.get("outbox_id") == artifacts["outbox_id"]
        )

    @staticmethod
    def _success(
        *,
        snapshot: Mapping[str, Any],
        proof: Mapping[str, Any],
        request: Mapping[str, Any],
        artifacts: Mapping[str, Any],
        replayed: bool,
    ) -> dict[str, Any]:
        public_proof = {
            key: value
            for key, value in dict(proof).items()
            if key
            not in {
                "attestation_signature",
                "proof_binding_json",
                "subject_snapshot_json",
            }
        }
        return {
            "status": "PROOF_APPLIED",
            "purpose": ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE,
            "effect_id": request["effect_id"],
            "case_id": request["case_id"],
            "outcome": request["outcome"],
            "target_effect_status": request["target_effect_status"],
            "receipt_ref": request["downstream_receipt_ref"],
            "proof": public_proof,
            "proof_id": artifacts["proof_id"],
            "outbox_id": artifacts["outbox_id"],
            "current_effect": dict(
                snapshot.get("current_effect")
                or snapshot.get("effect")
                or {}
            ),
            "replayed": bool(replayed),
        }

    @classmethod
    def _normalize_request(cls, **raw: Any) -> dict[str, Any]:
        purpose = str(raw.get("purpose") or "").strip().upper()
        outcome = str(raw.get("outcome") or "").strip().upper()
        target = {
            "NO_SIDE_EFFECT": "PENDING",
            "SIDE_EFFECT_CONFIRMED": "COMMITTED",
        }.get(outcome)
        receipt = (
            str(raw.get("downstream_receipt_ref") or "").strip() or None
        )
        quiescence = (
            str(raw.get("executor_quiescence_ref") or "").strip() or None
        )
        request = {
            "effect_id": str(raw.get("effect_id") or "").strip(),
            "case_id": str(raw.get("case_id") or "").strip(),
            "expected_case_snapshot_sha256": str(
                raw.get("expected_case_snapshot_sha256") or ""
            )
            .strip()
            .lower(),
            "expected_subject_snapshot_sha256": str(
                raw.get("expected_subject_snapshot_sha256") or ""
            )
            .strip()
            .lower(),
            "purpose": purpose,
            "outcome": outcome,
            "target_effect_status": target,
            "downstream_receipt_ref": receipt,
            "executor_quiescence_ref": quiescence,
            "reconciler_actor_id": str(
                raw.get("reconciler_actor_id") or ""
            ).strip(),
            "issued_ts_ms": raw.get("issued_ts_ms"),
            "attestation_ref": str(
                raw.get("attestation_ref") or ""
            ).strip(),
            "attestation_key_id": str(
                raw.get("attestation_key_id") or ""
            ).strip(),
            "attestation_signature": str(
                raw.get("attestation_signature") or ""
            ).strip(),
        }
        durable_fields = (
            "effect_id",
            "case_id",
            "attestation_ref",
        )
        if (
            purpose != ACCEPTANCE_EFFECT_RECONCILIATION_PURPOSE
            or target is None
            or any(
                _DURABLE_REFERENCE_PATTERN.fullmatch(request[field]) is None
                for field in durable_fields
            )
            or not request["reconciler_actor_id"]
            or len(request["reconciler_actor_id"]) > 512
            or not request["attestation_key_id"]
            or len(request["attestation_key_id"]) > 255
            or _SHA256_PATTERN.fullmatch(
                request["expected_case_snapshot_sha256"]
            )
            is None
            or _SHA256_PATTERN.fullmatch(
                request["expected_subject_snapshot_sha256"]
            )
            is None
            or type(request["issued_ts_ms"]) is not int
            or request["issued_ts_ms"] < 0
            or _SIGNATURE_PATTERN.fullmatch(
                request["attestation_signature"]
            )
            is None
            or (
                outcome == "NO_SIDE_EFFECT"
                and (
                    receipt is not None
                    or quiescence is None
                    or _DURABLE_REFERENCE_PATTERN.fullmatch(quiescence)
                    is None
                )
            )
            or (
                outcome == "SIDE_EFFECT_CONFIRMED"
                and (
                    receipt is None
                    or _DURABLE_REFERENCE_PATTERN.fullmatch(receipt) is None
                    or (
                        quiescence is not None
                        and _DURABLE_REFERENCE_PATTERN.fullmatch(quiescence)
                        is None
                    )
                )
            )
        ):
            raise AcceptanceEffectReconciliationError(
                ACCEPTANCE_EFFECT_RECONCILIATION_INVALID,
                "Acceptance Effect reconciliation request is invalid",
                side_effects_may_have_run=False,
            )
        return request

    @staticmethod
    def _reject(reason_code: str, detail: str) -> None:
        raise AcceptanceEffectReconciliationError(
            reason_code,
            detail,
            side_effects_may_have_run=False,
        )


__all__ = [
    "ACCEPTANCE_EFFECT_RECONCILIATION_INVALID",
    "ACCEPTANCE_EFFECT_RECONCILIATION_REJECTED",
    "ACCEPTANCE_EFFECT_RECONCILIATION_STATE_CONFLICT",
    "ACCEPTANCE_EFFECT_RECONCILIATION_UNCERTAIN",
    "AcceptanceEffectReconciliationError",
    "AcceptanceEffectReconciliationService",
]
