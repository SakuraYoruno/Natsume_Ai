"""Chat 涓婁笅鏂囨瀯寤鸿緟鍔╁嚱鏁般€?
浠?chat_handler.py 鎷嗗垎锛岃礋璐ｆ瀯寤?context pack銆乻tyle bindings 鍜?generation prompt payload銆?"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

from host_brain.app.chat_runtime_input import ChatRuntimeInput
from host_brain.app.dependencies import get_ledger
from host_brain.app.prompt_builder import _truncate_for_prompt
from host_brain.core.prompt_context import (
    build_emotion_pulse_slot,
    validate_prompt_context,
)
from host_brain.core.qa import check_wcanon_slot_violation


CONTEXT_DEGRADE_ORDER: tuple[str, ...] = (
    "six_route",
    "core_three",
    "minimal_context",
    "bare_llm",
)


def build_prompt_context(
    *,
    runtime_input: ChatRuntimeInput,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build prompt_context for prompt injection."""
    request_id = runtime_input.request_id
    session_id = runtime_input.session_id
    thread_id = runtime_input.thread_id
    retrieval_summary = dict(runtime_input.retrieval_summary)
    retrieval_evidence_items = list(runtime_input.retrieval_evidence_items)
    normalized_emotion_context = dict(runtime_input.emotion_state)
    recall_slot_text = dict(runtime_input.recall_slot_text)

    # style_canon and world_canon only enter prompt_context through RecallPack.
    _cpb_style = recall_slot_text.get("style_canon")
    style_rows: list[dict[str, Any]] = []
    if _cpb_style and str(_cpb_style).strip():
        style_rows.append(
            {
                "slot": "style_pack",
                "kind": "style_canon",
                "node_id": "recall_pack_builder",
                "canon_id": None,
                "version": None,
                "hash": None,
                "tone_only": True,
                "unsafe_as_fact": True,
                "citation": "recall_pack_builder://style_canon",
                "instruction": _truncate_for_prompt(str(_cpb_style), limit=1600),
                "source": "recall_pack_builder",
            }
        )

    _cpb_world = recall_slot_text.get("world_canon")
    authoritative_rows: list[dict[str, Any]] = []
    if _cpb_world and str(_cpb_world).strip():
        authoritative_rows.append(
            {
                "slot": "authoritative",
                "kind": "world_canon",
                "source_ref": "recall_pack_builder://world_canon",
                "content": _truncate_for_prompt(str(_cpb_world), limit=800),
                "citation": "recall_pack_builder://world_canon",
            }
        )

    for row in authoritative_rows:
        if isinstance(row, dict) and "source" not in row:
            row["source"] = "recall_pack_builder"

    retrieval_rows: list[dict[str, Any]] = []
    for row in retrieval_evidence_items[:10]:
        if not isinstance(row, Mapping):
            continue
        snippet = _truncate_for_prompt(row.get("snippet"), limit=240)
        if not snippet:
            continue
        metadata_raw = row.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
        store_id = str(
            row.get("store_id")
            or metadata.get("store_id")
            or ""
        ).strip().lower()
        retrieval_kind = "persona_utterance" if store_id == "persona_utterance" else "episodic"
        retrieval_rows.append(
            {
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
            }
        )

    mood_state = str(normalized_emotion_context.get("mood_state") or "steady")
    emotion_tag = str(normalized_emotion_context.get("emotion_tag") or "NEUTRAL")
    emotion_range_hint = _truncate_for_prompt(normalized_emotion_context.get("emotion_range_hint"), limit=200)
    allowed_emotion_tags = list(normalized_emotion_context.get("allowed_emotion_tags") or [])
    emotion_pulse = build_emotion_pulse_slot(
        None,
        emotion_tag=emotion_tag,
        mood_state=mood_state,
    )
    if emotion_range_hint:
        emotion_pulse["emotion_range_hint"] = emotion_range_hint
    memory_emotion_hint = {
        "mood_state": mood_state,
        "emotion_tag": emotion_tag,
        "emotion_range_hint": emotion_range_hint,
        "retrieval_status": str(retrieval_summary.get("status") or "UNKNOWN"),
        "retrieval_reason_code": str(retrieval_summary.get("reason_code") or "NONE"),
    }
    bond_texture = {
        "mood_state": mood_state,
        "emotion_tag": emotion_tag,
        "tone_hint": str(emotion_pulse.get("style_hint") or ""),
    }

    prompt_context = {
        "hard_snapshot": {
            "session_id": session_id,
            "thread_id": thread_id,
            "sys_mode": str(os.environ.get("HOST_SYS_MODE") or "normal").strip() or "normal",
            "mood_state": mood_state,
            "allowed_emotion_tags": allowed_emotion_tags,
            "emotion_range_hint": emotion_range_hint,
        },
        "bond_summary": {},
        "authoritative": authoritative_rows,
        "retrieval": retrieval_rows,
        "style_pack": {
            "style_canon": style_rows,
        },
        "constraints": {
            "strict_json_output": True,
            "no_false_execution_claim": True,
            "no_canon_as_fact": True,
            "tone_policy": "short_restrained",
            "contract_versions": {
                "core": "CoreDraft.v1",
                "render": "RenderDraft.v1",
                "verifier": "Verifier.v1",
            },
            "retrieval_status": str(retrieval_summary.get("status") or "UNKNOWN"),
            "retrieval_reason_code": str(retrieval_summary.get("reason_code") or "NONE"),
        },
        "tool_meta_pack": [],
        "emotion_pulse": emotion_pulse,
        "memory_emotion_hint": memory_emotion_hint,
        "bond_texture": bond_texture,
    }

    validation = validate_prompt_context(prompt_context)
    violations = [
        {"code": item.code, "detail": item.detail, "slot": item.slot}
        for item in validation.violations
    ]
    if validation.valid:
        return (
            prompt_context,
            {"valid": True, "degraded": False, "reason_code": None, "violation_count": 0, "violations": []},
        )

    for item in validation.violations:
        if item.slot == "authoritative":
            check_wcanon_slot_violation(
                slot_name="world_canon_slot",
                detail=item.detail,
                ledger=get_ledger(),
                request_id=request_id,
            )
            break

    degraded_pack = dict(prompt_context)
    degraded_pack["authoritative"] = []
    degraded_validation = validate_prompt_context(degraded_pack)
    if degraded_validation.valid:
        return (
            degraded_pack,
            {"valid": True, "degraded": True, "reason_code": "PROMPT_CONTEXT_DEGRADED", "violation_count": len(violations), "violations": violations},
        )

    minimal_pack = {
        "hard_snapshot": {}, "bond_summary": {}, "authoritative": [], "retrieval": [],
        "style_pack": {"style_canon": []}, "constraints": {}, "tool_meta_pack": [],
    }
    return (
        minimal_pack,
        {"valid": True, "degraded": True, "reason_code": "PROMPT_CONTEXT_MINIMAL_FALLBACK", "violation_count": len(violations), "violations": violations},
    )


