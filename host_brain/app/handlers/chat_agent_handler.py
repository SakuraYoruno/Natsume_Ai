"""ChatAgent Handler（M-MA5）。

职责：
- 处理普通聊天请求（SCENE_CHAT 快路径）
- 产出 AgentArtifact 工件，写入 state["agent_artifacts"]
- 不写 __next_node__（由 graph_executor 根据 need_judge 决定下一跳）
- 不写 committed_route_token

对应节点：chat_direct_ja
"""

from __future__ import annotations

import time
from typing import Any

from host_brain.app.agents.agent_registry import AGENT_ID_CHAT
from host_brain.integrations.graph_subgraph import write_agent_artifact_to_state


def chat_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """ChatAgent handler（M-MA5）。

    第一阶段：透传到 chat_direct_ja 节点执行，产出工件。
    chat_direct_ja 的实际 LLM 调用由 model node 机制处理，
    此 handler 负责工件封装和 agent_artifacts 写入。

    Args:
        payload: 当前图 payload
        context: 执行上下文（含 request_id、node_id 等）

    Returns:
        更新后的 state（含 agent_artifacts[chat_agent]）
    """
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()

    out = dict(payload)

    try:
        # 读取 chat_direct_ja 节点的输出（model node 已执行）
        chat_node_output = dict(payload.get("chat_direct_ja") or {})
        draft_summary = str(
            chat_node_output.get("payload", {}).get("response_text")
            or chat_node_output.get("payload", {}).get("answer")
            or "聊天回复已生成"
        ).strip()[:200]

        gateway_status = str(chat_node_output.get("gateway_status") or "OK").strip().upper()
        confidence = 0.9 if gateway_status == "OK" else 0.3
        risk_flags = []
        if gateway_status != "OK":
            risk_flags.append(f"GATEWAY_STATUS:{gateway_status}")

        artifact_ref = f"artifact://{request_id}/{AGENT_ID_CHAT}_{int(time.time() * 1000)}"

        # 写入 agent_artifacts（GraphState 顶层字段）
        write_agent_artifact_to_state(
            out,
            agent_id=AGENT_ID_CHAT,
            artifact_ref=artifact_ref,
            summary=draft_summary,
            confidence=confidence,
            risk_flags=risk_flags,
        )

        # 记录 agent 执行追踪
        out["chat_agent_trace"] = {
            "agent_id": AGENT_ID_CHAT,
            "artifact_ref": artifact_ref,
            "gateway_status": gateway_status,
            "ts_ms": int(time.time() * 1000),
        }

    except Exception as exc:
        # 降级：写入错误工件，不抛异常
        artifacts = out.setdefault("agent_artifacts", {})
        artifacts[AGENT_ID_CHAT] = {
            "artifact_ref": f"artifact://{request_id}/{AGENT_ID_CHAT}_error",
            "summary": "ChatAgent 执行异常",
            "confidence": 0.0,
            "risk_flags": [f"HANDLER_ERROR:{type(exc).__name__}"],
            "error": str(exc),
            "ts_ms": int(time.time() * 1000),
        }

    return out
