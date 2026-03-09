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
from typing import Any, AsyncGenerator, Dict, Mapping, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse

from host_brain.app.dependencies import (
    get_episodic_store,
    get_intent_engine,
    get_ledger,
    get_retrieval_orchestrator,
)
from host_brain.app.emotion_context import (
    build_chat_emotion_snapshot,
    build_chat_governance_snapshot,
    resolve_chat_emotion_context,
)
from host_brain.app.graph_config_builder import (
    CHAT_DIRECT_NODE_ID,
    SCENE_CHAT,
    build_unified_route_decision,
    resolve_chat_retrieval_gate,
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
from host_brain.channels.vts import TRIGGER_HOST, resolve_vts_expression
from host_brain.channels.vts_rate_limiter import VTSRateLimiter
from host_brain.core.ids import new_request_id
from host_brain.governor.schemas import DECISION_APPROVE
from host_brain.integrations.llm_helpers import (
    adjust_gen_params_for_emotion,
    astream_llm_chunks,
)
from host_brain.retrieval.orchestrator import (
    RetrievalOrchestratorRequest,
    build_degraded_retrieval_summary as _orchestrator_degraded_summary,
    build_skipped_retrieval_summary as _orchestrator_skipped_summary,
)

_logger = logging.getLogger("Natsume.chat_stream_handler")
_stream_vts_rate_limiter = VTSRateLimiter(max_per_second=2.0, burst_size=2.0)
_stream_vts_rate_limit_per_second = 2.0

# ---------------------------------------------------------------------------
# 内部辅助：构建 prompt
# ---------------------------------------------------------------------------


def _log_context_pack_observability(
    *,
    request_id: str,
    session_id: str,
    context_pack_observability: Mapping[str, Any],
) -> None:
    if not isinstance(context_pack_observability, Mapping) or not context_pack_observability:
        return

    selected_tier = str(context_pack_observability.get("selected_tier") or "").strip()
    selected_reason = str(context_pack_observability.get("selected_reason") or "").strip()
    retrieval_metrics_raw = context_pack_observability.get("retrieval_metrics")
    retrieval_metrics = dict(retrieval_metrics_raw) if isinstance(retrieval_metrics_raw, Mapping) else {}
    source_stats_raw = context_pack_observability.get("source_stats")
    source_stats = dict(source_stats_raw) if isinstance(source_stats_raw, Mapping) else {}
    slot_counts_raw = context_pack_observability.get("slot_item_counts")
    slot_counts = dict(slot_counts_raw) if isinstance(slot_counts_raw, Mapping) else {}

    _logger.info(
        "stream context pack observability",
        extra={
            "request_id": request_id,
            "session_id": session_id,
            "selected_tier": selected_tier or None,
            "selected_reason": selected_reason or None,
            "retrieval_status": retrieval_metrics.get("status"),
            "retrieval_path": retrieval_metrics.get("path"),
            "retrieval_route_count": retrieval_metrics.get("route_count"),
            "retrieval_candidate_count": retrieval_metrics.get("candidate_count"),
            "retrieval_rerank_topk": retrieval_metrics.get("rerank_topk"),
            "retrieval_cache_hit": retrieval_metrics.get("cache_hit"),
            "retrieval_cache_age_ms": retrieval_metrics.get("cache_age_ms"),
            "retrieval_used_items": source_stats.get("retrieval_used_items"),
            "retrieval_trimmed_items": source_stats.get("retrieval_trimmed_items"),
            "context_slot_counts": slot_counts,
            "context_size_chars": context_pack_observability.get("context_size_chars"),
        },
    )

    if selected_tier and selected_tier != "six_route":
        _logger.warning(
            "stream context degrade tier selected",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "selected_tier": selected_tier,
                "selected_reason": selected_reason or None,
                "degrade_order": list(context_pack_observability.get("degrade_order") or []),
            },
        )


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
    emotion_context_override: Dict[str, Any] | None = None,
    emotion_snapshot_override: Dict[str, Any] | None = None,
) -> tuple[str, str, dict, dict, dict[str, Any]]:
    """构建流式聊天的 prompt、system_prompt 和生成参数。

    返回: (prompt_text, system_prompt, gen_params, emotion_context, prompt_meta)
    """

    class _StreamReq:
        def __init__(self) -> None:
            self.message = message
            self.session_id = session_id
            self.thread_id = thread_id
            self.mood_state = mood_state
            self.emotion_tag = emotion_tag
            self.emotion_intensity = None
            self.emotion_range_hint = None
            self.emotion_context = None
            self.scene_type = None
            self.intent_draft = None

    resolved_emotion_context: Dict[str, Any]
    if isinstance(emotion_context_override, dict):
        resolved_emotion_context = dict(emotion_context_override)
    else:
        request_body = _StreamReq()
        resolved_emotion_context = resolve_chat_emotion_context(request_body=request_body)
    resolved_emotion_snapshot = (
        dict(emotion_snapshot_override)
        if isinstance(emotion_snapshot_override, dict)
        else build_chat_emotion_snapshot(
            emotion_context=resolved_emotion_context,
            source="stream_prompt",
            ts_ms=ts_ms,
        )
    )

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
        emotion_context=resolved_emotion_context if resolved_emotion_context else None,
        emotion_snapshot=resolved_emotion_snapshot,
    )
    if resolved_emotion_context:
        prompt_payload = adjust_gen_params_for_emotion(prompt_payload, resolved_emotion_context)

    gen_params = dict(prompt_payload.get("gen_params") or {})
    runtime_payload = prompt_payload.get("input") or dict(prompt_payload)
    prompt_text = render_host_generation_prompt(runtime_payload)
    prompt_meta = {
        "context_pack_validation": dict(prompt_payload.get("context_pack_validation") or {}),
        "context_pack_observability": dict(prompt_payload.get("context_pack_observability") or {}),
        "emotion_snapshot": dict(prompt_payload.get("emotion_snapshot") or {}),
    }
    system_prompt = (
        "You are 夏目藍 (Natsume Ai). "
        "You are NOT an AI or assistant — you are 夏目藍 herself. "
        "Output exactly one JSON object with keys narrative, intent_draft, meta. "
        "meta.draft_only must be true."
    )
    return prompt_text, system_prompt, gen_params, resolved_emotion_context, prompt_meta


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
    orchestrator = get_retrieval_orchestrator()
    orchestrator_result = await orchestrator.arun(
        request=RetrievalOrchestratorRequest(
            trace_id=request_id,
            query=message,
            session_state={
                "session_id": session_id,
                "emotion_tag": emotion_tag,
            },
            request_id=request_id,
            session_id=session_id,
            top_k=_retr_top_k,
            ts_ms=ts_ms,
            emotion_context=emotion_tag,
        )
    )
    return (
        dict(orchestrator_result.retrieval_summary),
        list(orchestrator_result.retrieval_evidence_items),
    )


