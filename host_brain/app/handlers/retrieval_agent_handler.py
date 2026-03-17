"""RetrievalAgent Handler（P12）。

职责：
- 处理知识库检索请求（SCENE_WORK_RETRIEVAL）
- 接入 ReAct 循环（run_react_loop，只读工具，allow_side_effects=False）
- 产出 AgentArtifact 工件，写入 state["agent_artifacts"]
- 写入 retrieval_agent_trace（字段契约已在 P12_SCOPE §4 写死）

P12 直接替换 legacy_chain 为 ReAct 模式，不引入双模式开关。
"""

from __future__ import annotations

import os
import time
from typing import Any

from host_brain.app.agents.agent_registry import AGENT_ID_RETRIEVAL
from host_brain.app.agents.react_loop import run_react_loop
from host_brain.app.agents.tool_registry import get_tools_for_agent
from host_brain.integrations.graph_subgraph import write_agent_artifact_to_state


def retrieval_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """RetrievalAgent handler（P12，ReAct 模式）。

    Observe → Think（LLM）→ Act（只读工具）→ Observe → ... → final_answer

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
        user_input = str(
            payload.get("user_input")
            or payload.get("query")
            or ""
        ).strip()

        # 获取 RetrievalAgent 可用工具（只读，allow_side_effects=False）
        tools = get_tools_for_agent(AGENT_ID_RETRIEVAL, allow_side_effects=False)

        # 获取 LLM gateway（从 context 或 payload 中读取）
        gateway = ctx.get("gateway") or payload.get("_gateway")

        # 执行 ReAct 循环（最多 3 步，检索场景无需多步推理）
        react_result = run_react_loop(
            agent_id=AGENT_ID_RETRIEVAL,
            user_input=user_input,
            tools=tools,
            gateway=gateway,
            system_prompt_extra="你是检索专家，负责从知识库中提取相关信息。",
            max_steps=int(os.environ.get("RETRIEVAL_AGENT_REACT_MAX_STEPS") or "3"),
            allow_side_effects=False,
            context=ctx,
        )

        # 写入 react_scratchpad（工作区，供 JudgeAgent 和 trace 读取）
        out["react_scratchpad"] = react_result.to_scratchpad_dict()

        # 从 react_scratchpad 中提取检索相关信息
        scratchpad = out["react_scratchpad"]
        retrieval_source = str(
            payload.get("workbench_retrieval", {}).get("payload", {}).get("source")
            or "knowledge_base"
        ).strip()
        result_items = (
            payload.get("workbench_retrieval", {}).get("payload", {}).get("results")
            or payload.get("workbench_retrieval", {}).get("payload", {}).get("items")
            or []
        )
        result_count = len(result_items) if isinstance(result_items, list) else 0

        artifact_ref = f"artifact://{request_id}/{AGENT_ID_RETRIEVAL}_{int(time.time() * 1000)}"
        write_agent_artifact_to_state(
            out,
            agent_id=AGENT_ID_RETRIEVAL,
            artifact_ref=artifact_ref,
            summary=react_result.final_answer[:200],
            confidence=react_result.confidence,
            risk_flags=react_result.risk_flags,
        )

        # P12 §4 契约字段（已写死，不得自行增减必填字段）
        out["retrieval_agent_trace"] = {
            "agent_id": AGENT_ID_RETRIEVAL,
            "mode": "react_loop",
            "artifact_ref": artifact_ref,
            "retrieval_source": retrieval_source,
            "result_count": result_count,
            "total_steps": react_result.total_steps,
            "stopped_reason": react_result.stopped_reason,
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