def _coerce_non_negative_int(value: Any, *, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(0, parsed)


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
    rerank_topk = _coerce_non_negative_int(retrieval_summary.get("rerank_topk"))
    if status == "OK" and not degraded:
        return "success"
    if route_count > 0 or candidate_count > 0 or rerank_topk > 0 or len(retrieval_evidence_items) > 0:
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
        + _coerce_non_negative_int(slot_item_counts.get("style_canon", 0))
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

    style_pack_raw = prompt_context.get("style_pack")
    style_pack = dict(style_pack_raw) if isinstance(style_pack_raw, Mapping) else {}
    style_rows = list(style_pack.get("style_canon") or []) if isinstance(style_pack.get("style_canon"), list) else []
    slot_item_counts = {
        "authoritative": len(authoritative_rows),
        "retrieval": len(retrieval_rows),
        "style_canon": len(style_rows),
        "tool_meta_pack": len(tool_meta_rows),
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

    context_chars = len(
        json.dumps(
            {
                "authoritative": authoritative_rows,
                "retrieval": retrieval_rows,
                "style_pack": style_pack,
                "constraints": dict(prompt_context.get("constraints") or {}),
                "emotion_pulse": dict(prompt_context.get("emotion_pulse") or {}),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )

    retrieval_input_count = len(list(retrieval_evidence_items or []))
    retrieval_used_count = len(retrieval_rows)
    retrieval_item_cap = 10

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
            "style_canon": _count_kind(style_rows),
        },
        "retrieval_metrics": {
            "status": str(retrieval_summary.get("status") or "UNKNOWN"),
            "reason_code": str(retrieval_summary.get("reason_code") or "NONE"),
            "degraded": bool(retrieval_summary.get("degraded", False)),
            "path": retrieval_path,
            "route_count": _coerce_non_negative_int(retrieval_summary.get("route_count")),
            "candidate_count": _coerce_non_negative_int(retrieval_summary.get("candidate_count")),
            "rerank_topk": _coerce_non_negative_int(retrieval_summary.get("rerank_topk")),
            "route_ids": route_ids,
            "cache_hit": bool(retrieval_summary.get("cache_hit", False)),
            "cache_ttl_ms": _coerce_non_negative_int(retrieval_summary.get("cache_ttl_ms")),
            "cache_age_ms": _coerce_non_negative_int(retrieval_summary.get("cache_age_ms")),
        },
        "source_stats": {
            "retrieval_input_items": retrieval_input_count,
            "retrieval_used_items": retrieval_used_count,
            "retrieval_item_cap": retrieval_item_cap,
            "retrieval_trimmed_items": max(0, retrieval_input_count - retrieval_used_count),
            "world_authoritative_items": len(authoritative_rows),
            "style_prompt_rows": len(style_rows),
        },
        "context_size_chars": context_chars,
    }


def build_chat_runtime_payload(
    *,
    runtime_input: ChatRuntimeInput,
) -> dict[str, Any]:
    prompt_context, prompt_context_validation = build_prompt_context(
        runtime_input=runtime_input,
    )
    prompt_context_observability = build_prompt_context_observability(
        prompt_context=prompt_context,
        prompt_context_validation=prompt_context_validation,
        retrieval_summary=runtime_input.retrieval_summary,
        retrieval_evidence_items=runtime_input.retrieval_evidence_items,
    )
    return runtime_input.to_runtime_payload(
        prompt_context=prompt_context,
        prompt_context_validation=prompt_context_validation,
        prompt_context_observability=prompt_context_observability,
    )


def extract_chat_graph_node_entry(graph_output: dict[str, Any], *, node_id: str) -> dict[str, Any]:
    payload_root = dict(graph_output.get("payload") or {})
    return dict(payload_root.get(node_id) or {})


def extract_chat_graph_gateway_payload(
    graph_output: dict[str, Any], *, node_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    node_entry = extract_chat_graph_node_entry(graph_output, node_id=node_id)
    gateway_payload_raw = node_entry.get("payload")
    gateway_payload = dict(gateway_payload_raw or {}) if isinstance(gateway_payload_raw, dict) else {}
    return node_entry, gateway_payload
