"""图运行时的统一失败转移层。

节点不能自行吞掉依赖故障、超时或执行异常；本模块把这些结果规范为轨迹和
降级状态，再按预先声明的回退边继续。只有结构性硬失败和人工审核闸门会停止
图，从而让调用方能区分“可恢复服务退化”与“不应继续执行”。
"""

from __future__ import annotations

from typing import Any

from host_brain.core.errors import (
    ORCH_DEPENDENCY_UNAVAILABLE,
    ORCH_NODE_MISSING,
    ORCH_TIMEOUT,
)
from host_brain.integrations.graph_contract import (
    GraphState,
    STATUS_AIRLOCK_PENDING,
    STATUS_DEGRADED,
    STATUS_FAILED,
)


# ---------------------------------------------------------------------------
# Fallback routing
# ---------------------------------------------------------------------------

def pick_fallback_node(*, state: GraphState, node_id: str) -> str | None:
    """仅从已编译的回退图取下一跳；缺失配置返回 ``None``，不猜测替代节点。"""
    fallback_map = dict(state.get("fallback_map") or {})
    next_node = str(fallback_map.get(node_id) or "").strip()
    return next_node or None


def pick_next_node(*, output: dict[str, Any], default_next: str | None) -> str | None:
    """优先消费执行器认可的显式下一跳，否则回到声明式主边。"""
    explicit = str(output.get("__next_node__") or "").strip()
    if explicit:
        return explicit
    return str(default_next) if default_next else None


# ---------------------------------------------------------------------------
# Degradation handlers
# ---------------------------------------------------------------------------

def handle_dependency_unavailable(
    *,
    state: GraphState,
    node_id: str,
    handler_id: str,
    detail: str,
) -> GraphState:
    """记录依赖不可用并转入回退链，避免把被跳过的节点伪装成成功。"""
    trace = list(state.get("trace") or [])
    trace.append(
        {
            "node_id": node_id,
            "handler_id": handler_id,
            "status": "SKIPPED",
            "reason_code": ORCH_DEPENDENCY_UNAVAILABLE,
            "detail": detail,
        }
    )
    return {
        **state,
        "trace": trace,
        "status": STATUS_DEGRADED,
        "reason_code": ORCH_DEPENDENCY_UNAVAILABLE,
        "detail": detail,
        "pending_node_id": pick_fallback_node(state=state, node_id=node_id),
    }


def handle_timeout(
    *,
    state: GraphState,
    node_id: str,
    handler_id: str,
) -> GraphState:
    """将单节点超时写入轨迹后转入回退链；全图策略超时由上层硬终止。"""
    detail = f"node timeout: {node_id}"
    trace = list(state.get("trace") or [])
    trace.append(
        {
            "node_id": node_id,
            "handler_id": handler_id,
            "status": "TIMEOUT",
            "reason_code": ORCH_TIMEOUT,
            "detail": detail,
        }
    )
    return {
        **state,
        "trace": trace,
        "status": STATUS_DEGRADED,
        "reason_code": ORCH_TIMEOUT,
        "detail": detail,
        "pending_node_id": pick_fallback_node(state=state, node_id=node_id),
    }


def handle_execution_error(
    *,
    state: GraphState,
    node_id: str,
    handler_id: str,
    error: Exception,
) -> GraphState:
    """把未分类执行异常转换为可审计降级，保留节点身份而不在此层泄露堆栈。"""
    detail = f"node execution failed: {node_id}: {error}"
    trace = list(state.get("trace") or [])
    trace.append(
        {
            "node_id": node_id,
            "handler_id": handler_id,
            "status": "FAILED",
            "reason_code": ORCH_DEPENDENCY_UNAVAILABLE,
            "detail": detail,
        }
    )
    return {
        **state,
        "trace": trace,
        "status": STATUS_DEGRADED,
        "reason_code": ORCH_DEPENDENCY_UNAVAILABLE,
        "detail": detail,
        "pending_node_id": pick_fallback_node(state=state, node_id=node_id),
    }


def fail_hard(
    state: GraphState,
    *,
    reason_code: str,
    detail: str,
) -> GraphState:
    """用于无法安全解释的结构性失败：清空待执行节点，禁止再进入任何回退边。"""
    return {
        **state,
        "status": STATUS_FAILED,
        "reason_code": reason_code,
        "detail": detail,
        "pending_node_id": None,
    }


def set_finish(state: GraphState) -> GraphState:
    """结束正常或已由上层决定终止的图；只移除待执行节点，不重写既有诊断。"""
    return {
        **state,
        "pending_node_id": None,
    }


# ---------------------------------------------------------------------------
# M35-J：AIRLOCK 暂停处理（Route Commitment Rule）
# ---------------------------------------------------------------------------

def handle_airlock_pending(
    *,
    state: GraphState,
    node_id: str,
) -> GraphState:
    # AIRLOCK 是等待人工审核的暂停，不是故障回退；因此不能借 fallback 绕过审核。
    """AIRLOCK 暂停处理：当 committed_route_token = GOV_AIRLOCK_REVIEW 时触发。

    将图状态设置为 AIRLOCK_PENDING，记录到 trace，安全终止图执行。
    不触发 fallback 链路（与 GOV_FAIL_FALLBACK 行为区分）。

    Args:
        state: 当前图状态
        node_id: 触发 AIRLOCK 的节点 ID

    Returns:
        更新后的图状态（status = AIRLOCK_PENDING，pending_node_id = None）
    """
    detail = f"AIRLOCK 暂停：等待人工审核，节点={node_id}"
    trace = list(state.get("trace") or [])
    trace.append(
        {
            "node_id": node_id,
            "status": STATUS_AIRLOCK_PENDING,
            "reason_code": "GOV_AIRLOCK_REVIEW",
            "detail": detail,
        }
    )
    return {
        **state,
        "trace": trace,
        "status": STATUS_AIRLOCK_PENDING,
        "reason_code": "GOV_AIRLOCK_REVIEW",
        "detail": detail,
        "pending_node_id": None,  # 安全终止，不进入 fallback
    }
