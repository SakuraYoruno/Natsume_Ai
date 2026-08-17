"""执行前 Review 挂起的窄范围耐久结算。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .admission import AdmittedExecution
from .budget_runtime import BudgetRuntimeContext
from .coordinator_records import _ParallelNodeOutcome, _text
from .errors import GOVERNED_DURABLE_STATE_CONFLICT, GovernedRuntimeError


async def settle_waiting_approval(
    *,
    admitted: AdmittedExecution,
    receipt_event: Mapping[str, Any],
    budget: BudgetRuntimeContext,
    parallel_settlement: Any,
) -> tuple[BudgetRuntimeContext, dict[str, Any]]:
    """为被拒绝的执行前门禁持久化唯一阻塞结算。

    拒绝不是普通 worker 失败：它必须作为标准并行终态交给共享结算器，才能同步
    收束预算、图状态与后续节点资格，防止局部 Review 分支遗漏图级阻塞事实。
    """

    receipt = receipt_event.get("execution_receipt")
    node_id = _text(receipt.get("node_id")) if isinstance(receipt, Mapping) else ""
    if not node_id:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "pre-execution review outcome has no TaskNode identity",
        )
    # receipt 中的 node_id 是耐久审查结果的唯一节点来源；不要使用调用方传参，
    # 否则可把一个节点的拒绝结算到另一个并行节点。
    outcome = _ParallelNodeOutcome.from_terminal_event(
        node_id=node_id,
        event=receipt_event,
    )
    budget, terminal = await parallel_settlement.resolve_parallel_outcomes(
        admitted=admitted,
        outcomes=(outcome,),
        budget=budget,
    )
    # 该分支预期一定产生阻塞终态；返回空值说明结算器与 Review 语义失配，
    # 继续调度会绕过已拒绝的门禁。
    if terminal is None:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "pre-execution review did not produce a blocking settlement",
        )
    return budget, terminal


__all__ = ["settle_waiting_approval"]
