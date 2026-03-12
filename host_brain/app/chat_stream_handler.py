"""SSE 流式聊天处理模块（SCENE_CHAT 专用）。

从 fastapi_app.py 拆分，负责 SCENE_CHAT 场景下的 token 级流式 SSE 响应。
由 chat_handler._unified_chat_sse_generator 在场景分类后调用。

完整链路：
  [Governor 鉴权（由调用方预完成）] → 并行 Retrieval → stream_llm_chunks → SentenceSplitter
  → Airlock Ingress 检疫 → episodic memory 写入 → COMMIT 事件

注意：/v1/chat/stream 接口已删除，统一走 POST /v1/chat（SSE 流式响应）。
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
    resolve_host_llm_runtime,  # 同步版本，供测试 mock 使用
    resolve_host_llm_runtime_async,  # 修复审计问题 #1：异步版本，供实际调用
)

# 修复审计问题 #1：模块级别名，供 _sse_event_generator 调用（可被测试 mock）
# 测试通过 monkeypatch.setattr(stream_mod, "resolve_host_llm_runtime", ...) 替换此引用
resolve_host_llm_runtime = resolve_host_llm_runtime_async  # noqa: F811
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
_CHAT_RENDER_NODE_ID = "renderer_ja_c_style"

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
        "流式上下文包可观测性数据",
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


def _resolve_stream_style_bindings(
    *,
    node_style_bindings: Mapping[str, Mapping[str, Any]] | None,
    render_target_lang: str,
) -> dict[str, dict[str, Any]]:
    resolved: dict[str, dict[str, Any]] = {}
    for node_id, row in dict(node_style_bindings or {}).items():
        if not isinstance(row, Mapping):
            continue
        resolved[str(node_id)] = dict(row)

    # 流式聊天当前通过单一合并节点（chat_direct_ja）路由。
    # 样式注入聚焦于日语 canon，避免中日混排漂移。
    if str(render_target_lang).strip().lower() != "ja":
        return resolved

    direct_binding_raw = resolved.get(CHAT_DIRECT_NODE_ID)
    render_binding_raw = resolved.get(_CHAT_RENDER_NODE_ID)
    preferred_raw = direct_binding_raw or render_binding_raw
    if not isinstance(preferred_raw, Mapping):
        return resolved

    preferred = dict(preferred_raw)
    if not preferred:
        return resolved

    return {
        CHAT_DIRECT_NODE_ID: dict(preferred),
        _CHAT_RENDER_NODE_ID: dict(preferred),
    }


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
    cpb_slots: Dict[str, Any] | None = None,
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
    from host_brain.app.prompt_builder import (  # noqa: PLC0415
        render_node_generation_prompt,
        _build_node_role_instruction,
    )

    render_target_lang = infer_render_target_lang(user_message=message)
    node_style_bindings = _resolve_stream_style_bindings(
        node_style_bindings=_build_style(render_target_lang=render_target_lang),
        render_target_lang=render_target_lang,
    )

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
        cpb_slots=cpb_slots,
    )
    if resolved_emotion_context:
        prompt_payload = adjust_gen_params_for_emotion(prompt_payload, resolved_emotion_context)

    gen_params = dict(prompt_payload.get("gen_params") or {})
    runtime_payload = prompt_payload.get("input") or dict(prompt_payload)
    runtime_payload_for_node = dict(runtime_payload)
    runtime_payload_for_node["render_target_lang"] = render_target_lang
    prompt_text = render_node_generation_prompt(
        node_id=CHAT_DIRECT_NODE_ID,
        runtime_payload=runtime_payload_for_node,
    )
    prompt_meta = {
        "context_pack_validation": dict(prompt_payload.get("context_pack_validation") or {}),
        "context_pack_observability": dict(prompt_payload.get("context_pack_observability") or {}),
        "emotion_snapshot": dict(prompt_payload.get("emotion_snapshot") or {}),
    }
    system_prompt = _build_node_role_instruction(
        CHAT_DIRECT_NODE_ID,
        render_target_lang=render_target_lang,
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
    """并行执行检索，返回 (retrieval_summary, retrieval_evidence_items)。"""
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
    raw = str(os.environ.get("HOST_STREAM_RETRIEVAL_TIMEOUT_MS") or "1200").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 1200
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


_CONTEXT_PACK_SLOT_ALIASES: dict[str, str] = {
    "world": "world_canon",
    "world_canon": "world_canon",
    "style": "style_canon",
    "style_canon": "style_canon",
    "episodic": "episodic_vector",
    "episodic_store": "episodic_vector",
    "episodic_vector": "episodic_vector",
    "bond": "bond_graph",
    "bond_graph": "bond_graph",
    "artifact": "artifact_vault",
    "artifact_vault": "artifact_vault",
    "persona": "persona_utterance",
    "persona_utterance": "persona_utterance",
    "sqlite": "sqlite_ledger",
    "sqlite_ledger": "sqlite_ledger",
    "ledger": "sqlite_ledger",
}

_CONTEXT_PACK_SLOT_TO_STORE_ID: dict[str, str] = {
    "world_canon": "world_canon",
    "style_canon": "style_canon",
    "episodic_vector": "episodic_vector",
    "bond_graph": "bond_graph",
    "artifact_vault": "artifact_vault",
    "persona_utterance": "persona_utterance",
    "sqlite_ledger": "sqlite_ledger",
}


def _normalize_context_pack_slot(
    *,
    slot_hint: str,
    source_ref: str,
) -> str | None:
    normalized_hint = str(slot_hint or "").strip().lower()
    if normalized_hint:
        direct = _CONTEXT_PACK_SLOT_ALIASES.get(normalized_hint)
        if direct:
            return direct

    normalized_source_ref = str(source_ref or "").strip().lower()
    if not normalized_source_ref:
        return None
    for alias, slot in _CONTEXT_PACK_SLOT_ALIASES.items():
        token = f"{alias}://"
        if normalized_source_ref.startswith(token):
            return slot
    return None


def _build_context_pack_recall_results(
    retrieval_evidence_items: list[dict[str, Any]],
) -> dict[str, str]:
    recall_results: dict[str, str] = {}
    for item in list(retrieval_evidence_items or []):
        if not isinstance(item, Mapping):
            continue

        metadata_raw = item.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
        slot = _normalize_context_pack_slot(
            slot_hint=str(
                item.get("slot_name")
                or item.get("source")
                or item.get("store_id")
                or metadata.get("store_id")
                or ""
            ),
            source_ref=str(item.get("source_ref") or metadata.get("source_ref") or ""),
        )
        if not slot:
            continue

        text = str(
            item.get("text")
            or item.get("content")
            or item.get("snippet")
            or ""
        ).strip()
        if not text:
            continue

        existing = recall_results.get(slot)
        if existing:
            merged = f"{existing}\n{text}"
            recall_results[slot] = _truncate_for_prompt(merged, limit=1600)
        else:
            recall_results[slot] = text

    return recall_results


def _context_pack_to_retrieval_evidence_items(context_pack: Any) -> list[dict[str, Any]]:
    from host_brain.app.schemas.context_pack import TRUNCATION_PRIORITY  # noqa: PLC0415

    rewritten: list[dict[str, Any]] = []
    for slot_name in TRUNCATION_PRIORITY:
        slot = context_pack.get_slot(slot_name)
        if slot is None:
            continue
        content = str(getattr(slot, "content", "") or "").strip()
        if not content:
            continue

        store_id = _CONTEXT_PACK_SLOT_TO_STORE_ID.get(slot_name, slot_name)
        rewritten.append(
            {
                "slot_name": slot_name,
                "source": slot_name,
                "store_id": store_id,
                "evidence_ref": f"context_pack://{slot_name}",
                "source_ref": f"{slot_name}://context_pack_builder",
                "snippet": _truncate_for_prompt(content, limit=240),
                "content": content,
                "text": content,
                "metadata": {
                    "store_id": store_id,
                    "source": "context_pack_builder",
                    "truncated": bool(getattr(slot, "truncated", False)),
                    "conflict_suppressed": bool(getattr(slot, "conflict_suppressed", False)),
                },
            }
        )
    return rewritten


def _normalize_state_arbiter_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("content", "body", "text", "summary", "value"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
        try:
            return json.dumps(dict(value), ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(value).strip()
    if isinstance(value, list):
        chunks: list[str] = []
        for row in value[:3]:
            if isinstance(row, Mapping):
                piece = (
                    str(row.get("content") or "").strip()
                    or str(row.get("body") or "").strip()
                    or str(row.get("text") or "").strip()
                    or str(row.get("title") or "").strip()
                )
            else:
                piece = str(row).strip()
            if piece:
                chunks.append(piece)
        return "\n".join(chunks).strip()
    return str(value).strip()


def _build_state_arbiter_world_evidence_item(
    *,
    fact_text: str,
    source_name: str,
    fact_key: str,
) -> dict[str, Any]:
    normalized_source = str(source_name or "unknown").strip() or "unknown"
    normalized_key = str(fact_key or "world_canon_facts").strip() or "world_canon_facts"
    snippet = _truncate_for_prompt(fact_text, limit=240)
    return {
        "slot_name": "world_canon",
        "source": "state_arbiter",
        "store_id": "world_canon",
        "evidence_ref": f"state_arbiter://{normalized_source}/{normalized_key}",
        "source_ref": f"state_arbiter://{normalized_source}",
        "snippet": snippet,
        "content": fact_text,
        "text": fact_text,
        "metadata": {
            "store_id": "world_canon",
            "source": "state_arbiter",
            "arbiter_source": normalized_source,
            "fact_key": normalized_key,
        },
    }


async def _resolve_state_arbiter_world_fact(
    *,
    session_id: str,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    summary: dict[str, Any] = {
        "status": "SKIPPED",
        "reason_code": "NOT_AVAILABLE",
        "fact_key": "world_canon_facts",
    }
    try:
        from host_brain.app.dependencies import get_state_arbiter  # noqa: PLC0415

        arbiter = get_state_arbiter()
        result = await arbiter.resolve("world_canon_facts", session_id=session_id)
        if result is None:
            summary["reason_code"] = "NO_MATCH"
            return summary, None

        fact_text = _normalize_state_arbiter_value(result.value)
        summary.update(
            {
                "source": str(getattr(result.source, "value", result.source) or "unknown"),
                "is_fallback": bool(getattr(result, "is_fallback", False)),
                "fallback_chain": [
                    str(getattr(row, "value", row) or "")
                    for row in list(getattr(result, "fallback_chain", []) or [])
                    if str(getattr(row, "value", row) or "").strip()
                ],
                "conflict_detected": bool(getattr(result, "conflict_detected", False)),
                "conflict_detail": str(getattr(result, "conflict_detail", "") or "").strip() or None,
            }
        )
        if not fact_text:
            summary["reason_code"] = "EMPTY_VALUE"
            return summary, None

        evidence_item = _build_state_arbiter_world_evidence_item(
            fact_text=fact_text,
            source_name=str(getattr(result.source, "value", result.source) or "unknown"),
            fact_key=str(getattr(result, "fact_key", "world_canon_facts") or "world_canon_facts"),
        )
        summary["status"] = "APPLIED"
        summary["reason_code"] = None
        summary["value_chars"] = len(fact_text)
        return summary, evidence_item
    except Exception as exc:
        _logger.debug(
            "StateArbiter 调用失败（降级）: %s",
            exc,
            extra={"request_id": request_id},
        )
        summary["status"] = "DEGRADED"
        summary["reason_code"] = "STATE_ARBITER_FAILED"
        summary["detail"] = str(exc)
        return summary, None


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
            "detail": "VTS 情感驱动已通过配置禁用",
        }

    normalized_emotion_tag = str((emotion_context or {}).get("emotion_tag") or "").strip().upper()
    if not normalized_emotion_tag:
        return {
            "status": "SKIPPED",
            "reason_code": "EMOTION_TAG_MISSING",
            "emotion_tag": None,
            "expression_id": None,
            "trigger": "HOST",
            "detail": "流式情感上下文中缺少情感标签",
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
            "流式路径中 VTS 情感驱动超时",
            extra={"request_id": request_id, "timeout_ms": timeout_ms},
        )
        return {
            "status": "DEGRADED",
            "reason_code": "VTS_EXPRESSION_TIMEOUT",
            "emotion_tag": normalized_emotion_tag,
            "expression_id": None,
            "trigger": "HOST",
            "detail": "VTS 情感驱动超时",
        }
    except Exception as exc:
        _logger.warning(
            "流式路径中 VTS 情感驱动失败: %s",
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


async def _wrap_vts_with_latency(
    *,
    request_id: str,
    emotion_context: Dict[str, Any] | None,
    ts_ms: int,
) -> tuple[Dict[str, Any], int]:
    """包装 _drive_stream_vts_emotion，返回 (result_dict, latency_ms) 元组，
    以便与 asyncio.gather 中其他返回 tuple 的协程统一解包。"""
    _t0 = int(time.time() * 1000)
    result = await _drive_stream_vts_emotion(
        request_id=request_id,
        emotion_context=emotion_context,
        ts_ms=ts_ms,
    )
    return result, max(0, int(time.time() * 1000) - _t0)


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
    _pre_approved_governor_result: Dict[str, Any] | None = None,
    _pre_recall_evidence: list[dict[str, Any]] | None = None,
) -> AsyncGenerator[str, None]:
    """完整链路 SSE 事件生成器（SCENE_CHAT 专用，token 级流式输出）。

    事件序列：
      PRESENCE(THINKING) → [Governor] → PRESENCE(GOV_APPROVED)
      → RENDER(chunk)* → SENTENCE(sentence)* → COMMIT

    当 _pre_approved_governor_result 传入时，跳过重复 Governor 鉴权
    （由 _unified_chat_sse_generator 在场景分类前已完成鉴权）。

    当 _pre_recall_evidence 传入时（来自 MainChain.RecallFusionNode 的预召回结果），
    跳过内部重复召回，直接使用预召回证据，避免双重召回浪费资源。
    """

    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    yield _sse({"phase": "PRESENCE", "event": "THINKING", "request_id": request_id})

    try:
        resolved_message = repair_utf8_latin1_mojibake(str(message or "").strip())
        if not resolved_message:
            resolved_message = str(message or "").strip()

        retrieval_latency_ms: int | None = None
        first_token_latency_ms: int | None = None

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
                "args": {"text": resolved_message},
                "nonce": f"nonce::{request_id}",
            }

        # ── 2. 情感上下文 ─────────────────────────────────────────────────
        class _StreamReq:
            def __init__(self) -> None:
                self.message = resolved_message
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

        # ── 3. Governor 鉴权（若已预鉴权则跳过）────────────────────────────
        if _pre_approved_governor_result is not None:
            # 由 _unified_chat_sse_generator 已完成鉴权，直接使用结果
            governor_result = _pre_approved_governor_result
        else:
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

        # 若是预鉴权路径，GOV_APPROVED 已由 _unified_chat_sse_generator 发出，此处跳过
        if _pre_approved_governor_result is None:
            yield _sse({"phase": "PRESENCE", "event": "GOV_APPROVED", "request_id": request_id})

        # ── 4. 并行启动 Retrieval ─────────────────────────────────────────
        # 本函数仅被 SCENE_CHAT 分支调用，scene_type 固定为 SCENE_CHAT
        scene_type = SCENE_CHAT
        retrieval_enabled, retrieval_disabled_reason = resolve_chat_retrieval_gate(scene_type=scene_type)

        retrieval_task: asyncio.Task | None = None
        retrieval_started_ms: int | None = None

        # 当 MainChain.RecallFusionNode 已完成预召回时，跳过重复召回
        _use_pre_recall = _pre_recall_evidence is not None
        if _use_pre_recall:
            _logger.debug(
                "[%s] _sse_event_generator 使用预召回结果（来自 RecallFusionNode），跳过重复召回 evidence_count=%d",
                request_id, len(_pre_recall_evidence),
            )
        elif retrieval_enabled:
            _retr_emotion = str(emotion_context.get("emotion_tag") or "") or None
            retrieval_started_ms = int(time.time() * 1000)
            retrieval_task = asyncio.create_task(
                _run_retrieval(
                    request_id=request_id,
                    message=resolved_message,
                    session_id=session_id,
                    ts_ms=ts_ms,
                    emotion_tag=_retr_emotion,
                )
            )

        retrieval_summary: Dict[str, Any]
        retrieval_evidence_items: list[dict[str, Any]]
        if _use_pre_recall:
            # 使用 RecallFusionNode 的预召回结果，跳过重复召回
            retrieval_evidence_items = list(_pre_recall_evidence)
            retrieval_summary = {
                "status": "PRE_RECALLED",
                "source": "RecallFusionNode",
                "evidence_count": len(retrieval_evidence_items),
            }
        elif retrieval_task is not None:
            retrieval_timeout_ms = _resolve_stream_retrieval_timeout_ms()
            try:
                retrieval_summary, retrieval_evidence_items = await asyncio.wait_for(
                    retrieval_task,
                    timeout=float(retrieval_timeout_ms) / 1000.0,
                )
                if retrieval_started_ms is not None:
                    retrieval_latency_ms = max(0, int(time.time() * 1000) - retrieval_started_ms)
            except asyncio.TimeoutError:
                retrieval_summary = _build_degraded_retrieval_summary("RETRIEVAL_TIMEOUT")
                retrieval_evidence_items = []
                if retrieval_started_ms is not None:
                    retrieval_latency_ms = max(0, int(time.time() * 1000) - retrieval_started_ms)
                if retrieval_task is not None:
                    def _swallow_retrieval_task_error(task: asyncio.Task) -> None:
                        try:
                            task.exception()
                        except asyncio.CancelledError:
                            return
                        except Exception:
                            return

                    retrieval_task.add_done_callback(_swallow_retrieval_task_error)
                _logger.warning(
                    "流式 prompt 构建前检索超时，允许后台预热任务继续运行",
                    extra={"request_id": request_id, "timeout_ms": retrieval_timeout_ms},
                )
            except Exception as exc:
                retrieval_summary = _build_degraded_retrieval_summary("RETRIEVAL_FAILED")
                retrieval_evidence_items = []
                if retrieval_started_ms is not None:
                    retrieval_latency_ms = max(0, int(time.time() * 1000) - retrieval_started_ms)
                _logger.warning(
                    "流式 prompt 构建前检索任务失败: %s",
                    exc,
                    extra={"request_id": request_id},
                )
        else:
            retrieval_summary = _build_skipped_retrieval_summary(
                str(retrieval_disabled_reason or "HOST_CHAT_RETRIEVAL_DISABLED")
            )
            retrieval_evidence_items = []
        if retrieval_latency_ms is not None:
            retrieval_summary["latency_ms"] = int(retrieval_latency_ms)

        state_arbiter_summary, state_arbiter_evidence_item = await _resolve_state_arbiter_world_fact(
            session_id=session_id,
            request_id=request_id,
        )
        retrieval_summary["state_arbiter"] = dict(state_arbiter_summary)
        if state_arbiter_evidence_item is not None:
            retrieval_evidence_items = [state_arbiter_evidence_item, *list(retrieval_evidence_items or [])]
            retrieval_summary["world_canon"] = {
                "status": "INJECTED",
                "source": "state_arbiter",
                "fact_key": str(state_arbiter_summary.get("fact_key") or "world_canon_facts"),
            }

        emotion_snapshot = build_chat_emotion_snapshot(
            emotion_context=emotion_context,
            source="stream_pre_llm",
            ts_ms=int(time.time() * 1000),
        )

        # ── 5. 检查 LLM 可用性 ────────────────────────────────────────────
        # 修复审计问题 #1：resolve_host_llm_runtime 在模块级被重新绑定为异步版本
        # 兼容测试 mock（同步 lambda）和生产代码（async def）
        _runtime_result = resolve_host_llm_runtime(
            rollout_key=session_id,
            scene_type=scene_type,
        )
        if asyncio.iscoroutine(_runtime_result):
            llm_runtime = await _runtime_result
        else:
            llm_runtime = _runtime_result
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
        # [选项二] 接入 ContextPackBuilder：将召回结果组装为 ContextPack，执行 token 预算控制
        context_pack_builder_summary: dict[str, Any] = {}
        _cpb_slots: dict[str, Any] = {}
        try:
            from host_brain.app.dependencies import get_context_pack_builder  # noqa: PLC0415
            _cpb = get_context_pack_builder()
            _evidence_items = list(retrieval_evidence_items or [])
            _recall_results = _build_context_pack_recall_results(_evidence_items)
            _context_pack_active = _parse_bool_env("HOST_CONTEXT_PACK_ACTIVE", default=True)
            if _recall_results:
                _cp = _cpb.build(recall_results=_recall_results, task_type="chat")
                _rewritten_items = _context_pack_to_retrieval_evidence_items(_cp)
                _applied_to_prompt = False
                if _context_pack_active and _rewritten_items:
                    retrieval_evidence_items = _rewritten_items
                    _applied_to_prompt = True
                # 提取 CPB 的 slot 内容，供 build_chat_context_pack 直接使用，
                # 统一两套 context_pack 构建路径（避免 slot 分配被覆盖）
                if _context_pack_active:
                    from host_brain.app.schemas.context_pack import TRUNCATION_PRIORITY  # noqa: PLC0415
                    for _slot_name in TRUNCATION_PRIORITY:
                        _slot = _cp.get_slot(_slot_name)
                        if _slot and str(getattr(_slot, "content", "") or "").strip():
                            _cpb_slots[_slot_name] = str(_slot.content).strip()
                context_pack_builder_summary = {
                    "status": "OK",
                    "mode": ("ACTIVE" if _applied_to_prompt else "SHADOW"),
                    "active_enabled": _context_pack_active,
                    "applied_to_prompt": _applied_to_prompt,
                    "rewritten_item_count": len(_rewritten_items),
                    "input_item_count": len(_evidence_items),
                    "total_tokens": _cp.total_token_count,
                    "slot_count": len([s for s in _cp.slots.values() if s and s.content]),
                    "conflict_events": len(_cp.conflict_events),
                    "subgraph_trace_count": len(_cp.subgraph_trace),
                    "cpb_slots_injected": list(_cpb_slots.keys()),
                }
            else:
                context_pack_builder_summary = {
                    "status": "SKIPPED",
                    "mode": "SHADOW",
                    "active_enabled": _context_pack_active,
                    "reason_code": "NO_RECALL_RESULTS",
                    "input_item_count": len(_evidence_items),
                }
        except Exception as _cpb_exc:
            context_pack_builder_summary = {"status": "DEGRADED", "reason": str(_cpb_exc)}
            _logger.debug(
                "ContextPackBuilder 调用失败（降级）: %s",
                _cpb_exc,
                extra={"request_id": request_id},
            )

        prompt_build_result = await _build_stream_chat_prompt(
            message=resolved_message,
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
            cpb_slots=_cpb_slots if _cpb_slots else None,
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
        llm_stream_started_ms = int(time.time() * 1000)

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
            if first_token_latency_ms is None:
                first_token_latency_ms = max(0, int(time.time() * 1000) - llm_stream_started_ms)
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

        # ── 8-10. 后处理并行执行：Airlock / Episodic / Bond / VTS ──────────
        # 四个步骤之间无数据依赖，使用 asyncio.gather 并行执行，
        # 总延迟 = max(各步骤延迟) 而非各步骤之和，减少 COMMIT 前等待时间。
        narrative = repair_utf8_latin1_mojibake(full_text) or "系统有点忙，请稍后再试。"

        _post_started_ms = int(time.time() * 1000)

        async def _run_airlock() -> tuple[Dict[str, Any], int]:
            _t0 = int(time.time() * 1000)
            _ingress_result = None
            try:
                _ingress_result = await asyncio.to_thread(
                    quarantine_ingress,
                    narrative,
                    request_id=request_id,
                    ledger=get_ledger(),
                    ts_ms=ts_ms,
                )
            except Exception as exc:
                _logger.warning("Airlock 入口检疫失败: %s", exc, extra={"request_id": request_id})
            _refs: Dict[str, Any] = {}
            if _ingress_result is not None:
                _refs = {
                    "ingress_classification": _ingress_result.classification,
                    "quarantine_digest": _ingress_result.quarantine_digest,
                }
            return _refs, max(0, int(time.time() * 1000) - _t0)

        async def _run_episodic() -> tuple[Dict[str, Any], int]:
            _t0 = int(time.time() * 1000)
            _summary: Dict[str, Any] = {}
            try:
                _summary = await asyncio.to_thread(
                    persist_chat_episodic_memory,
                    request_id=request_id,
                    intent_id=str(intent_id or request_id),
                    session_id=session_id,
                    user_message=resolved_message,
                    assistant_reply=narrative,
                    emotion_context=emotion_context,
                    ts_ms=ts_ms,
                    episodic_store=get_episodic_store(),
                )
            except Exception as exc:
                _summary = {
                    "status": "DEGRADED",
                    "reason_code": "EPISODIC_WRITE_FAILED",
                    "memory_id": None,
                    "detail": str(exc),
                }
                _logger.debug("流式模式下情节记忆写入跳过",
                              extra={"request_id": request_id, "error": str(exc)})
            return _summary, max(0, int(time.time() * 1000) - _t0)

        async def _run_bond() -> tuple[Dict[str, Any], int]:
            _t0 = int(time.time() * 1000)
            _summary: Dict[str, Any] = {"status": "SKIPPED", "reason_code": "NOT_ATTEMPTED"}
            try:
                from host_brain.app.dependencies import get_bond_stage_engine, get_bond_update_queue  # noqa: PLC0415
                from host_brain.bond.bond_dynamics import BondDynamicsWriter  # noqa: PLC0415
                _etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
                _intensity = float(emotion_context.get("emotion_intensity") or 0.5)
                _update_kind = BondDynamicsWriter.classify_interaction(
                    text=resolved_message,
                    emotion_intensity=_intensity,
                )
                _bond_payload = {
                    "request_id": request_id,
                    "intent_id": str(intent_id or request_id),
                    "session_id": session_id,
                    "seq": 1,
                    "user_id": session_id,
                    "topic_id": resolved_message[:64],
                    "capability_hint": "bond_memory",
                    "update_kind": _update_kind,
                    "evidence_refs": [str(intent_id or request_id)],
                    "emotion_tag": _etag,
                    "emotion_intensity": _intensity,
                    "ts_ms": ts_ms,
                }
                _bond_queue = get_bond_update_queue()
                _queue_result = _bond_queue.enqueue(_bond_payload)
                _bond_queue_status = str(_queue_result.get("status") or "UNKNOWN")
                if _bond_queue_status != "QUEUED":
                    _bond_writer = BondDynamicsWriter(
                        ledger=get_ledger(),
                        stage_engine=get_bond_stage_engine(),
                    )
                    await asyncio.to_thread(_bond_writer.apply_update, **_bond_payload)
                    _bond_queue_status = "DIRECT_FALLBACK_APPLIED"
                _summary = {"status": _bond_queue_status, "update_kind": _update_kind}
            except Exception as exc:
                _summary = {"status": "DEGRADED", "reason_code": str(exc)}
                _logger.debug("流式路径 Bond 更新跳过", extra={"request_id": request_id, "error": str(exc)})
            return _summary, max(0, int(time.time() * 1000) - _t0)

        async def _run_tts() -> tuple[Dict[str, Any], int]:
            """驱动 Edge TTS 语音合成（fire-and-forget，不阻塞 COMMIT）。

            仅当 HOST_STREAM_TTS_ENABLED=true（默认 true）且 narrative 非空时触发。
            通过 EdgeRpcClient.post_speak() 发送 HTTP POST /v1/edge/speak。
            """
            _t0 = int(time.time() * 1000)
            _tts_enabled = str(os.environ.get("HOST_STREAM_TTS_ENABLED") or "true").strip().lower()
            if _tts_enabled not in {"1", "true", "yes", "on"}:
                return {"status": "SKIPPED", "reason_code": "HOST_STREAM_TTS_DISABLED"}, 0
            if not narrative or not narrative.strip():
                return {"status": "SKIPPED", "reason_code": "NARRATIVE_EMPTY"}, 0
            try:
                from host_brain.channels.edge_rpc_client import post_speak as _post_speak  # noqa: PLC0415
                _etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
                _tts_result = await _post_speak(
                    text=narrative,
                    request_id=request_id,
                    emotion_tag=_etag,
                )
                return _tts_result.as_dict(), max(0, int(time.time() * 1000) - _t0)
            except Exception as exc:
                _logger.debug(
                    "流式路径 TTS 驱动失败（降级）: %s",
                    exc,
                    extra={"request_id": request_id},
                )
                return {
                    "status": "DEGRADED",
                    "reason_code": "TTS_CALL_FAILED",
                    "detail": str(exc),
                }, max(0, int(time.time() * 1000) - _t0)

        (
            (airlock_refs, airlock_latency_ms),
            (episodic_summary, episodic_latency_ms),
            (bond_summary, bond_latency_ms),
            (vts_summary, vts_latency_ms),
            (tts_summary, tts_latency_ms),
        ) = await asyncio.gather(
            _run_airlock(),
            _run_episodic(),
            _run_bond(),
            _wrap_vts_with_latency(
                request_id=request_id,
                emotion_context=emotion_context,
                ts_ms=ts_ms,
            ),
            _run_tts(),
        )

        background_tasks_total_ms = max(0, int(time.time() * 1000) - _post_started_ms)
        trace_metrics = {
            "retrieval_latency_ms": retrieval_latency_ms,
            "first_token_latency_ms": first_token_latency_ms,
            "background_tasks_latency_ms": {
                "airlock_ingress_ms": airlock_latency_ms,
                "episodic_memory_ms": episodic_latency_ms,
                "vts_emotion_ms": vts_latency_ms,
                "bond_update_ms": bond_latency_ms,
                "tts_speak_ms": tts_latency_ms,
                "total_ms": background_tasks_total_ms,
            },
        }

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
                "tts": tts_summary,
                "airlock": airlock_refs,
                "episodic_memory": episodic_summary,
                "trace_metrics": trace_metrics,
                "context_pack_builder": context_pack_builder_summary,
                "routing_matrix_decision": dict(llm_runtime.get("routing_matrix_decision") or {}),
                "state_arbiter": state_arbiter_summary,
            },
        })

    except Exception as exc:
        _logger.error("SSE 聊天流式处理失败: %s", exc, extra={"request_id": request_id})
        yield _sse({
            "phase": "COMMIT",
            "done": True,
            "decision": "REJECT",
            "reason_code": "INTERNAL_SERVER_ERROR",
            "request_id": request_id,
        })


# ---------------------------------------------------------------------------
# handle_chat_stream 和 handle_chat_stream_post 已移除。
# /v1/chat/stream 接口已删除，统一走 POST /v1/chat（SSE 流式响应）。
# _sse_event_generator 由 chat_handler._unified_chat_sse_generator 在
# SCENE_CHAT 场景下调用。
# ---------------------------------------------------------------------------
