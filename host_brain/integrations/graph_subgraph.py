"""Transparent Subgraph factory for Governed Runtime Contract（FIX-5.3 彻底迁移）。

自研 compile_governed_subgraph() 已删除，改为直接使用 LangGraph 原生子图。
此模块保留公共接口（compile_governed_subgraph / compile_intent_agent_subgraph 等），
内部委托给 graph_langgraph_subgraph.build_governed_subgraph()。

保留不变的约束：
- 子图每个节点执行后写入 subgraph_trace
- 子图结束时输出 subgraph_commit_summary
- 子图不允许直接写入 committed_* 字段（SUBGRAPH_PROTECTED_FIELDS 黑名单）
- 子图失败时 status = STATUS_DEGRADED，不影响主图继续执行
"""

from __future__ import annotations

import time
from typing import Any, Callable

from host_brain.integrations.graph_contract import (
    GraphState,
    NodeHandler,
    STATUS_DEGRADED,
    STATUS_FAILED,
    STATUS_OK,
    SUBGRAPH_PROTECTED_FIELDS,
    build_subgraph_commit_summary,
)
from host_brain.integrations.graph_langgraph_subgraph import build_governed_subgraph


def compile_governed_subgraph(
    subgraph_id: str,
    node_ids: list[str],
    handlers: dict[str, NodeHandler],
    evidence_plane: Any = None,
    *,
    edge_map: dict[str, str] | None = None,
    fallback_map: dict[str, str] | None = None,
) -> Any:
    """编译透明子图（FIX-5.3：委托给 LangGraph 原生子图）。

    接口与原自研版本兼容，内部改为调用 build_governed_subgraph()。

    Args:
        subgraph_id: 子图唯一 ID
        node_ids: 子图节点 ID 列表（按执行顺序）
        handlers: 节点处理器字典 {node_id: handler_fn(payload, context) -> dict}
        evidence_plane: Evidence Plane 实例（可选）
        edge_map: 条件分支路由（可选，None 时线性执行）
        fallback_map: 降级路由（可选）

    Returns:
        subgraph_adapter 函数（可调用 adapter(state) 执行）
    """
    # 将 handlers 适配为 node_fns 格式（handlers 接受 (payload, context)，与 node_fns 一致）
    node_fns: dict[str, Callable] = {nid: handlers[nid] for nid in node_ids if nid in handlers}

    # 构建 edges 列表（从 edge_map 转换）
    edges: list[tuple[str, str]] | None = None
    if edge_map:
        edges = list(edge_map.items())

    return build_governed_subgraph(
        subgraph_id=subgraph_id,
        node_fns=node_fns,
        edges=edges,
        evidence_plane=evidence_plane,
    )


def compile_intent_agent_subgraph(
    handlers: dict[str, NodeHandler],
    evidence_plane: Any = None,
) -> Any:
    """编译执行意图 agent 透明子图（IntentAgentSubgraph）。

    封装 SCENE_EXEC_INTENT 的核心推理链为透明子图：
        core_reasoner_primary → planner_constraints → tool_planner
    """
    _INTENT_SUBGRAPH_NODES = [
        "core_reasoner_primary",
        "planner_constraints",
        "tool_planner",
    ]
    return compile_governed_subgraph(
        subgraph_id="intent_agent_subgraph",
        node_ids=_INTENT_SUBGRAPH_NODES,
        handlers=handlers,
        evidence_plane=evidence_plane,
    )


def compile_code_refine_subgraph(
    handlers: dict[str, NodeHandler],
    evidence_plane: Any = None,
) -> Any:
    """编译代码精炼 agent 透明子图（CodeRefineSubgraph）。

    封装代码场景的多步精炼链为透明子图：
        workbench_code_assist → consistency_verifier
    """
    _CODE_REFINE_SUBGRAPH_NODES = [
        "workbench_code_assist",
        "consistency_verifier",
    ]
    return compile_governed_subgraph(
        subgraph_id="code_refine_subgraph",
        node_ids=_CODE_REFINE_SUBGRAPH_NODES,
        handlers=handlers,
        evidence_plane=evidence_plane,
    )


def _strip_committed_fields(output: dict[str, Any]) -> None:
    """权限约束：从子图节点输出中移除受保护字段（FIX-3.2 显式黑名单）。"""
    if not isinstance(output, dict):
        return
    for key in SUBGRAPH_PROTECTED_FIELDS:
        output.pop(key, None)


def write_agent_artifact_to_state(
    state: dict[str, Any],
    *,
    agent_id: str,
    artifact_ref: str,
    summary: str,
    confidence: float = 0.0,
    risk_flags: list[str] | None = None,
) -> None:
    """将子 agent 输出工件写入 GraphState 顶层 agent_artifacts 字段（M-MA3）。"""
    if not isinstance(state, dict):
        return
    artifacts = state.setdefault("agent_artifacts", {})
    if not isinstance(artifacts, dict):
        state["agent_artifacts"] = {}
        artifacts = state["agent_artifacts"]
    artifacts[str(agent_id)] = {
        "artifact_ref": str(artifact_ref),
        "summary": str(summary),
        "confidence": float(confidence),
        "risk_flags": list(risk_flags or []),
        "ts_ms": int(time.time() * 1000),
    }


# 向后兼容别名
write_agent_artifact_to_payload = write_agent_artifact_to_state
