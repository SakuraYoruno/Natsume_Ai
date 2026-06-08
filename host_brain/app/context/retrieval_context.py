"""retrieval_context.py — 检索行构建与上下文可观测性辅助。

负责检索行过滤、路径分类和上下文可观测性构建。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from host_brain.app.prompt_builder import _truncate_for_prompt
from host_brain.app.context.persona_expression import (
    _coerce_non_negative_int,
    _coerce_optional_float,
    _clamp_float,
    _coerce_optional_bool,
)

logger = logging.getLogger(__name__)

_LOW_SIGNAL_PERSONA_INTENTS: frozenset[str] = frozenset({"affection"})
_SHORT_CHAT_PERSONA_SCORE_FLOOR = 0.12

CONTEXT_DEGRADE_ORDER: tuple[str, ...] = (
    "six_route",
    "core_three",
    "minimal_context",
    "bare_llm",
)


# ---------------------------------------------------------------------------
# 检索行过滤
# ---------------------------------------------------------------------------

def _extract_query_plan_intent(retrieval_summary: Mapping[str, Any]) -> str:
    query_plan_raw = retrieval_summary.get("query_plan")
    query_plan = dict(query_plan_raw) if isinstance(query_plan_raw, Mapping) else {}
    return str(query_plan.get("intent") or "").strip().lower()


def _extract_query_plan_text_len(retrieval_summary: Mapping[str, Any]) -> int:
    query_plan_raw = retrieval_summary.get("query_plan")
    query_plan = dict(query_plan_raw) if isinstance(query_plan_raw, Mapping) else {}
    return len(str(query_plan.get("original_query") or "").strip())


def _should_drop_persona_row(
    *,
    store_id: str,
    score: Any,
    confidence: Any,
    query_plan_intent: str,
    query_plan_text_len: int,
) -> bool:
    if str(store_id or "").strip().lower() != "persona_utterance":
        return False
    if query_plan_intent in _LOW_SIGNAL_PERSONA_INTENTS:
        return True
    if query_plan_intent == "chat" and 0 < query_plan_text_len <= 8:
        row_score = _coerce_optional_float(confidence)
        if row_score is None:
            row_score = _coerce_optional_float(score)
        if row_score is not None and row_score < _SHORT_CHAT_PERSONA_SCORE_FLOOR:
            return True
    return False


def _build_retrieval_rows_fallback(
    *,
    retrieval_summary: Mapping[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    retrieval_rows: list[dict[str, Any]] = []
    query_plan_intent = _extract_query_plan_intent(retrieval_summary)
    query_plan_text_len = _extract_query_plan_text_len(retrieval_summary)
    for row in retrieval_evidence_items[:10]:
        if not isinstance(row, Mapping):
            continue
        snippet = _truncate_for_prompt(row.get("snippet"), limit=240)
        if not snippet:
            continue
        metadata_raw = row.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
        store_id = str(row.get("store_id") or metadata.get("store_id") or "").strip().lower()
        if _should_drop_persona_row(
            store_id=store_id,
            score=row.get("score"),
            confidence=row.get("confidence"),
            query_plan_intent=query_plan_intent,
            query_plan_text_len=query_plan_text_len,
        ):
            continue
        retrieval_kind = "persona_utterance" if store_id == "persona_utterance" else "episodic"
        emotion_color_tag = str(
            metadata.get("emotion_color_tag") or metadata.get("emotion_tag") or ""
        ).strip().upper() or None
        suppression_flag = _coerce_optional_bool(metadata.get("suppression_flag"))
        distortion_level = _coerce_optional_float(metadata.get("distortion_level"))
        if distortion_level is not None:
            distortion_level = round(_clamp_float(float(distortion_level), minimum=0.0, maximum=1.0), 4)
        retrieval_rows.append({
            "slot": "retrieval",
            "kind": retrieval_kind,
            "source_ref": str(row.get("source_ref") or "").strip() or None,
            "evidence_ref": str(row.get("evidence_ref") or "").strip() or None,
            "content": snippet,
            "score": row.get("score"),
            "confidence": row.get("confidence"),
            "store_id": store_id or None,
            "canon_id": str(metadata.get("canon_id") or "").strip() or None,
            "lang": str(metadata.get("lang") or "").strip().lower() or None,
            "speaker": str(metadata.get("speaker") or "").strip() or None,
            "file_rel_path": str(metadata.get("file_rel_path") or "").strip() or None,
            "block_id": str(metadata.get("block_id") or "").strip() or None,
            "line_index": metadata.get("line_index"),
            "emotion_color_tag": emotion_color_tag,
            "suppression_flag": suppression_flag,
            "distortion_level": distortion_level,
        })
    return retrieval_rows


def _build_retrieval_rows(
    *,
    retrieval_summary: Mapping[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        from host_brain.tribe.hippocampus_agent import get_hippocampus_agent  # noqa: PLC0415
        return get_hippocampus_agent().build_prompt_retrieval_rows(
            retrieval_summary=retrieval_summary,
            retrieval_evidence_items=retrieval_evidence_items,
            max_items=10,
            snippet_limit=240,
        )
    except Exception as exc:
        logger.debug("hippocampus retrieval row build failed, fallback local logic: %s", exc)
        return _build_retrieval_rows_fallback(
            retrieval_summary=retrieval_summary,
            retrieval_evidence_items=retrieval_evidence_items,
        )


# ---------------------------------------------------------------------------
# 可观测性辅助
# ---------------------------------------------------------------------------

def _count_kind(rows: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        kind = str(row.get("kind") or "unknown").strip().lower() or "unknown"
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _extract_route_ids(routes_value: Any) -> list[str]:
    route_ids: list[str] = []
    routes = list(routes_value or []) if isinstance(routes_value, list) else []
    for route in routes:
        route_id = ""
        if isinstance(route, Mapping):
            route_id = str(route.get("route_id") or route.get("store_id") or route.get("id") or "").strip()
        else:
            route_id = str(route).strip()
        if route_id and route_id not in route_ids:
            route_ids.append(route_id)
    return route_ids


def _classify_retrieval_path_for_obs(
    *,
    retrieval_summary: Mapping[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
) -> str:
    status = str(retrieval_summary.get("status") or "").strip().upper()
    degraded = bool(retrieval_summary.get("degraded", False))
    route_count = _coerce_non_negative_int(retrieval_summary.get("route_count"))
    candidate_count = _coerce_non_negative_int(retrieval_summary.get("candidate_count"))
    topk_count = _coerce_non_negative_int(retrieval_summary.get("topk_count"))
    if status == "OK" and not degraded:
        return "success"
    if route_count > 0 or candidate_count > 0 or topk_count > 0 or len(retrieval_evidence_items) > 0:
        return "partial_failure"
    return "all_failure"


def _select_context_degrade_tier(
    *,
    retrieval_summary: Mapping[str, Any],
    prompt_context_validation: Mapping[str, Any],
    slot_item_counts: Mapping[str, int],
) -> tuple[str, str]:
    validation_reason = str(prompt_context_validation.get("reason_code") or "").strip().upper()
    retrieval_status = str(retrieval_summary.get("status") or "").strip().upper()
    retrieval_degraded = bool(retrieval_summary.get("degraded", False))
    route_count = _coerce_non_negative_int(retrieval_summary.get("route_count"))
    retrieval_items = _coerce_non_negative_int(slot_item_counts.get("retrieval", 0))
    non_retrieval_items = (
        _coerce_non_negative_int(slot_item_counts.get("authoritative", 0))
        + _coerce_non_negative_int(slot_item_counts.get("tool_meta_pack", 0))
    )
    total_items = sum(_coerce_non_negative_int(value) for value in slot_item_counts.values())

    if validation_reason == "PROMPT_CONTEXT_MINIMAL_FALLBACK":
        if total_items == 0:
            return "bare_llm", "prompt_context_minimal_fallback_and_empty_slots"
        return "minimal_context", "prompt_context_minimal_fallback"
    if validation_reason == "PROMPT_CONTEXT_DEGRADED":
        return "minimal_context", "prompt_context_authoritative_slot_degraded"
    if route_count >= 6 and retrieval_items > 0 and retrieval_status == "OK" and not retrieval_degraded:
        return "six_route", "full_six_route_retrieval_ready"
    if route_count >= 3 and retrieval_items > 0:
        return "core_three", "core_three_retrieval_routes_ready"
    if retrieval_items > 0:
        return "core_three", "partial_retrieval_context_ready"
    if non_retrieval_items > 0:
        return "minimal_context", "retrieval_empty_using_non_retrieval_context"
    return "bare_llm", "no_context_slots_available"


def build_prompt_context_observability(
    *,
    prompt_context: Mapping[str, Any],
    prompt_context_validation: Mapping[str, Any],
    retrieval_summary: Mapping[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
) -> dict[str, Any]:
    authoritative_rows = list(prompt_context.get("authoritative") or []) if isinstance(prompt_context.get("authoritative"), list) else []
    retrieval_rows = list(prompt_context.get("retrieval") or []) if isinstance(prompt_context.get("retrieval"), list) else []
    tool_meta_rows = list(prompt_context.get("tool_meta_pack") or []) if isinstance(prompt_context.get("tool_meta_pack"), list) else []

    emotion_recall_text = str(prompt_context.get("emotion_recall") or "").strip()

    slot_item_counts = {
        "authoritative": len(authoritative_rows),
        "retrieval": len(retrieval_rows),
        "tool_meta_pack": len(tool_meta_rows),
        "emotion_recall": (1 if emotion_recall_text else 0),
    }

    selected_tier, selected_reason = _select_context_degrade_tier(
        retrieval_summary=retrieval_summary,
        prompt_context_validation=prompt_context_validation,
        slot_item_counts=slot_item_counts,
    )
    route_ids = _extract_route_ids(retrieval_summary.get("routes"))
    retrieval_path = _classify_retrieval_path_for_obs(
        retrieval_summary=retrieval_summary,
        retrieval_evidence_items=retrieval_evidence_items,
    )
    context_chars = len(json.dumps(
        {
            "authoritative": authoritative_rows,
            "retrieval": retrieval_rows,
            "constraints": dict(prompt_context.get("constraints") or {}),
            "emotion_pulse": dict(prompt_context.get("emotion_pulse") or {}),
            "emotion_recall": emotion_recall_text,
        },
        ensure_ascii=False,
        sort_keys=True,
    ))

    retrieval_input_count = len(list(retrieval_evidence_items or []))
    retrieval_used_count = len(retrieval_rows)

    return {
        "version": "prompt_context_obs.v1",
        "degrade_order": list(CONTEXT_DEGRADE_ORDER),
        "selected_tier": selected_tier,
        "selected_tier_rank": list(CONTEXT_DEGRADE_ORDER).index(selected_tier),
        "selected_reason": selected_reason,
        "slot_item_counts": slot_item_counts,
        "kind_breakdown": {
            "authoritative": _count_kind(authoritative_rows),
            "retrieval": _count_kind(retrieval_rows),
        },
        "retrieval_metrics": {
            "status": str(retrieval_summary.get("status") or "UNKNOWN"),
            "reason_code": str(retrieval_summary.get("reason_code") or "NONE"),
            "degraded": bool(retrieval_summary.get("degraded", False)),
            "path": retrieval_path,
            "route_count": _coerce_non_negative_int(retrieval_summary.get("route_count")),
            "candidate_count": _coerce_non_negative_int(retrieval_summary.get("candidate_count")),
            "topk_count": _coerce_non_negative_int(retrieval_summary.get("topk_count")),
            "route_ids": route_ids,
            "cache_hit": bool(retrieval_summary.get("cache_hit", False)),
            "cache_ttl_ms": _coerce_non_negative_int(retrieval_summary.get("cache_ttl_ms")),
            "cache_age_ms": _coerce_non_negative_int(retrieval_summary.get("cache_age_ms")),
        },
        "source_stats": {
            "retrieval_input_items": retrieval_input_count,
            "retrieval_used_items": retrieval_used_count,
            "retrieval_item_cap": 10,
            "retrieval_trimmed_items": max(0, retrieval_input_count - retrieval_used_count),
            "world_authoritative_items": len(authoritative_rows),
            "emotion_recall_present": bool(emotion_recall_text),
        },
        "context_size_chars": context_chars,
    }


build_retrieval_rows = _build_retrieval_rows


__all__ = [
    "CONTEXT_DEGRADE_ORDER",
    "_extract_query_plan_intent",
    "_extract_query_plan_text_len",
    "_should_drop_persona_row",
    "_build_retrieval_rows_fallback",
    "_build_retrieval_rows",
    "_count_kind",
    "_extract_route_ids",
    "_classify_retrieval_path_for_obs",
    "_select_context_degrade_tier",
    "build_prompt_context_observability",
    "build_retrieval_rows",
]