def _build_skipped_retrieval_summary(reason_code: str) -> Dict[str, Any]:
    return _orchestrator_skipped_summary(reason_code)


def _build_degraded_retrieval_summary(reason_code: str) -> Dict[str, Any]:
    return _orchestrator_degraded_summary(reason_code)


def _resolve_stream_retrieval_timeout_ms() -> int:
    raw = str(os.environ.get("HOST_STREAM_RETRIEVAL_TIMEOUT_MS") or "450").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 450
    return max(100, min(value, 5000))


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _resolve_stream_vts_timeout_ms() -> int:
    raw = str(os.environ.get("HOST_STREAM_VTS_TIMEOUT_MS") or "300").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 300
    return max(50, min(value, 3000))


def _resolve_stream_vts_max_per_second() -> float:
    raw = str(os.environ.get("HOST_STREAM_VTS_MAX_PER_SECOND") or "2.0").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 2.0
    return max(0.1, min(value, 20.0))


def _get_stream_vts_rate_limiter() -> VTSRateLimiter:
    global _stream_vts_rate_limit_per_second

    expected = _resolve_stream_vts_max_per_second()
    if abs(expected - _stream_vts_rate_limit_per_second) > 1e-6:
        _stream_vts_rate_limiter.update_rate(max_per_second=expected, burst_size=expected)
        _stream_vts_rate_limit_per_second = expected
    return _stream_vts_rate_limiter


