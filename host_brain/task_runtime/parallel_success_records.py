"""并行成功结算使用的纯冻结路由授权记录。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from .admission import AdmittedTaskNode
from .coordinator_records import _text
from .errors import GOVERNED_DURABLE_STATE_CONFLICT, GovernedRuntimeError
from .worker_registry import GovernedWorkerRoute


def _projection_route_authority(
    *,
    binding: AdmittedTaskNode,
    work_order: Any,
    command: HostCommand,
) -> tuple[str, str]:
    """从冻结图与 WorkOrder 事实解析 worker 投影路由。

    并行成功重开投影时，route/target 不能由当前调用参数或投影内容决定；它们
    必须同时吻合节点准入与计划谱系，才能阻止跨节点、跨 target 的结果嫁接。
    """

    try:
        frozen = GovernedWorkerRoute.parse(binding.worker_route)
    except Exception as exc:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            f"parallel success TaskNode route is invalid: {binding.node_id}",
        ) from exc
    expected_target = _text(frozen.target_id or command.target_id)
    if not expected_target:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            f"parallel success TaskNode target is missing: {binding.node_id}",
        )

    # 计划图额外保存节点绑定。若该谱系存在，就必须精确命中一条而不能回退到
    # 宽泛的 command target，因为计划的每个节点可能有不同执行器。
    metadata = getattr(work_order, "metadata", {})
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    planning_lineage = metadata.get("planning_lineage")
    planning_lineage = (
        dict(planning_lineage) if isinstance(planning_lineage, Mapping) else {}
    )
    raw_bindings = planning_lineage.get("node_bindings")
    if isinstance(raw_bindings, list) and raw_bindings:
        matches = [
            dict(candidate)
            for candidate in raw_bindings
            if isinstance(candidate, Mapping)
            and _text(candidate.get("node_id")) == binding.node_id
        ]
        if len(matches) != 1:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel success WorkOrder node binding is ambiguous: {binding.node_id}",
            )
        work_order_route = _text(matches[0].get("worker_route")).lower()
        work_order_target = _text(matches[0].get("target_id"))
        if (
            work_order_route != frozen.kind
            or work_order_target != expected_target
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel success WorkOrder route binding drifted: {binding.node_id}",
            )
    return frozen.kind, expected_target


__all__ = ["_projection_route_authority"]
# End of module.
