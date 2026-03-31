"""top_agent_prompt.py — 顶级 Agent prompt 构建（Phase XIV v4）。

核心策略：**直接复用现有 render_node_generation_prompt()**，
它已包含全部 prompt 组件（角色人设/风格/台词/Bond/情感/检索/世界观/
自省/内心独白/预测状态/episodic_vector 等 20+ 个注入槽位）。

只做两处定制：
1. node_id 使用 "top_agent"（走顶级 Agent 专属 role_instruction）
2. 追加子 Agent 能力描述块
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

from host_brain.app.sub_agent_protocol import AGENT_CAPABILITIES


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
TOP_AGENT_NODE_ID = "top_agent"


def build_top_agent_prompt(
    *,
    user_message: str,
    render_target_lang: str = "ja",
    emotion_context: dict[str, Any] | None = None,
    emotion_snapshot: dict[str, Any] | None = None,
    retrieval_summary: dict[str, Any] | None = None,
    retrieval_evidence_items: list[dict[str, Any]] | None = None,
    session_id: str = "",
    request_id: str = "",
    bond_summary: dict[str, Any] | None = None,
    recall_slot_text: dict[str, str] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """构建顶级 Agent 的 prompt。

    **复用完整的 render_node_generation_prompt()**，包含所有 20+ 个注入槽位。
    只是 node_id 使用 "top_agent" + 追加子 Agent 能力描述。

    Returns:
        (prompt_text, system_prompt, gen_params)
    """
    from host_brain.app.chat_runtime_input import build_chat_runtime_input  # noqa: PLC0415
    from host_brain.app.prompt_builder import render_node_generation_prompt  # noqa: PLC0415
    from host_brain.app.emotion_context import (  # noqa: PLC0415
        build_chat_emotion_snapshot,
    )

    emotion_context = emotion_context or {}
    emotion_snapshot = emotion_snapshot or {}

    # ── 1. 构建完整的 runtime_input（和旧链路一模一样）─────────────────
    runtime_input = build_chat_runtime_input(
        request_id=request_id,
        session_id=session_id,
        thread_id=None,
        user_message=user_message,
        render_target_lang=render_target_lang,
        retrieval_summary=retrieval_summary or {"status": "SKIPPED"},
        retrieval_evidence_items=retrieval_evidence_items or [],
        emotion_state=emotion_context,
        emotion_snapshot=emotion_snapshot,
        recall_slot_text=recall_slot_text,
        bond_summary=bond_summary,
        scene_type="CHAT",
    )

    # ── 2. 调用完整的 prompt 渲染（包含全部 20+ 个注入槽位）───────────
    # node_id="top_agent" 走专属 role_instruction
    full_prompt = render_node_generation_prompt(
        node_id=TOP_AGENT_NODE_ID,
        runtime_payload=runtime_input,
    )

    # ── 3. 追加子 Agent 能力描述 ─────────────────────────────────────
    agent_block = _build_agent_capabilities_block()
    full_prompt = full_prompt + "\n\n" + agent_block

    # ── 4. 拆分 system_prompt 和 prompt_text ─────────────────────────
    # render_node_generation_prompt 返回的是完整 prompt 文本，
    # 对于流式调用，我们把它作为 system_prompt，user_message 作为 prompt_text。
    system_prompt = full_prompt
    prompt_text = f"【用户消息】\n{user_message}"

    # ── 5. gen_params（情感调制）──────────────────────────────────────
    gen_params: dict[str, Any] = {
        "temperature": 0.75,
        "max_output_tokens": 2048,
    }
    etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper()
    if etag in ("EXCITED", "HAPPY", "LOVING"):
        gen_params["temperature"] = 0.85
    elif etag in ("SAD", "ANXIOUS", "ANGRY"):
        gen_params["temperature"] = 0.65

    return prompt_text, system_prompt, gen_params


# ---------------------------------------------------------------------------
# 子 Agent 能力描述
# ---------------------------------------------------------------------------

def _build_agent_capabilities_block() -> str:
    """构建子 Agent 能力描述块。"""
    lines = ["【可用能力（委托协议）】"]
    lines.append("如果用户的请求需要以下专业能力，在回复中嵌入 [DELEGATE:agent_id:任务描述] 标记委托对应 Agent。")
    lines.append("如果是日常闲聊/情感交流/知识问答，直接回复即可，不需要委托。")
    for agent_id, desc in AGENT_CAPABILITIES.items():
        lines.append(f"- {agent_id}：{desc}")
    lines.append("委托格式示例：[DELEGATE:code_agent:写一个快速排序算法]")
    return "\n".join(lines)
