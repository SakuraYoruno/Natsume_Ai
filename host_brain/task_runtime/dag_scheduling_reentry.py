"""以只读方式重建耐久化的并行节点终态结果。"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from .admission import AdmittedExecution
from .coordinator_receipts import terminal_event
from .coordinator_records import _ParallelNodeOutcome, _status
from .errors import GOVERNED_DURABLE_STATE_CONFLICT, GovernedRuntimeError


async def reconstruct_parallel_outcomes(
    *,
    admitted: AdmittedExecution,
    graph: Mapping[str, Any],
    durable_nodes: Mapping[str, Mapping[str, Any]],
    node_execution_projection: Any,
) -> tuple[_ParallelNodeOutcome, ...]:
    """仅重开耐久终态投影，绝不在重入过程中重新执行 worker。

    调度恢复只能基于可重建状态生成统一的并行结算输入；缺少回执即代表持久化
    谱系不完整，应失败关闭而不是以空结果继续，以免把未知副作用伪装成失败。
    """

    # RUNNING/PENDING 等非终态不能被猜测为结果；后续正常调度或专用恢复链会
    # 持有其 lease/检查点并决定如何继续。
    reconstructable = {
        "ACCEPTED",
        "FAILED",
        "RETRY_PENDING",
        "VERIFICATION_FAILED",
        "WAITING_ACCEPTANCE_REVIEW",
        "CANCELLED",
    }
    outcomes: list[_ParallelNodeOutcome] = []
    for binding in admitted.nodes:
        node = durable_nodes.get(binding.node_id)
        if not isinstance(node, Mapping) or _status(node) not in reconstructable:
            continue
        # 以节点派生 admission 重开，确保投影读取继承原图授权但不会获得其他
        # 并行节点的操作身份。
        node_admission = admitted.for_node(binding.node_id)
        receipt = node_execution_projection.terminal_replay(
            node_admission,
            graph=graph,
            node=node,
            allow_incomplete_atomic_graph_outcome=True,
        )
        if inspect.isawaitable(receipt):
            receipt = await receipt
        if receipt is None:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel re-entry cannot reconstruct TaskNode {binding.node_id}",
            )
        outcomes.append(
            _ParallelNodeOutcome.from_terminal_event(
                node_id=binding.node_id,
                event=terminal_event(receipt),
            )
        )
    return tuple(outcomes)


__all__ = ["reconstruct_parallel_outcomes"]
