"""SSE 流式聊天处理模块。

从 fastapi_app.py 拆分，负责 GET /v1/chat/stream SSE 流式响应。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncGenerator, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse

from host_brain.app.dependencies import get_intent_engine
from host_brain.app.emotion_context import (
    build_chat_governance_snapshot,
    resolve_chat_emotion_context,
)
from host_brain.app.graph_config_builder import resolve_host_llm_runtime
from host_brain.app.prompt_builder import (
    infer_render_target_lang,
    render_host_generation_prompt,
    repair_utf8_latin1_mojibake,
)
from host_brain.core.ids import new_request_id
from host_brain.governor.schemas import DECISION_APPROVE
from host_brain.integrations.llm_helpers import (
    adjust_gen_params_for_emotion,
    stream_llm_chunks,
)

_logger = logging.getLogger("Natsume.chat_stream_handler")


async def _stream_llm_chunks_async(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    prompt_text: str,
    system_prompt: str,
    gen_params: dict,
) -> AsyncGenerator[str, None]:
    """将同步的 stream_llm_chunks 生成器通过 asyncio.Queue 桥接为异步生成器。"""
    loop = asyncio.get_event_loop()
    chunk_queue: asyncio.Queue[str | None] = asyncio.Queue()

    def _producer() -> None:
        try:
            for chunk in stream_llm_chunks(
                provider_name=provider_name,
                model_id=model_id,
                api_key=api_key,
                timeout_ms=timeout_ms,
                prompt_text=prompt_text,
                system_prompt=system_prompt,
                gen_params=gen_params,
            ):
                asyncio.run_coroutine_threadsafe(chunk_queue.put(chunk), loop)
        except Exception:
            pass
        finally:
            asyncio.run_coroutine_threadsafe(chunk_queue.put(None), loop)

    import threading  # noqa: PLC0415
    thread = threading.Thread(target=_producer, daemon=True)
    thread.start()

    while True:
        chunk = await chunk_queue.get()
        if chunk is None:
            break
        yield chunk


async def _build_stream_chat_prompt(
    *,
    message: str,
    session_id: str,
    thread_id: Optional[str],
    mood_state: Optional[str],
    emotion_tag: Optional[str],
    request_id: str,
    ts_ms: int,
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

    from host_brain.app.chat_handler import _build_chat_generation_prompt_payload  # noqa: PLC0415

    render_target_lang = infer_render_target_lang(user_message=message)
    prompt_payload = _build_chat_generation_prompt_payload(
        request_id=request_id,
        session_id=session_id,
        thread_id=thread_id,
        user_message=message,
        render_target_lang=render_target_lang,
        retrieval_summary={"status": "SKIPPED", "reason_code": "SSE_STREAM_MODE"},
        retrieval_evidence_items=[],
        style_bindings={},
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

    async def _event_generator() -> AsyncGenerator[str, None]:
        yield f"data: {json.dumps({'phase': 'PRESENCE', 'event': 'THINKING', 'request_id': request_id}, ensure_ascii=False)}\n\n"

        try:
            class _StreamReq:
                def __init__(self) -> None:
                    self.message = message
                    self.session_id = resolved_session_id
                    self.thread_id = thread_id
                    self.mood_state = mood_state
                    self.emotion_tag = emotion_tag
                    self.emotion_range_hint = None
                    self.emotion_context = None
                    self.scene_type = None
                    self.intent_draft = None

            request_body = _StreamReq()

            intent_draft = {
                "schema_version": "Natsume.intent.v1",
                "id": f"intent::{request_id}",
                "ts_ms": ts_ms,
                "session_id": resolved_session_id,
                "seq": 1,
                "type": "query",
                "action": "narrative_only",
                "target": "host",
                "args": {"text": message},
                "nonce": f"nonce::{request_id}",
            }
            emotion_context = resolve_chat_emotion_context(request_body=request_body)
            governance_snapshot = build_chat_governance_snapshot(
                emotion_context=emotion_context,
                ts_ms=ts_ms,
            )

            engine = get_intent_engine()
            governor_result = await asyncio.to_thread(
                engine.evaluate_intent,
                raw_intent=intent_draft,
                request_id=request_id,
                thread_id=thread_id,
                governance_snapshot=governance_snapshot,
            )

            decision = governor_result.get("decision")
            if decision != DECISION_APPROVE:
                reason = governor_result.get("reason_code") or "REJECTED"
                yield f"data: {json.dumps({'phase': 'COMMIT', 'done': True, 'decision': 'REJECT', 'reason_code': reason}, ensure_ascii=False)}\n\n"
                return

            yield f"data: {json.dumps({'phase': 'PRESENCE', 'event': 'GOV_APPROVED', 'request_id': request_id}, ensure_ascii=False)}\n\n"

            llm_runtime = resolve_host_llm_runtime()
            provider_name = str(llm_runtime["provider_name"])
            model_id = str(llm_runtime["model_id"])
            timeout_ms_val = int(llm_runtime["timeout_ms"])
            llm_api_key = str(llm_runtime.get("llm_api_key") or "").strip()
            available = bool(llm_runtime["available"])

            if not available:
                yield f"data: {json.dumps({'phase': 'COMMIT', 'done': True, 'decision': 'REJECT', 'reason_code': 'LLM_UNAVAILABLE', 'narrative': '服务暂时不可用，请稍后再试。'}, ensure_ascii=False)}\n\n"
                return

            prompt_text, system_prompt, gen_params, _ = await _build_stream_chat_prompt(
                message=message,
                session_id=resolved_session_id,
                thread_id=thread_id,
                mood_state=mood_state,
                emotion_tag=emotion_tag,
                request_id=request_id,
                ts_ms=ts_ms,
            )

            full_text = ""
            async for chunk in _stream_llm_chunks_async(
                provider_name=provider_name,
                model_id=model_id,
                api_key=llm_api_key,
                timeout_ms=timeout_ms_val,
                prompt_text=prompt_text,
                system_prompt=system_prompt,
                gen_params=gen_params,
            ):
                full_text += chunk
                yield f"data: {json.dumps({'phase': 'RENDER', 'chunk': chunk}, ensure_ascii=False)}\n\n"

            narrative = repair_utf8_latin1_mojibake(full_text) or "系统有点忙，请稍后再试。"
            yield f"data: {json.dumps({'phase': 'COMMIT', 'done': True, 'decision': 'APPROVE', 'narrative': narrative, 'request_id': request_id}, ensure_ascii=False)}\n\n"

        except Exception as exc:
            _logger.error("SSE chat stream failed: %s", exc, extra={"request_id": request_id})
            yield f"data: {json.dumps({'phase': 'COMMIT', 'done': True, 'decision': 'REJECT', 'reason_code': 'INTERNAL_SERVER_ERROR'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
