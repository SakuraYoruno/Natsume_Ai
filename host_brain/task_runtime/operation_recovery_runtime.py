"""从单个持久化 Operation checkpoint 进行带围栏的在线续跑。

本运行时绝不判定不确定的外部副作用“未发生”：``RESERVED`` 与
``RECOVERY_REQUIRED`` 必须保持阻塞，直到别处记录权威签名的 COMMIT/RELEASE/
REACQUIRE。唯一可自动续跑的情况是已有 ``COMMITTED`` 回执且原租约失效，且仍复用
同一个 Attempt。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from natsume_contracts.task.v1 import HostCommand

from .admission import AdmittedExecution
from .coordinator_records import _status, _text
from .errors import (
    GOVERNED_OPERATION_RECOVERY_REQUIRED,
    GovernedRuntimeError,
)
from .operation_commit_reconciliation import commit_or_reconcile_exact
from .operation_checkpoint_validation import WorkerBudgetUsageCheckpointState
from .operation_recovery_checkpoint_loader import (
    _maybe_await,
    _required_text,
    assert_existing_operation_lineage,
    takeover_committed_checkpoint,
)
from .ports import ExecutionProjectionReaderPort, TaskStorePort
from .runtime_outbox import RuntimeOutbox


# 仅表示已封存 COMMIT 的恢复检查点；没有该事实时不得从“可能已执行”推导为可自动继续。
@dataclass(frozen=True, slots=True)
class CommittedOperationCheckpoint:
    """已提交 Operation 的恢复检查点，封装继续验收所需的规范持久化事实。"""
    reservation: dict[str, Any]
    attempt: dict[str, Any]
    worker_commit: dict[str, Any]
    receipt_ref: str
    payload_digest: str
    payload_digest_source: str
    budget_usage_state: WorkerBudgetUsageCheckpointState
    budget_usage: dict[str, Any] | None
    durable_elapsed_ms: int | None
    lease: dict[str, Any] | None

    @property
    def attempt_id(self) -> str:
        return _text(self.attempt.get("attempt_id"))

    @property
    def lease_id(self) -> str | None:
        return _text((self.lease or {}).get("lease_id")) or None

    @property
    def fencing_token(self) -> int | None:
        raw = (self.lease or {}).get("fencing_token")
        return int(raw) if type(raw) is int and raw > 0 else None


class DurableOperationRecoveryRuntime:
    """对账结果不明的 COMMIT，并在线接管已提交 checkpoint。"""

    def __init__(
        self,
        *,
        task_store: TaskStorePort,
        projection_reader: ExecutionProjectionReaderPort | None,
        runtime_outbox: RuntimeOutbox,
        owner_id: str,
        lease_ttl_ms: int,
    ) -> None:
        self._store = task_store
        self._projection_reader = projection_reader
        self._runtime_outbox = runtime_outbox
        self._owner_id = _required_text(owner_id, "owner_id")
        self._lease_ttl_ms = max(1, int(lease_ttl_ms))

    async def commit_or_reconcile(
        self,
        *,
        commit: Callable[[], Any],
        reservation_id: str,
        business_scope: str,
        operation_fingerprint: str,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        receipt_ref: str,
        checkpoint_digest_ref: str,
    ) -> dict[str, Any]:
        """仅当可见精确 COMMITTED 行时接受响应丢失，其他情况保持恢复阻塞。"""

        # 只接受与预约、租约、fencing token 和回执完全闭合的已提交行；模糊响应不触发重试。
        return await commit_or_reconcile_exact(
            task_store=self._store,
            commit=commit,
            reservation_id=reservation_id,
            business_scope=business_scope,
            operation_fingerprint=operation_fingerprint,
            attempt_id=attempt_id,
            lease_id=lease_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            expected_version=expected_version,
            receipt_ref=receipt_ref,
            checkpoint_digest_ref=checkpoint_digest_ref,
        )

    async def takeover_committed(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        reservation: Mapping[str, Any],
        latest_attempt: Mapping[str, Any],
        node_grant_ref: str | None,
        budget_id: str,
        durable_elapsed_ms_override: int | None = None,
        allow_multinode_checkpoint_continuation: bool = False,
    ) -> CommittedOperationCheckpoint:
        """在旧租约过期后接管已提交 checkpoint，继续同一 Attempt 而不重跑 worker。"""
        # 接管必须在旧租约失效后重新取得栅栏，并继续同一 Attempt，禁止重新运行 worker。
        return await takeover_committed_checkpoint(
            task_store=self._store,
            projection_reader=self._projection_reader,
            runtime_outbox=self._runtime_outbox,
            owner_id=self._owner_id,
            lease_ttl_ms=self._lease_ttl_ms,
            checkpoint_factory=CommittedOperationCheckpoint,
            admitted=admitted,
            command=command,
            reservation=reservation,
            latest_attempt=latest_attempt,
            node_grant_ref=node_grant_ref,
            budget_id=budget_id,
            durable_elapsed_ms_override=durable_elapsed_ms_override,
            allow_multinode_checkpoint_continuation=(
                allow_multinode_checkpoint_continuation
            ),
        )

    async def reconcile_reservation_state(
        self,
        *,
        admitted: AdmittedExecution,
        reservation: Mapping[str, Any],
        latest_attempt: Mapping[str, Any],
    ) -> dict[str, Any]:
        """在线仅应用 Store 所拥有的 RESERVED 过期收紧，绝不替代签名副作用结论。"""

        # 在线对账仅允许 Store 收紧 RESERVED 的状态；外部副作用的结论必须来自签名权威流程。
        refreshed = await _maybe_await(
            self._store.reconcile_operation_reservation_state(
                admitted.reservation_id
            )
        )
        if not isinstance(refreshed, Mapping):
            self._blocked("operation reconciliation returned no durable reservation")
        self.assert_existing_lineage(
            admitted=admitted,
            reservation=refreshed,
            latest_attempt=latest_attempt,
        )
        if _text(refreshed.get("reservation_id")) != _text(
            reservation.get("reservation_id")
        ):
            self._blocked("operation reconciliation changed reservation identity")
        return dict(refreshed)

    @staticmethod
    def unresolved_projection(reservation: Mapping[str, Any]) -> dict[str, Any]:
        """生成显式不可逆的不确定性投影，提示调用方进入签名对账而非自动重试。"""
        # 面向调用方显式呈现“不确定且不可逆”，阻止上层把恢复等待显示成普通失败后自动重跑。
        return {
            "reservation_id": _text(reservation.get("reservation_id")),
            "reservation_status": _status(reservation),
            "side_effect_state": "UNKNOWN",
            "blocking_side_effect_reversible": False,
            "required_signed_reconciliation_actions": [
                "COMMIT",
                "RELEASE",
                "REACQUIRE",
            ],
        }

    @classmethod
    def assert_existing_lineage(
        cls,
        *,
        admitted: AdmittedExecution,
        reservation: Mapping[str, Any],
        latest_attempt: Mapping[str, Any],
    ) -> None:
        """核验预约与最新 Attempt 仍属于当前冻结节点，防止跨图恢复。"""
        del cls
        assert_existing_operation_lineage(
            admitted=admitted,
            reservation=reservation,
            latest_attempt=latest_attempt,
        )

    @staticmethod
    def _blocked(detail: str) -> None:
        """以统一恢复必需错误中止任何无法安全解释的 reservation 状态。"""
        raise GovernedRuntimeError(GOVERNED_OPERATION_RECOVERY_REQUIRED, detail)



__all__ = [
    "CommittedOperationCheckpoint",
    "DurableOperationRecoveryRuntime",
]
