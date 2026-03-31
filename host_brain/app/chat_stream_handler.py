"""SSE 流式聊天处理模块（SCENE_CHAT 专用）。

从 fastapi_app.py 拆分，负责 SCENE_CHAT 场景下的 token 级流式 SSE 响应。
由 chat_handler._unified_chat_sse_generator 在场景分类后调用。

完整链路：
  [Governor 鉴权（由调用方预完成）] → 并行 Retrieval → stream_llm_chunks → SentenceSplitter
  → 响应内容分类 → episodic memory 写入 → COMMIT 事件

注意：/v1/chat/stream 接口已删除，统一走 POST /v1/chat（SSE 流式响应）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import string
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
from host_brain.app.intent_seq import allocate_intent_seq
from host_brain.app.emotion_context import (
    build_chat_emotion_snapshot,
    build_chat_governance_snapshot,
    resolve_chat_emotion_context,
)
from host_brain.app.chat_runtime_input import (
    ChatRuntimeInput,
    build_chat_runtime_input,
    get_chat_runtime_prompt_context_observability,
    get_chat_runtime_prompt_context_validation,
    get_chat_runtime_emotion_snapshot,
)
from host_brain.app.graph_config_builder import (
    CHAT_DIRECT_NODE_ID,
    SCENE_CHAT,
    build_unified_route_decision,
    resolve_chat_retrieval_gate,
    resolve_host_llm_runtime,  # 同步版本，供测试 mock 使用
    resolve_host_llm_runtime_async,  # 修复审计问题 #1：异步版本，供实际调用
)
from host_brain.bond.user_identity import resolve_bond_user_id

# 修复审计问题 #1：模块级别名，供 _sse_event_generator 调用（可被测试 mock）
# 测试通过 monkeypatch.setattr(stream_mod, "resolve_host_llm_runtime", ...) 替换此引用
resolve_host_llm_runtime = resolve_host_llm_runtime_async  # noqa: F811
from host_brain.app.observability_persist import (
    persist_chat_episodic_memory,
    persist_chat_observability_rows,
)
from host_brain.app.prompt_builder import (
    _truncate_for_prompt,
    extract_gateway_narrative,
    extract_json_object_from_text,
    infer_render_target_lang,
    repair_utf8_latin1_mojibake,
)
from host_brain.app.schemas import ChatRequest
from host_brain.app.sentence_splitter import SentenceSplitter
from host_brain.airlock.quarantine import classify_ingress
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

# 回复内容简单情感推断（仅当当前为 NEUTRAL 时用于 TTS/VTS 表情，与 vts_runtime_mapping 标签一致）
_EMOTION_INFER_KEYWORDS: list[tuple[str, str]] = [
    ("HAPPY", "哈哈|开心|高兴|嘿嘿|笑|太好了|耶"),
    ("SAD", "难过|伤心|哭|呜|抱歉|对不起"),
    ("ANGRY", "生气|气死|烦"),
    ("LOVING", "谢谢|感谢|感动|喜欢|爱你"),
    ("SURPRISED", "哇|真的吗|竟然"),
]

def _infer_emotion_tag_for_tts(narrative: str, current_tag: str) -> str:
    """当 current_tag 为 NEUTRAL 时，根据回复文案简单推断 emotion_tag 供 TTS/VTS 使用。"""
    import re
    if not narrative or str(current_tag or "").strip().upper() != "NEUTRAL":
        return str(current_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    text = (narrative or "")[:500]
    for tag, pattern in _EMOTION_INFER_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            return tag
    return "NEUTRAL"


def _should_buffer_stream_output(
    *,
    emotion_context: Mapping[str, Any] | None,
    bond_summary: Mapping[str, Any] | None,
) -> bool:
    """Buffer high-risk turns so reflection can revise before the first visible chunk."""
    if not _parse_bool_env("HOST_STREAM_REFLECTION_BUFFER_ENABLED", default=True):
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
# 内部辅助：构建 prompt
# ---------------------------------------------------------------------------


def _log_prompt_context_observability(
    *,
    request_id: str,
    session_id: str,
    prompt_context_observability: Mapping[str, Any],
) -> None:
    if not isinstance(prompt_context_observability, Mapping) or not prompt_context_observability:
        return

    selected_tier = str(prompt_context_observability.get("selected_tier") or "").strip()
    selected_reason = str(prompt_context_observability.get("selected_reason") or "").strip()
    retrieval_metrics_raw = prompt_context_observability.get("retrieval_metrics")
    retrieval_metrics = dict(retrieval_metrics_raw) if isinstance(retrieval_metrics_raw, Mapping) else {}
    source_stats_raw = prompt_context_observability.get("source_stats")
    source_stats = dict(source_stats_raw) if isinstance(source_stats_raw, Mapping) else {}
    slot_counts_raw = prompt_context_observability.get("slot_item_counts")
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
            "context_size_chars": prompt_context_observability.get("context_size_chars"),
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
                "degrade_order": list(prompt_context_observability.get("degrade_order") or []),
            },
        )


def _log_stream_voice_event(
    *,
    request_id: str,
    session_id: str,
    seq: int,
    event_type: str,
    summary: Mapping[str, Any],
    stage: str,
) -> None:
    try:
        get_ledger().log_voice_event(
            {
                "request_id": request_id,
                "session_id": session_id,
                "event_type": event_type,
                "direction": "host_to_edge",
                "seq": seq,
                "status": str(summary.get("status") or "UNKNOWN"),
                "reason_code": summary.get("reason_code"),
                "stage": stage,
                "payload": dict(summary),
                "detail": str(summary.get("detail") or "").strip() or None,
                "ts_ms": int(time.time() * 1000),
            }
        )
    except Exception as exc:
        _logger.debug(
            "stream voice event persistence skipped",
            extra={"request_id": request_id, "event_type": event_type, "error": str(exc)},
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


def _decode_partial_json_string(raw_text: str) -> str:
    if not raw_text:
        return ""
    out: list[str] = []
    idx = 0
    while idx < len(raw_text):
        ch = raw_text[idx]
        if ch != "\\":
            out.append(ch)
            idx += 1
            continue
        if idx + 1 >= len(raw_text):
            break
        nxt = raw_text[idx + 1]
        if nxt == "u":
            if idx + 6 <= len(raw_text):
                hex_value = raw_text[idx + 2 : idx + 6]
                if all(part in string.hexdigits for part in hex_value):
                    out.append(chr(int(hex_value, 16)))
                    idx += 6
                    continue
            break
        mapped = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }.get(nxt)
        out.append(mapped if mapped is not None else nxt)
        idx += 2
    return "".join(out)


def _extract_stream_render_text(raw_text: str) -> str:
    normalized = str(raw_text or "")
    if not normalized:
        return ""
    stripped = normalized.lstrip()
    token = '"text":"'
    marker = stripped.find(token)
    if marker < 0:
        for prefix_marker in ('{"narrative"', '{\\"narrative\\"', '[{"narrative"', '[{\\"narrative\\"'):
            prefix_index = stripped.find(prefix_marker)
            if prefix_index > 0:
                visible_prefix = stripped[:prefix_index].rstrip()
                if visible_prefix:
                    return repair_utf8_latin1_mojibake(visible_prefix)
        if stripped.startswith("{"):
            return ""
        return repair_utf8_latin1_mojibake(normalized)

    cursor = marker + len(token)
    collected: list[str] = []
    escaped = False
    while cursor < len(stripped):
        ch = stripped[cursor]
        if ch == '"' and not escaped:
            break
        collected.append(ch)
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        cursor += 1
    return repair_utf8_latin1_mojibake(_decode_partial_json_string("".join(collected)))


async def _build_stream_chat_prompt(
    *,
    runtime_input: ChatRuntimeInput,
) -> tuple[str, str, dict, dict, dict[str, Any]]:
    """构建流式聊天的 prompt、system_prompt 和生成参数。

    返回: (prompt_text, system_prompt, gen_params, emotion_context, prompt_meta)
    """

    resolved_emotion_context = dict(runtime_input.emotion_state)

    from host_brain.app.chat_context_builder import (  # noqa: PLC0415
        build_chat_prompt_runtime_payload as _build_prompt_runtime_payload,
    )
    from host_brain.app.prompt_builder import (  # noqa: PLC0415
        render_node_generation_prompt,
        _build_node_role_instruction,
    )

    render_target_lang = str(runtime_input.render_target_lang).strip().lower() or "ja"
    prompt_payload = _build_prompt_runtime_payload(runtime_input=runtime_input)
    if resolved_emotion_context:
        prompt_payload = adjust_gen_params_for_emotion(prompt_payload, resolved_emotion_context)

    gen_params = dict(prompt_payload.get("gen_params") or {})
    prompt_text = render_node_generation_prompt(
        node_id=CHAT_DIRECT_NODE_ID,
        runtime_payload=prompt_payload,
    )
    prompt_meta = {
        "prompt_context_validation": get_chat_runtime_prompt_context_validation(prompt_payload),
        "prompt_context_observability": get_chat_runtime_prompt_context_observability(prompt_payload),
        "emotion_snapshot": get_chat_runtime_emotion_snapshot(prompt_payload),
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
    render_target_lang: str,
) -> tuple[Dict[str, Any], list[dict[str, Any]]]:
    """并行执行检索，返回 (retrieval_summary, retrieval_evidence_items)。"""
    try:
        _retr_top_k = int(str(os.environ.get("HOST_RETR_TOP_K") or "8").strip())
    except ValueError:
        _retr_top_k = 8
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
            render_target_lang=render_target_lang,
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
    raw = str(os.environ.get("HOST_STREAM_RETRIEVAL_TIMEOUT_MS") or "5000").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 5000
    return max(250, min(value, 15000))


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


_RECALL_PACK_SLOT_ALIASES: dict[str, str] = {
    "world": "world_canon",
    "world_canon": "world_canon",
    # Phase XIII: style_canon/persona_utterance 已走独立通道，不再作为 RecallPack slot
    "episodic": "episodic_vector",
    "episodic_store": "episodic_vector",
    "episodic_vector": "episodic_vector",
    "bond": "bond_graph",
    "bond_graph": "bond_graph",
    "artifact": "artifact_vault",
    "artifact_vault": "artifact_vault",
    "sqlite": "sqlite_ledger",
    "sqlite_ledger": "sqlite_ledger",
    "ledger": "sqlite_ledger",
}

_RECALL_PACK_SLOT_TO_STORE_ID: dict[str, str] = {
    "world_canon": "world_canon",
    "episodic_vector": "episodic_vector",
    "bond_graph": "bond_graph",
    "artifact_vault": "artifact_vault",
    "sqlite_ledger": "sqlite_ledger",
}


def _normalize_recall_pack_slot(
    *,
    slot_hint: str,
    source_ref: str,
) -> str | None:
    normalized_hint = str(slot_hint or "").strip().lower()
    if normalized_hint:
        direct = _RECALL_PACK_SLOT_ALIASES.get(normalized_hint)
        if direct:
            return direct

    normalized_source_ref = str(source_ref or "").strip().lower()
    if not normalized_source_ref:
        return None
    for alias, slot in _RECALL_PACK_SLOT_ALIASES.items():
        token = f"{alias}://"
        if normalized_source_ref.startswith(token):
            return slot
    return None


def _build_recall_pack_inputs(
    retrieval_evidence_items: list[dict[str, Any]],
) -> dict[str, str]:
    recall_results: dict[str, str] = {}
    for item in list(retrieval_evidence_items or []):
        if not isinstance(item, Mapping):
            continue

        metadata_raw = item.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
        slot = _normalize_recall_pack_slot(
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


def _recall_pack_to_retrieval_evidence_items(recall_pack: Any) -> list[dict[str, Any]]:
    from host_brain.app.schemas.recall_pack import RECALL_SLOT_PRIORITY  # noqa: PLC0415

    rewritten: list[dict[str, Any]] = []
    for slot_name in RECALL_SLOT_PRIORITY:
        slot = recall_pack.get_slot(slot_name)
        if slot is None:
            continue
        content = str(getattr(slot, "content", "") or "").strip()
        if not content:
            continue

        store_id = _RECALL_PACK_SLOT_TO_STORE_ID.get(slot_name, slot_name)
        rewritten.append(
            {
                "slot_name": slot_name,
                "source": slot_name,
                "store_id": store_id,
                "evidence_ref": f"recall_pack://{slot_name}",
                "source_ref": f"{slot_name}://recall_pack_builder",
                "snippet": _truncate_for_prompt(content, limit=240),
                "content": content,
                "text": content,
                "metadata": {
                    "store_id": store_id,
                    "source": "recall_pack_builder",
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


async def _resolve_state_arbiter_style_fact(
    *,
    session_id: str,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """[Ref #17] 查询 StyleCanon 风格规范，注入 prompt 作为风格约束。"""
    summary: dict[str, Any] = {
        "status": "SKIPPED",
        "reason_code": "NOT_AVAILABLE",
        "fact_key": "style_canon_facts",
    }
    try:
        from host_brain.app.dependencies import get_state_arbiter  # noqa: PLC0415

        arbiter = get_state_arbiter()
        result = await arbiter.resolve("style_canon_facts", session_id=session_id)
        if result is None:
            summary["reason_code"] = "NO_MATCH"
            return summary, None

        fact_text = _normalize_state_arbiter_value(result.value)
        summary.update(
            {
                "source": str(getattr(result.source, "value", result.source) or "unknown"),
                "is_fallback": bool(getattr(result, "is_fallback", False)),
                "conflict_detected": bool(getattr(result, "conflict_detected", False)),
            }
        )
        if not fact_text:
            summary["reason_code"] = "EMPTY_VALUE"
            return summary, None

        # 构建 style_canon 证据条目（slot_name=style_canon，优先级低于 world_canon）
        evidence_item = {
            "slot_name": "style_canon",
            "doc_id": f"style_canon::{session_id}",
            "snippet": fact_text[:600],
            "score": 0.85,
            "source": "state_arbiter_style",
            "metadata": {
                "fact_key": "style_canon_facts",
                "source": str(getattr(result.source, "value", result.source) or "unknown"),
            },
        }
        summary["status"] = "APPLIED"
        summary["reason_code"] = None
        summary["value_chars"] = len(fact_text)
        return summary, evidence_item
    except Exception as exc:
        _logger.debug(
            "StateArbiter style_canon 调用失败（降级）: %s",
            exc,
            extra={"request_id": request_id},
        )
        summary["status"] = "DEGRADED"
        summary["reason_code"] = "STATE_ARBITER_STYLE_FAILED"
        summary["detail"] = str(exc)
        return summary, None


async def _resolve_state_arbiter_bond_fact(
    *,
    session_id: str,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """[Ref #18] 查询 BondGraph 关系强度，注入 prompt 作为关系上下文。

    BondGraph 记录了 Host 与用户之间的情感关系阶段（bond_stage）和互动强度，
    注入后可让 LLM 根据当前关系阶段调整回应风格（亲密度感知）。
    优先级：world_canon(1.0) > style_canon(0.85) > episodic_latest(0.75) > bond_graph(0.65)
    """
    summary: dict[str, Any] = {
        "status": "SKIPPED",
        "reason_code": "NOT_AVAILABLE",
        "fact_key": "bond_graph_state",
    }
    try:
        from host_brain.app.dependencies import get_bond_stage_engine  # noqa: PLC0415

        _bond_engine = get_bond_stage_engine()
        if _bond_engine is None:
            summary["reason_code"] = "BOND_ENGINE_NOT_AVAILABLE"
            return summary, None

        # 从 BondStageEngine 获取当前关系阶段和强度
        _bond_state = await asyncio.to_thread(
            _bond_engine.get_bond_state,
            session_id=session_id,
        )
        if _bond_state is None:
            summary["reason_code"] = "NO_BOND_STATE"
            return summary, None

        bond_stage = str(getattr(_bond_state, "stage", "") or "").strip()
        bond_score = float(getattr(_bond_state, "score", 0.0) or 0.0)
        bond_label = str(getattr(_bond_state, "label", "") or "").strip()
        interaction_count = int(getattr(_bond_state, "interaction_count", 0) or 0)

        if not bond_stage:
            summary["reason_code"] = "EMPTY_BOND_STAGE"
            return summary, None

        # 构建关系上下文文本（供 LLM 感知当前关系阶段）
        fact_parts: list[str] = [f"当前关系阶段：{bond_stage}"]
        if bond_label:
            fact_parts.append(f"关系标签：{bond_label}")
        if bond_score > 0:
            fact_parts.append(f"关系强度：{bond_score:.2f}")
        if interaction_count > 0:
            fact_parts.append(f"累计互动次数：{interaction_count}")
        fact_text = "，".join(fact_parts)

        evidence_item = {
            "slot_name": "bond_graph",
            "doc_id": f"bond_graph::{session_id}",
            "snippet": fact_text[:300],
            "score": 0.65,
            "source": "state_arbiter_bond",
            "metadata": {
                "fact_key": "bond_graph_state",
                "bond_stage": bond_stage,
                "bond_score": bond_score,
                "interaction_count": interaction_count,
                "source": "bond_stage_engine",
            },
        }
        summary["status"] = "APPLIED"
        summary["reason_code"] = None
        summary["bond_stage"] = bond_stage
        summary["bond_score"] = bond_score
        summary["value_chars"] = len(fact_text)
        return summary, evidence_item

    except Exception as exc:
        _logger.debug(
            "BondGraph 状态查询失败（降级）: %s",
            exc,
            extra={"request_id": request_id},
        )
        summary["status"] = "DEGRADED"
        summary["reason_code"] = "BOND_GRAPH_FAILED"
        summary["detail"] = str(exc)
        return summary, None


async def _resolve_state_arbiter_episodic_fact(
    *,
    session_id: str,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """[Ref #17] 查询最新情节记忆，注入 prompt 作为近期记忆上下文。"""
    summary: dict[str, Any] = {
        "status": "SKIPPED",
        "reason_code": "NOT_AVAILABLE",
        "fact_key": "episodic_latest",
    }
    try:
        from host_brain.app.dependencies import get_state_arbiter  # noqa: PLC0415

        arbiter = get_state_arbiter()
        result = await arbiter.resolve("episodic_latest", session_id=session_id)
        if result is None:
            summary["reason_code"] = "NO_MATCH"
            return summary, None

        fact_text = _normalize_state_arbiter_value(result.value)
        summary.update(
            {
                "source": str(getattr(result.source, "value", result.source) or "unknown"),
                "is_fallback": bool(getattr(result, "is_fallback", False)),
                "conflict_detected": bool(getattr(result, "conflict_detected", False)),
            }
        )
        if not fact_text:
            summary["reason_code"] = "EMPTY_VALUE"
            return summary, None

        # 构建 episodic_latest 证据条目（slot_name=episodic_latest，优先级最低）
        evidence_item = {
            "slot_name": "episodic_latest",
            "doc_id": f"episodic_latest::{session_id}",
            "snippet": fact_text[:400],
            "score": 0.75,
            "source": "state_arbiter_episodic",
            "metadata": {
                "fact_key": "episodic_latest",
                "source": str(getattr(result.source, "value", result.source) or "unknown"),
            },
        }
        summary["status"] = "APPLIED"
        summary["reason_code"] = None
        summary["value_chars"] = len(fact_text)
        return summary, evidence_item
    except Exception as exc:
        _logger.debug(
            "StateArbiter episodic_latest 调用失败（降级）: %s",
            exc,
            extra={"request_id": request_id},
        )
        summary["status"] = "DEGRADED"
        summary["reason_code"] = "STATE_ARBITER_EPISODIC_FAILED"
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

        # 方向 C：计算过渡帧（VTS 渐变动画）
        transition_frames: list[dict[str, Any]] = []
        try:
            from host_brain.channels.vts import build_emotion_transition_frames  # noqa: PLC0415
            _prev_tag = str((emotion_context or {}).get("_prev_emotion_tag") or "").strip().upper()
            if result.status == "APPLIED" and result.emotion_tag:
                frames = build_emotion_transition_frames(
                    from_tag=_prev_tag or None,
                    to_tag=result.emotion_tag,
                )
                if len(frames) > 1:
                    transition_frames = [
                        {
                            "frame_index": f.frame_index,
                            "emotion_tag": f.emotion_tag,
                            "expression_id": f.expression_id,
                            "delay_ms": f.delay_ms,
                            "interpolation_t": f.interpolation_t,
                        }
                        for f in frames
                    ]
        except Exception:
            pass  # 过渡帧计算失败时静默降级

        return {
            "status": str(result.status),
            "reason_code": result.reason_code,
            "emotion_tag": result.emotion_tag,
            "expression_id": result.expression_id,
            "trigger": result.trigger,
            "detail": result.detail,
            # 方向 C：过渡帧数据（供 Edge 端执行渐变动画）
            "transition_frames": transition_frames,
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
    suppress_edge_vts: bool = False,
) -> tuple[Dict[str, Any], int]:
    """包装 _drive_stream_vts_emotion，返回 (result_dict, latency_ms) 元组，
    以便与 asyncio.gather 中其他返回 tuple 的协程统一解包。"""
    _t0 = int(time.time() * 1000)
    if bool(suppress_edge_vts):
        return {
            "status": "SKIPPED",
            "reason_code": "SUPPRESSED_BY_INPUT_POLICY",
            "emotion_tag": None,
            "expression_id": None,
            "trigger": "HOST",
            "detail": "edge VTS suppressed by input policy",
        }, 0
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
    suppress_edge_tts: bool = False,
    suppress_edge_vts: bool = False,
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
            intent_seq = allocate_intent_seq(session_id=session_id)
            intent_draft = {
                "schema_version": "Natsume.intent.v1",
                "id": f"intent::{request_id}",
                "ts_ms": ts_ms,
                "session_id": session_id,
                "seq": intent_seq,
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
        render_target_lang = infer_render_target_lang(user_message=resolved_message)
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
                    render_target_lang=render_target_lang,
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

        # [Ref #17/18] 扩展 StateArbiter 查询：style_canon_facts + episodic_latest + bond_graph
        # 并行查询，降级时不阻断主链路
        (
            (_style_summary, _style_evidence_item),
            (_episodic_summary, _episodic_evidence_item),
            (_bond_summary, _bond_evidence_item),
        ) = await asyncio.gather(
            _resolve_state_arbiter_style_fact(session_id=session_id, request_id=request_id),
            _resolve_state_arbiter_episodic_fact(session_id=session_id, request_id=request_id),
            _resolve_state_arbiter_bond_fact(session_id=session_id, request_id=request_id),
        )
        retrieval_summary["style_canon"] = dict(_style_summary)
        retrieval_summary["episodic_latest"] = dict(_episodic_summary)
        retrieval_summary["bond_graph"] = dict(_bond_summary)
        # 注入优先级：world_canon(1.0) > style_canon(0.85) > episodic_latest(0.75) > bond_graph(0.65)
        if _style_evidence_item is not None:
            retrieval_evidence_items = list(retrieval_evidence_items or []) + [_style_evidence_item]
            retrieval_summary["style_canon"]["injected"] = True
        if _episodic_evidence_item is not None:
            retrieval_evidence_items = list(retrieval_evidence_items or []) + [_episodic_evidence_item]
            retrieval_summary["episodic_latest"]["injected"] = True
        # bond_graph 追加到最末（最低优先级，关系上下文感知）
        if _bond_evidence_item is not None:
            retrieval_evidence_items = list(retrieval_evidence_items or []) + [_bond_evidence_item]
            retrieval_summary["bond_graph"]["injected"] = True

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
        # Option 2: build an internal RecallPack from recall results with token budgeting.
        recall_pack_builder_summary: dict[str, Any] = {}
        _recall_slot_text: dict[str, Any] = {}
        try:
            from host_brain.app.dependencies import get_recall_pack_builder  # noqa: PLC0415
            _cpb = get_recall_pack_builder()
            _evidence_items = list(retrieval_evidence_items or [])
            _recall_results = _build_recall_pack_inputs(_evidence_items)
            _recall_pack_active = _parse_bool_env("HOST_RECALL_PACK_ACTIVE", default=True)
            _slot_budget_overrides = dict(retrieval_summary.get("slot_budget_overrides") or {})
            if _recall_results:
                _cp = _cpb.build(
                    recall_results=_recall_results,
                    task_type="chat",
                    slot_budget_overrides=_slot_budget_overrides,
                )
                _rewritten_items = _recall_pack_to_retrieval_evidence_items(_cp)
                _applied_to_prompt = False
                if _recall_pack_active and _rewritten_items:
                    retrieval_evidence_items = _rewritten_items
                    _applied_to_prompt = True
                # Extract RecallPack slot text for prompt-facing context-pack assembly,
                # 统一 prompt_context 构建路径（避免 slot 分配被覆盖）
                if _recall_pack_active:
                    from host_brain.app.schemas.recall_pack import RECALL_SLOT_PRIORITY  # noqa: PLC0415
                    for _slot_name in RECALL_SLOT_PRIORITY:
                        _slot = _cp.get_slot(_slot_name)
                        if _slot and str(getattr(_slot, "content", "") or "").strip():
                            _recall_slot_text[_slot_name] = str(_slot.content).strip()
                recall_pack_builder_summary = {
                    "status": "OK",
                    "mode": ("ACTIVE" if _applied_to_prompt else "SHADOW"),
                    "active_enabled": _recall_pack_active,
                    "applied_to_prompt": _applied_to_prompt,
                    "rewritten_item_count": len(_rewritten_items),
                    "input_item_count": len(_evidence_items),
                    "total_tokens": _cp.total_token_count,
                    "slot_count": len(
                        [
                            _slot_name
                            for _slot_name in RECALL_SLOT_PRIORITY
                            if (_slot := _cp.get_slot(_slot_name)) and str(getattr(_slot, "content", "") or "").strip()
                        ]
                    ),
                    "conflict_events": len(_cp.conflict_events),
                    "subgraph_trace_count": len(_cp.subgraph_trace),
                    "recall_slot_text_injected": list(_recall_slot_text.keys()),
                    "slot_budget_overrides": _slot_budget_overrides,
                }
            else:
                recall_pack_builder_summary = {
                    "status": "SKIPPED",
                    "mode": "SHADOW",
                    "active_enabled": _recall_pack_active,
                    "reason_code": "NO_RECALL_RESULTS",
                    "input_item_count": len(_evidence_items),
                }
        except Exception as _cpb_exc:
            recall_pack_builder_summary = {"status": "DEGRADED", "reason": str(_cpb_exc)}
            _logger.debug(
                "RecallPackBuilder 调用失败（降级）: %s",
                _cpb_exc,
                extra={"request_id": request_id},
            )

        # 从 _bond_summary 提取关系阶段信息传入 runtime_input
        _bond_stage_for_ctx = str(_bond_summary.get("bond_stage") or "").strip()
        _bond_summary_for_ctx: dict[str, Any] = {}
        if _bond_stage_for_ctx:
            _bond_summary_for_ctx = {
                "stage": _bond_stage_for_ctx,
                "trend": str(_bond_summary.get("trend") or ""),
                "capability_hint": str(_bond_summary.get("capability_hint") or ""),
            }

        runtime_input = build_chat_runtime_input(
            request_id=request_id,
            session_id=session_id,
            thread_id=thread_id,
            user_message=resolved_message,
            render_target_lang=render_target_lang,
            retrieval_summary=retrieval_summary or {"status": "SKIPPED", "reason_code": "SSE_STREAM_MODE"},
            retrieval_evidence_items=retrieval_evidence_items or [],
            emotion_state=emotion_context if emotion_context else None,
            emotion_snapshot=emotion_snapshot,
            recall_slot_text=_recall_slot_text if _recall_slot_text else None,
            bond_summary=_bond_summary_for_ctx or None,
            scene_type=scene_type,
        )
        prompt_build_result = await _build_stream_chat_prompt(
            runtime_input=runtime_input,
        )
        prompt_context_validation: dict[str, Any] = {}
        prompt_context_observability: dict[str, Any] = {}
        emotion_snapshot_for_audit: dict[str, Any] = dict(emotion_snapshot)
        if len(prompt_build_result) >= 5:
            prompt_text, system_prompt, gen_params, _, prompt_meta = prompt_build_result
            if isinstance(prompt_meta, Mapping):
                prompt_context_validation = dict(prompt_meta.get("prompt_context_validation") or {})
                prompt_context_observability = dict(prompt_meta.get("prompt_context_observability") or {})
                emotion_snapshot_for_audit = dict(prompt_meta.get("emotion_snapshot") or emotion_snapshot)
        else:
            prompt_text, system_prompt, gen_params, _ = prompt_build_result  # type: ignore[misc]

        # ── Ref #70: 读取 PromptBuilder 注入检测结果，写入 Ledger audit_log ──
        try:
            from host_brain.app.prompt_builder import get_last_injection_meta  # noqa: PLC0415
            _inj_meta = get_last_injection_meta()
            if _inj_meta.get("mode") == "ACTIVE" and _inj_meta.get("detected"):
                _inj_risk = str(_inj_meta.get("risk_level") or "LOW").upper()
                _inj_tag = f"INJECTION_DETECTED:{_inj_risk}"
                ledger.append_audit_log(
                    request_id=request_id,
                    session_id=session_id,
                    stage="prompt_builder",
                    audit_tag=_inj_tag,
                    detail=json.dumps({
                        "risk_level": _inj_risk,
                        "matched_patterns": list(_inj_meta.get("matched_patterns") or [])[:5],
                        "mode": "ACTIVE",
                    }, ensure_ascii=False),
                    ts_ms=ts_ms,
                )
        except Exception:
            pass
        _log_prompt_context_observability(
            request_id=request_id,
            session_id=session_id,
            prompt_context_observability=prompt_context_observability,
        )

        # ── 7. 流式生成 + SentenceSplitter ───────────────────────────────
        splitter = SentenceSplitter()
        full_text = ""
        rendered_text = ""
        sentence_index = 0
        llm_stream_started_ms = int(time.time() * 1000)
        _llm_timed_out = False
        buffer_stream_output = _should_buffer_stream_output(
            emotion_context=emotion_context,
            bond_summary=_bond_summary_for_ctx or None,
        )

        try:
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
                visible_text = _extract_stream_render_text(full_text)
                if not visible_text:
                    continue
                if visible_text.startswith(rendered_text):
                    render_chunk = visible_text[len(rendered_text):]
                else:
                    render_chunk = visible_text
                if not render_chunk:
                    continue
                rendered_text = visible_text
                if buffer_stream_output:
                    continue
                yield _sse({"phase": "RENDER", "chunk": render_chunk})

                # 句子边界检测
                for sentence in splitter.feed(render_chunk):
                    yield _sse({
                        "phase": "SENTENCE",
                        "sentence": sentence,
                        "sentence_index": sentence_index,
                    })
                    sentence_index += 1
        except (TimeoutError, Exception) as _llm_exc:
            # LLM 超时或冷启动失败时，先发送占位句让用户知道系统在线
            _elapsed_ms = max(0, int(time.time() * 1000) - llm_stream_started_ms)
            _is_timeout = isinstance(_llm_exc, TimeoutError) or "timeout" in str(_llm_exc).lower()
            _logger.warning(
                "LLM 流式生成失败（%s）: %s elapsed=%dms",
                "timeout" if _is_timeout else "error",
                _llm_exc,
                _elapsed_ms,
                extra={"request_id": request_id},
            )
            if not full_text.strip():
                # 没有任何输出时，发送陪伴性占位句
                _placeholder = "ちょっと待ってね…"
                full_text = _placeholder
                rendered_text = _placeholder
                if not buffer_stream_output:
                    yield _sse({"phase": "RENDER", "chunk": _placeholder})
                    for sentence in splitter.feed(_placeholder):
                        yield _sse({
                            "phase": "SENTENCE",
                            "sentence": sentence,
                            "sentence_index": sentence_index,
                        })
                        sentence_index += 1
            _llm_timed_out = True
            # 记录超时到 error_log
            try:
                ledger.append_audit_log(
                    request_id=request_id,
                    session_id=session_id,
                    stage="S_LLM_GATEWAY_PROVIDER",
                    audit_tag="LLM_STREAM_FAILED",
                    detail=json.dumps({
                        "reason": "timeout" if _is_timeout else "error",
                        "elapsed_ms": _elapsed_ms,
                        "error": str(_llm_exc)[:200],
                    }, ensure_ascii=False),
                    ts_ms=ts_ms,
                )
            except Exception:
                pass

        # ── 8-10. 后处理并行执行：Airlock / Episodic / Bond / VTS ──────────
        # 四个步骤之间无数据依赖，使用 asyncio.gather 并行执行，
        # 总延迟 = max(各步骤延迟) 而非各步骤之和，减少 COMMIT 前等待时间。
        narrative = repair_utf8_latin1_mojibake(full_text) or "系统有点忙，请稍后再试。"
        raw_narrative = repair_utf8_latin1_mojibake(full_text)
        if rendered_text.strip():
            narrative = repair_utf8_latin1_mojibake(rendered_text)
        llm_intent_draft: dict[str, Any] = {}
        llm_contract_payload: dict[str, Any] = {}
        try:
            llm_contract_payload = extract_json_object_from_text(raw_narrative or narrative)
            extracted_narrative = extract_gateway_narrative(llm_contract_payload)
            if extracted_narrative:
                narrative = extracted_narrative
            intent_draft_raw = llm_contract_payload.get("intent_draft")
            if isinstance(intent_draft_raw, Mapping):
                llm_intent_draft = dict(intent_draft_raw)
        except Exception:
            if rendered_text.strip():
                narrative = repair_utf8_latin1_mojibake(rendered_text)
                llm_contract_payload = {
                    "narrative": {"text": narrative},
                    "intent_draft": {},
                    "meta": {"draft_only": True, "salvaged_from_stream": True},
                }
            else:
                llm_contract_payload = {}
        if narrative:
            final_visible_text = repair_utf8_latin1_mojibake(narrative)
            if not buffer_stream_output:
                if final_visible_text.startswith(rendered_text):
                    trailing_render_chunk = final_visible_text[len(rendered_text):]
                else:
                    trailing_render_chunk = final_visible_text
                if trailing_render_chunk:
                    rendered_text = final_visible_text
                    yield _sse({"phase": "RENDER", "chunk": trailing_render_chunk})
                    for sentence in splitter.feed(trailing_render_chunk):
                        yield _sse({
                            "phase": "SENTENCE",
                            "sentence": sentence,
                            "sentence_index": sentence_index,
                        })
                        sentence_index += 1
            try:
                from host_brain.app.intelligent_runtime import apply_response_intelligence  # noqa: PLC0415

                narrative, response_intelligence_meta = apply_response_intelligence(
                    request_id=request_id,
                    user_message=resolved_message,
                    narrative=final_visible_text,
                    emotion_context=emotion_context,
                    bond_summary=_bond_summary_for_ctx or None,
                    render_lang=render_target_lang,
                    allow_rewrite=buffer_stream_output,
                )
                if buffer_stream_output:
                    if narrative:
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
                if isinstance(response_intelligence_meta, Mapping):
                    response_intelligence_meta = dict(response_intelligence_meta)
                    response_intelligence_meta["stream_buffered"] = bool(buffer_stream_output)
            except Exception as exc:
                response_intelligence_meta = {
                    "reflection": {"status": "DEGRADED", "reason_code": "POSTPROCESS_FAILED", "detail": str(exc)},
                    "stream_buffered": bool(buffer_stream_output),
                }
        else:
            response_intelligence_meta = {
                "reflection": {"status": "SKIPPED", "reason_code": "EMPTY_NARRATIVE"},
                "multimodal_output": {"status": "SKIPPED", "reason_code": "EMPTY_NARRATIVE"},
                "stream_buffered": bool(buffer_stream_output),
            }

        # flush 剩余未成句文本
        remainder = splitter.flush()
        if remainder:
            yield _sse({
                "phase": "SENTENCE",
                "sentence": remainder,
                "sentence_index": sentence_index,
            })
            sentence_index += 1
        llm_total_latency_ms = max(0, int(time.time() * 1000) - llm_stream_started_ms)
        llm_status = "OK" if narrative.strip() else "DEGRADED"
        llm_reason_code = None if narrative.strip() else "LLM_EMPTY_OUTPUT"
        llm_summary: dict[str, Any] = {
            "status": llm_status,
            "reason_code": llm_reason_code,
            "provider": provider_name,
            "model_id": model_id,
            "route_target": str(llm_runtime.get("route_target") or "cloud"),
            "route_reason_code": str(llm_runtime.get("route_reason_code") or ""),
            "emotion_snapshot": emotion_snapshot_for_audit,
            "prompt_context_validation": prompt_context_validation,
            "prompt_context_observability": prompt_context_observability,
            "latency_ms": llm_total_latency_ms,
            "intent_draft": llm_intent_draft,
            "node_count": 1,
            "nodes": [
                {
                    "node_id": CHAT_DIRECT_NODE_ID,
                    "status": llm_status,
                    "reason_code": llm_reason_code,
                    "provider": provider_name,
                    "model_id": model_id,
                    "latency_ms": llm_total_latency_ms,
                    "input": {
                        "user_message": resolved_message,
                        "render_target_lang": render_target_lang,
                        "retrieval_status": retrieval_summary.get("status"),
                        "prompt_context_validation": prompt_context_validation,
                    },
                    "output": {
                        "narrative": narrative,
                        "contract_payload": llm_contract_payload,
                        "emotion_snapshot": emotion_snapshot_for_audit,
                        "response_intelligence": response_intelligence_meta,
                    },
                }
            ],
        }

        _post_started_ms = int(time.time() * 1000)

        async def _run_airlock() -> tuple[Dict[str, Any], int]:
            _t0 = int(time.time() * 1000)
            _classification = None
            try:
                _classification = await asyncio.to_thread(classify_ingress, narrative)
            except Exception as exc:
                _logger.warning("响应分类失败: %s", exc, extra={"request_id": request_id})
            _refs: Dict[str, Any] = {}
            if _classification:
                _refs = {
                    "ingress_classification": _classification,
                    "quarantine_digest": None,
                    "airlock_mode": "OBSERVE_ONLY",
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
                    "user_id": resolve_bond_user_id(session_id=session_id),
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
            if bool(suppress_edge_tts):
                return {"status": "SKIPPED", "reason_code": "SUPPRESSED_BY_INPUT_POLICY"}, 0
            _tts_enabled = str(os.environ.get("HOST_STREAM_TTS_ENABLED") or "true").strip().lower()
            if _tts_enabled not in {"1", "true", "yes", "on"}:
                return {"status": "SKIPPED", "reason_code": "HOST_STREAM_TTS_DISABLED"}, 0
            if not narrative or not narrative.strip():
                return {"status": "SKIPPED", "reason_code": "NARRATIVE_EMPTY"}, 0
            try:
                from host_brain.channels.edge_rpc_client import post_speak as _post_speak  # noqa: PLC0415
                from host_brain.app.intelligent_runtime import resolve_tts_route  # noqa: PLC0415

                _base_etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
                _tts_route = resolve_tts_route(
                    text=narrative,
                    emotion_context={**dict(emotion_context), "emotion_tag": _base_etag},
                    bond_summary=_bond_summary_for_ctx or None,
                )
                _etag = str(_tts_route.get("emotion_tag") or _infer_emotion_tag_for_tts(narrative, _base_etag)).strip()
                _intensity_raw = emotion_context.get("emotion_intensity")
                _intensity = None
                if _intensity_raw is not None:
                    try:
                        _intensity = max(0.0, min(1.0, float(_intensity_raw)))
                    except (TypeError, ValueError):
                        pass
                _tts_result = await _post_speak(
                    text=narrative,
                    request_id=request_id,
                    session_id=session_id,
                    speed=float(_tts_route.get("speed") or 1.0),
                    pitch=float(_tts_route.get("pitch") or 0.0),
                    emotion_tag=_etag,
                    emotion_intensity=_intensity,
                    style_note=str(_tts_route.get("style_note") or ""),
                    lang_hint=str(_tts_route.get("lang_hint") or render_target_lang),
                )
                _tts_payload = _tts_result.as_dict()
                _tts_payload["route"] = dict(_tts_route.get("route") or {})
                return _tts_payload, max(0, int(time.time() * 1000) - _t0)
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
                suppress_edge_vts=bool(suppress_edge_vts),
            ),
            _run_tts(),
        )

        _log_stream_voice_event(
            request_id=request_id,
            session_id=session_id,
            seq=1,
            event_type="HOST_STREAM_VTS",
            summary=vts_summary,
            stage="S_EDGE_VTS",
        )
        _log_stream_voice_event(
            request_id=request_id,
            session_id=session_id,
            seq=2,
            event_type="HOST_STREAM_TTS",
            summary=tts_summary,
            stage="S_EDGE_TTS",
        )

        _obs_persist_started_ms = int(time.time() * 1000)
        obs_persist_latency_ms = 0
        try:
            persist_chat_observability_rows(
                ledger=get_ledger(),
                request_id=request_id,
                session_id=session_id,
                llm_summary=llm_summary,
                ts_ms=ts_ms,
                fallback_reason_code=str(llm_summary.get("route_reason_code") or "") or None,
            )
        except Exception as exc:
            _logger.warning(
                "stream chat observability persistence failed",
                extra={"request_id": request_id, "error": str(exc)},
            )
        finally:
            obs_persist_latency_ms = max(0, int(time.time() * 1000) - _obs_persist_started_ms)

        background_tasks_total_ms = max(0, int(time.time() * 1000) - _post_started_ms)
        trace_metrics = {
            "retrieval_latency_ms": retrieval_latency_ms,
            "first_token_latency_ms": first_token_latency_ms,
            "stream_buffered_rewrite": bool(buffer_stream_output),
            "background_tasks_latency_ms": {
                "observability_persist_ms": obs_persist_latency_ms,
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
        llm_summary["route_decision"] = route_decision
        yield _sse({
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": narrative,
            "intent_draft": llm_intent_draft,
            "request_id": request_id,
            "audit_refs": {
                "intent_id": intent_id,
                "retrieval": retrieval_summary,
                "llm": llm_summary,
                "route_decision": route_decision,
                "emotion_snapshot": emotion_snapshot_for_audit,
                "prompt_context_validation": prompt_context_validation,
                "prompt_context_observability": prompt_context_observability,
                "vts": vts_summary,
                "tts": tts_summary,
                "airlock": airlock_refs,
                "episodic_memory": episodic_summary,
                "trace_metrics": trace_metrics,
                "recall_pack_builder": recall_pack_builder_summary,
                "routing_matrix_decision": dict(llm_runtime.get("routing_matrix_decision") or {}),
                "state_arbiter": state_arbiter_summary,
                "response_intelligence": response_intelligence_meta,
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
