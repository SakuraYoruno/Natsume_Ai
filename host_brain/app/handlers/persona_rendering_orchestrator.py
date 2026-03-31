"""PersonaRenderingOrchestrator — Phase XIV 父子级 Agent 拟人化渲染。

职责：
- 接收子 Agent 的原始产出（代码/翻译/执行结果/视觉分析）
- 构建拟人化渲染 prompt（复用 SCENE_CHAT 完整管线）
- 调用 LLM 流式生成拟人化回复
- 降级策略：渲染失败时直接返回原始产出

设计原则：
- 子 Agent 只负责"做事"，父 Agent（CHAT 管线）负责"说话"
- 复用 build_chat_runtime_input + _build_stream_chat_prompt 全套管线
  → character_profile + style_canon v5 + 台词参考 + Bond + 情感
- 场景感知指令模板告诉 LLM "你刚帮用户做了 xxx"
- 专业内容（代码/翻译原文等）原样保留，只改"说话的部分"

配置开关：
- HOST_PERSONA_RENDERING_ENABLED（默认 true）
- HOST_PERSONA_RENDERING_SCENES（默认 WORK_CODE,WORK_TRANSLATE,EXEC_INTENT,VISION_UI）
- HOST_PERSONA_RENDERING_MAX_RAW_CHARS（默认 4000）
- HOST_PERSONA_RENDERING_TIMEOUT_MS（默认 15000）
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Mapping

logger = logging.getLogger("Natsume.persona_rendering")

# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------

def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _parse_int_env(name: str, *, default: int, min_value: int = 0, max_value: int = 999999) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(min_value, min(value, max_value))


def _get_enabled_scenes() -> frozenset[str]:
    raw = str(os.environ.get("HOST_PERSONA_RENDERING_SCENES") or "").strip()
    if not raw:
        return frozenset({"WORK_CODE", "WORK_TRANSLATE", "EXEC_INTENT", "VISION_UI"})
    return frozenset(
        s.strip().upper()
        for s in raw.split(",")
        if s.strip()
    )


def is_persona_rendering_enabled(scene_type: str) -> bool:
    """检查给定场景是否启用拟人化渲染。"""
    if not _parse_bool_env("HOST_PERSONA_RENDERING_ENABLED", default=True):
        return False
    normalized_scene = str(scene_type or "").strip().upper()
    return normalized_scene in _get_enabled_scenes()


# ---------------------------------------------------------------------------
# 场景感知指令模板
# ---------------------------------------------------------------------------

_SCENE_RENDERING_INSTRUCTIONS: dict[str, str] = {
    "WORK_CODE": (
        "SCENE_CONTEXT:\n"
        "你刚帮用户完成了一个代码相关的任务。"
        "以下是技术处理的结果（请以此为事实依据，用你自己的方式回复用户）：\n"
        "---\n{raw_result}\n---\n\n"
        "渲染要求：\n"
        "- 用你自己的说话方式回复，不要像技术文档或客服\n"
        "- 代码块（```...```）原样保留，不做任何修改\n"
        "- 先简要说明你做了什么，代码附在后面\n"
        "- 如果结果包含解释，用你的语气重新表达，但技术事实不要改\n"
        "- 保持自然，不要刻意卖萌或过度装饰\n"
    ),
    "WORK_TRANSLATE": (
        "SCENE_CONTEXT:\n"
        "你刚帮用户完成了一个翻译任务。"
        "以下是翻译结果：\n"
        "---\n{raw_result}\n---\n\n"
        "渲染要求：\n"
        "- 简要介绍翻译内容，然后给出译文\n"
        "- 译文本身原样保留，不做修改\n"
        "- 用你自己的说话方式包裹，不要像翻译机器\n"
        "- 如果翻译有注释或说明，用你的语气重新表达\n"
    ),
    "EXEC_INTENT": (
        "SCENE_CONTEXT:\n"
        "你刚帮用户执行了一个操作请求。"
        "以下是执行结果：\n"
        "---\n{raw_result}\n---\n\n"
        "渲染要求：\n"
        "- 用你自己的方式汇报执行结果\n"
        "- 如果执行成功，自然地确认一下\n"
        "- 如果执行失败或部分失败，温和地说明原因和建议\n"
        "- 不要像系统通知，要像朋友在告诉你事情做好了\n"
    ),
    "VISION_UI": (
        "SCENE_CONTEXT:\n"
        "你刚帮用户分析了一张屏幕截图或界面。"
        "以下是分析结果：\n"
        "---\n{raw_result}\n---\n\n"
        "渲染要求：\n"
        "- 用你自己的话描述你看到了什么\n"
        "- 提供有用的建议或解读\n"
        "- 不要像在写报告，要像在和朋友聊天时描述屏幕内容\n"
    ),
}

# 兜底模板
_DEFAULT_RENDERING_INSTRUCTION = (
    "SCENE_CONTEXT:\n"
    "你刚帮用户完成了一个任务。以下是处理结果：\n"
    "---\n{raw_result}\n---\n\n"
    "渲染要求：\n"
    "- 用你自己的说话方式回复\n"
    "- 专业内容原样保留\n"
    "- 保持自然\n"
)


def _build_scene_instruction(scene_type: str, raw_result: str) -> str:
    """根据场景类型构建场景感知指令。"""
    max_chars = _parse_int_env(
        "HOST_PERSONA_RENDERING_MAX_RAW_CHARS",
        default=4000, min_value=500, max_value=16000,
    )
    truncated_result = raw_result
    if len(raw_result) > max_chars:
        truncated_result = raw_result[:max_chars] + f"\n... (truncated, original {len(raw_result)} chars)"

    template = _SCENE_RENDERING_INSTRUCTIONS.get(
        str(scene_type or "").strip().upper(),
        _DEFAULT_RENDERING_INSTRUCTION,
    )
    return template.format(raw_result=truncated_result)


# ---------------------------------------------------------------------------
# 拟人化渲染节点常量
# ---------------------------------------------------------------------------

PERSONA_RENDERER_NODE_ID = "persona_renderer"


# ---------------------------------------------------------------------------
# 核心渲染函数
# ---------------------------------------------------------------------------

async def render_with_persona(
    *,
    raw_narrative: str,
    scene_type: str,
    request_id: str,
    session_id: str,
    thread_id: str | None,
    user_message: str,
    render_target_lang: str,
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
    retrieval_summary: dict[str, Any] | None = None,
    retrieval_evidence_items: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """对子 Agent 的原始产出进行拟人化渲染。

    完整复用 SCENE_CHAT 的 prompt 构建管线：
    - character_profile（常驻角色人设）
    - style_canon v5（说话风格）
    - persona_examples（台词参考）
    - bond_aware_style（关系适配）
    - emotion_binding（情感绑定）
    - persona_expression_hint（人格化表达策略）

    在此基础上注入场景感知指令 + 子 Agent 的 raw_result。

    Args:
        raw_narrative: 子 Agent 的原始产出文本
        scene_type: 场景类型（WORK_CODE/WORK_TRANSLATE/EXEC_INTENT/VISION_UI）
        request_id: 请求 ID
        session_id: 会话 ID
        thread_id: 线程 ID
        user_message: 原始用户消息
        render_target_lang: 渲染目标语言（zh/ja）
        emotion_context: 情感上下文
        emotion_snapshot: 情感快照
        retrieval_summary: 检索摘要（可选，用于注入 prompt）
        retrieval_evidence_items: 检索证据（可选）

    Returns:
        (rendered_narrative, rendering_meta) —— 渲染后的回复 + 渲染元数据
    """
    rendering_started_ms = int(time.time() * 1000)
    rendering_meta: dict[str, Any] = {
        "status": "SKIPPED",
        "reason_code": "NOT_ATTEMPTED",
        "scene_type": scene_type,
    }

    if not raw_narrative or not raw_narrative.strip():
        rendering_meta["reason_code"] = "RAW_NARRATIVE_EMPTY"
        return raw_narrative, rendering_meta

    if not is_persona_rendering_enabled(scene_type):
        rendering_meta["reason_code"] = "PERSONA_RENDERING_DISABLED"
        return raw_narrative, rendering_meta

    try:
        # ── 延迟导入，避免循环依赖 ──────────────────────────────────
        from host_brain.app.chat_runtime_input import build_chat_runtime_input  # noqa: PLC0415
        from host_brain.app.chat_stream_handler import _build_stream_chat_prompt  # noqa: PLC0415
        from host_brain.app.graph_config_builder import resolve_host_llm_runtime_async  # noqa: PLC0415
        from host_brain.integrations.llm_helpers import (  # noqa: PLC0415
            adjust_gen_params_for_emotion,
            astream_llm_chunks,
        )
        from host_brain.app.prompt_builder import (  # noqa: PLC0415
            _truncate_for_prompt,
            repair_utf8_latin1_mojibake,
        )
        from host_brain.app.chat_stream_handler import (  # noqa: PLC0415
            _extract_stream_render_text,
        )

        # ── Step 1: 构建场景感知指令 ──────────────────────────────────
        scene_instruction = _build_scene_instruction(scene_type, raw_narrative)

        # ── Step 2: 构建 runtime_input（复用 SCENE_CHAT 完整管线）─────
        runtime_input = build_chat_runtime_input(
            request_id=request_id,
            session_id=session_id,
            thread_id=thread_id,
            user_message=user_message,
            render_target_lang=render_target_lang,
            retrieval_summary=retrieval_summary or {},
            retrieval_evidence_items=retrieval_evidence_items or [],
            emotion_state=emotion_context,
            emotion_snapshot=emotion_snapshot,
            scene_type="CHAT",  # 渲染阶段以 CHAT 身份构建 prompt
            scene_type_source="persona_rendering_orchestrator",
        )

        # ── Step 3: 构建完整 prompt（含 character_profile + style + persona 等）─
        prompt_build_result = await _build_stream_chat_prompt(
            runtime_input=runtime_input,
        )
        if len(prompt_build_result) >= 5:
            prompt_text, system_prompt, gen_params, _, prompt_meta = prompt_build_result
        else:
            prompt_text, system_prompt, gen_params, _ = prompt_build_result  # type: ignore[misc]

        # ── Step 4: 在 prompt 末尾追加场景感知指令 ─────────────────────
        prompt_text = f"{prompt_text}\n\n{scene_instruction}\n"

        # ── Step 5: 覆盖 system_prompt 为渲染专用 role_instruction ─────
        system_prompt = _build_persona_renderer_role_instruction(
            render_target_lang=render_target_lang,
            scene_type=scene_type,
        )

        # ── Step 6: 情感参数注入 ──────────────────────────────────────
        try:
            _adjusted = adjust_gen_params_for_emotion(
                {"gen_params": gen_params},
                emotion_context,
            )
            gen_params = _adjusted.get("gen_params") or gen_params
        except Exception:
            pass  # 降级：不调整生成参数

        # ── Step 7: LLM 运行时解析 ──────────────────────────────────
        llm_runtime = await resolve_host_llm_runtime_async(scene_type="CHAT")
        provider_name = str(llm_runtime.get("provider_name") or "gemini").strip().lower()
        model_id = str(llm_runtime.get("model_id") or "").strip()
        llm_api_key = str(llm_runtime.get("llm_api_key") or llm_runtime.get("api_key") or "").strip()
        timeout_ms = _parse_int_env(
            "HOST_PERSONA_RENDERING_TIMEOUT_MS",
            default=15000, min_value=3000, max_value=60000,
        )
        base_url_override = str(llm_runtime.get("base_url_override") or "").strip() or None
        available = bool(llm_runtime.get("available", True))

        if not available:
            rendering_meta = {
                "status": "DEGRADED",
                "reason_code": "LLM_UNAVAILABLE",
                "scene_type": scene_type,
                "latency_ms": max(0, int(time.time() * 1000) - rendering_started_ms),
            }
            logger.warning(
                "[%s] PersonaRendering LLM 不可用，降级返回原始 narrative",
                request_id,
            )
            return raw_narrative, rendering_meta

        if not llm_api_key:
            from host_brain.integrations.llm_helpers import _load_api_key_from_env  # noqa: PLC0415
            llm_api_key = str(_load_api_key_from_env(provider_name) or "").strip()

        # ── Step 8: 流式 LLM 生成 ────────────────────────────────────
        full_text = ""
        rendered_text = ""
        first_token_ms: int | None = None
        stream_started_ms = int(time.time() * 1000)

        async for chunk in astream_llm_chunks(
            provider_name=provider_name,
            model_id=model_id,
            api_key=llm_api_key,
            timeout_ms=timeout_ms,
            prompt_text=prompt_text,
            system_prompt=system_prompt,
            gen_params=gen_params,
            base_url_override=base_url_override,
        ):
            if not chunk:
                continue
            if first_token_ms is None:
                first_token_ms = max(0, int(time.time() * 1000) - stream_started_ms)
            full_text += chunk

            # 尝试提取可渲染文本（跳过 think tag 等）
            try:
                visible = _extract_stream_render_text(full_text)
            except Exception:
                visible = full_text
            if visible:
                rendered_text = visible

        # ── Step 9: 提取最终 narrative ────────────────────────────────
        from host_brain.app.prompt_builder import (  # noqa: PLC0415
            extract_gateway_narrative,
            extract_json_object_from_text,
        )

        final_narrative = repair_utf8_latin1_mojibake(rendered_text or full_text)

        # 尝试从 JSON 中提取 narrative
        if final_narrative:
            try:
                payload = extract_json_object_from_text(final_narrative)
                extracted = extract_gateway_narrative(payload)
                if extracted:
                    final_narrative = extracted
            except Exception:
                pass  # 非 JSON 格式，直接使用原文

        llm_latency_ms = max(0, int(time.time() * 1000) - stream_started_ms)

        if not final_narrative or not final_narrative.strip():
            # 渲染输出为空，降级返回原始 narrative
            rendering_meta = {
                "status": "DEGRADED",
                "reason_code": "RENDERING_OUTPUT_EMPTY",
                "scene_type": scene_type,
                "latency_ms": llm_latency_ms,
                "first_token_ms": first_token_ms,
                "provider": provider_name,
                "model_id": model_id,
            }
            logger.warning(
                "[%s] PersonaRendering 输出为空，降级返回原始 narrative len=%d",
                request_id, len(raw_narrative),
            )
            return raw_narrative, rendering_meta

        rendering_meta = {
            "status": "OK",
            "scene_type": scene_type,
            "latency_ms": llm_latency_ms,
            "first_token_ms": first_token_ms,
            "provider": provider_name,
            "model_id": model_id,
            "raw_narrative_len": len(raw_narrative),
            "rendered_narrative_len": len(final_narrative),
        }

        total_latency_ms = max(0, int(time.time() * 1000) - rendering_started_ms)
        logger.info(
            "[%s] PersonaRendering 完成 scene=%s raw_len=%d rendered_len=%d llm_ms=%d total_ms=%d",
            request_id, scene_type, len(raw_narrative), len(final_narrative),
            llm_latency_ms, total_latency_ms,
        )
        return final_narrative, rendering_meta

    except Exception as exc:
        total_latency_ms = max(0, int(time.time() * 1000) - rendering_started_ms)
        rendering_meta = {
            "status": "DEGRADED",
            "reason_code": "RENDERING_EXCEPTION",
            "scene_type": scene_type,
            "detail": str(exc)[:200],
            "latency_ms": total_latency_ms,
        }
        logger.warning(
            "[%s] PersonaRendering 异常，降级返回原始 narrative: %s",
            request_id, exc,
        )
        return raw_narrative, rendering_meta


# ---------------------------------------------------------------------------
# 渲染节点 role_instruction
# ---------------------------------------------------------------------------

def _build_persona_renderer_role_instruction(
    *,
    render_target_lang: str = "zh",
    scene_type: str = "",
) -> str:
    """构建拟人化渲染节点的 system_prompt。"""
    scene_label = {
        "WORK_CODE": "代码任务",
        "WORK_TRANSLATE": "翻译任务",
        "EXEC_INTENT": "执行操作",
        "VISION_UI": "视觉分析",
    }.get(str(scene_type or "").strip().upper(), "任务")

    if str(render_target_lang).strip().lower() == "zh":
        return (
            f"角色：persona_renderer（拟人化渲染节点）。\n"
            f"目标：将{scene_label}的技术产出转化为你自己风格的自然回复。\n"
            f"策略：\n"
            f"- 以 SCENE_CONTEXT 中的技术结果为事实依据，不要编造或遗漏关键信息\n"
            f"- 代码块、翻译原文等专业内容必须原样保留，不做任何修改\n"
            f"- 只改变"说话的方式"——用你的语气、节奏和句式来表达\n"
            f"- 遵循【说话风格】和【台词参考】中的语言指导\n"
            f"- 不要说自己是 AI、助手或系统\n"
            f"- 不要使用客服式措辞（"已为您完成"、"请问还有什么需要帮助的吗"）\n"
            f"- 保持自然简洁，不要过度装饰\n"
        )
    return (
        f"角色：persona_renderer（拟人化レンダリングノード）。\n"
        f"目標：{scene_label}の技術出力を自然な話し言葉に変換する。\n"
        f"策略：\n"
        f"- SCENE_CONTEXT の技術結果を事実根拠とし、捏造や省略をしない\n"
        f"- コードブロック、翻訳原文などの専門的コンテンツはそのまま保持\n"
        f"- 「話し方」だけを変える——あなたの語気、リズム、文体で表現する\n"
        f"- 【話し方スタイル】と【台詞参考】の言語ガイダンスに従う\n"
        f"- AI、アシスタント、システムと自称しない\n"
        f"- カスタマーサポート調の表現を避ける\n"
        f"- 自然で簡潔に、過度な装飾をしない\n"
    )
