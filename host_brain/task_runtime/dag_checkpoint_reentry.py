"""已提交检查点重入后的规范图终态结算。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .admission import AdmittedExecution
from .budget_runtime import BudgetRuntimeContext
from .coordinator_records import _ParallelNodeOutcome, _text
from .errors import GOVERNED_DURABLE_STATE_CONFLICT, GovernedRuntimeError
from .internal_checkpoint_budget_rejection import is_internal_budget_terminal
from .parallel_settlement_runtime import ParallelSettlementRuntime


async def settle_committed_checkpoint_reentry(
    *,
    admitted: AdmittedExecution,
    recovery_admission: AdmittedExecution,
    command: Any,
    recovery_terminal: Mapping[str, Any],
    budget: BudgetRuntimeContext,
    parallel_success_enabled: bool,
    parallel_settlement: ParallelSettlementRuntime,
) -> tuple[BudgetRuntimeContext, dict[str, Any] | None]:
    """在图级结算前保留子节点检查点终态，不把它提前暴露为全图结论。

    单节点图、需要恢复的回执以及内部预算终态可直接返回；其余并行图必须交给
    共享结算器，将重入节点重新并入所有节点结果与预算状态，避免恢复路径绕过
    并行成功/失败的统一仲裁。
    """

    terminal = dict(recovery_terminal)
    receipt = terminal.get("execution_receipt")
    if not isinstance(receipt, Mapping):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "committed checkpoint recovery terminal has no receipt",
        )
    # ``RECOVERY_REQUIRED`` 仍是待处理的控制信号而非可聚合结果；把它交给并行
    # 结算会错误消耗预算或将未完成的恢复标记为最终失败。
    if (
        _text(receipt.get("status")).upper() == "RECOVERY_REQUIRED"
        or len(admitted.nodes) == 1
        or (
            parallel_success_enabled
            and is_internal_budget_terminal(admitted, command, receipt)
        )
    ):
        return budget, terminal

    # 重新构造成标准并行结果，由同一 settlement runtime 处理并发节点的顺序、
    # 幂等与剩余预算，而不是在检查点分支复制图状态机规则。
    outcome = _ParallelNodeOutcome.from_terminal_event(
        node_id=recovery_admission.node_id,
        event=terminal,
    )
    return await parallel_settlement.resolve_parallel_outcomes(
        admitted=admitted,
        outcomes=(outcome,),
        budget=budget,
    )


__all__ = ["settle_committed_checkpoint_reentry"]
