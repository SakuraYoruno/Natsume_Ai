"""Gateway Orchestrator Handler — SCENE_GATEWAY 专用 SSE 生成器。

职责：
- 接管所有 Gateway（微信/QQ/飞书/企微）输入的回复生成
- 内部控制 style canon 选择：纯文字→cn，需要语音→文字仍 cn + TTS 走 ja
- 意图识别复用 pre_intent_classifier
- 非 CHAT 场景委托给对应复杂场景处理器
- TTS 行为：suppress_local_play=True, suppress_vts=True（不驱动本地扬声器/VTS）
- 遵守 SSE 事件协议：SCENE_ROUTED → TOKEN → SENTENCE → COMMIT

安全保证：
- 本 handler 通过 SceneRouter.route_sse(SCENE_GATEWAY) 调用
- 上游已经过 AirlockQuarantine → Governor → RecallFusion 等前置安全节点
- 下游将经过 QAContract → EpisodicStore → Audit → AirlockSanitizer 后置安全节点
- 不绕过任何安全审计环节
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 环境变量解析辅助
# ---------------------------------------------------------------------------

def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _parse_bool(val: Any, *, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    raw = str(val).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


# ---------------------------------------------------------------------------
# SSE 事件构建辅助
# ---------------------------------------------------------------------------

def _sse_event(data: dict[str, Any]) -> str:
    """构建标准 SSE 事件字符串。"""
    return f"data:{json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_scene_routed(request_id: str, scene: str, source: str) -> str:
    return _sse_event({
        "phase": "SCENE_ROUTED",
        "request_id": request_id,
        "scene_type": scene,
        "scene_type_source": source,
        "ts_ms": int(time.time() * 1000),
    })


def _sse_token(request_id: str, token: str, index: int) -> str:
    return _sse_event({
        "phase": "TOKEN",
        "request_id": request_id,
        "token": token,
        "index": index,
        "ts_ms": int(time.time() * 1000),
    })


def _sse_sentence(request_id: str, sentence: str, index: int) -> str:
    return _sse_event({
        "phase": "SENTENCE",
        "request_id": request_id,
        "sentence": sentence,
        "sentence_index": index,
        "ts_ms": int(time.time() * 1000),
    })


def _sse_commit(
    request_id: str,
    decision: str,
    narrative: str,
    *,
    reason_code: str = "",
    detail: str = "",
    tts_ref: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "phase": "COMMIT",
        "done": True,
        "request_id": request_id,
        "decision": decision,
        "narrative": narrative,
        "reason_code": reason_code,
        "detail": detail,
        "ts_ms": int(time.time() * 1000),
    }
    if tts_ref:
        payload["tts_ref"] = tts_ref
    return _sse_event(payload)


# ---------------------------------------------------------------------------
# Style Canon 构建
# ---------------------------------------------------------------------------

def _build_gateway_style_bindings() -> dict[str, dict[str, Any]]:
    """为 Gateway 链路构建 style bindings，强制使用 natsume_ai_cn。

    Gateway 输出的文字回复始终为中文，因此 render_target_lang 固定为 "zh"，
    对应 style canon 为 natsume_ai_cn。
    """
    from host_brain.app.chat_context_builder import build_node_style_bindings  # noqa: PLC0415

    return build_node_style_bindings(render_target_lang="zh")


# ---------------------------------------------------------------------------
# LLM 调用（中文回复生成）
# ---------------------------------------------------------------------------

async def _generate_cn_narrative(
    *,
    message: str,
    session_id: str,
    request_id: str,
    style_bindings: dict[str, dict[str, Any]],
    emotion_context: dict[str, Any] | None = None,
    pre_recall_evidence: list[dict] | None = None,
) -> AsyncGenerator[str, None]:
    """调用 LLM 生成中文回复，流式输出 token。

    复用现有的 LLM 运行时解析和 astream_llm_chunks 接口，
    仅切换 style canon 为 cn（通过 render_target_lang="zh"）。
    """
    from host_brain.app.graph_config_builder import resolve_host_llm_runtime_async  # noqa: PLC0415
    from host_brain.app.chat_context_builder import (  # noqa: PLC0415
        build_context_pack,
        normalize_node_style_bindings,
    )
    from host_brain.integrations.llm_helpers import (  # noqa: PLC0415
        _load_api_key_from_env,
        astream_llm_chunks,
    )

    # 解析 LLM 运行时（provider/model/timeout 等）
    llm_runtime = await resolve_host_llm_runtime_async(scene_type="CHAT")
    provider_name = str(llm_runtime.get("provider_name") or "gemini").strip().lower()
    model_id = str(llm_runtime.get("model_id") or "").strip()
    api_key = str(llm_runtime.get("api_key") or _load_api_key_from_env(provider_name) or "").strip()
    timeout_ms = int(llm_runtime.get("timeout_ms") or 15000)
    base_url_override = llm_runtime.get("base_url_override") or None
    gen_params: dict[str, Any] = dict(llm_runtime.get("gen_params") or {"temperature": 0.7, "top_p": 0.9})

    # 构建 context_pack（中文 style）
    normalized_bindings = normalize_node_style_bindings(style_bindings)
    context_pack = build_context_pack(
        style_bindings=normalized_bindings,
        emotion_context=emotion_context or {},
    )

    # 构建 system prompt（从 style_bindings 中提取中文风格指令）
    style_instructions: list[str] = []
    for _node_id, row in normalized_bindings.items():
        instruction = str(row.get("style_instruction") or "").strip()
        if instruction:
            style_instructions.append(instruction)
    system_prompt = "\n".join(style_instructions) if style_instructions else "你是夏目藍，用温暖、克制的中文回复。"

    # 构建 prompt text（用户消息）
    prompt_text = message

    # 流式生成
    full_text = ""
    async for chunk in astream_llm_chunks(
        provider_name=provider_name,
        model_id=model_id,
        api_key=api_key,
        timeout_ms=timeout_ms,
        prompt_text=prompt_text,
        system_prompt=system_prompt,
        gen_params=gen_params,
        base_url_override=base_url_override,
    ):
        if chunk:
            full_text += chunk
            yield chunk

    if not full_text.strip():
        yield "…"


# ---------------------------------------------------------------------------
# TTS 异步生成（可选，不阻塞主流程）
# ---------------------------------------------------------------------------

async def _generate_tts_async(
    *,
    narrative_cn: str,
    request_id: str,
    session_id: str,
) -> str | None:
    """异步生成日语 TTS 音频（不本地播放、不驱动 VTS）。

    返回音频引用 ID（供 Gateway OutboundDispatcher 获取音频数据），
    失败时返回 None（降级为纯文字回复）。
    """
    tts_enabled = _parse_bool_env("HOST_GATEWAY_TTS_ENABLED", default=False)
    if not tts_enabled:
        return None

    try:
        from host_brain.channels.edge_rpc_client import post_speak  # noqa: PLC0415

        # TTS 调用通过 Edge RPC 的 /v1/edge/speak 端点
        # Edge 端 TTS 引擎负责实际的语音合成
        # 注意：suppress_local_play / suppress_vts 由 InputSourcePolicy 控制，
        # 此处调用 post_speak 仅生成音频，本地播放/VTS 由 Edge 端根据请求参数决定
        result = await post_speak(
            text=narrative_cn,
            request_id=request_id,
            session_id=session_id,
        )
        if result and result.success:
            # Edge RPC 返回的 event 中可能包含 audio_ref
            audio_ref = result.event.get("audio_ref")
            logger.info(
                "[%s] Gateway TTS 生成成功 status=%s audio_ref=%s",
                request_id, result.status, audio_ref,
            )
            return str(audio_ref) if audio_ref else "tts_generated"
        logger.warning(
            "[%s] Gateway TTS 生成失败（降级为纯文字）: %s",
            request_id, result.detail if result else "no_result",
        )
        return None
    except Exception as exc:
        logger.warning(
            "[%s] Gateway TTS 异常（降级为纯文字）: %s",
            request_id, exc,
        )
        return None


# ---------------------------------------------------------------------------
# 主入口：Gateway Orchestrator SSE 生成器
# ---------------------------------------------------------------------------

async def gateway_orchestrator_sse_generator(
    *,
    request_body: Any,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: dict[str, Any],
    scene_type: str,
    scene_type_source: str,
    governor_result: dict[str, Any],
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
    intent_id: Any,
    reason_code: Any,
    metrics: Any,
    _pre_recall_evidence: list[dict] | None = None,
    **kwargs: Any,
):
    """SCENE_GATEWAY 专用 SSE 生成器。

    由 SceneRouter.route_sse() 在 scene_type == "GATEWAY" 时调用。
    上游已通过 Airlock/Governor/Recall 等前置安全节点。

    流程：
    1. 意图识别（复用 pre_intent_classifier）
    2. 场景决策：CHAT → 中文回复生成；其他 → 委托给复杂场景处理器
    3. 流式输出 TOKEN/SENTENCE 事件
    4. 可选 TTS 异步生成
    5. 发出 COMMIT 事件
    """
    from host_brain.app.graph_config_builder import pre_intent_classifier  # noqa: PLC0415
    from host_brain.app.prompt_builder import SCENE_CHAT, SCENE_GATEWAY  # noqa: PLC0415

    message = str(getattr(request_body, "message", "") or "").strip()
    start_ts = time.monotonic()

    logger.info(
        "[%s] GatewayOrchestrator 启动 session=%s message=%.60s scene_source=%s",
        request_id, session_id, message, scene_type_source,
    )

    # ── 0. 发出 SCENE_ROUTED 事件 ──
    yield _sse_scene_routed(request_id, "GATEWAY", scene_type_source)

    # ── 1. 意图识别（复用 pre_intent_classifier）──
    sub_scene = pre_intent_classifier(message)
    logger.info(
        "[%s] GatewayOrchestrator 意图识别结果 sub_scene=%s",
        request_id, sub_scene,
    )

    # ── 2. 非 CHAT 场景：委托给复杂场景处理器 ──
    if sub_scene != SCENE_CHAT:
        logger.info(
            "[%s] GatewayOrchestrator 委托复杂场景: %s",
            request_id, sub_scene,
        )
        from host_brain.app.chat_handler_complex import _complex_scene_sse_generator  # noqa: PLC0415

        async for event in _complex_scene_sse_generator(
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            scene_type=sub_scene,
            scene_type_source="gateway_orchestrator",
            governor_result=governor_result,
            emotion_context=emotion_context,
            emotion_snapshot=emotion_snapshot,
            intent_id=intent_id,
            reason_code=reason_code,
            metrics=metrics,
        ):
            yield event
        return

    # ── 3. CHAT 场景：中文回复生成 ──
    style_bindings = _build_gateway_style_bindings()
    full_narrative = ""
    token_index = 0
    sentence_buffer = ""
    sentence_index = 0
    # 句子分割标记
    _sentence_delimiters = {"。", "！", "？", "…", "\n", "!", "?"}

    try:
        async for token in _generate_cn_narrative(
            message=message,
            session_id=session_id,
            request_id=request_id,
            style_bindings=style_bindings,
            emotion_context=emotion_context,
            pre_recall_evidence=_pre_recall_evidence,
        ):
            # 发出 TOKEN 事件
            yield _sse_token(request_id, token, token_index)
            token_index += 1
            full_narrative += token
            sentence_buffer += token

            # 检测句子边界，发出 SENTENCE 事件
            for delim in _sentence_delimiters:
                if delim in sentence_buffer:
                    parts = sentence_buffer.split(delim, 1)
                    completed_sentence = parts[0] + delim
                    if completed_sentence.strip():
                        yield _sse_sentence(request_id, completed_sentence.strip(), sentence_index)
                        sentence_index += 1
                    sentence_buffer = parts[1] if len(parts) > 1 else ""
                    break

        # 刷出残余 buffer
        if sentence_buffer.strip():
            yield _sse_sentence(request_id, sentence_buffer.strip(), sentence_index)
            sentence_index += 1

    except Exception as exc:
        logger.error(
            "[%s] GatewayOrchestrator LLM 生成异常: %s",
            request_id, exc,
        )
        # 降级：输出错误提示
        full_narrative = "抱歉，处理出了点问题，请稍后再试。"
        yield _sse_sentence(request_id, full_narrative, 0)

    # ── 4. 可选 TTS 异步生成 ──
    tts_ref: str | None = None
    need_voice = _parse_bool(
        getattr(request_body, "need_voice", None),
        default=False,
    )
    if need_voice and full_narrative.strip():
        tts_ref = await _generate_tts_async(
            narrative_cn=full_narrative,
            request_id=request_id,
            session_id=session_id,
        )

    # ── 5. 发出 COMMIT 事件 ──
    elapsed_ms = int((time.monotonic() - start_ts) * 1000)
    logger.info(
        "[%s] GatewayOrchestrator 完成 narrative_len=%d tts_ref=%s elapsed_ms=%d",
        request_id, len(full_narrative), tts_ref, elapsed_ms,
    )

    yield _sse_commit(
        request_id=request_id,
        decision="APPROVE",
        narrative=full_narrative.strip(),
        detail=f"gateway_orchestrator elapsed_ms={elapsed_ms}",
        tts_ref=tts_ref,
    )
