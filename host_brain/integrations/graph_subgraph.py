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

from host_brain.core.trace_redaction import (
    content_ref,
    normalize_trace_refs,
    public_decision_summary,
    stable_payload_hash,
)
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
) -> Any:
    # 该兼容入口只负责把已注册处理器交给原生隔离子图，不自行实现另一套执行语义。
    """编译透明子图（FIX-5.3：委托给 LangGraph 原生子图）。

    内部直接调用 build_governed_subgraph()。

    Args:
        subgraph_id: 子图唯一 ID
        node_ids: 子图节点 ID 列表（按执行顺序）
        handlers: 节点处理器字典 {node_id: handler_fn(payload, context) -> dict}
        evidence_plane: Evidence Plane 实例（可选）
        edge_map: 条件分支路由（可选，None 时线性执行）

    Returns:
        subgraph_adapter 函数（可调用 adapter(state) 执行）
    """
    # 将 handlers 适配为 node_fns 格式（handlers 接受 (payload, context)，与 node_fns 一致）
    node_fns: dict[str, Callable] = {nid: handlers[nid] for nid in node_ids if nid in handlers}
    # 未注册节点不会被伪造为占位处理器；底层构建器会据实际节点集合进行显式校验。

    # 构建 edges 列表（从 edge_map 转换）
    edges: list[tuple[str, str]] | None = None
    if edge_map:
        # 边只由声明式映射转换而来，子图节点不能在运行期用输出改写其拓扑。
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
    # 固定链使意图推理的步骤和审计轨迹可预测；实际节点能力仍来自显式 handlers。
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
    # 代码精炼链与意图链隔离，避免不同 Agent 场景共享隐式的控制或状态通道。
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
    # 保留此防线以兼容直接调用者；主适配层亦会过滤，形成两层控制面保护。
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
    # Agent 只回写可公开摘要、稳定引用与哈希；原始输出不直接嵌入共享 GraphState。
    """将子 agent 输出工件写入 GraphState 顶层 agent_artifacts 字段（M-MA3）。"""
    if not isinstance(state, dict):
        # 无法安全合并的状态对象不做隐式转换，避免工件记录改变调用方的数据类型。
        return
    artifacts = state.setdefault("agent_artifacts", {})
    if not isinstance(artifacts, dict):
        state["agent_artifacts"] = {}
        artifacts = state["agent_artifacts"]
    raw_summary = str(summary or "")
    safe_summary = public_decision_summary(
        # 摘要先经过脱敏与长度限制，再作为展示/决策索引，完整内容仅以引用关联。
        raw_summary,
        fallback="Agent artifact recorded; inspect the authorized output reference.",
        max_chars=200,
    )
    artifact_refs = normalize_trace_refs([artifact_ref])
    safe_artifact_ref = artifact_refs[0] if artifact_refs else content_ref("artifact", artifact_ref)
    artifacts[str(agent_id)] = {
        # 哈希让审计能检测内容漂移，同时避免将原始 Agent 结果复制进状态快照。
        "artifact_ref": safe_artifact_ref,
        "summary": safe_summary,
        "decision_summary": safe_summary,
        "output_refs": [content_ref("agent-output", raw_summary)] if raw_summary else [],
        "evidence_refs": [safe_artifact_ref],
        "payload_hash": stable_payload_hash(raw_summary),
        "confidence": float(confidence),
        "risk_flags": list(risk_flags or []),
        "ts_ms": int(time.time() * 1000),
    }
