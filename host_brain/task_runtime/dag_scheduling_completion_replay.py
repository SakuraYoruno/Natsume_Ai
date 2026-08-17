"""精确已完成 Internal 检查点重放的只读守卫。"""

from __future__ import annotations

import inspect
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from .admission import AdmittedExecution
from .internal_checkpoint_budget_rejection import requires_internal_terminal_bundle


async def is_completed_internal_checkpoint_without_parallel_decision(
    *,
    task_store: Any,
    admitted: AdmittedExecution,
    command: HostCommand,
    graph_status: str,
) -> bool:
    """仅允许精确的恢复完成 Internal 路线重放节点。

    图虽为 ``COMPLETED`` 并不足以重放：若已有并行成功决策，说明图终态已由
    正常聚合链结算，重放会越过该决策或重复交付内部结果。只有没有该决策的
    Internal 终态 bundle 才属于历史检查点恢复的兼容入口。
    """

    if graph_status != "COMPLETED" or not requires_internal_terminal_bundle(
        admitted,
        command,
    ):
        return False
    # 只读查询用于区分“恢复完成”与“已被并行结算”；不在守卫中修补状态，
    # 以免一次资格判断意外改变后续调度的幂等结果。
    decision = task_store.get_latest_parallel_success_decision(
        admitted.graph_id,
        admitted.graph_revision,
    )
    if inspect.isawaitable(decision):
        decision = await decision
    return decision is None


__all__ = ["is_completed_internal_checkpoint_without_parallel_decision"]
