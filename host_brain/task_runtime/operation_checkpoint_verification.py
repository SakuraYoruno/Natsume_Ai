"""已提交 TaskNode 验证决定的只读重新打开。

节点已处于 VERIFIED 时，恢复路径必须读取原先随版本写入的验证决定，而不是再次调用
验证器；这样可保持原证据结论稳定，并避免恢复造成不一致的重复验证。
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from .admission import AdmittedExecution
from .coordinator_records import _mapping, _version
from .ports import TaskStorePort


async def reopen_durable_verification(
    *,
    task_store: TaskStorePort,
    admitted: AdmittedExecution,
    current_node: Mapping[str, Any],
) -> dict[str, Any]:
    """按当前节点版本读取并规范化其持久化验证结果。"""
    raw = task_store.get_node_verification_decision(
        admitted.graph_id,
        admitted.graph_revision,
        admitted.node_id,
        node_version_after=_version(current_node),
    )
    if inspect.isawaitable(raw):
        raw = await raw
    decision = _mapping(raw, field_name="durable verification decision")
    return _mapping(
        decision.get("verification_result"),
        field_name="durable verification result",
    )


__all__ = ["reopen_durable_verification"]
