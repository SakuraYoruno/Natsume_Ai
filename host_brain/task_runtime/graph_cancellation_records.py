"""持久化图取消的纯校验与状态投影规则。

本模块不持有 TaskStore、Outbox、registry、锁或 Coordinator；它只定义取消请求和
最终收束允许如何改变节点及预算状态，供持久化 UoW 在提交前验证。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.contracts.agent_runtime.graph_cancellation import (
    GraphCancellationContractError,
    normalize_graph_cancellation_intent,
)

from .admission import AdmittedExecution
from .coordinator_records import _mapping, _status, _text, _version
from .errors import (
    GOVERNED_BUDGET_STATE_CONFLICT,
    GOVERNED_DURABLE_STATE_CONFLICT,
    GOVERNED_GRAPH_CANCELLATION_PENDING,
    GovernedRuntimeError,
)


def _validated_graph_cancellation_intent(
    admitted: AdmittedExecution,
    record: Mapping[str, Any],
) -> Any:
    """规范化并验证 durable 取消意图仍属于当前已准入图修订。"""
    try:
        intent = normalize_graph_cancellation_intent(
            _mapping(record.get("payload"), field_name="cancellation intent payload")
        )
    except (GraphCancellationContractError, GovernedRuntimeError) as exc:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            f"durable graph cancellation intent is invalid: {exc}",
        ) from exc
    if (
        _text(record.get("intent_ref")) != intent.intent_ref
        or intent.payload["graph_id"] != admitted.graph_id
        or intent.payload["graph_revision"] != admitted.graph_revision
        or _status(record) not in {"REQUESTED", "FINALIZED"}
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "durable graph cancellation identity drifted",
        )
    return intent

_CANCELLATION_TERMINAL_NODE_STATUSES = frozenset(
    {"ACCEPTED", "FAILED", "VERIFICATION_FAILED", "CANCELLED"}
)

_CANCELLATION_DIRECT_NODE_STATUSES = frozenset(
    {"WAITING_APPROVAL", "WAITING_ACCEPTANCE_REVIEW", "RETRY_PENDING"}
)

_CANCELLATION_REQUEST_NODE_STATUSES = frozenset(
    {
        "CREATED",
        "BLOCKED",
        "READY",
        "LEASED",
        "RUNNING",
        "EXECUTED",
        "VERIFYING",
        "VERIFIED",
    }
)

def _cancellation_node_states(
    nodes: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """提取取消前节点的最小状态快照，按 node_id 排序以获得确定性负载。"""
    if not nodes:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "graph cancellation has no TaskNode snapshot",
        )
    return {
        node_id: {
            "status": _status(nodes[node_id]),
            "status_version": _version(nodes[node_id]),
        }
        for node_id in sorted(nodes)
    }

def _cancellation_requested_node_states(
    before: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """投影取消请求后的节点状态：可直接关闭的等待态与需协调的执行态分开处理。"""
    after: dict[str, dict[str, Any]] = {}
    for node_id in sorted(before):
        state = before[node_id]
        status = _text(state.get("status")).upper()
        version = int(state.get("status_version") or 0)
        # 已终态不可回退；审查等待态没有外部执行，直接取消；其余执行态先标记请求。
        if status in _CANCELLATION_TERMINAL_NODE_STATUSES or status == "CANCEL_REQUESTED":
            after[node_id] = {"status": status, "status_version": version}
        elif status in _CANCELLATION_DIRECT_NODE_STATUSES:
            after[node_id] = {
                "status": "CANCELLED",
                "status_version": version + 1,
            }
        elif status in _CANCELLATION_REQUEST_NODE_STATUSES:
            after[node_id] = {
                "status": "CANCEL_REQUESTED",
                "status_version": version + 1,
            }
        else:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"TaskNode {node_id} cannot enter graph cancellation from {status}",
            )
    return after

def _cancellation_final_node_states(
    before: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """投影最终取消：仅已请求或既有终态节点才能收束。"""
    after: dict[str, dict[str, Any]] = {}
    for node_id in sorted(before):
        state = before[node_id]
        status = _text(state.get("status")).upper()
        version = int(state.get("status_version") or 0)
        if status == "CANCEL_REQUESTED":
            after[node_id] = {
                "status": "CANCELLED",
                "status_version": version + 1,
            }
        elif status in _CANCELLATION_TERMINAL_NODE_STATUSES:
            after[node_id] = {"status": status, "status_version": version}
        else:
            raise GovernedRuntimeError(
                GOVERNED_GRAPH_CANCELLATION_PENDING,
                f"TaskNode {node_id} cancellation has not converged from {status}",
            )
    return after

def _cancellation_budget_target(status: str | None) -> str | None:
    """给出图取消时预算的合法目标状态，未知状态必须显式冲突。"""
    if status is None:
        return None
    normalized = _text(status).upper()
    targets = {
        "PENDING": "CANCELLED",
        "ACTIVE": "RELEASED",
        "EXCEEDED": "RELEASED",
        "RELEASED": "RELEASED",
    }
    target = targets.get(normalized)
    if target is None:
        raise GovernedRuntimeError(
            GOVERNED_BUDGET_STATE_CONFLICT,
            f"Budget cannot follow graph cancellation from {normalized}",
        )
    return target

def _cancellation_budget_projection(
    budget: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """抽取可安全公开的预算字段，避免把完整 durable 记录带入终态事件。"""
    if budget is None:
        return None
    return {
        key: budget.get(key)
        for key in (
            "budget_id",
            "scope",
            "status",
            "status_version",
            "limits",
            "consumed",
            "remaining",
            "exceeded_dimensions",
        )
        if key in budget
    }

__all__ = [
    "_cancellation_budget_projection",
    "_cancellation_budget_target",
    "_cancellation_final_node_states",
    "_cancellation_node_states",
    "_cancellation_requested_node_states",
    "_validated_graph_cancellation_intent",
]
