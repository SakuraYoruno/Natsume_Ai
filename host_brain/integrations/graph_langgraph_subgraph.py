"""LangGraph 原生 Subgraph + 字段保护适配器（FIX-5.3）。

这是从自研 compile_governed_subgraph() 迁移到 LangGraph 原生 Subgraph 的过渡模块。

设计原则（FIX-PLAN §5.3）：
- 保留 SUBGRAPH_PROTECTED_FIELDS 黑名单（FIX-3.2 的修复是前提）
- 子图使用独立的 SubgraphState（不包含 committed_* 等受保护字段）
- 通过 subgraph_adapter() 隔离子图 state，防止子图写入受保护字段
- 利用 LangGraph 原生子图的可视化、调试工具、条件边能力
- 通过 LANGGRAPH_SUBGRAPH_ENABLED 环境变量控制是否启用（默认 False，渐进迁移）

使用方式：
    from host_brain.integrations.graph_langgraph_subgraph import (
        build_governed_subgraph,
        is_langgraph_subgraph_enabled,
    )

    if is_langgraph_subgraph_enabled():
        subgraph_fn = build_governed_subgraph(
            subgraph_id="intent_agent_subgraph",
            node_fns={
                "core_reasoner_primary": core_reasoner_fn,
                "planner_constraints": planner_fn,
                "tool_planner": tool_planner_fn,
            },
            edges=[
                ("core_reasoner_primary", "planner_constraints"),
                ("planner_constraints", "tool_planner"),
            ],
        )
        graph.add_node("intent_subgraph", subgraph_fn)
    else:
        # 降级：使用自研 compile_governed_subgraph()
        subgraph = compile_governed_subgraph(...)
        graph.add_node("intent_subgraph", subgraph.invoke)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, TypedDict

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 环境变量控制
# ---------------------------------------------------------------------------

_ENV_ENABLED = "LANGGRAPH_SUBGRAPH_ENABLED"


def is_langgraph_subgraph_enabled() -> bool:
    """检查是否启用 LangGraph 原生 Subgraph（通过环境变量控制）。

    Returns:
        True 表示启用 LangGraph 原生 Subgraph；False 表示使用自研 compile_governed_subgraph()（默认）
    """
    raw = str(os.environ.get(_ENV_ENABLED, "false")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# SubgraphState：子图独立状态（不含受保护字段）
# ---------------------------------------------------------------------------

class SubgraphState(TypedDict):
    """子图独立状态（不包含 committed_* 等受保护字段）。

    FIX-5.3：子图使用独立的 SubgraphState，而非完整的 GraphState，
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

    # 尝试构建 LangGraph 原生子图
    compiled_sub = _try_build_langgraph_subgraph(
        subgraph_id=subgraph_id,
        node_fns=node_fns,
        node_ids=node_ids,
        edges=edges,
        entry_node=resolved_entry,
    )

    def subgraph_adapter(state: dict[str, Any]) -> dict[str, Any]:
        """适配器：隔离子图 state，防止子图写入受保护字段。

        FIX-5.3 核心：
        1. 从完整 GraphState 中提取子图所需字段（payload + subgraph_trace）
        2. 执行子图（LangGraph 原生或降级路径）
        3. 只将非受保护字段合并回主图 state
        """
        from host_brain.integrations.graph_contract import (  # noqa: PLC0415
            SUBGRAPH_PROTECTED_FIELDS,
            STATUS_DEGRADED,
            STATUS_FAILED,
            STATUS_OK,
            build_subgraph_commit_summary,
        )

        sub_state: SubgraphState = {
            "payload": dict(state.get("payload") or {}),
            "subgraph_trace": list(state.get("subgraph_trace") or []),
            "request_id": str(state.get("request_id") or ""),
            "graph_id": str(state.get("graph_id") or ""),
            "timeout_ms": int(state.get("timeout_ms") or 5000),
        }

        if compiled_sub is not None:
            # LangGraph 原生子图执行路径
            try:
                sub_result = compiled_sub.invoke(sub_state)
                sub_result = dict(sub_result) if sub_result else {}
            except Exception as exc:
                _logger.warning(
                    "build_governed_subgraph[%s]: LangGraph 子图执行失败（%s），降级到线性执行",
                    subgraph_id, exc,
                )
                sub_result = _linear_execute(
                    node_ids=node_ids,
                    node_fns=node_fns,
                    sub_state=sub_state,
                    subgraph_id=subgraph_id,
                    evidence_plane=evidence_plane,
                )
        else:
            # 降级路径：线性执行
            sub_result = _linear_execute(
                node_ids=node_ids,
                node_fns=node_fns,
                sub_state=sub_state,
                subgraph_id=subgraph_id,
                evidence_plane=evidence_plane,
            )

        # 生成 subgraph_commit_summary
        subgraph_trace = list(sub_result.get("subgraph_trace") or sub_state["subgraph_trace"])
        summary = build_subgraph_commit_summary(
            subgraph_id=subgraph_id,
            subgraph_trace=subgraph_trace,
        )

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


