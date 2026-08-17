"""在副作用结果未知后，以签名证明确认一个耐久 worker 检查点。

本服务与图取消结算刻意隔离：只有签名下游回执能重开为同一不可变 HostCommand、
TaskGraph 节点和 Attempt 的规范投影时，才可将精确的 ``RECOVERY_REQUIRED``
操作推进为 ``COMMITTED``。它从不推断 ``NO_SIDE_EFFECT``，因而不能作为通用的
预留释放通道。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import re
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_payload,
)

from .admission import AdmittedExecution
from .coordinator_receipts import _digest_from_receipt_ref
from .coordinator_records import _status, _text
from .graph_cancellation_operation_reconciliation import (
    GraphCancellationOperationReconciliationError,
    OPERATION_RECONCILIATION_INVALID,
    OPERATION_RECONCILIATION_REJECTED,
    OPERATION_RECONCILIATION_STATE_CONFLICT,
    OPERATION_RECONCILIATION_UNCERTAIN,
)
from .operation_checkpoint_validation import (
    assert_canonical_projection_identity,
    expected_route_authority,
)
from .ports import (
    ExecutionProjectionReaderPort,
    HostCommandAdmissionReaderPort,
    OperationCheckpointReconciliationStorePort,
    TaskOutboxFactoryPort,
)
from host_brain.contracts.agent_runtime.operation_reconciliation import (
    OperationReconciliationStoreRejected,
)


_DURABLE_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,1023}$"
)
_WORKER_PROJECTION_REFERENCE_PATTERN = re.compile(
    r"^worker-projection://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*/"
    r"[0-9a-f]{64}$"
)
_SIGNATURE_PATTERN = re.compile(r"^ed25519:[A-Za-z0-9_-]{86}==$")
_CONTINUABLE_ATTEMPT_STATUSES = frozenset(
    {"LEASED", "RUNNING", "RECOVERABLE"}
)
_CONTINUABLE_NODE_STATUSES = frozenset(
    {"LEASED", "RUNNING", "EXECUTED", "VERIFYING", "VERIFIED"}
)


class OperationCheckpointReconciliationError(
    GraphCancellationOperationReconciliationError
):
    """耐久检查点对账的确定性拒绝或结果不确定错误。"""


class OperationCheckpointReconciliationService:
    """确认精确的 worker 投影，并解除其图的恢复阻塞。

    服务先重新拼接 Operation→Attempt→准入命令→投影的不可变谱系，再交给存储以
    比较版本完成提交。它不信任单独传入的回执引用，以免别的任务的成功结果越权
    消解当前操作的不确定副作用。
    """

    __slots__ = (
        "_clock_ms",
        "_commands",
        "_outbox_factory",
        "_projections",
        "_store",
    )

    def __init__(
        self,
        *,
        task_store: OperationCheckpointReconciliationStorePort,
        command_repository: HostCommandAdmissionReaderPort,
        projection_reader: ExecutionProjectionReaderPort,
        outbox_factory: TaskOutboxFactoryPort,
        clock_ms: Callable[[], int],
    ) -> None:
        """注入只读谱系来源、提交端口和确定 Lease 是否过期的时钟。"""
        if not callable(clock_ms):
            raise TypeError("checkpoint reconciliation clock must be callable")
        self._store = task_store
        self._commands = command_repository
        self._projections = projection_reader
        self._outbox_factory = outbox_factory
        self._clock_ms = clock_ms

    def reconcile_operation_checkpoint(
        self,
        *,
        reservation_id: str,
        expected_version: int,
        action: str,
        reconciler_actor_id: str,
        downstream_receipt_ref: str | None,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
    ) -> Mapping[str, Any]:
        """以签名回执确认一次未知结果的操作，返回稳定且可重放的对账投影。

        只有 ``COMMIT`` 被接受；持久层调用异常时会重新读取终态来区分“已提交但
        响应丢失”和真正未知，后者必须保留 ``side_effects_may_have_run`` 标记，
        交由更高层恢复，而不是擅自重试副作用。
        """
        request = self._normalize_request(
            reservation_id=reservation_id,
            expected_version=expected_version,
            action=action,
            reconciler_actor_id=reconciler_actor_id,
            downstream_receipt_ref=downstream_receipt_ref,
            attestation_ref=attestation_ref,
            attestation_key_id=attestation_key_id,
            attestation_signature=attestation_signature,
        )
        operation, attempt, projection, admitted = self._load_exact_lineage(
            request
        )
        if self._is_exact_terminal(operation, request):
            return self._success(
                operation=operation,
                attempt=attempt,
                projection=projection,
                replayed=True,
            )
        self._require_recovery_candidate(
            operation=operation,
            attempt=attempt,
            admitted=admitted,
            request=request,
        )
        try:
            outbox = self._build_outbox(
                operation=operation,
                request=request,
            )
        except Exception as exc:
            raise OperationCheckpointReconciliationError(
                OPERATION_RECONCILIATION_REJECTED,
                "canonical checkpoint reconciliation Outbox could not be constructed",
                side_effects_may_have_run=False,
            ) from exc
        try:
            settled_raw = self._store.reconcile_operation_checkpoint(
                request["reservation_id"],
                action="COMMIT",
                attempt_id=_required_text(attempt, "attempt_id"),
                expected_version=request["expected_version"],
                outbox=outbox,
                reconciler_actor_id=request["reconciler_actor_id"],
                downstream_receipt_ref=request["downstream_receipt_ref"],
                attestation_ref=request["attestation_ref"],
                attestation_key_id=request["attestation_key_id"],
                attestation_signature=request["attestation_signature"],
            )
        except OperationReconciliationStoreRejected as exc:
            raise OperationCheckpointReconciliationError(
                exc.reason_code,
                exc.detail,
                side_effects_may_have_run=False,
            ) from exc
        except Exception as exc:
            # 存储可能已经提交后才失去响应；只读重开验证精确终态，不能直接重放写入。
            replay = self._terminal_after_unknown_outcome(request)
            if replay is not None:
                replay_operation, replay_attempt, replay_projection = replay
                return self._success(
                    operation=replay_operation,
                    attempt=replay_attempt,
                    projection=replay_projection,
                    replayed=True,
                )
            raise OperationCheckpointReconciliationError(
                OPERATION_RECONCILIATION_UNCERTAIN,
                "checkpoint reconciliation outcome is uncertain",
                side_effects_may_have_run=True,
            ) from exc
        settled = _record(
            settled_raw,
            field_name="settled OperationReservation",
        )
        if not self._is_exact_terminal(settled, request):
            raise OperationCheckpointReconciliationError(
                OPERATION_RECONCILIATION_UNCERTAIN,
                "store returned a mismatched checkpoint reconciliation result",
                side_effects_may_have_run=True,
            )
        return self._success(
            operation=settled,
            attempt=attempt,
            projection=projection,
            replayed=False,
        )

    def _load_exact_lineage(
        self,
        request: Mapping[str, Any],
    ) -> tuple[
        Mapping[str, Any],
        Mapping[str, Any],
        Mapping[str, Any],
        AdmittedExecution,
    ]:
        """重开并交叉校验该操作的准入谱系与签名 worker 投影。"""
        try:
            operation = _record(
                self._store.get_operation_reservation(
                    request["reservation_id"]
                ),
                field_name="OperationReservation",
            )
            attempt_id = _required_text(operation, "attempt_id")
            attempt = _record(
                self._store.get_attempt(attempt_id),
                field_name="operation Attempt",
            )
            graph_id = _required_text(attempt, "graph_id")
            graph_revision = _positive_int(
                attempt.get("graph_revision"),
                "graph_revision",
            )
            node_id = _required_text(attempt, "node_id")
            record = _record(
                self._commands.get_by_graph(
                    graph_id=graph_id,
                    graph_revision=graph_revision,
                ),
                field_name="HostCommand admission",
            )
            command = record.get("command")
            assignment = record.get("assignment")
            graph = record.get("graph")
            if not isinstance(command, HostCommand):
                self._reject("HostCommand admission is invalid")
            admitted = AdmittedExecution.from_contracts(
                command=command,
                assignment=assignment,
                graph=graph,
            ).for_node(node_id)
            loaded = self._projections.load(
                request["downstream_receipt_ref"]
            )
            projection = validate_worker_projection_payload(
                _record(loaded, field_name="worker projection"),
                expected_projection_id=request["downstream_receipt_ref"],
            )
        except OperationCheckpointReconciliationError:
            raise
        except (WorkerProjectionStoreError, TypeError, ValueError) as exc:
            raise OperationCheckpointReconciliationError(
                OPERATION_RECONCILIATION_STATE_CONFLICT,
                "durable worker checkpoint is missing, malformed, or drifted",
                side_effects_may_have_run=False,
            ) from exc
        except Exception as exc:
            raise OperationCheckpointReconciliationError(
                OPERATION_RECONCILIATION_REJECTED,
                "checkpoint reconciliation prerequisites could not be reopened",
                side_effects_may_have_run=False,
            ) from exc
        if (
            _text(operation.get("reservation_id"))
            != request["reservation_id"]
            or _text(operation.get("attempt_id")) != attempt_id
            or _text(attempt.get("attempt_id")) != attempt_id
            or admitted.reservation_id != request["reservation_id"]
            or admitted.business_scope
            != _text(operation.get("business_scope"))
            or admitted.operation_fingerprint
            != _text(operation.get("operation_fingerprint"))
            or _text(attempt.get("operation_scope"))
            != admitted.business_scope
            or _text(attempt.get("operation_fingerprint"))
            != admitted.operation_fingerprint
        ):
            # 任一不可变绑定漂移都意味着回执可能属于另一次执行，必须拒绝跨谱系提交。
            self._reject("Operation, Attempt, and immutable admission lineage drifted")
        receipt_digest = _digest_from_receipt_ref(
            request["downstream_receipt_ref"]
        )
        if (
            receipt_digest is None
            or _text(projection.get("payload_digest")).lower()
            != receipt_digest
        ):
            self._reject(
                "worker projection digest differs from the signed receipt"
            )
        expected_route, expected_target = expected_route_authority(
            admitted=admitted,
            command=command,
            node_grant_ref=None,
        )
        try:
            assert_canonical_projection_identity(
                projection,
                admitted=admitted,
                command=command,
                latest_attempt=attempt,
                expected_route=expected_route,
                expected_target=expected_target,
                node_grant_ref=None,
            )
        except Exception as exc:
            raise OperationCheckpointReconciliationError(
                OPERATION_RECONCILIATION_STATE_CONFLICT,
                "worker projection does not belong to the exact admission",
                side_effects_may_have_run=False,
            ) from exc
        return operation, attempt, projection, admitted

    def _require_recovery_candidate(
        self,
        *,
        operation: Mapping[str, Any],
        attempt: Mapping[str, Any],
        admitted: AdmittedExecution,
        request: Mapping[str, Any],
    ) -> None:
        """确认操作仍可恢复，且没有取消、活跃租约或节点终态与提交相冲突。"""
        if (
            _status(operation) != "RECOVERY_REQUIRED"
            or operation.get("status_version") != request["expected_version"]
            or _status(attempt) not in _CONTINUABLE_ATTEMPT_STATUSES
        ):
            self._reject(
                "operation is not the exact continuable RECOVERY_REQUIRED version"
            )
        cancellation = self._store.get_graph_cancellation_for_graph(
            admitted.graph_id,
            admitted.graph_revision,
        )
        if cancellation is not None:
            # 取消链拥有图终结权；检查点确认不能绕过该更高优先级的安全决定。
            self._reject(
                "checkpoint confirmation cannot bypass graph cancellation"
            )
        graph = _record(
            self._store.get_graph(
                admitted.graph_id,
                admitted.graph_revision,
            ),
            field_name="TaskGraph",
        )
        node = _record(
            self._store.get_node(
                admitted.graph_id,
                admitted.graph_revision,
                admitted.node_id,
            ),
            field_name="TaskNode",
        )
        if (
            _status(graph) != "ACTIVE"
            or _status(node) not in _CONTINUABLE_NODE_STATUSES
        ):
            self._reject(
                "checkpoint target Graph/Node is not continuable"
            )
        lease = self._store.get_active_lease(
            _required_text(attempt, "attempt_id")
        )
        if lease is None:
            return
        lease_record = _record(lease, field_name="active Lease")
        expires_ts_ms = lease_record.get("expires_ts_ms")
        now = int(self._clock_ms())
        if (
            type(expires_ts_ms) is not int
            or expires_ts_ms < 0
            or now < 0
            or _text(lease_record.get("attempt_id"))
            != _text(attempt.get("attempt_id"))
        ):
            self._reject("checkpoint Lease identity or expiry is invalid")
        if expires_ts_ms > now:
            # 原 worker 的租约仍有效时不能接管其不确定结果，避免两个执行者并发结算。
            self._reject(
                "checkpoint confirmation is waiting for the former Lease to expire"
            )

    def _terminal_after_unknown_outcome(
        self,
        request: Mapping[str, Any],
    ) -> tuple[
        Mapping[str, Any],
        Mapping[str, Any],
        Mapping[str, Any],
    ] | None:
        """在写入结果未知后只读重开；仅精确终态可作为成功重放返回。"""
        try:
            operation, attempt, projection, _admitted = (
                self._load_exact_lineage(request)
            )
        except Exception:
            return None
        return (
            (operation, attempt, projection)
            if self._is_exact_terminal(operation, request)
            else None
        )

    @staticmethod
    def _is_exact_terminal(
        operation: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> bool:
        return (
            _text(operation.get("reservation_id"))
            == request["reservation_id"]
            and _status(operation) == "COMMITTED"
            and operation.get("status_version")
            == request["expected_version"] + 1
            and _text(operation.get("receipt_ref"))
            == request["downstream_receipt_ref"]
            and _text(operation.get("reconciliation_action")).upper()
            == "COMMIT"
            and _text(operation.get("reconciliation_outcome")).upper()
            == "SIDE_EFFECT_CONFIRMED"
            and _text(operation.get("reconciler_actor_id"))
            == request["reconciler_actor_id"]
            and _text(operation.get("reconciliation_proof_ref"))
            == request["attestation_ref"]
            and _text(operation.get("reconciliation_proof_key_id"))
            == request["attestation_key_id"]
            and _text(operation.get("reconciliation_proof_signature"))
            == request["attestation_signature"]
            and operation.get("reconciliation_recovery_version")
            == request["expected_version"]
        )

    def _build_outbox(
        self,
        *,
        operation: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> Any:
        """构造与恢复版本、签名证明绑定的确定性 Outbox 事件。"""
        version = request["expected_version"] + 1
        material = "\n".join(
            (
                request["reservation_id"],
                str(request["expected_version"]),
                request["reconciler_actor_id"],
                request["attestation_ref"],
                request["attestation_key_id"],
                request["downstream_receipt_ref"],
            )
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return self._outbox_factory.create(
            aggregate_type="OPERATION_RESERVATION",
            aggregate_id=request["reservation_id"],
            aggregate_version=version,
            event_type="OPERATION_RECOVERY_COMMIT",
            payload_ref=(
                "taskstore://event/OPERATION_RESERVATION/"
                f"{request['reservation_id']}/{version}"
            ),
            idempotency_key=f"operation-checkpoint-reconciliation:{digest}",
            attempt_id=_text(operation.get("attempt_id")) or None,
            evidence_refs=(
                request["attestation_ref"],
                request["downstream_receipt_ref"],
            ),
            outbox_id=f"outbox::operation-checkpoint-reconciliation::{digest}",
        )

    @staticmethod
    def _success(
        *,
        operation: Mapping[str, Any],
        attempt: Mapping[str, Any],
        projection: Mapping[str, Any],
        replayed: bool,
    ) -> Mapping[str, Any]:
        """投影对账结果；``replayed`` 说明本次仅重开既有耐久提交。"""
        return {
            "status": "RECONCILED",
            "graph_id": _text(attempt.get("graph_id")),
            "graph_revision": attempt.get("graph_revision"),
            "node_id": _text(attempt.get("node_id")),
            "attempt_id": _text(attempt.get("attempt_id")),
            "operation": {
                "reservation_id": _text(operation.get("reservation_id")),
                "status": _status(operation),
                "status_version": operation.get("status_version"),
                "receipt_ref": _text(operation.get("receipt_ref")) or None,
                "reconciliation_action": "COMMIT",
                "reconciliation_outcome": "SIDE_EFFECT_CONFIRMED",
                "attestation_ref": _text(
                    operation.get("reconciliation_proof_ref")
                ),
                "attestation_key_id": _text(
                    operation.get("reconciliation_proof_key_id")
                ),
                "recovery_version": operation.get(
                    "reconciliation_recovery_version"
                ),
            },
            "checkpoint": {
                "projection_ref": _text(projection.get("projection_id")),
                "payload_digest": _text(projection.get("payload_digest")),
                "decision": _text(projection.get("decision")),
            },
            "replayed": bool(replayed),
        }

    @classmethod
    def _normalize_request(cls, **raw: Any) -> dict[str, Any]:
        """校验引用形状与 Ed25519 证明，拒绝把不可信文本交给持久化提交。"""
        reservation_id = _text(raw.get("reservation_id"))
        expected_version = raw.get("expected_version")
        action = _text(raw.get("action")).upper()
        actor = _text(raw.get("reconciler_actor_id"))
        receipt = _text(raw.get("downstream_receipt_ref"))
        attestation_ref = _text(raw.get("attestation_ref"))
        key_id = _text(raw.get("attestation_key_id"))
        signature = _text(raw.get("attestation_signature"))
        if (
            not reservation_id
            or type(expected_version) is not int
            or expected_version < 0
            or action != "COMMIT"
            or not actor
            or not _WORKER_PROJECTION_REFERENCE_PATTERN.fullmatch(receipt)
            or not _DURABLE_REFERENCE_PATTERN.fullmatch(attestation_ref)
            or not key_id
            or not _SIGNATURE_PATTERN.fullmatch(signature)
        ):
            raise OperationCheckpointReconciliationError(
                OPERATION_RECONCILIATION_INVALID,
                "checkpoint COMMIT request fields are invalid",
                side_effects_may_have_run=False,
            )
        return {
            "reservation_id": reservation_id,
            "expected_version": expected_version,
            "action": action,
            "reconciler_actor_id": actor,
            "downstream_receipt_ref": receipt,
            "attestation_ref": attestation_ref,
            "attestation_key_id": key_id,
            "attestation_signature": signature,
        }

    @staticmethod
    def _reject(detail: str) -> None:
        """统一产出“未执行副作用”的谱系冲突，供调用方安全停止。"""
        raise OperationCheckpointReconciliationError(
            OPERATION_RECONCILIATION_STATE_CONFLICT,
            detail,
            side_effects_may_have_run=False,
        )


def _record(value: Any, *, field_name: str) -> Mapping[str, Any]:
    """要求端口返回结构化记录，避免缺字段数据参与身份绑定。"""
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _required_text(value: Mapping[str, Any], field_name: str) -> str:
    """读取不可为空的记录主键。"""
    normalized = _text(value.get(field_name))
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _positive_int(value: Any, field_name: str) -> int:
    """约束版本与修订号为严格正整数。"""
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


__all__ = [
    "OperationCheckpointReconciliationError",
    "OperationCheckpointReconciliationService",
]
