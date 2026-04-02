"""chat_agent.py — ChatAgent 人格渲染管线（Phase XV）。

夏目蓝本人的"说话"模块。
职责：拿到上下文→构建 prompt→流式生成→反思链→净化→COMMIT。
调用者：DispatchAgent（通过 chat_agent_sse_generator 函数）。

架构原则：
  - ChatAgent 只负责「说出来」，不做路由/调度决策。
  - 所有输入通过 ChatAgentInput 结构体传入（sub_agent_context 可带子 Agent 结果）。
  - 所有输出经完整人格管线：角色人设 + 情感 + Bond + 风格蒸馏 + 反思链。
  - 不调用 SkillLoader，不做委托判断，不感知 sys_mode 路由。

关系：
  DispatchAgent  ──────────────▶  ChatAgent
   (路由决策/        ChatAgentInput   (人格渲染/
    Skill执行/        结构体传递       LLM流式生成/
    子Agent调度)                       COMMIT输出)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

_logger = logging.getLogger("Natsume.chat_agent")


# ---------------------------------------------------------------------------
# 输入/输出结构
# ---------------------------------------------------------------------------

@dataclass
class SubAgentContext:
    """子 Agent 执行结果，注入 ChatAgent prompt。"""
    agent_id: str = ""
    task: str = ""
    result_text: str = ""
    success: bool = True
    latency_ms: int = 0
    error: str = ""


@dataclass
class ChatAgentInput:
    """ChatAgent 的完整输入上下文。"""
    # ── 必填 ──
    request_id: str = ""
    session_id: str = ""
    user_message: str = ""           # 净化后的用户消息
    person_id: str = ""

    # ── 情感 / Bond 上下文 ──
    emotion_context: Dict[str, Any] = field(default_factory=dict)
    emotion_snapshot: Dict[str, Any] = field(default_factory=dict)
    bond_summary: Dict[str, Any] = field(default_factory=dict)

    # ── 检索结果（RecallFusion 输出）──
    recall_slot_text: Dict[str, str] = field(default_factory=dict)
    retrieval_evidence_items: List[Dict[str, Any]] = field(default_factory=list)
    retrieval_summary: Dict[str, Any] = field(default_factory=lambda: {"status": "SKIPPED"})

    # ── 子 Agent 执行结果（执行路径时由 DispatchAgent 填入）──
    sub_agent_context: Optional[SubAgentContext] = None

    # ── 通道参数 ──
    render_target_lang: str = "zh"   # Phase XV: 双轨，文字通道永远 zh
    reply_channel: Optional[str] = None   # GATEWAY / ASR / WEB 等
    suppress_edge_tts: Optional[bool] = None
    suppress_edge_vts: Optional[bool] = None
    reply_depth_policy: str = "normal"    # "normal" / "gateway"（QQ 要求更短）

    # ── 链路追踪 ──
    thread_id: Optional[str] = None
    ts_ms: int = 0
    intent_draft: Dict[str, Any] = field(default_factory=dict)

    # ── LLM 运行时（由调用者 resolve 后传入，避免重复 resolve）──
    llm_runtime: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# 工具函数（从 top_agent.py 复用）
# ---------------------------------------------------------------------------

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _airlock_sanitize_output(text: str) -> str:
    """出口净化：null byte + 长度截断。"""
    text = text.replace("\x00", "").strip()
    max_chars = int(os.environ.get("HOST_QA_MAX_RESPONSE_CHARS") or "4000")
    if len(text) > max_chars:
        truncated = text[:max_chars]
        for ch in ("。", "！", "？", ".", "!", "?"):
            last = truncated.rfind(ch)
            if last > max_chars * 0.7:
                truncated = truncated[: last + 1]
                break
        text = truncated
    return text


def _build_reply_preview(text: str, *, limit: int = 120) -> str:
    normalized = str(text or "").strip().replace("\r", " ").replace("\n", " ")
    if len(normalized) <= int(limit):
        return normalized
    return normalized[: max(1, int(limit) - 3)].rstrip() + "..."


def _infer_emotion_tag_for_tts(narrative: str, current_tag: str) -> str:
    import re as _re
    _EMOTION_INFER_KEYWORDS: list[tuple[str, str]] = [
        ("HAPPY", "哈哈|开心|高兴|嘿嘿|笑|太好了|耶"),
        ("SAD", "难过|伤心|哭|呜|抱歉|对不起"),
        ("ANGRY", "生气|气死|烦"),
        ("LOVING", "谢谢|感谢|感动|喜欢|爱你"),
        ("SURPRISED", "哇|真的吗|竟然"),
    ]
    if not narrative or str(current_tag or "").strip().upper() != "NEUTRAL":
        return str(current_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    text = (narrative or "")[:500]
    for tag, pattern in _EMOTION_INFER_KEYWORDS:
        if _re.search(pattern, text, _re.IGNORECASE):
            return tag
    return "NEUTRAL"


async def _post_speak_sentence(
    *,
    text: str,
    utterance_id: str,
    emotion_tag: str,
    request_id: str,
    session_id: str,
) -> None:
    """Phase XV P7：向 Edge 发送单句 TTS 请求（fire-and-forget）。

    语音通道永远使用日语（lang_hint="ja"），不受 render_target_lang 影响。
    """
    try:
        from host_brain.channels.edge_rpc_client import post_speak  # noqa: PLC0415
        await post_speak(
            text=text,
            utterance_id=utterance_id,
            request_id=request_id,
            session_id=session_id,
            emotion_tag=emotion_tag,
            lang_hint="ja",  # Phase XV: 语音通道永远日语
        )
    except Exception as exc:
        _logger.debug("[%s] P7 sentence TTS failed (non-critical): %s", request_id, exc)


def _extract_visible_text(raw: str) -> str:
    """从 LLM 原始输出中提取可见文本（支持 JSON 包装格式）。"""
    text = raw.strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                narrative = parsed.get("narrative")
                if isinstance(narrative, dict):
                    # Phase XV P6：双轨语言，文字通道取 text_zh
                    return str(narrative.get("text_zh") or "").strip()
        except (json.JSONDecodeError, ValueError):
            pass
    return text


def _extract_tts_text(raw: str) -> str:
    """提取 TTS 语音通道文本（日语，text_ja 字段）。"""
    text = raw.strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                narrative = parsed.get("narrative")
                if isinstance(narrative, dict):
                    # Phase XV P6：TTS 语音通道取 text_ja
                    return str(narrative.get("text_ja") or "").strip()
        except (json.JSONDecodeError, ValueError):
            pass
    return ""


def _should_buffer_stream_output(
    *,
    emotion_context: Any,
    bond_summary: Any,
) -> bool:
    """高风险对话缓冲判断。"""
    env_key = "HOST_STREAM_REFLECTION_BUFFER_ENABLED"
    raw = str(os.environ.get(env_key, "")).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    try:
        from host_brain.app.dependencies import get_reflection_chain  # noqa: PLC0415
        reflection = get_reflection_chain()
        return bool(
            reflection.should_reflect(
                emotion_tag=str((emotion_context or {}).get("emotion_tag") or ""),
                intent=str((bond_summary or {}).get("intent") or ""),
                bond_stage=str((bond_summary or {}).get("stage") or "STRANGER"),
                turn_count=int((emotion_context or {}).get("turn_count") or 1),
            )
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 子 Agent 结果渲染（P1 改造：不再用正则拼接，统一走 ChatAgent 人格渲染）
# ---------------------------------------------------------------------------

def _build_sub_agent_context_prompt(ctx: SubAgentContext) -> str:
    """将子 Agent 结果转换为注入 ChatAgent prompt 的上下文块。

    Phase XV 核心改造：子 Agent 结果不再直接输出给用户，
    而是作为 context 注入 ChatAgent，由 ChatAgent 用夏目蓝语气重新组织。
    """
    if not ctx or not ctx.result_text:
        return ""

    if not ctx.success or ctx.error:
        return (
            f"[子 Agent 执行结果]\n"
            f"  执行状态：失败\n"
            f"  Agent：{ctx.agent_id}\n"
            f"  任务：{ctx.task}\n"
            f"  错误：{ctx.error or '未知错误'}\n"
            f"  说明：请用自然的语气告知用户任务遇到了问题，不要暴露内部错误细节。\n"
        )

    return (
        f"[子 Agent 执行结果]\n"
        f"  Agent：{ctx.agent_id}\n"
        f"  任务：{ctx.task}\n"
        f"  结果：{ctx.result_text[:2000]}\n"
        f"  说明：请用夏目蓝的语气自然地呈现上述结果，不要逐字复读，要像自己说话一样组织语言。\n"
    )


# ---------------------------------------------------------------------------
# ChatAgent 主生成器
# ---------------------------------------------------------------------------

async def chat_agent_sse_generator(
    agent_input: ChatAgentInput,
) -> AsyncGenerator[str, None]:
    """ChatAgent SSE 生成器（Phase XV 核心）。

    职责：接收 ChatAgentInput → 构建完整人格 prompt → LLM 流式生成
          → SentenceSplitter → 反思链 → 净化 → COMMIT → 异步后处理。

    调用者：DispatchAgent（top_agent.py 改造后调用此函数）。
    """
    request_id = agent_input.request_id
    session_id = agent_input.session_id
    user_message = agent_input.user_message
    person_id = agent_input.person_id
    emotion_context = agent_input.emotion_context
    emotion_snapshot = agent_input.emotion_snapshot
    bond_summary = agent_input.bond_summary
    recall_slot_text = agent_input.recall_slot_text
    retrieval_evidence_items = agent_input.retrieval_evidence_items
    retrieval_summary = agent_input.retrieval_summary
    render_target_lang = agent_input.render_target_lang  # Phase XV: 固定 "zh"
    thread_id = agent_input.thread_id
    ts_ms = agent_input.ts_ms or int(time.time() * 1000)
    intent_draft = agent_input.intent_draft or {}
    sub_agent_ctx = agent_input.sub_agent_context

    # ── 1. 构建 prompt（复用完整 top_agent_prompt 管线）──────────────
    from host_brain.app.top_agent_prompt import build_top_agent_prompt  # noqa: PLC0415

    # 如果有子 Agent 结果，注入到 user_message 作为额外上下文
    # Phase XV P1: 子 Agent 结果统一经 ChatAgent 人格渲染，不直接输出
    effective_user_message = user_message
    sub_agent_context_prompt = ""
    if sub_agent_ctx:
        sub_agent_context_prompt = _build_sub_agent_context_prompt(sub_agent_ctx)
        # 子 Agent 结果作为额外 context 注入到 intent_draft，让 prompt_builder 感知
        intent_draft = {
            **intent_draft,
            "sub_agent_context": sub_agent_context_prompt,
            "sub_agent_id": sub_agent_ctx.agent_id,
        }

    # 回复深度策略：Gateway 通道使用更短的回复
    reply_depth_hint = ""
    if agent_input.reply_depth_policy == "gateway":
        reply_depth_hint = "（回复简洁，12-60字，适合移动端消息）"

    try:
        prompt_text, system_prompt, gen_params = build_top_agent_prompt(
            user_message=effective_user_message + reply_depth_hint,
            render_target_lang=render_target_lang,
            emotion_context=emotion_context,
            emotion_snapshot=emotion_snapshot,
            retrieval_summary=retrieval_summary,
            retrieval_evidence_items=retrieval_evidence_items,
            person_id=person_id,
            session_id=session_id,
            request_id=request_id,
            thread_id=thread_id,
            bond_summary=bond_summary,
            recall_slot_text=recall_slot_text if recall_slot_text else None,
        )
    except Exception as _pb_exc:
        _logger.warning("[%s] ChatAgent prompt_builder failed: %s", request_id, _pb_exc)
        yield _sse({
            "phase": "COMMIT", "done": True, "decision": "REJECT",
            "reason_code": "PROMPT_BUILD_FAILED", "request_id": request_id,
            "narrative": "……嗯，蓝这边有点小状况，等一下再来找我好不好？",
        })
        return

    # ── 2. 解析 LLM 运行时（ChatAgent 专用配置）─────────────────────
    llm_runtime = agent_input.llm_runtime
    if llm_runtime is None:
        try:
            from host_brain.app.graph_config_builder import (  # noqa: PLC0415
                resolve_host_llm_runtime_async,
                resolve_chat_agent_llm_runtime,
            )
            base_runtime = await resolve_host_llm_runtime_async(
                rollout_key=session_id,
                scene_type="CHAT",
            )
            llm_runtime = resolve_chat_agent_llm_runtime(base_runtime=base_runtime)
        except Exception as _rt_exc:
            _logger.warning("[%s] ChatAgent llm_runtime resolve failed: %s", request_id, _rt_exc)
            yield _sse({
                "phase": "COMMIT", "done": True, "decision": "REJECT",
                "reason_code": "LLM_UNAVAILABLE", "request_id": request_id,
                "narrative": "……抱歉，蓝现在脑子有点转不动，过一会儿再来找我吧。",
            })
            return

    if not llm_runtime.get("available"):
        yield _sse({
            "phase": "COMMIT", "done": True, "decision": "REJECT",
            "reason_code": "LLM_UNAVAILABLE", "request_id": request_id,
            "narrative": "……抱歉，蓝现在脑子有点转不动，过一会儿再来找我吧。",
        })
        return

    provider_name = str(llm_runtime["provider_name"])
    model_id = str(llm_runtime["model_id"])
    timeout_ms = int(llm_runtime["timeout_ms"])
    api_key = str(llm_runtime.get("llm_api_key") or "").strip()
    base_url = str(llm_runtime.get("base_url_override") or "").strip() or None

    # ChatAgent 专属 gen_params（高温度，自然流畅）
    agent_gen_params = dict(gen_params or {})
    agent_overrides = llm_runtime.get("agent_gen_params") or {}
    agent_gen_params.update({
        k: v for k, v in agent_overrides.items()
        if v is not None
    })

    # ── 3. 流式生成 + SentenceSplitter ──────────────────────────────
    from host_brain.integrations.llm_helpers import astream_llm_chunks  # noqa: PLC0415
    from host_brain.app.prompt_builder import repair_utf8_latin1_mojibake  # noqa: PLC0415
    from host_brain.app.sentence_splitter import SentenceSplitter  # noqa: PLC0415

    # 诊断日志：确认 system_prompt 是否包含人设（前 150 字）
    _logger.info(
        "[%s] ChatAgent system_prompt preview: %s",
        request_id,
        (system_prompt or "")[:150].replace("\n", "↵"),
    )

    splitter = SentenceSplitter()
    full_text = ""
    rendered_text = ""
    first_token_ms: int | None = None
    sentence_index = 0
    llm_started_ms = int(time.time() * 1000)

    buffer_stream = _should_buffer_stream_output(
        emotion_context=emotion_context,
        bond_summary=bond_summary or None,
    )

    # Phase XV P7：分句 TTS 开关
    # suppress_edge_tts=True 时（Gateway 模式），本地 TTS 不触发
    _suppress_tts = bool(agent_input.suppress_edge_tts)
    _tts_sentence_stream_enabled = (
        not _suppress_tts
        and str(os.environ.get("HOST_TTS_SENTENCE_STREAM_ENABLED") or "true").strip().lower()
        not in ("0", "false", "no", "off")
    )
    # 分句 TTS 用的 SentenceSplitter（对 text_ja 分句）
    tts_splitter = SentenceSplitter() if _tts_sentence_stream_enabled else None  # type: ignore[assignment]
    tts_sentence_index = 0
    # 追踪已渲染的 text_ja（用于增量提取）
    _tts_rendered_text = ""

    try:
        async for chunk in astream_llm_chunks(
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            prompt_text=prompt_text,
            system_prompt=system_prompt,
            gen_params=agent_gen_params,
            base_url_override=base_url,
        ):
            if first_token_ms is None:
                first_token_ms = int(time.time() * 1000) - llm_started_ms

            full_text += chunk
            visible = _extract_visible_text(full_text)
            if not visible:
                continue

            if visible.startswith(rendered_text):
                new_chunk = visible[len(rendered_text):]
            else:
                new_chunk = visible
            if not new_chunk:
                continue

            rendered_text = visible

            if buffer_stream:
                continue

            yield _sse({"phase": "RENDER", "chunk": new_chunk})

            for sentence in splitter.feed(new_chunk):
                yield _sse({
                    "phase": "SENTENCE",
                    "sentence": sentence,
                    "sentence_index": sentence_index,
                })
                sentence_index += 1

            # Phase XV P7：分句 TTS（对 text_ja 逐句触发，不阻塞流式回复）
            if _tts_sentence_stream_enabled and tts_splitter is not None:
                # 提取当前累积的 text_ja
                _tts_full = _extract_tts_text(full_text)
                if _tts_full and _tts_full.startswith(_tts_rendered_text):
                    _tts_new = _tts_full[len(_tts_rendered_text):]
                    _tts_rendered_text = _tts_full
                    for _tts_sentence in tts_splitter.feed(_tts_new):
                        if _tts_sentence.strip():
                            _etag = _infer_emotion_tag_for_tts(
                                _tts_sentence,
                                str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                            )
                            asyncio.create_task(_post_speak_sentence(
                                text=_tts_sentence,
                                utterance_id=f"utt_{request_id}_{tts_sentence_index:03d}",
                                emotion_tag=_etag,
                                request_id=request_id,
                                session_id=session_id,
                            ))
                            tts_sentence_index += 1

    except (TimeoutError, Exception) as _llm_exc:
        _logger.warning("[%s] ChatAgent LLM failed: %s", request_id, _llm_exc)
        if not full_text.strip():
            # 根据 render_target_lang 选择 fallback 语言
            full_text = "ちょっと待ってね…" if render_target_lang == "ja" else "等一下呢…"
            if not buffer_stream:
                yield _sse({"phase": "RENDER", "chunk": full_text})
                for sentence in splitter.feed(full_text):
                    yield _sse({
                        "phase": "SENTENCE",
                        "sentence": sentence,
                        "sentence_index": sentence_index,
                    })
                    sentence_index += 1

    # ── 4. 组装 narrative ────────────────────────────────────────────
    narrative = repair_utf8_latin1_mojibake(full_text) or full_text
    narrative = _extract_visible_text(narrative) or narrative

    # 提取 TTS 文本（Phase XV 双轨语言：text_ja 走语音通道）
    tts_text = _extract_tts_text(full_text)

    # ── 5. apply_response_intelligence（反思链 + 多模态后缀）─────────
    response_intelligence_meta: dict[str, Any] = {}
    try:
        from host_brain.app.intelligent_runtime import apply_response_intelligence  # noqa: PLC0415

        narrative, response_intelligence_meta = apply_response_intelligence(
            request_id=request_id,
            user_message=user_message,
            narrative=narrative,
            emotion_context=emotion_context,
            bond_summary=bond_summary or None,
            render_lang=render_target_lang,
            allow_rewrite=buffer_stream,
        )

        if buffer_stream and narrative:
            rendered_text = narrative
            yield _sse({"phase": "RENDER", "chunk": narrative})
            for sentence in splitter.feed(narrative):
                yield _sse({
                    "phase": "SENTENCE",
                    "sentence": sentence,
                    "sentence_index": sentence_index,
                })
                sentence_index += 1
        elif narrative.startswith(rendered_text):
            postprocess_chunk = narrative[len(rendered_text):]
            if postprocess_chunk:
                rendered_text = narrative
                yield _sse({"phase": "RENDER", "chunk": postprocess_chunk})
                for sentence in splitter.feed(postprocess_chunk):
                    yield _sse({
                        "phase": "SENTENCE",
                        "sentence": sentence,
                        "sentence_index": sentence_index,
                    })
                    sentence_index += 1
    except Exception as _ri_exc:
        response_intelligence_meta = {
            "reflection": {"status": "DEGRADED", "reason_code": str(_ri_exc)[:100]},
        }
        _logger.debug("[%s] ChatAgent response_intelligence failed: %s", request_id, _ri_exc)

    # flush 剩余
    remainder = splitter.flush()
    if remainder:
        yield _sse({
            "phase": "SENTENCE",
            "sentence": remainder,
            "sentence_index": sentence_index,
        })
        sentence_index += 1

    # ── 6. 出口净化 ──────────────────────────────────────────────────
    narrative = _airlock_sanitize_output(narrative)

    # PII 出口检查
    _pii_egress_stats: dict[str, Any] = {}
    try:
        from host_brain.airlock.pii_filter import _check_channel, pii_scrub_text  # noqa: PLC0415
        if _check_channel("EGRESS"):
            _pii_eg = pii_scrub_text(narrative, context="egress")
            narrative = _pii_eg.scrubbed_text
            if _pii_eg.has_pii:
                _pii_egress_stats = {
                    "detections": _pii_eg.total_detections,
                    "rules": sorted({d["rule_id"] for d in _pii_eg.detections}),
                }
    except Exception:
        pass

    # ── 7. INPUT_COMPLETED 同步写入 ──────────────────────────────────
    total_latency_ms = int(time.time() * 1000) - ts_ms
    llm_latency_ms = int(time.time() * 1000) - llm_started_ms

    try:
        from host_brain.app.dependencies import get_ledger as _get_pre_ledger  # noqa: PLC0415
        _get_pre_ledger().log_voice_event({
            "request_id": request_id,
            "session_id": session_id,
            "event_type": "INPUT_COMPLETED",
            "direction": "HOST_INTERNAL",
            "status": "OK" if narrative.strip() else "DEGRADED",
            "reason_code": None if narrative.strip() else "EMPTY_NARRATIVE",
            "stage": "chat_agent.pre_commit",
            "detail": (
                f"source={'GATEWAY' if agent_input.reply_channel else 'CHAT_API'} "
                f"text_len={len(user_message)} reply_len={len(narrative)}"
            ),
            "payload": {
                "source": agent_input.reply_channel or "CHAT_API",
                "person_id": person_id,
                "input_preview": _build_reply_preview(user_message, limit=160),
                "reply_preview": _build_reply_preview(narrative, limit=160),
                "render_target_lang": render_target_lang,
                "sub_agent_id": sub_agent_ctx.agent_id if sub_agent_ctx else None,
            },
            "ts_ms": int(time.time() * 1000),
        })
    except Exception as _ve_exc:
        _logger.debug("[%s] pre-commit INPUT_COMPLETED failed: %s", request_id, _ve_exc)

    # ── 8. COMMIT ────────────────────────────────────────────────────
    yield _sse({
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "narrative": narrative,
        "request_id": request_id,
        "audit_refs": {
            "request_id": request_id,
            "path": f"chat_agent:sub={sub_agent_ctx.agent_id}" if sub_agent_ctx else "chat_agent:direct",
            "latency_ms": total_latency_ms,
            "first_token_ms": first_token_ms,
            "llm_latency_ms": llm_latency_ms,
            "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
            "stream_buffered": buffer_stream,
            "model_id": model_id,
            "render_target_lang": render_target_lang,
            "pii_stats": {"egress": _pii_egress_stats} if _pii_egress_stats else {},
        },
    })

    # ── 9. 异步后处理（fire-and-forget）─────────────────────────────
    # 复用 top_agent._async_post_process（不动后处理逻辑）
    try:
        from host_brain.app.top_agent import _async_post_process  # noqa: PLC0415
        asyncio.create_task(
            _async_post_process(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=user_message,
                narrative=narrative,
                emotion_context=emotion_context,
                bond_summary=bond_summary,
                ts_ms=ts_ms,
                render_target_lang=render_target_lang,
                total_latency_ms=total_latency_ms,
                llm_latency_ms=llm_latency_ms,
                first_token_ms=first_token_ms,
                provider_name=provider_name,
                model_id=model_id,
                buffer_stream=buffer_stream,
                delegated_agents=(
                    [{"agent_id": sub_agent_ctx.agent_id,
                      "success": sub_agent_ctx.success,
                      "latency_ms": sub_agent_ctx.latency_ms}]
                    if sub_agent_ctx else []
                ),
                retrieval_summary=retrieval_summary,
                gov_decision={"decision": "APPROVE"},
                pii_ingress_stats={},
                pii_egress_stats=_pii_egress_stats,
                # Phase XV trace 增强：传入 LLM 输入摘要和召回证据
                llm_input_digest=(system_prompt or "")[:500] or None,
                retrieval_evidence_items=list(retrieval_evidence_items or []),
            )
        )
    except Exception as _pp_exc:
        _logger.debug("[%s] ChatAgent post_process task failed: %s", request_id, _pp_exc)
