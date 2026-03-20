"""Transparent Subgraph factory for Governed Runtime Contract (M35-E).

实现 compile_governed_subgraph() 工厂函数，确保子图内部节点对 Evidence Plane 透明。

设计原则（ModelPlane.md §10 Rule 3）：
- 子图每个节点执行后写入 subgraph_trace
- 子图结束时输出 subgraph_commit_summary
- 子图不允许直接写入 committed_* 字段（权限约束）
- 子图失败时 status = STATUS_DEGRADED，不影响主图继续执行
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from host_brain.integrations.graph_contract import (
    GraphState,
    NodeHandler,
    STATUS_DEGRADED,
    STATUS_FAILED,
    STATUS_OK,
    build_subgraph_commit_summary,
)


@dataclass
class CompiledSubgraph:
    """已编译的透明子图（Transparent Subgraph）。

    通过 compile_governed_subgraph() 工厂创建，不可直接实例化。
    每个节点执行后写入 subgraph_trace，子图结束时输出 subgraph_commit_summary。
    """

    subgraph_id: str
    node_ids: list[str]
    handlers: dict[str, NodeHandler]
    _compiled: bool = field(default=False, repr=False)
    # M37-E：Evidence Plane 实时写入（可选，默认 None）
    _evidence_plane: Any = field(default=None, repr=False)

    def invoke(self, state: GraphState) -> GraphState:
        """执行子图，写入 subgraph_trace，返回更新后的 GraphState。

        Args:
            state: 当前主图状态

        Returns:
            更新后的 GraphState（subgraph_trace 已追加，subgraph_commit_summary 已写入 payload）
        """
        subgraph_trace = list(state.get("subgraph_trace") or [])
        payload = dict(state.get("payload") or {})
        current_status = str(state.get("status") or STATUS_OK)

        for node_id in self.node_ids:
            ts_ms = int(time.time() * 1000)
            handler = self.handlers.get(node_id)
            if handler is None:
                # 节点处理器缺失：记录失败，继续执行（不影响主图）
                subgraph_trace.append({
                    "node_id": node_id,
                    "status": STATUS_FAILED,
                    "ts_ms": ts_ms,
                    "reason_code": "SUBGRAPH_HANDLER_MISSING",
                    "detail": f"子图节点处理器缺失: {node_id}",
                })
                current_status = STATUS_DEGRADED
                continue

            try:
                output = handler(payload, {
                    "request_id": str(state.get("request_id") or ""),
                    "node_id": node_id,
                    "graph_id": str(state.get("graph_id") or ""),
                    "subgraph_id": self.subgraph_id,
                    "timeout_ms": int(state.get("timeout_ms") or 5000),
                })
                # 权限约束：子图不允许写入 committed_* 字段
                _strip_committed_fields(output)
                payload.update(output or {})
                trace_entry = {
                    "node_id": node_id,
                    "status": STATUS_OK,
                    "ts_ms": ts_ms,
                    "reason_code": None,
                    "detail": "subgraph node executed",
                }
                subgraph_trace.append(trace_entry)
                # M37-E：Evidence Plane 实时写入（成功）
                self._write_node_trace(request_id=str(state.get("request_id") or ""),
                                       graph_id=str(state.get("graph_id") or ""),
                                       entry=trace_entry)
            except Exception as exc:
                trace_entry = {
                    "node_id": node_id,
                    "status": STATUS_FAILED,
                    "ts_ms": ts_ms,
                    "reason_code": "SUBGRAPH_NODE_ERROR",
                    "detail": f"子图节点执行失败: {node_id}: {exc}",
                }
                subgraph_trace.append(trace_entry)
                # M37-E：Evidence Plane 实时写入（失败）
                self._write_node_trace(request_id=str(state.get("request_id") or ""),
                                       graph_id=str(state.get("graph_id") or ""),
                                       entry=trace_entry)
                current_status = STATUS_DEGRADED
                # 子图失败不影响主图继续执行（继续下一个节点）

        # 子图结束：生成 subgraph_commit_summary 写入 payload
        summary = build_subgraph_commit_summary(
            subgraph_id=self.subgraph_id,
            subgraph_trace=subgraph_trace,
        )
        payload["subgraph_commit_summary"] = summary

        # 子图状态降级时，主图状态同步降级（但不覆盖 FAILED）
        # 优先级：FAILED > DEGRADED > OK
        # 修复：取 current_status 和 state["status"] 中"更严重"的那个，
        # 避免子图 DEGRADED 覆盖主图已有的 FAILED 状态
        _STATUS_SEVERITY = {STATUS_FAILED: 2, STATUS_DEGRADED: 1, STATUS_OK: 0}
        original_status = str(state.get("status") or STATUS_OK)
        final_status = (
            original_status
            if _STATUS_SEVERITY.get(original_status, 0) >= _STATUS_SEVERITY.get(current_status, 0)
            else current_status
        )

        return {
            **state,
            "payload": payload,
            "subgraph_trace": subgraph_trace,
            "status": final_status,
        }

    def _write_node_trace(self, *, request_id: str, graph_id: str, entry: dict[str, Any]) -> None:
        """M37-E：子图节点执行后实时写入 Evidence Plane（静默降级）。"""
        if self._evidence_plane is None:
            return
        try:
            self._evidence_plane.write_trace(
                request_id=request_id,
                graph_id=graph_id,
                trace=[entry],
            )
        except Exception:
            pass  # 静默降级，不影响子图执行


def compile_governed_subgraph(
    subgraph_id: str,
    node_ids: list[str],
    handlers: dict[str, NodeHandler],
    evidence_plane: Any = None,
) -> CompiledSubgraph:
    """编译透明子图工厂函数（Transparent Subgraph Rule，ModelPlane.md §10 Rule 3）。

    子图内部节点必须对 Evidence Plane 透明：
    - 每个节点执行后写入 subgraph_trace
    - 子图结束时输出 subgraph_commit_summary
    - 子图不允许直接写入 committed_* 字段

    Args:
        subgraph_id: 子图唯一 ID（用于 Evidence Plane 追踪）
        node_ids: 子图节点 ID 列表（按执行顺序）
        handlers: 节点处理器字典 {node_id: handler_fn}
        evidence_plane: Evidence Plane 实例（可选，M37-E 新增，默认 None）

    Returns:
        CompiledSubgraph（可调用 .invoke(state) 执行）

    Raises:
        ValueError: subgraph_id 为空或 node_ids 为空
    """
    if not str(subgraph_id or "").strip():
        raise ValueError("subgraph_id 不能为空")
    if not node_ids:
        raise ValueError("node_ids 不能为空")

    return CompiledSubgraph(
        subgraph_id=str(subgraph_id).strip(),
        node_ids=[str(nid).strip() for nid in node_ids if str(nid or "").strip()],
        handlers=dict(handlers or {}),
        _compiled=True,
        _evidence_plane=evidence_plane,
    )


def _strip_committed_fields(output: dict[str, Any]) -> None:
    """权限约束：从子图节点输出中移除 committed_* 字段。

    子图节点不允许写入 committed_* 字段（只有 Host Governor 可以写入）。
    此函数在子图节点输出合并到 payload 前调用。
    """
    if not isinstance(output, dict):
        return
    committed_keys = [k for k in list(output.keys()) if str(k or "").startswith("committed_")]
    for key in committed_keys:
        del output[key]


# ---------------------------------------------------------------------------
# M-MA2：Multi-Agent 透明子图工厂（第二阶段）
# ---------------------------------------------------------------------------

def compile_intent_agent_subgraph(
    handlers: dict[str, NodeHandler],
    evidence_plane: Any = None,
) -> CompiledSubgraph:
    """编译执行意图 agent 透明子图（IntentAgentSubgraph）。

    封装 SCENE_EXEC_INTENT 的核心推理链为透明子图：
        core_reasoner_primary → planner_constraints → tool_planner

    子图约束（继承自 compile_governed_subgraph）：
    - 每个节点执行后写入 subgraph_trace
    - 子图结束时输出 subgraph_commit_summary
    - 子图不允许写入 committed_* 字段
    - 子图失败时 status = STATUS_DEGRADED，不影响主图继续执行

    Args:
        handlers: 节点处理器字典，必须包含以下节点的处理器：
            - core_reasoner_primary
            - planner_constraints
            - tool_planner
        evidence_plane: Evidence Plane 实例（可选，M37-E 新增）

    Returns:
        CompiledSubgraph（可调用 .invoke(state) 执行）
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
) -> CompiledSubgraph:
    """编译代码精炼 agent 透明子图（CodeRefineSubgraph）。

    封装代码场景的多步精炼链为透明子图：
        workbench_code_assist → consistency_verifier

    适用于需要多轮精炼的复杂代码任务。
    简单代码任务仍走单节点 workbench_code_assist。

    Args:
        handlers: 节点处理器字典，必须包含以下节点的处理器：
            - workbench_code_assist
            - consistency_verifier
        evidence_plane: Evidence Plane 实例（可选）

    Returns:
        CompiledSubgraph（可调用 .invoke(state) 执行）
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


def write_agent_artifact_to_state(
    state: dict[str, Any],
    *,
    agent_id: str,
    artifact_ref: str,
    summary: str,
    confidence: float = 0.0,
    risk_flags: list[str] | None = None,
) -> None:
    """将子 agent 输出工件写入 GraphState 顶层 agent_artifacts 字段（M-MA3）。

    子 agent 执行完毕后调用此函数，将工件摘要写入 GraphState.agent_artifacts，
    供 JudgeAgent 和 Supervisor 读取。

    Args:
        state: 当前图状态字典（GraphState，原地修改）
        agent_id: 子 agent ID
        artifact_ref: 工件引用（artifact://...）
        summary: 工件摘要
        confidence: 置信度（0.0 ~ 1.0）
        risk_flags: 风险标记列表
    """
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


# 向后兼容别名（第三阶段迁移期间保留，后续版本删除）
write_agent_artifact_to_payload = write_agent_artifact_to_state
