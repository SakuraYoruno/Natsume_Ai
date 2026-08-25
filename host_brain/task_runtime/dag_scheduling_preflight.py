"""耐久 DAG 调度的失败关闭准入与就绪预检阶段。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from natsume_contracts.task.v1 import HostCommand
from host_brain.contracts.agent_runtime.task_graph import TaskGraphCompletionPolicy

from .admission import AdmittedExecution
from .budget_runtime import BudgetRuntimeContext, DurableBudgetRuntime
from .dag_scheduling_records import _validated_node_grant_refs
from .dag_scheduling_review_denial import normalize_denied_review_node
from .errors import (
    GOVERNED_ADMISSION_INVALID,
    GOVERNED_DURABLE_STATE_CONFLICT,
    GovernedRuntimeError,
)
from .graph_cancellation_runtime import GraphCancellationRuntime
from .ports import TaskStorePort
from host_brain.core.runtime_helpers import maybe_await as _maybe_await


@dataclass(frozen=True, slots=True)
class DagSchedulingPreflight:
    """一轮调度经验证的不可变输入，以及唯一可复用的 grant 映射。

    预检把取消、审查拒绝与共享预算的事实集中冻结，避免后续各节点在并发执行时
    对同一图作出彼此不一致的入口判断。
    """

    grant_refs: dict[str, str]
    parallel_success_grant_refs: dict[str, str]
    node_ids: frozenset[str]
    denied_review_node: str | None
    parallel_success_enabled: bool
    cancellation_event: dict[str, Any] | None
    budget: BudgetRuntimeContext | None


async def prepare_dag_scheduling_preflight(
    *,
    admitted: AdmittedExecution,
    command: HostCommand,
    work_order: Any,
    node_grant_refs: Mapping[str, str] | None,
    denied_review_node_id: str | None,
    store: TaskStorePort,
    budgets: DurableBudgetRuntime,
    graph_cancellations: GraphCancellationRuntime,
    supports_completion_policy: Callable[[str | None], bool],
) -> DagSchedulingPreflight:
    """一次性校验准入、存储就绪、取消状态与共享 Budget。

    调度器调用此函数后只消费返回的快照；任何缺失的持久化能力或原子成功契约
    都在启动 worker 前拒绝，不能运行中静默降级为另一套图完成语义。
    """

    # 准入快照是本轮调度的身份锚点：后续节点执行只可消费这里已经核对过的
    # command_id 与图版本，不能把调用方后来传入的对象当成新的授权来源。
    admitted.validate()
    if not isinstance(command, HostCommand) or command.command_id != admitted.command_id:
        raise GovernedRuntimeError(
            GOVERNED_ADMISSION_INVALID,
            "runtime command differs from admitted command",
        )
    grant_refs = _validated_node_grant_refs(admitted, node_grant_refs)
    # 平行成功结算拥有独立副本，防止后续节点局部处理意外修改其需要重开授权的
    # grant 映射。
    parallel_success_grant_refs = dict(grant_refs)
    node_ids = frozenset(binding.node_id for binding in admitted.nodes)
    denied_review_node = normalize_denied_review_node(
        denied_review_node_id,
        node_ids,
        grant_refs,
    )
    parallel_success_enabled = (
        admitted.completion_policy
        == TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1.value
    )
    if parallel_success_enabled and (
        not supports_completion_policy(admitted.completion_policy)
        or not admitted.result_node_id
        or (not denied_review_node and set(grant_refs) != node_ids)
    ):
        # 原子图成功需要完整节点授权、投影读取和效果能力；缺任一项就拒绝，
        # 不能降级成“各节点完成即成功”，否则会改变图契约承诺的可见终态。
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "TaskGraph requires atomic success semantics that this runtime cannot execute",
        )
    await _maybe_await(store.assert_dag_scheduling_ready())
    await _maybe_await(store.assert_parallel_settlement_decision_ready())
    await _maybe_await(store.assert_graph_cancellation_ready())
    if parallel_success_enabled:
        await _maybe_await(store.assert_parallel_success_decision_ready())
    # 取消优先于预算预留：已请求取消的图不应为了准备执行而再次占用共享额度。
    cancellation_event = (
        await graph_cancellations.continue_requested_graph_cancellation(
            admitted=admitted,
            command=command,
            work_order=work_order,
        )
    )
    budget = (
        None
        if cancellation_event is not None
        # ensure_active 返回的是持久 Budget 权威，而非单纯余额读取；它可同时
        # 处理恢复后的状态收紧，供这一整轮节点调度复用。
        else await budgets.ensure_active(admitted=admitted, work_order=work_order)
    )
    return DagSchedulingPreflight(
        grant_refs=grant_refs,
        parallel_success_grant_refs=parallel_success_grant_refs,
        node_ids=node_ids,
        denied_review_node=denied_review_node,
        parallel_success_enabled=parallel_success_enabled,
        cancellation_event=cancellation_event,
        budget=budget,
    )




__all__ = ["DagSchedulingPreflight", "prepare_dag_scheduling_preflight"]
