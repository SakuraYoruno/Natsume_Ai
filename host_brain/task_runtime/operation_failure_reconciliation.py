"""结果未知 Operation 的签名终态失败落库服务。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from host_brain.contracts.agent_runtime.operation_reconciliation import (
    OperationReconciliationContractError,
    build_operation_failure_outbox_identity,
)

from .graph_cancellation_operation_reconciliation import (
    GraphCancellationOperationReconciliationError,
    OPERATION_RECONCILIATION_INVALID,
    OPERATION_RECONCILIATION_REJECTED,
    OPERATION_RECONCILIATION_STATE_CONFLICT,
    OPERATION_RECONCILIATION_UNCERTAIN,
)


class OperationFailureReconciliationStorePort(Protocol):
    """终态失败对账所需的最小耐久端口；实现负责原子释放与图级联动。"""
    def get_operation_reservation(
        self,
        reservation_id: str,
    ) -> Mapping[str, Any]: ...

    def reconcile_operation_failure(
        self,
        reservation_id: str,
        *,
        expected_version: int,
        outbox: Any,
        reconciler_actor_id: str,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
    ) -> Mapping[str, Any]: ...

    def resolve_reconciled_operation_failure(
        self,
        reservation_id: str,
    ) -> Mapping[str, Any]: ...


class OperationFailureReconciliationService:
    """在签名证明“未发生副作用”后，失败关闭一个精确节点操作。

    服务不会重新执行 worker；它将 RECOVERY_REQUIRED 的精确版本转为 RELEASED，
    并通过存储端同时维护 Attempt、Node、图结算和预算的耐久一致性。
    """

    __slots__ = ("_outbox_factory", "_store")

    def __init__(
        self,
        *,
        task_store: OperationFailureReconciliationStorePort,
        outbox_factory: Any,
    ) -> None:
        self._store = task_store
        self._outbox_factory = outbox_factory

    def reconcile_operation_failure(
        self,
        *,
        reservation_id: str,
        expected_version: int,
        reconciler_actor_id: str,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
    ) -> Mapping[str, Any]:
        """提交或精确重放一次签名终态失败对账。

        先比对 reservation 版本，再以签名绑定生成 Outbox。写入异常时只回读是否
        已取得同一终态；无法确认则不臆测外部副作用，保留明确拒绝/不确定语义。
        """
        request = self._normalize_request(
            reservation_id=reservation_id,
            expected_version=expected_version,
            reconciler_actor_id=reconciler_actor_id,
            attestation_ref=attestation_ref,
            attestation_key_id=attestation_key_id,
            attestation_signature=attestation_signature,
        )
        operation = self._read_operation(request["reservation_id"])
        if self._matches_terminal(operation, request=request):
            return self._success(
                self._resolve_terminal(request["reservation_id"]),
                replayed=True,
            )
        if (
            self._text(operation.get("status")).upper()
            != "RECOVERY_REQUIRED"
            or self._non_negative_int(operation.get("status_version"))
            != request["expected_version"]
        ):
            self._reject(
                OPERATION_RECONCILIATION_STATE_CONFLICT,
                "operation is not the exact RECOVERY_REQUIRED version",
            )
        outbox = self._build_outbox(operation=operation, request=request)
        try:
            terminal_raw = self._store.reconcile_operation_failure(
                request["reservation_id"],
                expected_version=request["expected_version"],
                outbox=outbox,
                reconciler_actor_id=request["reconciler_actor_id"],
                attestation_ref=request["attestation_ref"],
                attestation_key_id=request["attestation_key_id"],
                attestation_signature=request["attestation_signature"],
            )
        except GraphCancellationOperationReconciliationError:
            raise
        except Exception as exc:
            try:
                terminal = self._resolve_terminal(
                    request["reservation_id"]
                )
                reopened = terminal.get("operation")
                if not isinstance(reopened, Mapping) or not self._matches_terminal(
                    reopened,
                    request=request,
                ):
                    raise ValueError("terminal binding differs")
            except Exception:
                raise GraphCancellationOperationReconciliationError(
                    OPERATION_RECONCILIATION_REJECTED,
                    "operation failure reconciliation was rejected",
                    side_effects_may_have_run=False,
                ) from exc
            return self._success(terminal, replayed=True)
        if not isinstance(terminal_raw, Mapping):
            raise GraphCancellationOperationReconciliationError(
                OPERATION_RECONCILIATION_UNCERTAIN,
                "operation failure store returned an invalid terminal",
                side_effects_may_have_run=True,
            )
        terminal = dict(terminal_raw)
        settled = terminal.get("operation")
        if not isinstance(settled, Mapping) or not self._matches_terminal(
            settled,
            request=request,
        ):
            raise GraphCancellationOperationReconciliationError(
                OPERATION_RECONCILIATION_UNCERTAIN,
                "operation failure store returned a drifting terminal",
                side_effects_may_have_run=True,
            )
        return self._success(
            terminal,
            replayed=bool(terminal.get("replayed")),
        )

    def _resolve_terminal(self, reservation_id: str) -> dict[str, Any]:
        """回读完整终态 bundle，用于响应丢失后的精确重放验证。"""
        try:
            value = self._store.resolve_reconciled_operation_failure(
                reservation_id
            )
        except Exception as exc:
            raise GraphCancellationOperationReconciliationError(
                OPERATION_RECONCILIATION_REJECTED,
                "reconciled operation failure could not be reopened",
                side_effects_may_have_run=False,
            ) from exc
        if not isinstance(value, Mapping):
            self._reject(
                OPERATION_RECONCILIATION_REJECTED,
                "reconciled operation failure is invalid",
            )
        return dict(value)

    def _read_operation(self, reservation_id: str) -> Mapping[str, Any]:
        """读取当前 reservation；读取故障不等价于可以释放操作。"""
        try:
            value = self._store.get_operation_reservation(reservation_id)
        except Exception as exc:
            raise GraphCancellationOperationReconciliationError(
                OPERATION_RECONCILIATION_REJECTED,
                "OperationReservation could not be read",
                side_effects_may_have_run=False,
            ) from exc
        if not isinstance(value, Mapping):
            self._reject(
                OPERATION_RECONCILIATION_REJECTED,
                "OperationReservation is invalid",
            )
        return value

    def _build_outbox(
        self,
        *,
        operation: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> Any:
        """生成与恢复版本及 attestation 绑定的幂等 RELEASE Outbox。"""
        attempt_id = self._text(operation.get("attempt_id"))
        if not attempt_id:
            self._reject(
                OPERATION_RECONCILIATION_STATE_CONFLICT,
                "terminal failure Operation has no Attempt",
            )
        try:
            outbox_id, idempotency_key = (
                build_operation_failure_outbox_identity(
                    reservation_id=request["reservation_id"],
                    recovery_version=request["expected_version"],
                    reconciler_actor_id=request["reconciler_actor_id"],
                    attestation_ref=request["attestation_ref"],
                    attestation_key_id=request["attestation_key_id"],
                )
            )
        except OperationReconciliationContractError as exc:
            self._reject(OPERATION_RECONCILIATION_INVALID, str(exc))
        version = request["expected_version"] + 1
        return self._outbox_factory.create(
            aggregate_type="OPERATION_RESERVATION",
            aggregate_id=request["reservation_id"],
            aggregate_version=version,
            event_type="OPERATION_RECOVERY_RELEASE",
            payload_ref=(
                "taskstore://event/OPERATION_RESERVATION/"
                f"{request['reservation_id']}/{version}"
            ),
            idempotency_key=idempotency_key,
            outbox_id=outbox_id,
            attempt_id=attempt_id,
            evidence_refs=(request["attestation_ref"],),
        )

    @classmethod
    def _matches_terminal(
        cls,
        operation: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
    ) -> bool:
        """判断操作是否精确落在本次请求声明的无副作用 RELEASE 终态。"""
        return (
            cls._text(operation.get("reservation_id"))
            == request["reservation_id"]
            and cls._text(operation.get("status")).upper() == "RELEASED"
            and cls._non_negative_int(operation.get("status_version"))
            == request["expected_version"] + 1
            and cls._text(
                operation.get("reconciliation_action")
            ).upper()
            == "RELEASE"
            and cls._text(
                operation.get("reconciliation_outcome")
            ).upper()
            == "NO_SIDE_EFFECT"
            and not cls._text(operation.get("receipt_ref"))
            and cls._text(operation.get("reconciler_actor_id"))
            == request["reconciler_actor_id"]
            and cls._text(
                operation.get("reconciliation_proof_ref")
            )
            == request["attestation_ref"]
            and cls._text(
                operation.get("reconciliation_proof_key_id")
            )
            == request["attestation_key_id"]
            and cls._text(
                operation.get("reconciliation_proof_signature")
            )
            == request["attestation_signature"]
            and cls._non_negative_int(
                operation.get("reconciliation_recovery_version")
            )
            == request["expected_version"]
        )

    @staticmethod
    def _success(
        terminal: Mapping[str, Any],
        *,
        replayed: bool,
    ) -> Mapping[str, Any]:
        """将完整终态 bundle 缩减为调用方可安全使用的恢复投影。"""
        operation = terminal.get("operation")
        attempt = terminal.get("attempt")
        node = terminal.get("node")
        graph = terminal.get("graph")
        if not all(
            isinstance(value, Mapping)
            for value in (operation, attempt, node, graph)
        ):
            raise GraphCancellationOperationReconciliationError(
                OPERATION_RECONCILIATION_UNCERTAIN,
                "operation failure terminal is incomplete",
                side_effects_may_have_run=True,
            )
        terminal_projection = {
            "authority_kind": terminal.get("authority_kind"),
            "graph_status": graph.get("status"),
            "node_status": node.get("status"),
            "attempt_status": attempt.get("status"),
            "budget_status": (
                terminal.get("budget", {}).get("status")
                if isinstance(terminal.get("budget"), Mapping)
                else None
            ),
            "lease_status": (
                terminal.get("lease", {}).get("status")
                if isinstance(terminal.get("lease"), Mapping)
                else None
            ),
            "failure_reason_code": terminal.get(
                "failure_reason_code"
            ),
            "error_ref": terminal.get("error_ref"),
        }
        if terminal.get("settlement_scope") == "NODE_ONLY":
            terminal_projection.update(
                {
                    "settlement_scope": "NODE_ONLY",
                    "graph_settlement_status": terminal.get(
                        "graph_settlement_status"
                    ),
                    "parallel_settlement_ref": terminal.get(
                        "parallel_settlement_ref"
                    ),
                }
            )
        return {
            "status": "RECONCILED",
            "graph_id": str(graph.get("graph_id") or ""),
            "graph_revision": graph.get("revision"),
            "node_id": str(node.get("node_id") or ""),
            "attempt_id": str(attempt.get("attempt_id") or ""),
            "operation": dict(operation),
            "terminal": terminal_projection,
            "replayed": bool(replayed),
        }

    @staticmethod
    def _normalize_request(**raw: Any) -> dict[str, Any]:
        """规范请求字段，拒绝模糊版本、空身份与非签名证明。"""
        request = {
            "reservation_id": str(
                raw.get("reservation_id") or ""
            ).strip(),
            "expected_version": raw.get("expected_version"),
            "reconciler_actor_id": str(
                raw.get("reconciler_actor_id") or ""
            ).strip(),
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
        if (
            not request["reservation_id"]
            or isinstance(request["expected_version"], bool)
            or not isinstance(request["expected_version"], int)
            or request["expected_version"] < 0
            or not request["reconciler_actor_id"]
            or not request["attestation_ref"]
            or not request["attestation_key_id"]
            or not request["attestation_signature"].startswith("ed25519:")
        ):
            raise GraphCancellationOperationReconciliationError(
                OPERATION_RECONCILIATION_INVALID,
                "operation failure reconciliation request is invalid",
                side_effects_may_have_run=False,
            )
        return request

    @staticmethod
    def _text(value: Any) -> str:
        return str(getattr(value, "value", value) or "").strip()

    @staticmethod
    def _non_negative_int(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            return -1
        return value if value >= 0 else -1

    @staticmethod
    def _reject(reason_code: str, detail: str) -> None:
        raise GraphCancellationOperationReconciliationError(
            reason_code,
            detail,
            side_effects_may_have_run=False,
        )


__all__ = [
    "OperationFailureReconciliationService",
    "OperationFailureReconciliationStorePort",
]
