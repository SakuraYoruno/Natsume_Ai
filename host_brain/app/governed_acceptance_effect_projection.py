"""将 Acceptance 后的 Effect 调度状态投影回 Host 审计引用。

节点执行成功并不意味着外部效果已经发生：只有 Task Runtime 接受图终态后，
本模块才请求共享 dispatcher 领取效果。它不修改终态决定，仅补充可观察的
前台交付/后台任务状态，使客户端能区分“回复已交付”与“仍在恢复”的情况。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_REALTIME_REPLY_PREFIX_TYPES = frozenset(
    {"conversation.user_turn", "surface.reply_delivery"}
)


async def dispatch_accepted_effects(
    event: Mapping[str, Any],
    dispatcher: Any,
) -> dict[str, Any]:
    """仅为已接受的精确图调度效果，并把结果以审计元数据回填。

    缺少有效 receipt、图身份或 dispatcher 时保持原事件不变；这使重放和
    兼容路径不会凭推测终态触发副作用。优先选择交互式调度入口，以便前台
    ``surface.reply_delivery`` 可先完成，剩余效果则由恢复机制接手。
    """
    projected = dict(event)
    receipt = projected.get("execution_receipt")
    if dispatcher is None or not isinstance(receipt, Mapping):
        return projected
    if (
        not bool(receipt.get("accepted"))
        or str(receipt.get("status") or "") != "ACCEPTED"
    ):
        return projected
    graph_id = str(receipt.get("graph_id") or "").strip()
    graph_revision = int(receipt.get("graph_revision") or 0)
    if not graph_id or graph_revision < 1:
        return projected
    interactive_dispatch = getattr(dispatcher, "dispatch_interactive_graph", None)
    dispatch = (
        interactive_dispatch
        if callable(interactive_dispatch)
        else dispatcher.dispatch_graph
    )
    results = await dispatch(
        graph_id=graph_id,
        graph_revision=graph_revision,
    )
    statuses = [str(row.get("status") or "") for row in results]
    delivery_rows = [
        row
        for row in results
        if str(row.get("effect_type") or "") == "surface.reply_delivery"
    ]
    delivery_committed = bool(delivery_rows) and all(
        str(row.get("status") or "") == "COMMITTED" for row in delivery_rows
    )
    recovery_pending = sum(
        bool(row.get("recovery_pending"))
        or str(row.get("dispatch_state") or "") == "RECOVERY_PENDING"
        or str(row.get("status") or "") == "CLAIMED"
        or (
            str(row.get("status") or "") == "PENDING"
            and int(row.get("attempt_count") or 0) > 0
        )
        for row in results
    )
    audit_refs = dict(projected.get("audit_refs") or {})
    audit_refs["acceptance_effects"] = {
        "count": len(results),
        "committed": statuses.count("COMMITTED"),
        "failed": statuses.count("FAILED"),
        "pending": sum(
            status not in {"COMMITTED", "FAILED"} for status in statuses
        ),
        "recovery_pending": recovery_pending,
        "dispatch_status": _dispatch_status(
            statuses=statuses,
            recovery_pending=recovery_pending,
            delivery_committed=delivery_committed,
        ),
    }
    if delivery_rows:
        audit_refs["acceptance_effects"].update(
            {
                "delivery_committed": delivery_committed,
                "background_pending": sum(
                    str(row.get("effect_type") or "")
                    not in _REALTIME_REPLY_PREFIX_TYPES
                    and str(row.get("status") or "")
                    not in {"COMMITTED", "FAILED"}
                    for row in results
                ),
            }
        )
    projected["audit_refs"] = audit_refs
    return projected


def _dispatch_status(
    *,
    statuses: list[str],
    recovery_pending: int,
    delivery_committed: bool,
) -> str:
    """把逐项效果状态压缩成稳定摘要，保留已交付但后台失败/待恢复的差异。"""
    if delivery_committed:
        if "FAILED" in statuses:
            return "DELIVERY_COMMITTED_BACKGROUND_FAILED"
        if any(status not in {"COMMITTED", "FAILED"} for status in statuses):
            return "DELIVERY_COMMITTED_BACKGROUND_PENDING"
        return "COMMITTED"
    if "FAILED" in statuses:
        return "FAILED"
    if recovery_pending:
        return "RECOVERY_PENDING"
    if statuses and all(status == "COMMITTED" for status in statuses):
        return "COMMITTED"
    if statuses:
        return "PENDING"
    return "NO_EFFECTS"


__all__ = ["dispatch_accepted_effects"]
