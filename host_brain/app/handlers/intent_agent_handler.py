"""IntentAgent Handler（M-MA5）。

职责：
- 处理执行意图请求（SCENE_EXEC_INTENT / SCENE_VISION_UI）
- 调用 intent_agent_subgraph（core_reasoner_primary → planner_constraints → tool_planner）
- 产出 AgentArtifact 工件，写入 state["agent_artifacts"]
- 高风险场景，risk_flags 更严格

对应子图：intent_agent_subgraph（compile_intent_agent_subgraph）
"""

from __future__ import annotations

import time
from typing import Any

from host_brain.app.agents.agent_registry import AGENT_ID_INTENT
from host_brain.integrations.graph_subgraph import write_agent_artifact_to_state


def intent_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """IntentAgent handler（M-MA5）。

    读取执行意图链（core_reasoner_primary / planner_constraints / tool_planner）的输出，
    封装为 AgentArtifact 工件。

    第一阶段：读取现有节点输出，不直接调用子图（子图接入在后续迭代完成）。
    INTENT_SUBGRAPH_ENABLED=true 时，将调用 compile_intent_agent_subgraph。

    Args:
        payload: 当前图 payload
        context: 执行上下文

    Returns:
        更新后的 state（含 agent_artifacts[intent_agent]）
    """
    import os
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()

    out = dict(payload)

    try:
        # 读取执行意图链的输出（优先读 tool_planner，回退到 core_reasoner_primary）
        tool_planner_output = dict(payload.get("tool_planner") or {})
        core_reasoner_output = dict(payload.get("core_reasoner_primary") or {})

        draft_summary = str(
            tool_planner_output.get("payload", {}).get("plan_summary")
            or tool_planner_output.get("payload", {}).get("response_text")
            or core_reasoner_output.get("payload", {}).get("response_text")
            or "执行意图计划已生成"
        ).strip()[:200]

        # 风险评估：执行意图场景默认中高风险
        gateway_status = str(
            tool_planner_output.get("gateway_status")
            or core_reasoner_output.get("gateway_status")
            or "OK"
        ).strip().upper()

        confidence = 0.75 if gateway_status == "OK" else 0.15
        risk_flags = ["EXEC_INTENT_SCENE"]  # 执行意图场景默认标记
        if gateway_status != "OK":
            risk_flags.append(f"GATEWAY_STATUS:{gateway_status}")

        # 检查 safety_verifier 是否有风险标记
        safety_output = dict(payload.get("safety_verifier") or {})
        safety_risk = str(safety_output.get("payload", {}).get("risk_level") or "").strip().upper()
        if safety_risk in {"HIGH", "CRITICAL"}:
            risk_flags.append(f"SAFETY_RISK:{safety_risk}")
            confidence = min(confidence, 0.4)

        artifact_ref = f"artifact://{request_id}/{AGENT_ID_INTENT}_{int(time.time() * 1000)}"

        write_agent_artifact_to_state(
            out,
            agent_id=AGENT_ID_INTENT,
            artifact_ref=artifact_ref,
            summary=draft_summary,
            confidence=confidence,
            risk_flags=risk_flags,
        )

        out["intent_agent_trace"] = {
            "agent_id": AGENT_ID_INTENT,
            "artifact_ref": artifact_ref,
            "gateway_status": gateway_status,
            "safety_risk": safety_risk or "NONE",
            "intent_subgraph_enabled": str(os.environ.get("INTENT_SUBGRAPH_ENABLED") or "false").strip().lower() in {"1", "true", "yes", "on"},
            "ts_ms": int(time.time() * 1000),
        }

    except Exception as exc:
        artifacts = out.setdefault("agent_artifacts", {})
        artifacts[AGENT_ID_INTENT] = {
            "artifact_ref": f"artifact://{request_id}/{AGENT_ID_INTENT}_error",
            "summary": "IntentAgent 执行异常",
            "confidence": 0.0,
            "risk_flags": ["EXEC_INTENT_SCENE", f"HANDLER_ERROR:{type(exc).__name__}"],
            "error": str(exc),
            "ts_ms": int(time.time() * 1000),
        }

    return out