def _try_build_langgraph_subgraph(
    *,
    subgraph_id: str,
    node_fns: dict[str, Callable],
    node_ids: list[str],
    edges: list[tuple[str, str]] | None,
    entry_node: str,
) -> Any:
    """尝试构建 LangGraph 原生子图（静默降级）。

    Returns:
        编译后的 LangGraph 子图；若 LangGraph 不可用则返回 None
    """
    try:
        from langgraph.graph import END, START, StateGraph  # noqa: PLC0415
        from langchain_core.runnables import RunnableLambda  # noqa: PLC0415

        sub = StateGraph(SubgraphState)

        # 包装节点函数：将 SubgraphState 格式转换为 (payload, context) 格式
        for node_id, fn in node_fns.items():
            _fn = fn  # 闭包捕获

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
                        from host_brain.integrations.graph_contract import SUBGRAPH_PROTECTED_FIELDS  # noqa: PLC0415
                        safe_output = {k: v for k, v in (output or {}).items() if k not in SUBGRAPH_PROTECTED_FIELDS}
                        payload.update(safe_output)
                        trace.append({"node_id": nid, "status": "OK", "ts_ms": ts_ms, "reason_code": None})
                    except Exception as exc:
                        trace.append({"node_id": nid, "status": "FAILED", "ts_ms": ts_ms,
                                      "reason_code": "SUBGRAPH_NODE_ERROR", "detail": str(exc)})
                    return {**sub_state, "payload": payload, "subgraph_trace": trace}
                return _wrapped

            sub.add_node(node_id, RunnableLambda(_make_node_fn(_fn, node_id)))

        # 添加边
        if edges:
            sub.add_edge(START, entry_node)
            for from_node, to_node in edges:
                sub.add_edge(from_node, to_node)
            # 找到没有出边的节点，连接到 END
            has_outgoing = {from_node for from_node, _ in edges}
            for nid in node_ids:
                if nid not in has_outgoing:
                    sub.add_edge(nid, END)
        else:
            # 线性连接
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
    except ImportError:
        _logger.warning(
            "build_governed_subgraph[%s]: LangGraph 不可用，降级到线性执行", subgraph_id
        )
        return None
    except Exception as exc:
        _logger.warning(
            "build_governed_subgraph[%s]: LangGraph 子图构建失败（%s），降级到线性执行",
            subgraph_id, exc,
        )
        return None


def _linear_execute(
    *,
    node_ids: list[str],
    node_fns: dict[str, Callable],
    sub_state: SubgraphState,
    subgraph_id: str,
    evidence_plane: Any,
) -> dict[str, Any]:
    """降级路径：线性顺序执行子图节点（与自研 _invoke_linear 逻辑一致）。"""
    from host_brain.integrations.graph_contract import SUBGRAPH_PROTECTED_FIELDS  # noqa: PLC0415

    payload = dict(sub_state.get("payload") or {})
    trace = list(sub_state.get("subgraph_trace") or [])

    for node_id in node_ids:
        ts_ms = int(time.time() * 1000)
        fn = node_fns.get(node_id)
        if fn is None:
            trace.append({"node_id": node_id, "status": "FAILED", "ts_ms": ts_ms,
                          "reason_code": "SUBGRAPH_HANDLER_MISSING"})
            continue
        try:
            output = fn(payload, {
                "request_id": str(sub_state.get("request_id") or ""),
                "node_id": node_id,
                "subgraph_id": subgraph_id,
                "timeout_ms": int(sub_state.get("timeout_ms") or 5000),
            })
            safe_output = {k: v for k, v in (output or {}).items() if k not in SUBGRAPH_PROTECTED_FIELDS}
            payload.update(safe_output)
            entry = {"node_id": node_id, "status": "OK", "ts_ms": ts_ms, "reason_code": None}
            trace.append(entry)
            if evidence_plane is not None:
                try:
                    evidence_plane.write_trace(
                        request_id=str(sub_state.get("request_id") or ""),
                        graph_id=str(sub_state.get("graph_id") or ""),
                        trace=[entry],
                    )
                except Exception:
                    pass
        except Exception as exc:
            entry = {"node_id": node_id, "status": "FAILED", "ts_ms": ts_ms,
                     "reason_code": "SUBGRAPH_NODE_ERROR", "detail": str(exc)}
            trace.append(entry)
            if evidence_plane is not None:
                try:
                    evidence_plane.write_trace(
                        request_id=str(sub_state.get("request_id") or ""),
                        graph_id=str(sub_state.get("graph_id") or ""),
                        trace=[entry],
                    )
                except Exception:
                    pass

    return {**sub_state, "payload": payload, "subgraph_trace": trace}


__all__ = [
    "SubgraphState",
    "build_governed_subgraph",
    "is_langgraph_subgraph_enabled",
]
