"""被拒绝的耐久审查继续执行时的失败关闭式守卫。

调度器可在不签发新 worker 授权的情况下，为精确匹配的已拒绝或已过期审查写入终态。
本模块刻意只承担身份与状态校验：它既不授予权限，也不直接转换耐久状态，避免恢复
入口借审查拒绝越过正常执行授权。
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from typing import Any

from .errors import GOVERNED_DURABLE_STATE_CONFLICT, GovernedRuntimeError


_WAITING_REVIEW_STATUSES = frozenset(
    {"WAITING_APPROVAL", "WAITING_ACCEPTANCE_REVIEW"}
)
_ACTIVE_EXECUTION_STATUSES = frozenset(
    {"LEASED", "RUNNING", "EXECUTED", "VERIFYING", "VERIFIED"}
)


def normalize_denied_review_node(
    value: str | None,
    expected_node_ids: Set[str],
    grant_refs: Mapping[str, str],
) -> str | None:
    """仅接受无 worker 授权且精确属于准入图的审查终态目标。

    ``grant_refs`` 非空说明路径已转入执行态；此时继续按拒绝审查收口会把活跃任务
    错误终结，因此必须拒绝而非猜测调用者意图。
    """

    node_id = str(value or "").strip()
    if not node_id:
        return None
    if node_id not in expected_node_ids or grant_refs:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "denied Review continuation must target one admitted node without worker grants",
        )
    return node_id


def assert_denied_review_waiting_node(
    node_id: str | None,
    *,
    statuses: Mapping[str, str],
) -> None:
    """在执行路径发生任何变更前拒绝过期或被重新定向的声明。

    除目标节点外的活动节点意味着图仍有执行所有者，不能由单个审查拒绝提前关闭。
    """

    if not node_id:
        return
    if (
        statuses.get(node_id) not in _WAITING_REVIEW_STATUSES
        or any(
            status in _ACTIVE_EXECUTION_STATUSES
            for candidate_id, status in statuses.items()
            if candidate_id != node_id
        )
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "denied Review continuation is no longer an exact waiting TaskNode",
        )


def assert_denied_review_gate(
    node_id: str | None,
    *,
    disposition: str,
    phase: str,
) -> None:
    """要求 ReviewStore 仍报告同一终态拒绝，防止旧恢复请求覆盖新审查结论。"""

    if node_id and str(disposition or "").strip() != "REVIEW_DENIED":
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            f"denied Review continuation no longer has a terminal {phase} decision",
        )


def build_review_terminal_projection(
    *,
    gate: Any,
    budget: Any,
    settlement: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """构造等待态与终态共用的回执投影，保留审查、预算与结算证据。"""

    projection = {
        "review": gate.to_dict(),
        "budget": budget.execution_payload(),
    }
    if settlement is not None:
        projection["review_denial_settlement"] = dict(settlement)
    return projection


__all__ = [
    "assert_denied_review_gate",
    "assert_denied_review_waiting_node",
    "build_review_terminal_projection",
    "normalize_denied_review_node",
]
