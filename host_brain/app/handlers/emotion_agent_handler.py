"""EmotionAgent Handler（方向 G Phase 2）。

职责：
- 在 Supervised 图中提供深度情感分析能力
- 接入 ReAct 循环（run_react_loop，只读工具，allow_side_effects=False）
- 产出 AgentArtifact 工件：emotion_analysis 报告 + recommended_emotion_tag
- 可调用情感专用工具：emotion_history_search、bond_status_query、emotion_timeline_query

触发条件（Supervisor 分派时）：
- Phase 6 级联管线标记了 needs_deep_emotion_analysis=true
- 或 SUPERVISOR_MODE=llm 模式下 Supervisor LLM 认为需要深度情感分析

设计原则：
- 只产出工件，不直接控图
- 只读工具，无副作用
- 失败时写入 agent_artifacts[emotion_agent]["error"]，不抛异常
"""

from __future__ import annotations

import os
import time
from typing import Any

from host_brain.app.agents.agent_registry import AGENT_ID_EMOTION
from host_brain.app.agents.react_loop import run_react_loop
from host_brain.app.agents.tool_registry import get_tools_for_agent
from host_brain.integrations.graph_subgraph import write_agent_artifact_to_state


def emotion_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """EmotionAgent handler（方向 G Phase 2，ReAct 模式深度情感分析）。

    Observe（读取对话上下文 + 当前情感状态）
    → Think（LLM 分析情感、检索情感记忆、评估 Bond 状态）
    → Act（调用 emotion_history_search / bond_status_query / emotion_timeline_query）
    → Observe → ... → final_answer（结构化情感分析报告）

    Args:
        payload: 当前图 payload
        context: 执行上下文

    Returns:
        更新后的 state（含 agent_artifacts[emotion_agent] 和 emotion_agent_trace）
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

        # 提取当前情感上下文
        emotion_context = ctx.get("emotion_context") or payload.get("emotion_context") or {}
        current_emotion_tag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper()
        mood_state = str(emotion_context.get("mood_state") or "steady").strip()
        emotion_echo = emotion_context.get("emotion_echo") or {}
        user_profile = emotion_context.get("user_emotion_profile") or {}

        # 构建情感分析专用的 system_prompt
        system_prompt_extra = _build_emotion_agent_system_prompt(
            current_emotion_tag=current_emotion_tag,
            mood_state=mood_state,
            emotion_echo=emotion_echo,
            user_profile=user_profile,
        )

        # 获取 EmotionAgent 可用工具（只读，allow_side_effects=False）
        tools = get_tools_for_agent(AGENT_ID_EMOTION, allow_side_effects=False)

        # 获取 LLM gateway
        gateway = ctx.get("gateway") or payload.get("_gateway")

        # 执行 ReAct 循环（最多 3 步，情感分析无需多步推理）
        react_result = run_react_loop(
            agent_id=AGENT_ID_EMOTION,
            user_input=user_input,
            tools=tools,
            gateway=gateway,
            system_prompt_extra=system_prompt_extra,
            max_steps=int(os.environ.get("EMOTION_AGENT_REACT_MAX_STEPS") or "3"),
            allow_side_effects=False,
            context=ctx,
        )

        # 写入 react_scratchpad
        out["react_scratchpad"] = react_result.to_scratchpad_dict()

        # 从 final_answer 中尝试提取结构化情感分析结果
        emotion_analysis = _parse_emotion_analysis(react_result.final_answer)

        artifact_ref = f"artifact://{request_id}/{AGENT_ID_EMOTION}_{int(time.time() * 1000)}"
        write_agent_artifact_to_state(
            out,
            agent_id=AGENT_ID_EMOTION,
            artifact_ref=artifact_ref,
            summary=react_result.final_answer[:200],
            confidence=react_result.confidence,
            risk_flags=react_result.risk_flags,
        )

        # 方向 G Phase 2 契约字段
        out["emotion_agent_trace"] = {
            "agent_id": AGENT_ID_EMOTION,
            "mode": "react_loop",
            "artifact_ref": artifact_ref,
            "current_emotion_tag": current_emotion_tag,
            "recommended_emotion_tag": emotion_analysis.get("recommended_emotion_tag", current_emotion_tag),
            "emotion_reasoning": emotion_analysis.get("reasoning", ""),
            "total_steps": react_result.total_steps,
            "stopped_reason": react_result.stopped_reason,
            "ts_ms": int(time.time() * 1000),
        }

    except Exception as exc:
        artifacts = out.setdefault("agent_artifacts", {})
        artifacts[AGENT_ID_EMOTION] = {
            "artifact_ref": f"artifact://{request_id}/{AGENT_ID_EMOTION}_error",
            "summary": "EmotionAgent 执行异常",
            "confidence": 0.0,
            "risk_flags": [f"HANDLER_ERROR:{type(exc).__name__}"],
            "error": str(exc),
            "ts_ms": int(time.time() * 1000),
        }

    return out


def _build_emotion_agent_system_prompt(
    *,
    current_emotion_tag: str,
    mood_state: str,
    emotion_echo: dict[str, Any],
    user_profile: dict[str, Any],
) -> str:
    """构建情感 Agent 的系统提示词。"""
    parts: list[str] = [
        "你是情感分析专家，负责深度分析用户的情感状态。",
        f"当前情感标签: {current_emotion_tag}",
        f"当前心情状态: {mood_state}",
    ]

    # 情感回声
    echo_style = str(emotion_echo.get("echo_style") or "").strip()
    if echo_style:
        parts.append(f"情感回声风格: {echo_style}")

    # 用户画像
    profile_hint = str(user_profile.get("profile_hint") or "").strip()
    if profile_hint:
        parts.append(f"用户画像: {profile_hint}")

    parts.extend([
        "",
        "你可以使用以下工具辅助分析：",
        "- emotion_history_search: 检索用户的历史情感记忆",
        "- emotion_timeline_query: 查询最近的情感变化轨迹",
        "- search_knowledge: 在知识库中检索相关上下文",
        "",
        "分析完成后，请给出：",
        "1. 用户当前的真实情感状态（可能与表面不同）",
        "2. 推荐的 Natsume 情感反应标签",
        "3. 分析推理过程",
    ])

    return "\n".join(parts)


def _parse_emotion_analysis(final_answer: str) -> dict[str, Any]:
    """从 ReAct final_answer 中提取结构化情感分析结果。"""
    import json as _json  # noqa: PLC0415

    result: dict[str, Any] = {
        "recommended_emotion_tag": "NEUTRAL",
        "reasoning": final_answer[:200],
    }

    # 尝试从 final_answer 中提取 JSON
    text = str(final_answer or "").strip()
    if not text:
        return result

    try:
        # 尝试直接解析
        parsed = _json.loads(text)
        if isinstance(parsed, dict):
            if "recommended_emotion_tag" in parsed:
                result["recommended_emotion_tag"] = str(parsed["recommended_emotion_tag"]).strip().upper()
            elif "natsume_emotion" in parsed:
                result["recommended_emotion_tag"] = str(parsed["natsume_emotion"]).strip().upper()
            if "reasoning" in parsed:
                result["reasoning"] = str(parsed["reasoning"])[:200]
            return result
    except (TypeError, ValueError, _json.JSONDecodeError):
        pass

    # 从自然语言中提取情感标签关键词
    from host_brain.app.emotion_llm_inference import _VALID_EMOTION_TAGS  # noqa: PLC0415
    text_upper = text.upper()
    for tag in _VALID_EMOTION_TAGS:
        if tag in text_upper and tag != "NEUTRAL":
            result["recommended_emotion_tag"] = tag
            break

    return result
