"""已提交 Operation checkpoint 的预算恢复边界。

该模块仅根据已封存的 BudgetUsage 证据对账或补建缺失计量；结果不明时返回 HOLD，
不把预算通信失败误归类为 worker 失败，也不自行推进节点生命周期。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .admission import AdmittedExecution
from .budget_runtime import BudgetCharge, BudgetRuntimeContext, DurableBudgetRuntime
from .coordinator_receipts import build_execution_receipt, terminal_event
from .errors import GOVERNED_OPERATION_RECOVERY_REQUIRED, GovernedRuntimeError
from .operation_checkpoint_validation import WorkerBudgetUsageCheckpointState
from .operation_recovery_runtime import CommittedOperationCheckpoint
from .runtime_outbox import RuntimeOutbox


async def recover_checkpoint_budget(
    *,
    budget_runtime: DurableBudgetRuntime,
    runtime_outbox: RuntimeOutbox,
    admitted: AdmittedExecution,
    budget: BudgetRuntimeContext,
    checkpoint: CommittedOperationCheckpoint,
    worker_commit: Mapping[str, Any],
    attempt_id: str,
    receipt_ref: str,
    payload_digest: str,
    evidence_refs: tuple[str, ...],
) -> tuple[BudgetCharge | None, dict[str, Any] | None]:
    """对账预算证据，或返回不写生命周期的恢复 HOLD。

    受治理的对账失败代表证据不确定，而非 worker 失败。本边界不拥有生命周期权威，
    也不创建终态 Outbox 事件。
    """

    try:
        state = checkpoint.budget_usage_state
        if state in {
            WorkerBudgetUsageCheckpointState.EXACT_ACCEPTED,
            WorkerBudgetUsageCheckpointState.EXACT_REJECTED,
        }:
            if not isinstance(checkpoint.budget_usage, Mapping):
                _blocked("exact worker BudgetUsage checkpoint lost its durable row")
            charge = await budget_runtime.reconcile_worker_checkpoint(
                budget,
                node_id=admitted.node_id,
                attempt_id=attempt_id,
                worker_commit=worker_commit,
                durable_usage=checkpoint.budget_usage,
            )
        elif state is WorkerBudgetUsageCheckpointState.MISSING:
            # 仅在已接管的 lease 和正的持久化耗时都存在时补建计量，防止旧执行被重复扣费。
            durable_elapsed_ms = checkpoint.durable_elapsed_ms
            if (
                type(durable_elapsed_ms) is not int
                or durable_elapsed_ms <= 0
                or checkpoint.lease is None
            ):
                _blocked(
                    "missing worker BudgetUsage has no positive durable elapsed "
                    "time and takeover Lease"
                )
            charge = await budget_runtime.settle_worker_checkpoint(
                budget,
                node_id=admitted.node_id,
                attempt_id=attempt_id,
                worker_commit=worker_commit,
                durable_elapsed_ms=durable_elapsed_ms,
            )
        else:
            _blocked("worker BudgetUsage checkpoint is conflicting")
    except Exception:
        # 已提交 Operation 可能已有 BudgetUsage 但写入或回读响应丢失；普通对账异常只能
        # 形成不确定性 HOLD。取消和进程控制 BaseException 仍按原语义向上传播。
        await runtime_outbox.publish_pending(best_effort=True)
        receipt = build_execution_receipt(
            admitted,
            attempt_id=attempt_id,
            receipt_ref=receipt_ref,
            payload_digest=payload_digest,
            status="RECOVERY_REQUIRED",
            verification_status="NOT_RUN",
            accepted=False,
            reason_code=GOVERNED_OPERATION_RECOVERY_REQUIRED,
            evidence_refs=evidence_refs,
            projection={
                "worker_replayed": False,
                "budget_usage_reconstructed": (
                    checkpoint.budget_usage_state
                    is WorkerBudgetUsageCheckpointState.MISSING
                ),
                "budget": budget.execution_payload(),
                "budget_settlement_state": "UNKNOWN",
                "recovery_state": "HOLD",
            },
        )
        return None, terminal_event(receipt)
    return charge, None


def _blocked(detail: str) -> None:
    """以统一恢复必需错误停止无法由持久化预算事实解释的 checkpoint。"""
    raise GovernedRuntimeError(GOVERNED_OPERATION_RECOVERY_REQUIRED, detail)


__all__ = ["recover_checkpoint_budget"]
