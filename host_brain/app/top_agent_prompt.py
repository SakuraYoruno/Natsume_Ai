"""top_agent_prompt.py — 顶级 Agent prompt 构建（Phase XIV v4）。

核心策略：**复用旧链路完整的 prompt 构建管线**：
  build_chat_runtime_input()
    → build_chat_prompt_runtime_payload()   ← 会调用 build_prompt_intelligence()
       → 注入 predictive_state / self_reflection / bond_narrative /
         persona_expression_hint / inner_monologue / emotion_vector 等全部槽位
    → render_node_generation_prompt(node_id="top_agent")

与旧链路唯一的差异：
  1. node_id 使用 "top_agent"（走顶级 Agent 专属 role_instruction）
  2. 追加子 Agent 能力描述块
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from host_brain.app.sub_agent_protocol import AGENT_CAPABILITIES

_logger = logging.getLogger("Natsume.top_agent_prompt")

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
    thread_id: str | None = None,
    bond_summary: dict[str, Any] | None = None,
    recall_slot_text: dict[str, str] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """构建顶级 Agent 的 prompt。

    **走完整的 prompt 构建管线**（和旧链路 _build_stream_chat_prompt 等价）：
    ChatRuntimeInput → build_chat_prompt_runtime_payload() → render_node_generation_prompt()

    这确保 build_prompt_intelligence() 被调用，注入全部 20+ 个槽位。

    Returns:
        (prompt_text, system_prompt, gen_params)
    """
    from host_brain.app.chat_runtime_input import build_chat_runtime_input  # noqa: PLC0415
    from host_brain.app.chat_context_builder import (  # noqa: PLC0415
        build_chat_prompt_runtime_payload,
    )
    from host_brain.app.prompt_builder import (  # noqa: PLC0415
        render_node_generation_prompt,
        _build_node_role_instruction,
    )
    from host_brain.integrations.llm_helpers import adjust_gen_params_for_emotion  # noqa: PLC0415

    emotion_context = emotion_context or {}
    emotion_snapshot = emotion_snapshot or {}

    # ── 1. 构建完整的 runtime_input ──────────────────────────────────
    runtime_input = build_chat_runtime_input(
        request_id=request_id,
        session_id=session_id,
        thread_id=thread_id,
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

    # ── 2. 走完整的 prompt 构建管线 ─────────────────────────────────
    # build_chat_prompt_runtime_payload 内部会调用 build_prompt_intelligence()，
    # 注入 predictive_state / self_reflection_pulse / bond_narrative /
    # persona_expression_hint / inner_monologue / emotion_vector 等全部数据。
    prompt_payload = build_chat_prompt_runtime_payload(runtime_input=runtime_input)

    # 情感调制 gen_params（和旧链路一致）
    if emotion_context:
        prompt_payload = adjust_gen_params_for_emotion(prompt_payload, emotion_context)

    gen_params = dict(prompt_payload.get("gen_params") or {})

    # ── 3. 渲染完整 prompt（包含全部 20+ 个注入槽位）─────────────────
    full_prompt = render_node_generation_prompt(
        node_id=TOP_AGENT_NODE_ID,
        runtime_payload=prompt_payload,
    )

    # ── 4. 追加子 Agent 能力描述 ─────────────────────────────────────
    agent_block = _build_agent_capabilities_block()
    full_prompt = full_prompt + "\n\n" + agent_block

    # ── 5. 拆分 system_prompt 和 prompt_text ─────────────────────────
    system_prompt = _build_node_role_instruction(
        TOP_AGENT_NODE_ID,
        render_target_lang=render_target_lang,
    )
    prompt_text = full_prompt + "\n\n" + f"【用户消息】\n{user_message}"

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