async def _drive_stream_vts_emotion(
    *,
    request_id: str,
    emotion_context: Dict[str, Any] | None,
    ts_ms: int,
) -> Dict[str, Any]:
    if not _parse_bool_env("HOST_STREAM_VTS_EMOTION_ENABLED", default=True):
        return {
            "status": "SKIPPED",
            "reason_code": "HOST_STREAM_VTS_EMOTION_DISABLED",
            "emotion_tag": None,
            "expression_id": None,
            "trigger": "HOST",
            "detail": "vts emotion drive disabled by config",
        }

    normalized_emotion_tag = str((emotion_context or {}).get("emotion_tag") or "").strip().upper()
    if not normalized_emotion_tag:
        return {
            "status": "SKIPPED",
            "reason_code": "EMOTION_TAG_MISSING",
            "emotion_tag": None,
            "expression_id": None,
            "trigger": "HOST",
            "detail": "emotion tag missing in stream emotion context",
        }

    ledger = get_ledger()
    limiter = _get_stream_vts_rate_limiter()
    limit_result = limiter.check()
    if not limit_result.allowed:
        limiter.log_rate_limit_event(
            ledger=ledger,
            request_id=request_id,
            emotion_tag=normalized_emotion_tag,
            ts_ms=ts_ms,
        )
        return {
            "status": "RATE_LIMITED",
            "reason_code": limit_result.reason_code,
            "emotion_tag": normalized_emotion_tag,
            "expression_id": None,
            "trigger": "RATE_LIMITER",
            "detail": limit_result.detail,
        }

    edge_available = _parse_bool_env("HOST_STREAM_VTS_EDGE_AVAILABLE", default=True)
    timeout_ms = _resolve_stream_vts_timeout_ms()
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                resolve_vts_expression,
                emotion_tag=normalized_emotion_tag,
                ledger=ledger,
                request_id=request_id,
                trigger=TRIGGER_HOST,
                ts_ms=ts_ms,
                edge_available=edge_available,
            ),
            timeout=float(timeout_ms) / 1000.0,
        )
        return {
            "status": str(result.status),
            "reason_code": result.reason_code,
            "emotion_tag": result.emotion_tag,
            "expression_id": result.expression_id,
            "trigger": result.trigger,
            "detail": result.detail,
        }
    except asyncio.TimeoutError:
        _logger.warning(
            "vts emotion drive timeout in stream path",
            extra={"request_id": request_id, "timeout_ms": timeout_ms},
        )
        return {
            "status": "DEGRADED",
            "reason_code": "VTS_EXPRESSION_TIMEOUT",
            "emotion_tag": normalized_emotion_tag,
            "expression_id": None,
            "trigger": "HOST",
            "detail": "vts emotion drive timed out",
        }
    except Exception as exc:
        _logger.warning(
            "vts emotion drive failed in stream path: %s",
            exc,
            extra={"request_id": request_id},
        )
        return {
            "status": "DEGRADED",
            "reason_code": "VTS_CONNECTION_FAILED",
            "emotion_tag": normalized_emotion_tag,
            "expression_id": None,
            "trigger": "HOST",
            "detail": str(exc),
        }


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
                self.emotion_intensity = None
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
        scene_type = str(getattr(request_body_obj, "scene_type", None) or SCENE_CHAT).strip().upper() or SCENE_CHAT
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

        retrieval_summary: Dict[str, Any]
        retrieval_evidence_items: list[dict[str, Any]]
        if retrieval_task is not None:
            retrieval_timeout_ms = _resolve_stream_retrieval_timeout_ms()
            try:
                retrieval_summary, retrieval_evidence_items = await asyncio.wait_for(
                    retrieval_task,
                    timeout=float(retrieval_timeout_ms) / 1000.0,
                )
            except asyncio.TimeoutError:
                retrieval_task.cancel()
                retrieval_summary = _build_degraded_retrieval_summary("RETRIEVAL_TIMEOUT")
                retrieval_evidence_items = []
                _logger.warning(
                    "retrieval timeout before stream prompt build",
                    extra={"request_id": request_id, "timeout_ms": retrieval_timeout_ms},
                )
            except Exception as exc:
                retrieval_summary = _build_degraded_retrieval_summary("RETRIEVAL_FAILED")
                retrieval_evidence_items = []
                _logger.warning(
                    "retrieval task failed before stream prompt build: %s",
                    exc,
                    extra={"request_id": request_id},
                )
        else:
            retrieval_summary = _build_skipped_retrieval_summary(
                str(retrieval_disabled_reason or "HOST_CHAT_RETRIEVAL_DISABLED")
            )
            retrieval_evidence_items = []
        emotion_snapshot = build_chat_emotion_snapshot(
            emotion_context=emotion_context,
            source="stream_pre_llm",
            ts_ms=int(time.time() * 1000),
        )

        # ── 5. 检查 LLM 可用性 ────────────────────────────────────────────
        llm_runtime = resolve_host_llm_runtime()
        provider_name = str(llm_runtime["provider_name"])
        model_id = str(llm_runtime["model_id"])
        timeout_ms_val = int(llm_runtime["timeout_ms"])
        llm_api_key = str(llm_runtime.get("llm_api_key") or "").strip()
        available = bool(llm_runtime["available"])
        base_url_override = str(llm_runtime.get("base_url_override") or "").strip() or None

        if not available:
            if retrieval_task is not None:
                retrieval_task.cancel()
            yield _sse({"phase": "COMMIT", "done": True, "decision": "REJECT",
                        "reason_code": "LLM_UNAVAILABLE",
                        "narrative": "服务暂时不可用，请稍后再试。",
                        "request_id": request_id})
            return

        # ── 6. 构建 prompt（已注入 Retrieval + Emotion 上下文）───────────
        prompt_build_result = await _build_stream_chat_prompt(
            message=message,
            session_id=session_id,
            thread_id=thread_id,
            mood_state=mood_state,
            emotion_tag=emotion_tag,
            request_id=request_id,
            ts_ms=ts_ms,
            retrieval_summary=retrieval_summary,
            retrieval_evidence_items=retrieval_evidence_items,
            emotion_context_override=emotion_context,
            emotion_snapshot_override=emotion_snapshot,
        )
        context_pack_validation: dict[str, Any] = {}
        context_pack_observability: dict[str, Any] = {}
        emotion_snapshot_for_audit: dict[str, Any] = dict(emotion_snapshot)
        if len(prompt_build_result) >= 5:
            prompt_text, system_prompt, gen_params, _, prompt_meta = prompt_build_result
            if isinstance(prompt_meta, Mapping):
                context_pack_validation = dict(prompt_meta.get("context_pack_validation") or {})
                context_pack_observability = dict(prompt_meta.get("context_pack_observability") or {})
                emotion_snapshot_for_audit = dict(prompt_meta.get("emotion_snapshot") or emotion_snapshot)
        else:
            prompt_text, system_prompt, gen_params, _ = prompt_build_result  # type: ignore[misc]
        _log_context_pack_observability(
            request_id=request_id,
            session_id=session_id,
            context_pack_observability=context_pack_observability,
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
            base_url_override=base_url_override,
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

        # ── 8. Airlock Ingress 检疫 ───────────────────────────────────────
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

        # ── 9. episodic memory 写入 ───────────────────────────────────────
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

        # ── 10. VTS 情感驱动（异步线程，不阻塞流式 token 输出）────────────
        vts_summary = await _drive_stream_vts_emotion(
            request_id=request_id,
            emotion_context=emotion_context,
            ts_ms=ts_ms,
        )

        # ── 11. COMMIT 事件 ───────────────────────────────────────────────
        route_decision = build_unified_route_decision(
            scene_type=scene_type,
            scene_type_source="stream_request",
            route_target=str(llm_runtime.get("route_target") or "cloud"),
            route_reason_code=str(llm_runtime.get("route_reason_code") or ""),
            next_node=CHAT_DIRECT_NODE_ID,
            router_node="stream_chat_handler",
            provider_name=provider_name,
            model_id=model_id,
            sys_mode=str(llm_runtime.get("sys_mode") or ""),
        )
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
                    "route_target": str(llm_runtime.get("route_target") or "cloud"),
                    "route_reason_code": str(llm_runtime.get("route_reason_code") or ""),
                    "route_decision": route_decision,
                    "emotion_snapshot": emotion_snapshot_for_audit,
                    "context_pack_validation": context_pack_validation,
                    "context_pack_observability": context_pack_observability,
                },
                "route_decision": route_decision,
                "emotion_snapshot": emotion_snapshot_for_audit,
                "context_pack_validation": context_pack_validation,
                "context_pack_observability": context_pack_observability,
                "vts": vts_summary,
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
