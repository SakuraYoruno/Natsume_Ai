"""Signed no-side-effect Operation reacquisition and command dispatch."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
from typing import Any

from host_brain.contracts.agent_runtime.operation_reconciliation import (
    OperationReconciliationContractError,
    build_reconciliation_attestation_binding,
)

from .graph_cancellation_operation_reconciliation import (
    GraphCancellationOperationReconciliationError,
    GraphCancellationOperationReconciliationService,
    OPERATION_RECONCILIATION_INVALID,
    OPERATION_RECONCILIATION_REJECTED,
    OPERATION_RECONCILIATION_STATE_CONFLICT,
    OPERATION_RECONCILIATION_UNCERTAIN,
)
from .ports import (
    OperationReacquisitionStorePort,
    ReconciliationProofVerifierPort,
    TaskOutboxFactoryPort,
)
from .operation_checkpoint_reconciliation import (
    OperationCheckpointReconciliationService,
)
from .operation_failure_reconciliation import (
    OperationFailureReconciliationService,
)


_DURABLE_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_SIGNATURE_PATTERN = re.compile(r"^ed25519:[A-Za-z0-9_-]{86}==$")


class OperationReacquisitionError(GraphCancellationOperationReconciliationError):
    """A signed REACQUIRE failure with explicit mutation uncertainty."""


class OperationReacquisitionService:
    """Advance an expired uncertain Operation to one higher-fence owner."""

    __slots__ = (
        "_lease_ttl_ms",
        "_outbox_factory",
        "_owner_id",
        "_proof_verifier",
        "_store",
    )

    def __init__(
        self,
        *,
        task_store: OperationReacquisitionStorePort,
        outbox_factory: TaskOutboxFactoryPort,
        proof_verifier: ReconciliationProofVerifierPort | None,
        lease_ttl_ms: int,
    ) -> None:
        if int(lease_ttl_ms) <= 0:
            raise ValueError("operation reacquisition requires positive lease_ttl_ms")
        self._store = task_store
        self._outbox_factory = outbox_factory
        self._proof_verifier = proof_verifier
        self._lease_ttl_ms = int(lease_ttl_ms)
        self._owner_id: str | None = None

    def bind_execution_owner(self, owner_id: str) -> None:
        """Bind exactly once to the app-local worker owner."""

        # owner 是新租约的持有者身份；进程启动后禁止改绑，避免同一服务对象
        # 在恢复途中以两个执行者名义取得栅栏，从而破坏“谁有权继续”的判定。
        normalized = str(owner_id or "").strip()
        if not normalized:
            raise ValueError("operation reacquisition execution owner is required")
        if self._owner_id is not None and self._owner_id != normalized:
            raise RuntimeError("operation reacquisition execution owner is already bound")
        self._owner_id = normalized

    def reconcile_operation_reacquisition(
        self,
        *,
        reservation_id: str,
        expected_version: int,
        reconciler_actor_id: str,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
    ) -> Mapping[str, Any]:
        request = self._normalize_request(
            reservation_id=reservation_id,
            expected_version=expected_version,
            reconciler_actor_id=reconciler_actor_id,
            attestation_ref=attestation_ref,
            attestation_key_id=attestation_key_id,
            attestation_signature=attestation_signature,
        )
        owner_id = self._owner_id
        if owner_id is None:
            self._reject(
                OPERATION_RECONCILIATION_REJECTED,
                "operation reacquisition execution owner is not bound",
            )
        operation = self._read_operation(request["reservation_id"])
        if self._is_exact_consumed_reacquisition(
            operation,
            request=request,
            owner_id=owner_id,
        ):
            # 幂等重放只接受已落库且逐字段闭合的同一份认领结果；不能因为
            # 操作“看起来已恢复”就放宽为任意 RESERVED/COMMITTED 记录。
            return self._success(operation, replayed=True)
        if (
            self._text(operation.get("status")).upper() != "RECOVERY_REQUIRED"
            or self._non_negative_int(operation.get("status_version"))
            != request["expected_version"]
        ):
            self._reject(
                OPERATION_RECONCILIATION_STATE_CONFLICT,
                "operation is not the exact RECOVERY_REQUIRED version",
            )
        self._verify_attestation(operation, request)
        attempt_id = self._text(operation.get("attempt_id"))
        if not attempt_id:
            self._reject(
                OPERATION_RECONCILIATION_STATE_CONFLICT,
                "operation reacquisition requires its original Attempt",
            )
        attempt = self._record(
            self._store.get_attempt(attempt_id),
            field_name="operation Attempt",
        )
        attempt_version = self._non_negative_int(attempt.get("status_version"))
        if attempt_version < 0:
            self._reject(
                OPERATION_RECONCILIATION_STATE_CONFLICT,
                "operation Attempt version is invalid",
            )
        lease_mutated = False
        try:
            # 先原子取得更高 fencing token，再把 REACQUIRE 结论写回 reservation。
            # 两步之间若连接中断，结果可能已部分生效，下面必须走精确回读而非重试。
            lease = self._record(
                self._store.acquire_lease(
                    attempt_id,
                    owner_id=owner_id,
                    ttl_ms=self._lease_ttl_ms,
                    expected_attempt_version=attempt_version,
                    existing_operation_reservation_id=request["reservation_id"],
                    operation_reacquisition_authorization={
                        "expected_version": request["expected_version"],
                        "reconciler_actor_id": request["reconciler_actor_id"],
                        "attestation_ref": request["attestation_ref"],
                        "attestation_key_id": request["attestation_key_id"],
                        "attestation_signature": request[
                            "attestation_signature"
                        ],
                    },
                    outbox=self._build_lease_outbox(
                        operation=operation,
                        attempt_id=attempt_id,
                        attempt_version=attempt_version,
                        owner_id=owner_id,
                    ),
                ),
                field_name="reacquired Lease",
            )
            lease_mutated = True
            self._assert_advanced_lease(
                operation=operation,
                lease=lease,
                owner_id=owner_id,
            )
            settled = self._store.recover_operation_reservation(
                request["reservation_id"],
                action="REACQUIRE",
                attempt_id=attempt_id,
                lease_id=self._text(lease.get("lease_id")),
                owner_id=owner_id,
                fencing_token=self._positive_int(lease.get("fencing_token")),
                expected_version=request["expected_version"],
                outbox=self._build_reconciliation_outbox(
                    operation=operation,
                    request=request,
                    attempt_id=attempt_id,
                ),
                reconciler_actor_id=request["reconciler_actor_id"],
                attestation_ref=request["attestation_ref"],
                attestation_key_id=request["attestation_key_id"],
                attestation_signature=request["attestation_signature"],
                ttl_ms=self._lease_ttl_ms,
            )
        except OperationReacquisitionError:
            raise
        except Exception as exc:
            # 只有尚未触及租约时才能把失败定性为拒绝；一旦 acquire_lease 返回过，
            # 调用者必须把结论视为不确定，防止重复认领造成两个有效执行窗口。
            replay = self._try_exact_replay(request=request, owner_id=owner_id)
            if replay is not None:
                return self._success(replay, replayed=True)
            raise OperationReacquisitionError(
                (
                    OPERATION_RECONCILIATION_UNCERTAIN
                    if lease_mutated
                    else OPERATION_RECONCILIATION_REJECTED
                ),
                (
                    "operation reacquisition outcome is uncertain"
                    if lease_mutated
                    else "operation reacquisition lease was rejected"
                ),
                side_effects_may_have_run=lease_mutated,
            ) from exc
        settled_record = self._record(
            settled,
            field_name="reacquired OperationReservation",
        )
        if not self._is_exact_consumed_reacquisition(
            settled_record,
            request=request,
            owner_id=owner_id,
        ):
            raise OperationReacquisitionError(
                OPERATION_RECONCILIATION_UNCERTAIN,
                "store returned a mismatched reacquisition result",
                side_effects_may_have_run=True,
            )
        return self._success(settled_record, replayed=False)

    def _verify_attestation(
        self,
        operation: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> None:
        verifier = self._proof_verifier
        if verifier is None or not verifier.has_key(request["attestation_key_id"]):
            # 无法验证“无副作用”证明时宁可保持 RECOVERY_REQUIRED；恢复服务
            # 没有权限自行判断外部 worker 尚未执行。
            self._reject(
                OPERATION_RECONCILIATION_REJECTED,
                "operation reacquisition verification authority is unavailable",
            )
        try:
            binding = build_reconciliation_attestation_binding(
                reservation_id=request["reservation_id"],
                business_scope=self._text(operation.get("business_scope")),
                operation_fingerprint=self._text(
                    operation.get("operation_fingerprint")
                ),
                recovery_version=request["expected_version"],
                reconciler_actor_id=request["reconciler_actor_id"],
                attestation_ref=request["attestation_ref"],
                recovery_action="REACQUIRE",
                reconciliation_outcome="NO_SIDE_EFFECT",
            )
        except OperationReconciliationContractError as exc:
            self._reject(OPERATION_RECONCILIATION_INVALID, str(exc))
        if not verifier.verify(
            key_id=request["attestation_key_id"],
            message=binding,
            signature=request["attestation_signature"],
        ):
            self._reject(
                OPERATION_RECONCILIATION_REJECTED,
                "operation reacquisition signature is invalid or binding drifted",
            )

    def _assert_advanced_lease(
        self,
        *,
        operation: Mapping[str, Any],
        lease: Mapping[str, Any],
        owner_id: str,
    ) -> None:
        if (
            self._text(lease.get("status")).upper() != "ACTIVE"
            or self._text(lease.get("attempt_id"))
            != self._text(operation.get("attempt_id"))
            or self._text(lease.get("owner_id")) != owner_id
            or self._text(lease.get("lease_id"))
            == self._text(operation.get("lease_id"))
            or self._positive_int(lease.get("fencing_token"))
            <= self._positive_int(operation.get("fencing_token"))
        ):
            self._reject(
                OPERATION_RECONCILIATION_STATE_CONFLICT,
                "operation reacquisition did not establish a higher live fence",
                side_effects_may_have_run=True,
            )

    def _try_exact_replay(
        self,
        *,
        request: Mapping[str, Any],
        owner_id: str,
    ) -> Mapping[str, Any] | None:
        try:
            operation = self._read_operation(request["reservation_id"])
        except Exception:
            return None
        return (
            operation
            if self._is_exact_consumed_reacquisition(
                operation,
                request=request,
                owner_id=owner_id,
            )
            else None
        )

    @classmethod
    def _is_exact_consumed_reacquisition(
        cls,
        operation: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
        owner_id: str,
    ) -> bool:
        status = cls._text(operation.get("status")).upper()
        return (
            status in {"RESERVED", "COMMITTED"}
            and cls._text(operation.get("reservation_id"))
            == request["reservation_id"]
            and cls._text(operation.get("owner_id")) == owner_id
            and cls._text(operation.get("reconciliation_action")).upper()
            == "REACQUIRE"
            and cls._text(operation.get("reconciliation_outcome")).upper()
            == "NO_SIDE_EFFECT"
            and cls._text(operation.get("reconciler_actor_id"))
            == request["reconciler_actor_id"]
            and cls._text(operation.get("reconciliation_proof_ref"))
            == request["attestation_ref"]
            and cls._text(operation.get("reconciliation_proof_key_id"))
            == request["attestation_key_id"]
            and cls._text(operation.get("reconciliation_proof_signature"))
            == request["attestation_signature"]
            and cls._non_negative_int(
                operation.get("reconciliation_recovery_version")
            )
            == request["expected_version"]
            and cls._non_negative_int(operation.get("status_version"))
            >= request["expected_version"] + 1
            and cls._positive_int(operation.get("fencing_token")) > 0
            and bool(cls._text(operation.get("attempt_id")))
            and bool(cls._text(operation.get("lease_id")))
        )

    def _build_lease_outbox(
        self,
        *,
        operation: Mapping[str, Any],
        attempt_id: str,
        attempt_version: int,
        owner_id: str,
    ) -> Any:
        material = "\n".join(
            (
                self._text(operation.get("reservation_id")),
                str(operation.get("status_version")),
                attempt_id,
                str(attempt_version),
                owner_id,
                str(self._lease_ttl_ms),
            )
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        version = attempt_version + 1
        return self._outbox_factory.create(
            aggregate_type="TASK_ATTEMPT",
            aggregate_id=attempt_id,
            aggregate_version=version,
            event_type="TASK_ATTEMPT_LEASED",
            payload_ref=_payload_ref("TASK_ATTEMPT", attempt_id, version),
            idempotency_key=f"operation-reacquire-lease:{digest}",
            attempt_id=attempt_id,
            outbox_id=f"outbox::operation-reacquire-lease::{digest}",
        )

    def _build_reconciliation_outbox(
        self,
        *,
        operation: Mapping[str, Any],
        request: Mapping[str, Any],
        attempt_id: str,
    ) -> Any:
        material = "\n".join(
            (
                request["reservation_id"],
                str(request["expected_version"]),
                request["reconciler_actor_id"],
                request["attestation_ref"],
                request["attestation_key_id"],
            )
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        version = request["expected_version"] + 1
        return self._outbox_factory.create(
            aggregate_type="OPERATION_RESERVATION",
            aggregate_id=request["reservation_id"],
            aggregate_version=version,
            event_type="OPERATION_RECOVERY_REACQUIRE",
            payload_ref=_payload_ref(
                "OPERATION_RESERVATION",
                request["reservation_id"],
                version,
            ),
            idempotency_key=f"operation-reacquisition:{digest}",
            attempt_id=attempt_id,
            evidence_refs=(request["attestation_ref"],),
            outbox_id=f"outbox::operation-reacquisition::{digest}",
        )

    def _read_operation(self, reservation_id: str) -> Mapping[str, Any]:
        try:
            return self._record(
                self._store.get_operation_reservation(reservation_id),
                field_name="OperationReservation",
            )
        except OperationReacquisitionError:
            raise
        except Exception as exc:
            raise OperationReacquisitionError(
                OPERATION_RECONCILIATION_REJECTED,
                "durable OperationReservation could not be read",
                side_effects_may_have_run=False,
            ) from exc

    def _success(
        self,
        operation: Mapping[str, Any],
        *,
        replayed: bool,
    ) -> Mapping[str, Any]:
        attempt = self._record(
            self._store.get_attempt(self._text(operation.get("attempt_id"))),
            field_name="reacquired Attempt",
        )
        return {
            "status": "RECONCILED",
            "graph_id": self._text(attempt.get("graph_id")),
            "graph_revision": self._positive_int(attempt.get("graph_revision")),
            "node_id": self._text(attempt.get("node_id")),
            "attempt_id": self._text(attempt.get("attempt_id")),
            "operation": {
                "reservation_id": self._text(operation.get("reservation_id")),
                "status": self._text(operation.get("status")).upper(),
                "status_version": self._non_negative_int(
                    operation.get("status_version")
                ),
                "lease_id": self._text(operation.get("lease_id")),
                "owner_id": self._text(operation.get("owner_id")),
                "fencing_token": self._positive_int(
                    operation.get("fencing_token")
                ),
                "reconciliation_action": "REACQUIRE",
                "reconciliation_outcome": "NO_SIDE_EFFECT",
                "attestation_ref": self._text(
                    operation.get("reconciliation_proof_ref")
                ),
                "attestation_key_id": self._text(
                    operation.get("reconciliation_proof_key_id")
                ),
                "recovery_version": self._non_negative_int(
                    operation.get("reconciliation_recovery_version")
                ),
            },
            "replayed": bool(replayed),
        }

    @classmethod
    def _normalize_request(cls, **raw: Any) -> dict[str, Any]:
        reservation_id = cls._text(raw.get("reservation_id"))
        actor = cls._text(raw.get("reconciler_actor_id"))
        attestation_ref = cls._text(raw.get("attestation_ref"))
        key_id = cls._text(raw.get("attestation_key_id"))
        signature = cls._text(raw.get("attestation_signature"))
        expected_version = raw.get("expected_version")
        if (
            not reservation_id
            or type(expected_version) is not int
            or expected_version < 0
            or not actor
            or not _DURABLE_REFERENCE_PATTERN.fullmatch(attestation_ref)
            or not key_id
            or not _SIGNATURE_PATTERN.fullmatch(signature)
        ):
            cls._reject(
                OPERATION_RECONCILIATION_INVALID,
                "REACQUIRE request fields are invalid",
            )
        return {
            "reservation_id": reservation_id,
            "expected_version": expected_version,
            "reconciler_actor_id": actor,
            "attestation_ref": attestation_ref,
            "attestation_key_id": key_id,
            "attestation_signature": signature,
        }

    @staticmethod
    def _record(value: Any, *, field_name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{field_name} must be a mapping")
        return value

    @staticmethod
    def _text(value: Any) -> str:
        return str(getattr(value, "value", value) or "").strip()

    @staticmethod
    def _non_negative_int(value: Any) -> int:
        return value if type(value) is int and value >= 0 else -1

    @staticmethod
    def _positive_int(value: Any) -> int:
        return value if type(value) is int and value > 0 else 0

    @staticmethod
    def _reject(
        reason_code: str,
        detail: str,
        *,
        side_effects_may_have_run: bool = False,
    ) -> None:
        raise OperationReacquisitionError(
            reason_code,
            detail,
            side_effects_may_have_run=side_effects_may_have_run,
        )


class OperationReconciliationCommandService:
    """Dispatch cancellation, terminal failure, checkpoint and reacquisition."""

    __slots__ = (
        "_cancellation",
        "_checkpoint",
        "_failure",
        "_reacquisition",
    )

    def __init__(
        self,
        *,
        cancellation_service: GraphCancellationOperationReconciliationService,
        reacquisition_service: OperationReacquisitionService,
        checkpoint_service: OperationCheckpointReconciliationService,
        failure_service: OperationFailureReconciliationService,
    ) -> None:
        self._cancellation = cancellation_service
        self._reacquisition = reacquisition_service
        self._checkpoint = checkpoint_service
        self._failure = failure_service

    def bind_execution_owner(self, owner_id: str) -> None:
        self._reacquisition.bind_execution_owner(owner_id)

    def reconcile_operation(
        self,
        *,
        intent_ref: str | None,
        reservation_id: str,
        expected_version: int,
        action: str,
        reconciler_actor_id: str,
        downstream_receipt_ref: str | None,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
    ) -> Mapping[str, Any]:
        normalized_action = str(action or "").strip().upper()
        cancellation_ref = str(intent_ref or "").strip() or None
        if normalized_action == "REACQUIRE":
            if cancellation_ref is not None or downstream_receipt_ref is not None:
                raise OperationReacquisitionError(
                    OPERATION_RECONCILIATION_INVALID,
                    "REACQUIRE cannot carry cancellation intent or downstream receipt",
                    side_effects_may_have_run=False,
                )
            return self._reacquisition.reconcile_operation_reacquisition(
                reservation_id=reservation_id,
                expected_version=expected_version,
                reconciler_actor_id=reconciler_actor_id,
                attestation_ref=attestation_ref,
                attestation_key_id=attestation_key_id,
                attestation_signature=attestation_signature,
            )
        if normalized_action == "COMMIT" and cancellation_ref is None:
            return self._checkpoint.reconcile_operation_checkpoint(
                reservation_id=reservation_id,
                expected_version=expected_version,
                action=normalized_action,
                reconciler_actor_id=reconciler_actor_id,
                downstream_receipt_ref=downstream_receipt_ref,
                attestation_ref=attestation_ref,
                attestation_key_id=attestation_key_id,
                attestation_signature=attestation_signature,
            )
        if normalized_action == "RELEASE" and cancellation_ref is None:
            if downstream_receipt_ref is not None:
                raise OperationReacquisitionError(
                    OPERATION_RECONCILIATION_INVALID,
                    "terminal failure RELEASE cannot carry a downstream receipt",
                    side_effects_may_have_run=False,
                )
            return self._failure.reconcile_operation_failure(
                reservation_id=reservation_id,
                expected_version=expected_version,
                reconciler_actor_id=reconciler_actor_id,
                attestation_ref=attestation_ref,
                attestation_key_id=attestation_key_id,
                attestation_signature=attestation_signature,
            )
        if normalized_action not in {"COMMIT", "RELEASE"} or cancellation_ref is None:
            raise OperationReacquisitionError(
                OPERATION_RECONCILIATION_INVALID,
                "COMMIT requires either cancellation intent or an exact worker "
                "checkpoint",
                side_effects_may_have_run=False,
            )
        return self._cancellation.reconcile_graph_cancellation_operation(
            intent_ref=cancellation_ref,
            reservation_id=reservation_id,
            expected_version=expected_version,
            action=normalized_action,
            reconciler_actor_id=reconciler_actor_id,
            downstream_receipt_ref=downstream_receipt_ref,
            attestation_ref=attestation_ref,
            attestation_key_id=attestation_key_id,
            attestation_signature=attestation_signature,
        )

    def reconcile_graph_cancellation_operation(self, **kwargs: Any) -> Mapping[str, Any]:
        """Compatibility surface retained for cancellation-only callers."""

        return self.reconcile_operation(**kwargs)


def _payload_ref(aggregate_type: str, aggregate_id: str, version: int) -> str:
    return f"taskstore://event/{aggregate_type}/{aggregate_id}/{int(version)}"


__all__ = [
    "OperationReacquisitionError",
    "OperationReacquisitionService",
    "OperationReconciliationCommandService",
]
