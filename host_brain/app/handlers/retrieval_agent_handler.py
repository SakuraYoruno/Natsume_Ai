"""RetrievalAgent Handler（P11）。

职责：
- 处理知识库检索请求（SCENE_WORK_RETRIEVAL）
- 读取上游 workbench_retrieval 节点输出，封装为 AgentArtifact
- 写入 retrieval_agent_trace（字段契约已在 P11_SCOPE §3.3 写死）

P12 将在此基础上接入 ReAct 循环（run_react_loop）。
"""

from __future__ import annotations

import time
from typing import Any

from host_brain.app.agents.agent_registry import AGENT_ID_RETRIEVAL
from host_brain.integrations.graph_subgraph import write_agent_artifact_to_state


def retrieval_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """RetrievalAgent handler（P11）。

    读取 workbench_retrieval 节点输出，封装为 AgentArtifact，
    写入 retrieval_agent_trace（字段契约见 P11_SCOPE §3.3）。

    Args:
        payload: 当前图 payload
        context: 执行上下文

    Returns:
        更新后的 state（含 agent_artifacts[retrieval_agent] 和 retrieval_agent_trace）
    """
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()

    out = dict(payload)

    try:
        # ----------------------------------------------------------------
        # P11：legacy_chain 模式 — 读取 workbench_retrieval 节点输出
        # ----------------------------------------------------------------
        if not out.get("workbench_retrieval") and callable(ctx.get("invoke_model_node")):
            model_out = ctx["invoke_model_node"]("workbench_retrieval")
            if model_out:
                out["workbench_retrieval"] = model_out.get("workbench_retrieval") or model_out
                if isinstance(model_out.get("__model_calls__"), list):
                    out["__model_calls__"] = list(out.get("__model_calls__") or []) + model_out["__model_calls__"]

        retrieval_node_output = dict(out.get("workbench_retrieval") or {})

        # 检索结果摘要
        result_items = (
            retrieval_node_output.get("payload", {}).get("results")
            or retrieval_node_output.get("payload", {}).get("items")
            or []
        )
        result_count = len(result_items) if isinstance(result_items, list) else 0

        draft_summary = str(
            retrieval_node_output.get("payload", {}).get("summary")
            or retrieval_node_output.get("payload", {}).get("response_text")
            or (f"检索到 {result_count} 条结果" if result_count > 0 else "检索完成，无匹配结果")
        ).strip()[:200]

        # 检索来源标识
        retrieval_source = str(
            retrieval_node_output.get("payload", {}).get("source")
            or retrieval_node_output.get("source")
            or "knowledge_base"
        ).strip()

        gateway_status = str(retrieval_node_output.get("gateway_status") or "OK").strip().upper()
        confidence = 0.85 if gateway_status == "OK" else 0.2
        risk_flags: list[str] = []
        if gateway_status != "OK":
            risk_flags.append(f"GATEWAY_STATUS:{gateway_status}")

        artifact_ref = f"artifact://{request_id}/{AGENT_ID_RETRIEVAL}_{int(time.time() * 1000)}"

        write_agent_artifact_to_state(
            out,
            agent_id=AGENT_ID_RETRIEVAL,
            artifact_ref=artifact_ref,
            summary=draft_summary,
            confidence=confidence,
            risk_flags=risk_flags,
        )

        # P11 §3.3 契约字段（已写死，不得自行增减必填字段）
        out["retrieval_agent_trace"] = {
            "agent_id": AGENT_ID_RETRIEVAL,
            "mode": "legacy_chain",
            "artifact_ref": artifact_ref,
            "retrieval_source": retrieval_source,
            "result_count": result_count,
            "ts_ms": int(time.time() * 1000),
        }

    except Exception as exc:
        artifacts = out.setdefault("agent_artifacts", {})
        artifacts[AGENT_ID_RETRIEVAL] = {
            "artifact_ref": f"artifact://{request_id}/{AGENT_ID_RETRIEVAL}_error",
            "summary": "RetrievalAgent 执行异常",
            "confidence": 0.0,
            "risk_flags": [f"HANDLER_ERROR:{type(exc).__name__}"],
            "error": str(exc),
            "ts_ms": int(time.time() * 1000),
        }

    return out
