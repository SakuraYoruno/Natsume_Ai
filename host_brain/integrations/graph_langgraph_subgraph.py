"""LangGraph 原生 Subgraph + 字段保护适配器（FIX-5.3 彻底迁移，无兼容开关）。

直接使用 LangGraph 原生 StateGraph 构建子图，不再有任何环境变量开关或降级路径。
SUBGRAPH_PROTECTED_FIELDS 字段保护通过 subgraph_adapter() 隔离层保留。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langchain_core.runnables import RunnableLambda

from host_brain.integrations.graph_contract import (
    SUBGRAPH_PROTECTED_FIELDS,
    build_subgraph_commit_summary,
)

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SubgraphState：子图独立状态（不含受保护字段）
# ---------------------------------------------------------------------------

class SubgraphState(TypedDict):
    """子图独立状态（不包含 committed_* 等受保护字段）。

    从类型层面防止子图写入受保护字段。
    """
    payload: dict[str, Any]
    subgraph_trace: list[dict[str, Any]]
    request_id: str
    graph_id: str
    timeout_ms: int


# ---------------------------------------------------------------------------
# FIX-5.3：build_governed_subgraph()
# ---------------------------------------------------------------------------

def build_governed_subgraph(
    subgraph_id: str,
    node_fns: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]],
    *,
    edges: list[tuple[str, str]] | None = None,
    entry_node: str | None = None,
    evidence_plane: Any = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """构建带 Governor 字段保护的 LangGraph 子图适配器（FIX-5.3）。

    子图使用独立的 SubgraphState（不包含 committed_* 字段），
    通过 subgraph_adapter() 隔离子图 state，防止子图写入受保护字段。

    Args:
        subgraph_id: 子图唯一 ID（用于 Evidence Plane 追踪）
        node_fns: 节点函数字典 {node_id: fn(payload, context) -> dict}
        edges: 节点间的有向边列表 [(from_node, to_node), ...]（可选，None 时按 node_fns 顺序线性连接）
        entry_node: 入口节点 ID（可选，None 时使用 node_fns 的第一个 key）
        evidence_plane: Evidence Plane 实例（可选）

    Returns:
        subgraph_adapter 函数：接受完整 GraphState，返回更新后的 GraphState 片段

    Raises:
        ValueError: subgraph_id 为空或 node_fns 为空
    """
    if not str(subgraph_id or "").strip():
        raise ValueError("subgraph_id 不能为空")
    if not node_fns:
        raise ValueError("node_fns 不能为空")

    subgraph_id = str(subgraph_id).strip()
    node_ids = list(node_fns.keys())
    resolved_entry = str(entry_node or node_ids[0]).strip()

    # 构建 LangGraph 原生子图
    compiled_sub = _build_langgraph_subgraph(
        subgraph_id=subgraph_id,
        node_fns=node_fns,
        node_ids=node_ids,
        edges=edges,
        entry_node=resolved_entry,
    )

    def subgraph_adapter(state: dict[str, Any]) -> dict[str, Any]:
        """适配器：隔离子图 state，防止子图写入受保护字段。"""
        sub_state: SubgraphState = {
            "payload": dict(state.get("payload") or {}),
            "subgraph_trace": list(state.get("subgraph_trace") or []),
            "request_id": str(state.get("request_id") or ""),
            "graph_id": str(state.get("graph_id") or ""),
            "timeout_ms": int(state.get("timeout_ms") or 5000),
        }

        sub_result = dict(compiled_sub.invoke(sub_state) or {})

        # 生成 subgraph_commit_summary
        subgraph_trace = list(sub_result.get("subgraph_trace") or sub_state["subgraph_trace"])
        summary = build_subgraph_commit_summary(
            subgraph_id=subgraph_id,
            subgraph_trace=subgraph_trace,
        )

        # 写入 Evidence Plane（如果提供）
        if evidence_plane is not None:
            try:
                evidence_plane.write_trace(
                    request_id=sub_state["request_id"],
                    graph_id=sub_state["graph_id"],
                    trace=subgraph_trace,
                )
            except Exception:
                pass  # 静默降级，不影响子图执行

        # 只将非受保护字段合并回主图 state（核心安全约束）
        safe_payload = {
            k: v for k, v in (sub_result.get("payload") or {}).items()
            if k not in SUBGRAPH_PROTECTED_FIELDS
        }
        safe_payload["subgraph_commit_summary"] = summary

        return {
            "payload": safe_payload,
            "subgraph_trace": subgraph_trace,
        }

    return subgraph_adapter


def _build_langgraph_subgraph(
    *,
    subgraph_id: str,
    node_fns: dict[str, Callable],
    node_ids: list[str],
    edges: list[tuple[str, str]] | None,
    entry_node: str,
) -> Any:
    """构建 LangGraph 原生子图（直接构建，无降级路径）。

    Returns:
        编译后的 LangGraph 子图
    """
    sub = StateGraph(SubgraphState)

    for node_id, fn in node_fns.items():
        def _make_node_fn(node_fn: Callable, nid: str) -> Callable:
            def _wrapped(sub_state: SubgraphState) -> SubgraphState:
                ts_ms = int(time.time() * 1000)
                payload = dict(sub_state.get("payload") or {})
                trace = list(sub_state.get("subgraph_trace") or [])
                try:
                    output = node_fn(payload, {
                        "request_id": str(sub_state.get("request_id") or ""),
                        "node_id": nid,
                        "subgraph_id": subgraph_id,
                        "timeout_ms": int(sub_state.get("timeout_ms") or 5000),
                    })
                    # 剥离受保护字段
                    safe_output = {k: v for k, v in (output or {}).items() if k not in SUBGRAPH_PROTECTED_FIELDS}
                    payload.update(safe_output)
                    trace.append({"node_id": nid, "status": "OK", "ts_ms": ts_ms, "reason_code": None})
                except Exception as exc:
                    trace.append({"node_id": nid, "status": "FAILED", "ts_ms": ts_ms,
                                  "reason_code": "SUBGRAPH_NODE_ERROR", "detail": str(exc)})
                return {**sub_state, "payload": payload, "subgraph_trace": trace}
            return _wrapped

        sub.add_node(node_id, RunnableLambda(_make_node_fn(fn, node_id)))

    # 添加边
    if edges:
        sub.add_edge(START, entry_node)
        for from_node, to_node in edges:
            sub.add_edge(from_node, to_node)
        has_outgoing = {from_node for from_node, _ in edges}
        for nid in node_ids:
            if nid not in has_outgoing:
                sub.add_edge(nid, END)
    else:
        sub.add_edge(START, node_ids[0])
        for i in range(len(node_ids) - 1):
            sub.add_edge(node_ids[i], node_ids[i + 1])
        sub.add_edge(node_ids[-1], END)

    compiled = sub.compile()
    _logger.info(
        "build_governed_subgraph[%s]: LangGraph 原生子图构建成功，节点数=%d",
        subgraph_id, len(node_ids),
    )
    return compiled


__all__ = [
    "SubgraphState",
    "build_governed_subgraph",
]
