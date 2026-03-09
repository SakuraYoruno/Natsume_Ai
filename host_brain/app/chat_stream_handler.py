"""SSE 流式聊天处理模块。

从 fastapi_app.py 拆分，负责 GET /v1/chat/stream 和 POST /v1/chat/stream SSE 流式响应。

完整链路：
  Governor 鉴权 → 并行 Retrieval → stream_llm_chunks → SentenceSplitter
  → Airlock Ingress 检疫 → episodic memory 写入 → COMMIT 事件
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse

from host_brain.app.dependencies import (
    get_episodic_store,
    get_intent_engine,
    get_ledger,
    get_retrieval_pipeline,
)
from host_brain.app.emotion_context import (
    build_chat_governance_snapshot,
    resolve_chat_emotion_context,
)
from host_brain.app.graph_config_builder import (
    resolve_chat_retrieval_gate,
    resolve_chat_scene_type,
    resolve_host_llm_runtime,
)
from host_brain.app.observability_persist import persist_chat_episodic_memory
from host_brain.app.prompt_builder import (
    _truncate_for_prompt,
    infer_render_target_lang,
    repair_utf8_latin1_mojibake,
)
from host_brain.app.schemas import ChatRequest
from host_brain.app.sentence_splitter import SentenceSplitter
from host_brain.airlock.quarantine import quarantine_ingress
from host_brain.core.ids import new_request_id
from host_brain.governor.schemas import DECISION_APPROVE
from host_brain.integrations.llm_helpers import (
    adjust_gen_params_for_emotion,
    astream_llm_chunks,
)

_logger = logging.getLogger("Natsume.chat_stream_handler")

# ---------------------------------------------------------------------------
# 内部辅助：构建 prompt
# ---------------------------------------------------------------------------

async def _build_stream_chat_prompt(
    *,
    message: str,
    session_id: str,
    thread_id: Optional[str],
    mood_state: Optional[str],
    emotion_tag: Optional[str],
    request_id: str,
    ts_ms: int,
    retrieval_summary: Dict[str, Any] | None = None,
    retrieval_evidence_items: list[dict[str, Any]] | None = None,
) -> tuple[str, str, dict, dict]:
    """构建流式聊天的 prompt、system_prompt 和生成参数。

    返回: (prompt_text, system_prompt, gen_params, emotion_context)
    """

    class _StreamReq:
        def __init__(self) -> None:
            self.message = message
            self.session_id = session_id
            self.thread_id = thread_id
            self.mood_state = mood_state
            self.emotion_tag = emotion_tag
            self.emotion_range_hint = None
            self.emotion_context = None
            self.scene_type = None
            self.intent_draft = None

    request_body = _StreamReq()
    emotion_context = resolve_chat_emotion_context(request_body=request_body)

    from host_brain.app.chat_context_builder import (  # noqa: PLC0415
        build_chat_generation_prompt_payload as _build_payload,
        build_node_style_bindings as _build_style,
    )
    from host_brain.app.prompt_builder import render_host_generation_prompt  # noqa: PLC0415

    render_target_lang = infer_render_target_lang(user_message=message)
    node_style_bindings = _build_style(render_target_lang=render_target_lang)

    prompt_payload = _build_payload(
        request_id=request_id,
        session_id=session_id,
        thread_id=thread_id,
        user_message=message,
        render_target_lang=render_target_lang,
        retrieval_summary=retrieval_summary or {"status": "SKIPPED", "reason_code": "SSE_STREAM_MODE"},
        retrieval_evidence_items=retrieval_evidence_items or [],
        style_bindings=node_style_bindings,
        emotion_context=emotion_context if emotion_context else None,
    )
    if emotion_context:
        prompt_payload = adjust_gen_params_for_emotion(prompt_payload, emotion_context)

    gen_params = dict(prompt_payload.get("gen_params") or {})
    runtime_payload = prompt_payload.get("input") or dict(prompt_payload)
    prompt_text = render_host_generation_prompt(runtime_payload)
    system_prompt = (
        "You are 夏目藍 (Natsume Ai). "
        "You are NOT an AI or assistant — you are 夏目藍 herself. "
        "Output exactly one JSON object with keys narrative, intent_draft, meta. "
        "meta.draft_only must be true."
    )
    return prompt_text, system_prompt, gen_params, emotion_context


# ---------------------------------------------------------------------------
# 内部辅助：执行 Retrieval
# ---------------------------------------------------------------------------

async def _run_retrieval(
    *,
    request_id: str,
    message: str,
    session_id: str,
    ts_ms: int,
    emotion_tag: Optional[str],
) -> tuple[Dict[str, Any], list[dict[str, Any]]]:
    """并行执行 Retrieval，返回 (retrieval_summary, retrieval_evidence_items)。"""
    try:
        _retr_top_k = int(str(os.environ.get("HOST_RETR_TOP_K") or "5").strip())
    except ValueError:
        _retr_top_k = 5
    _retr_top_k = max(1, min(_retr_top_k, 20))

    pipeline = get_retrieval_pipeline()
    retrieval_result = await asyncio.to_thread(
        pipeline.run,
        request_id=request_id,
        query=message,
        intent_id=None,
        session_id=session_id,
        top_k=_retr_top_k,
        ts_ms=ts_ms,
        emotion_context=emotion_tag,
    )

    recall_output = dict(retrieval_result.output.get("recall") or {})
    rerank_output = dict(retrieval_result.output.get("rerank") or {})
    evidence_pack = dict(retrieval_result.output.get("evidence_pack") or {})
    rerank_topk_items = list(rerank_output.get("topk") or [])

    evidence_items: list[dict[str, Any]] = []
    for item in rerank_topk_items[:5]:
        if not isinstance(item, dict):
            continue
        snippet = _truncate_for_prompt(item.get("snippet"), limit=240)
        if not snippet:
            continue
        evidence_items.append({
            "evidence_ref": str(item.get("evidence_ref") or "").strip() or str(item.get("doc_id") or ""),
            "source_ref": str(item.get("source_ref") or "").strip(),
            "snippet": snippet,
            "score": item.get("score"),
            "confidence": item.get("confidence"),
        })

    summary: Dict[str, Any] = {
        "status": retrieval_result.status,
        "reason_code": retrieval_result.reason_code,
        "route_count": len(recall_output.get("routes") or []),
        "routes": list(recall_output.get("routes") or []),
        "candidate_count": int(recall_output.get("candidate_count") or 0),
        "rerank_topk": len(rerank_topk_items),
        "evidence_refs": list(evidence_pack.get("evidence_refs") or []),
        "degraded": bool(retrieval_result.output.get("degraded", False)),
    }
    return summary, evidence_items


# ---------------------------------------------------------------------------
# 核心 SSE 事件生成器
# ---------------------------------------------------------------------------

async def _sse_event_generator(
    *,
    message: str,
    session_id: str,
    thread_id: Optional[str],
    mood_state: Optional[str],
    emotion_tag: Optional[str],
    request_id: str,
    ts_ms: int,
    intent_draft: Dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    """完整链路 SSE 事件生成器。

    事件序列：
      PRESENCE(THINKING) → [Governor] → PRESENCE(GOV_APPROVED)
      → RENDER(chunk)* → SENTENCE(sentence)* → COMMIT
    """

    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    yield _sse({"phase": "PRESENCE", "event": "THINKING", "request_id": request_id})

    try:
        # ── 1. 构建 intent_draft ──────────────────────────────────────────
        if intent_draft is None:
            intent_draft = {
                "schema_version": "Natsume.intent.v1",
                "id": f"intent::{request_id}",
                "ts_ms": ts_ms,
                "session_id": session_id,
                "seq": 1,
                "type": "query",
                "action": "narrative_only",
                "target": "host",
                "args": {"text": message},
                "nonce": f"nonce::{request_id}",
            }

        # ── 2. 情感上下文 ─────────────────────────────────────────────────
        class _StreamReq:
            def __init__(self) -> None:
                self.message = message
                self.session_id = session_id
                self.thread_id = thread_id
                self.mood_state = mood_state
                self.emotion_tag = emotion_tag
                self.emotion_range_hint = None
                self.emotion_context = None
                self.scene_type = None
                self.intent_draft = None

        request_body_obj = _StreamReq()
        emotion_context = resolve_chat_emotion_context(request_body=request_body_obj)
        governance_snapshot = build_chat_governance_snapshot(
            emotion_context=emotion_context,
            ts_ms=ts_ms,
        )

        # ── 3. Governor 鉴权 ──────────────────────────────────────────────
        engine = get_intent_engine()
        governor_result = await asyncio.to_thread(
            engine.evaluate_intent,
            raw_intent=intent_draft,
            request_id=request_id,
            thread_id=thread_id,
            governance_snapshot=governance_snapshot,
        )

        decision = governor_result.get("decision")
        intent_id = governor_result.get("intent_id")

        if decision != DECISION_APPROVE:
            reason = governor_result.get("reason_code") or "REJECTED"
            yield _sse({"phase": "COMMIT", "done": True, "decision": "REJECT",
                        "reason_code": reason, "request_id": request_id})
            return

        yield _sse({"phase": "PRESENCE", "event": "GOV_APPROVED", "request_id": request_id})

        # ── 4. 并行启动 Retrieval ─────────────────────────────────────────
        scene_type, _ = resolve_chat_scene_type(request_body=request_body_obj)
        retrieval_enabled, retrieval_disabled_reason = resolve_chat_retrieval_gate(scene_type=scene_type)

        retrieval_task: asyncio.Task | None = None
        if retrieval_enabled:
            _retr_emotion = str(emotion_context.get("emotion_tag") or "") or None
            retrieval_task = asyncio.create_task(
                _run_retrieval(
                    request_id=request_id,
                    message=message,
                    session_id=session_id,
                    ts_ms=ts_ms,
                    emotion_tag=_retr_emotion,
                )
            )

        # ── 5. 检查 LLM 可用性 ────────────────────────────────────────────
        llm_runtime = resolve_host_llm_runtime()
        provider_name = str(llm_runtime["provider_name"])
        model_id = str(llm_runtime["model_id"])
        timeout_ms_val = int(llm_runtime["timeout_ms"])
        llm_api_key = str(llm_runtime.get("llm_api_key") or "").strip()
        available = bool(llm_runtime["available"])

        if not available:
            if retrieval_task is not None:
                retrieval_task.cancel()
            yield _sse({"phase": "COMMIT", "done": True, "decision": "REJECT",
                        "reason_code": "LLM_UNAVAILABLE",
                        "narrative": "服务暂时不可用，请稍后再试。",
                        "request_id": request_id})
            return

        # ── 6. 构建 prompt（此时 Retrieval 仍在并行运行）────────────────
        prompt_text, system_prompt, gen_params, _ = await _build_stream_chat_prompt(
            message=message,
            session_id=session_id,
            thread_id=thread_id,
            mood_state=mood_state,
            emotion_tag=emotion_tag,
            request_id=request_id,
            ts_ms=ts_ms,
            # Retrieval 结果尚未就绪，先用空摘要构建 prompt；
            # 若需要 Retrieval 注入 prompt，可在此 await retrieval_task 后再构建。
            retrieval_summary=None,
            retrieval_evidence_items=None,
        )

        # ── 7. 流式生成 + SentenceSplitter ───────────────────────────────
        splitter = SentenceSplitter()
        full_text = ""
        sentence_index = 0

        async for chunk in astream_llm_chunks(
            provider_name=provider_name,
            model_id=model_id,
            api_key=llm_api_key,
            timeout_ms=timeout_ms_val,
            prompt_text=prompt_text,
            system_prompt=system_prompt,
            gen_params=gen_params,
        ):
            full_text += chunk
            yield _sse({"phase": "RENDER", "chunk": chunk})

            # 句子边界检测
            for sentence in splitter.feed(chunk):
                yield _sse({
                    "phase": "SENTENCE",
                    "sentence": sentence,
                    "sentence_index": sentence_index,
                })
                sentence_index += 1

        # flush 剩余未成句文本
        remainder = splitter.flush()
        if remainder:
            yield _sse({
                "phase": "SENTENCE",
                "sentence": remainder,
                "sentence_index": sentence_index,
            })
            sentence_index += 1

        # ── 8. 等待 Retrieval 结果 ────────────────────────────────────────
        retrieval_summary: Dict[str, Any] = {}
        if retrieval_task is not None:
            try:
                retrieval_summary, _ = await retrieval_task
            except Exception as exc:
                _logger.warning("retrieval task failed in stream mode: %s", exc,
                                extra={"request_id": request_id})
                retrieval_summary = {
                    "status": "DEGRADED",
                    "reason_code": "RETRIEVAL_FAILED",
                    "degraded": True,
                }
        else:
            retrieval_summary = {
                "status": "SKIPPED",
                "reason_code": str(retrieval_disabled_reason or "HOST_CHAT_RETRIEVAL_DISABLED"),
                "degraded": False,
            }

        # ── 9. Airlock Ingress 检疫 ───────────────────────────────────────
        narrative = repair_utf8_latin1_mojibake(full_text) or "系统有点忙，请稍后再试。"
        ingress_result = None
        try:
            ingress_result = quarantine_ingress(
                narrative,
                request_id=request_id,
                ledger=get_ledger(),
                ts_ms=ts_ms,
            )
        except Exception as exc:
            _logger.warning("airlock ingress failed: %s", exc, extra={"request_id": request_id})

        airlock_refs: Dict[str, Any] = {}
        if ingress_result is not None:
            airlock_refs = {
                "ingress_classification": ingress_result.classification,
                "quarantine_digest": ingress_result.quarantine_digest,
            }

        # ── 10. episodic memory 写入 ──────────────────────────────────────
        episodic_summary: Dict[str, Any] = {}
        try:
            episodic_summary = persist_chat_episodic_memory(
                request_id=request_id,
                intent_id=str(intent_id or request_id),
                session_id=session_id,
                user_message=message,
                assistant_reply=narrative,
                emotion_context=emotion_context,
                ts_ms=ts_ms,
                episodic_store=get_episodic_store(),
            )
        except Exception as exc:
            episodic_summary = {
                "status": "DEGRADED",
                "reason_code": "EPISODIC_WRITE_FAILED",
                "memory_id": None,
                "detail": str(exc),
            }
            _logger.debug("episodic memory write skipped in stream mode",
                          extra={"request_id": request_id, "error": str(exc)})

        # ── 11. COMMIT 事件 ───────────────────────────────────────────────
        yield _sse({
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": narrative,
            "request_id": request_id,
            "audit_refs": {
                "intent_id": intent_id,
                "retrieval": retrieval_summary,
                "llm": {
                    "status": "OK",
                    "provider": provider_name,
                    "model_id": model_id,
                },
                "airlock": airlock_refs,
                "episodic_memory": episodic_summary,
            },
        })

    except Exception as exc:
        _logger.error("SSE chat stream failed: %s", exc, extra={"request_id": request_id})
        yield _sse({
            "phase": "COMMIT",
            "done": True,
            "decision": "REJECT",
            "reason_code": "INTERNAL_SERVER_ERROR",
            "request_id": request_id,
        })


# ---------------------------------------------------------------------------
# 公开处理函数：GET /v1/chat/stream
# ---------------------------------------------------------------------------

async def handle_chat_stream(
    message: str,
    session_id: Optional[str],
    thread_id: Optional[str],
    mood_state: Optional[str],
    emotion_tag: Optional[str],
    request: Optional[Request],
) -> StreamingResponse:
    """GET /v1/chat/stream SSE 流式聊天处理函数。"""
    request_id = (
        getattr(request.state, "request_id", None) if request is not None else None
    ) or new_request_id()
    ts_ms = int(time.time() * 1000)
    resolved_session_id = session_id or f"session::{request_id}"

    return StreamingResponse(
        _sse_event_generator(
            message=message,
            session_id=resolved_session_id,
            thread_id=thread_id,
            mood_state=mood_state,
            emotion_tag=emotion_tag,
            request_id=request_id,
            ts_ms=ts_ms,
            intent_draft=None,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# 公开处理函数：POST /v1/chat/stream
# ---------------------------------------------------------------------------

async def handle_chat_stream_post(
    request_body: ChatRequest,
    request: Optional[Request],
) -> StreamingResponse:
    """POST /v1/chat/stream SSE 流式聊天处理函数。

    接受与 POST /v1/chat 相同的 ChatRequest 请求体，
    返回与 GET /v1/chat/stream 相同格式的 SSE 事件流。
    """
    request_id = (
        getattr(request.state, "request_id", None) if request is not None else None
    ) or new_request_id()
    ts_ms = int(time.time() * 1000)
    session_id = request_body.session_id or f"session::{request_id}"

    # 从请求体中提取 intent_draft（若已提供则直接使用，保持 seq 一致性）
    intent_draft = request_body.intent_draft
    if intent_draft is None:
        intent_draft = {
            "schema_version": "Natsume.intent.v1",
            "id": f"intent::{request_id}",
            "ts_ms": ts_ms,
            "session_id": session_id,
            "seq": 1,
            "type": "query",
            "action": "narrative_only",
            "target": "host",
            "args": {"text": request_body.message},
            "nonce": f"nonce::{request_id}",
        }

    return StreamingResponse(
        _sse_event_generator(
            message=request_body.message,
            session_id=session_id,
            thread_id=request_body.thread_id,
            mood_state=request_body.mood_state,
            emotion_tag=request_body.emotion_tag,
            request_id=request_id,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
