"""耐久 DAG 调度使用的纯校验记录。"""

from __future__ import annotations

from collections.abc import Mapping

from .admission import AdmittedExecution
from .coordinator_records import _text
from .errors import GOVERNED_ASSIGNMENT_CONFLICT, GovernedRuntimeError


def _validated_node_grant_refs(
    admitted: AdmittedExecution,
    value: Mapping[str, str] | None,
) -> dict[str, str]:
    """验证 node grant 映射精确覆盖当前准入图，不允许子集或额外节点。

    并行调度会把每个节点的 grant 作为后续 Preview 重开依据；宽松接受映射会使
    被遗漏的节点无授权执行，或让调用方携带另一张图的 grant 影响当前运行。
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise GovernedRuntimeError(
            GOVERNED_ASSIGNMENT_CONFLICT,
            "node_grant_refs must be an exact TaskNode mapping",
        )
    # 先规范化文本再比较集合，同时保留长度检查，借此拒绝空键/空值在过滤后
    # 伪装为一个覆盖完整的较小映射。
    refs = {
        _text(node_id): _text(grant_ref)
        for node_id, grant_ref in value.items()
        if _text(node_id) and _text(grant_ref)
    }
    expected = {binding.node_id for binding in admitted.nodes}
    if set(refs) != expected or len(refs) != len(value):
        raise GovernedRuntimeError(
            GOVERNED_ASSIGNMENT_CONFLICT,
            "node_grant_refs must cover the admitted TaskGraph exactly",
        )
    return refs


__all__ = ["_validated_node_grant_refs"]
# End of module.
