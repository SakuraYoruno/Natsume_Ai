"""Chat 上下文构建辅助函数。

从 chat_handler.py 拆分，负责构建 context pack、style bindings 和 generation prompt payload。
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from host_brain.app.dependencies import get_ledger
from host_brain.app.graph_config_builder import CHAT_DIRECT_NODE_ID
from host_brain.app.prompt_builder import _truncate_for_prompt, infer_render_target_lang
from host_brain.core.context_pack import (
    build_emotion_pulse_slot,
    build_world_canon_slot,
    validate_context_pack,
)
from host_brain.core.qa import check_wcanon_slot_violation
from host_brain.memory.canon_style_store import StyleCanonStore


def normalize_node_style_bindings(
    style_bindings: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for node_id, row in dict(style_bindings or {}).items():
        if not isinstance(row, Mapping):
            continue
        normalized[str(node_id)] = {
            "canon_id": str(row.get("canon_id") or "").strip(),
            "version": row.get("version"),
            "hash": str(row.get("hash") or "").strip(),
            "tone_only": bool(row.get("tone_only", True)),
            "unsafe_as_fact": bool(row.get("unsafe_as_fact", True)),
            "style_instruction": str(row.get("style_instruction") or "").strip(),
            "citation": str(row.get("citation") or "").strip(),
        }
    return normalized


def build_node_style_bindings(*, render_target_lang: str | None = None) -> dict[str, dict[str, Any]]:
    style_root = str(os.environ.get("Natsume_STYLE_CANON_ROOT") or "host_brain/canon/style").strip()
    core_canon_id = str(os.environ.get("HOST_STYLE_CORE_CANON_ID") or "natsume_ai_cn").strip()
    resolved_render_lang = str(render_target_lang or "").strip().lower()
    explicit_render_canon_id = str(os.environ.get("HOST_STYLE_RENDER_CANON_ID") or "").strip()
    if explicit_render_canon_id:
        render_canon_id = explicit_render_canon_id
    elif resolved_render_lang == "zh":
        render_canon_id = (
            str(os.environ.get("HOST_STYLE_RENDER_CN_CANON_ID") or "").strip()
            or core_canon_id
            or "natsume_ai_cn"
        )
    else:
        render_canon_id = str(os.environ.get("HOST_STYLE_RENDER_JA_CANON_ID") or "natsume_ai_ja").strip()
    secondary_canon_id = str(os.environ.get("HOST_STYLE_SECONDARY_CANON_ID") or core_canon_id).strip()

    store = StyleCanonStore(style_root=style_root, ledger=get_ledger())
    resolved: dict[str, dict[str, Any]] = {}
    mapping = [
        ("core_reasoner_primary", core_canon_id),
        ("core_reasoner_secondary", secondary_canon_id),
        (CHAT_DIRECT_NODE_ID, render_canon_id),
        ("renderer_ja_c_style", render_canon_id),
    ]
    for node_id, canon_id in mapping:
        if not canon_id:
            continue
        try:
            entry = store.get(canon_id)
        except Exception:
            continue
        if entry is None:
            continue
        resolved[node_id] = {
            "canon_id": entry.canon_id,
            "version": int(entry.version),
            "hash": entry.hash,
            "tone_only": bool(entry.tone_only),
            "unsafe_as_fact": bool(entry.unsafe_as_fact),
            "style_instruction": entry.content,
            "citation": f"style_canon:{entry.canon_id}@v{entry.version}:{entry.hash[:12]}",
        }
    return resolved


def build_chat_context_pack(
    *,
    request_id: str,
    session_id: str,
    thread_id: str | None,
    retrieval_summary: Mapping[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    style_bindings: Mapping[str, Mapping[str, Any]] | None,
    emotion_context: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from host_brain.app.emotion_context import normalize_chat_emotion_context  # noqa: PLC0415
    normalized_style_bindings = normalize_node_style_bindings(style_bindings)
    normalized_emotion_context = normalize_chat_emotion_context(emotion_context)

    style_rows: list[dict[str, Any]] = []
    for node_id, row in normalized_style_bindings.items():
        style_rows.append(
            {
                "slot": "style_pack",
                "kind": "style_canon",
                "node_id": node_id,
                "canon_id": row.get("canon_id"),
                "version": row.get("version"),
                "hash": row.get("hash"),
                "tone_only": bool(row.get("tone_only", True)),
                "unsafe_as_fact": bool(row.get("unsafe_as_fact", True)),
                "citation": row.get("citation"),
                "instruction": _truncate_for_prompt(row.get("style_instruction"), limit=1600),
            }
        )

    authoritative_rows: list[dict[str, Any]] = []
    world_root = str(os.environ.get("Natsume_WORLD_CANON_ROOT") or "").strip() or None
    try:
        world_top_k = int(str(os.environ.get("HOST_WCANON_TOP_K") or "3").strip())
    except ValueError:
        world_top_k = 3
    world_top_k = max(1, min(world_top_k, 10))
    try:
        authoritative_rows = build_world_canon_slot(world_root=world_root, top_k=world_top_k)
    except Exception as exc:
        check_wcanon_slot_violation(
            slot_name="world_canon_slot",
            detail=f"world canon slot build failed: {exc}",
            ledger=get_ledger(),
            request_id=request_id,
        )
        authoritative_rows = []

    retrieval_rows: list[dict[str, Any]] = []
    for row in retrieval_evidence_items[:10]:
        if not isinstance(row, Mapping):
            continue
        snippet = _truncate_for_prompt(row.get("snippet"), limit=240)
        if not snippet:
            continue
        retrieval_rows.append(
            {
                "slot": "retrieval",
                "kind": "episodic",
                "source_ref": str(row.get("source_ref") or "").strip() or None,
                "evidence_ref": str(row.get("evidence_ref") or "").strip() or None,
                "content": snippet,
                "score": row.get("score"),
                "confidence": row.get("confidence"),
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

    context_pack = {
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
            "node_bindings": normalized_style_bindings,
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

    validation = validate_context_pack(context_pack)
    violations = [
        {"code": item.code, "detail": item.detail, "slot": item.slot}
        for item in validation.violations
    ]
    if validation.valid:
        return (
            context_pack,
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

    degraded_pack = dict(context_pack)
    degraded_pack["authoritative"] = []
    degraded_validation = validate_context_pack(degraded_pack)
    if degraded_validation.valid:
        return (
            degraded_pack,
            {"valid": True, "degraded": True, "reason_code": "CONTEXT_PACK_DEGRADED", "violation_count": len(violations), "violations": violations},
        )

    minimal_pack = {
        "hard_snapshot": {}, "bond_summary": {}, "authoritative": [], "retrieval": [],
        "style_pack": {}, "constraints": {}, "tool_meta_pack": [],
    }
    return (
        minimal_pack,
        {"valid": True, "degraded": True, "reason_code": "CONTEXT_PACK_MINIMAL_FALLBACK", "violation_count": len(violations), "violations": violations},
    )


def build_chat_generation_prompt_payload(
    *,
    request_id: str,
    session_id: str,
    thread_id: str | None,
    user_message: str,
    retrieval_summary: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    style_bindings: Mapping[str, Mapping[str, Any]] | None = None,
    emotion_context: Mapping[str, Any] | None = None,
    render_target_lang: str | None = None,
) -> dict[str, Any]:
    from host_brain.app.emotion_context import normalize_chat_emotion_context  # noqa: PLC0415
    normalized_style_bindings = normalize_node_style_bindings(style_bindings)
    normalized_emotion_context = normalize_chat_emotion_context(emotion_context)
    resolved_render_lang = str(render_target_lang or "").strip().lower()
    if resolved_render_lang not in {"zh", "ja"}:
        resolved_render_lang = infer_render_target_lang(user_message=user_message)
    context_pack, context_pack_validation = build_chat_context_pack(
        request_id=request_id,
        session_id=session_id,
        thread_id=thread_id,
        retrieval_summary=retrieval_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        style_bindings=normalized_style_bindings,
        emotion_context=normalized_emotion_context,
    )
    return {
        "request_id": request_id,
        "session_id": session_id,
        "thread_id": thread_id,
        "user_message": user_message,
        "render_target_lang": resolved_render_lang,
        "style_bindings": normalized_style_bindings,
        "emotion_context": {
            "mood_state": normalized_emotion_context.get("mood_state"),
            "emotion_tag": normalized_emotion_context.get("emotion_tag"),
            "emotion_range_hint": normalized_emotion_context.get("emotion_range_hint"),
        },
        "context_pack": context_pack,
        "context_pack_validation": context_pack_validation,
        "retrieval": {
            "status": retrieval_summary.get("status"),
            "reason_code": retrieval_summary.get("reason_code"),
            "route_count": retrieval_summary.get("route_count"),
            "candidate_count": retrieval_summary.get("candidate_count"),
            "rerank_topk": retrieval_summary.get("rerank_topk"),
            "degraded": retrieval_summary.get("degraded"),
            "evidence_items": retrieval_evidence_items,
        },
    }


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
