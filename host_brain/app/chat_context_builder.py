"""Chat 上下文构建辅助函数。

从 chat_handler.py 拆分，负责构建 context pack、style bindings 和 generation prompt payload。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

from host_brain.app.dependencies import get_ledger
from host_brain.app.graph_config_builder import CHAT_DIRECT_NODE_ID
from host_brain.app.prompt_builder import _truncate_for_prompt, infer_render_target_lang
from host_brain.app.chat_runtime_input import build_emotion_recall_text
from host_brain.bond.user_identity import get_canonical_bond_user_id
from host_brain.core.prompt_context import (
    build_emotion_pulse_slot,
    build_world_canon_slot,
    validate_prompt_context as validate_context_pack,
)
from host_brain.core.qa import check_wcanon_slot_violation
from host_brain.memory.canon_style_store import StyleCanonStore


CONTEXT_DEGRADE_ORDER: tuple[str, ...] = (
    "six_route",
    "core_three",
    "minimal_context",
    "bare_llm",
)


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
    resolved_render_lang = str(render_target_lang or "").strip().lower()

    # ── Phase XIII v5 优先：从蒸馏产物直接读取风格指导文本 ────────────
    # v5 蒸馏产物比旧版 JSON 更丰富（句式模板+情感回应模式+真实台词示例）
    v5_instruction = _load_v5_style_instruction(resolved_render_lang)
    if v5_instruction:
        v5_entry = {
            "canon_id": f"natsume_ai_{'ja' if resolved_render_lang == 'ja' else 'cn'}_v5",
            "version": 5,
            "hash": "",
            "tone_only": True,
            "unsafe_as_fact": True,
            "style_instruction": v5_instruction,
            "citation": f"style_canon:v5_distilled:{'ja' if resolved_render_lang == 'ja' else 'cn'}",
        }
        return {
            "core_reasoner_primary": v5_entry,
            "core_reasoner_secondary": v5_entry,
            CHAT_DIRECT_NODE_ID: v5_entry,
            "renderer_ja_c_style": v5_entry,
        }

    # ── Fallback：旧版 JSON 文件（v4 及以前）────────────────────────────
    style_root = str(os.environ.get("Natsume_STYLE_CANON_ROOT") or "host_brain/canon/style").strip()
    core_canon_id = str(os.environ.get("HOST_STYLE_CORE_CANON_ID") or "natsume_ai_cn").strip()
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


def _load_v5_style_instruction(render_lang: str) -> str:
    """从 style_profile_v5.json 加载对应语言的渲染文本。

    JSON 结构：
      {
        "instruction_cn": "你是夏目藍...(中文风格指导)",
        "instruction_ja": "あなたは夏目藍...(日语风格指导)",
        ...其他统计数据...
      }

    返回空字符串表示 v5 不可用，应走旧版 fallback。
    """
    import json as _json

    v5_root = Path(os.environ.get("STYLE_V5_ROOT") or "runtime/style_canon")
    v5_path = v5_root / "style_profile_v5.json"

    if not v5_path.exists():
        return ""

    try:
        data = _json.loads(v5_path.read_text(encoding="utf-8"))
        key = "instruction_ja" if render_lang == "ja" else "instruction_cn"
        text = str(data.get(key) or "").strip()
        return text if len(text) > 100 else ""  # 至少 100 字才算有效
    except Exception:
        return ""


def build_chat_context_pack(
    *,
    request_id: str,
    session_id: str,
    thread_id: str | None,
    retrieval_summary: Mapping[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    style_bindings: Mapping[str, Mapping[str, Any]] | None,
    emotion_context: Mapping[str, Any] | None = None,
    render_target_lang: str | None = None,
    cpb_slots: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """构建 context_pack。

    当 cpb_slots 传入时（来自 ContextPackBuilder.build() 的 slot 输出），
    直接使用 CPB 的 authoritative/style_pack slot 内容，
    跳过默认的 world_canon_slot 和 style_bindings 构建逻辑，
    统一两套 context_pack 构建路径。
    """
    from host_brain.app.emotion_context import normalize_chat_emotion_context  # noqa: PLC0415
    normalized_style_bindings = normalize_node_style_bindings(style_bindings)
    normalized_emotion_context = normalize_chat_emotion_context(emotion_context)

    # ── style_pack slot ──────────────────────────────────────────────────
    # 优先使用 CPB 的 style_canon slot 内容（已经过 token 预算控制）
    _cpb_style = dict(cpb_slots or {}).get("style_canon") if cpb_slots else None
    style_rows: list[dict[str, Any]] = []
    if _cpb_style and str(_cpb_style).strip():
        # CPB 路径：将 style_canon slot 内容包装为单条 style_row
        style_rows.append(
            {
                "slot": "style_pack",
                "kind": "style_canon",
                "node_id": "context_pack_builder",
                "canon_id": None,
                "version": None,
                "hash": None,
                "tone_only": True,
                "unsafe_as_fact": True,
                "citation": "context_pack_builder://style_canon",
                "instruction": _truncate_for_prompt(str(_cpb_style), limit=1600),
            }
        )
    else:
        # 默认路径：从 style_bindings 构建
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

    # ── authoritative slot ───────────────────────────────────────────────
    # 优先使用 CPB 的 world_canon slot 内容（已经过冲突检测和 token 预算控制）
    _cpb_world = dict(cpb_slots or {}).get("world_canon") if cpb_slots else None
    authoritative_rows: list[dict[str, Any]] = []
    if _cpb_world and str(_cpb_world).strip():
        # CPB 路径：将 world_canon slot 内容包装为单条 authoritative_row
        authoritative_rows.append(
            {
                "slot": "authoritative",
                "kind": "world_canon",
                "source_ref": "context_pack_builder://world_canon",
                "content": _truncate_for_prompt(str(_cpb_world), limit=800),
                "citation": "context_pack_builder://world_canon",
            }
        )
    else:
        # 默认路径：从 world_canon_slot 构建
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
                detail=f"world canon slot 构建失败: {exc}",
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
    emotion_recall_text = (
        str(dict(cpb_slots or {}).get("emotion_recall") or "").strip()
        or build_emotion_recall_text(
            normalized_emotion_context,
            render_target_lang=render_target_lang,
        )
    )
    bond_texture = {
        "mood_state": mood_state,
        "emotion_tag": emotion_tag,
        "tone_hint": str(emotion_pulse.get("style_hint") or ""),
    }
    # Phase 4: 注入 BondEmotionalTexture 亲密温度和裂痕状态（让关系有温度）
    try:
        from host_brain.app.dependencies import get_bond_stage_engine  # noqa: PLC0415
        _bse = get_bond_stage_engine()
        if _bse is not None:
            _user_id = get_canonical_bond_user_id()
            _bond_state = _bse.get_state(_user_id)
            bond_texture["bond_stage"] = _bond_state.current_stage
            bond_texture["bond_trend"] = _bond_state.trend()
            # 亲密温度
            _texture = _bond_state.texture
            bond_texture["intimacy_temperature"] = round(_texture.intimacy_temperature, 3)
            # 亲密温度 → 自然语言描述
            if _texture.intimacy_temperature > 0.8:
                bond_texture["texture_hint"] = "和对方的关系很亲密，可以自在地表达真实想法，偶尔撒娇也没关系"
            elif _texture.intimacy_temperature > 0.6:
                bond_texture["texture_hint"] = "关系不错，交流起来很舒适，可以分享更多内心感受"
            elif _texture.intimacy_temperature > 0.4:
                bond_texture["texture_hint"] = "关系在自然发展中，保持真诚和适度的温暖"
            elif _texture.intimacy_temperature > 0.2:
                bond_texture["texture_hint"] = "关系还比较生疏，表达上注意分寸，多一些耐心"
            else:
                bond_texture["texture_hint"] = "初识阶段，保持礼貌和善意，不要过分亲密"
            # 裂痕状态
            _rift = _bond_state.rift
            if _rift is not None and _rift.is_active():
                bond_texture["rift_active"] = True
                bond_texture["rift_cause"] = _rift.rift_cause
                bond_texture["rift_repair_progress"] = round(_rift.repair_progress, 2)
                if _rift.repair_progress > 0.7:
                    bond_texture["rift_hint"] = "之前的小误会正在化解，气氛已经好多了，继续温暖互动"
                elif _rift.repair_progress > 0.3:
                    bond_texture["rift_hint"] = "之前有些不愉快，正在修复中，需要更多耐心和温柔"
                else:
                    bond_texture["rift_hint"] = f"关系有些裂痕（{_rift.rift_cause}），说话要特别小心，多表达关心"
            else:
                bond_texture["rift_active"] = False
    except (ImportError, AttributeError, Exception):
        pass  # BondStageEngine 不可用时静默降级

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
        "emotion_recall": emotion_recall_text,
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
            {"valid": True, "degraded": True, "reason_code": "PROMPT_CONTEXT_DEGRADED", "violation_count": len(violations), "violations": violations},
        )

    minimal_pack = {
        "hard_snapshot": {}, "bond_summary": {}, "authoritative": [], "retrieval": [],
        "style_pack": {}, "constraints": {}, "tool_meta_pack": [],
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
    node_bindings_raw = style_pack.get("node_bindings")
    node_bindings = dict(node_bindings_raw) if isinstance(node_bindings_raw, Mapping) else {}
    emotion_recall_text = str(prompt_context.get("emotion_recall") or "").strip()

    slot_item_counts = {
        "authoritative": len(authoritative_rows),
        "retrieval": len(retrieval_rows),
        "style_canon": len(style_rows),
        "style_node_bindings": len(node_bindings),
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

    context_chars = len(
        json.dumps(
            {
                "authoritative": authoritative_rows,
                "retrieval": retrieval_rows,
                "style_pack": style_pack,
                "constraints": dict(prompt_context.get("constraints") or {}),
                "emotion_pulse": dict(prompt_context.get("emotion_pulse") or {}),
                "emotion_recall": emotion_recall_text,
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
            "style_binding_nodes": len(node_bindings),
            "emotion_recall_present": bool(emotion_recall_text),
        },
        "context_size_chars": context_chars,
    }


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
    emotion_snapshot: Mapping[str, Any] | None = None,
    render_target_lang: str | None = None,
    cpb_slots: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.app.emotion_context import (  # noqa: PLC0415
        build_chat_emotion_snapshot,
        normalize_chat_emotion_context,
    )
    normalized_style_bindings = normalize_node_style_bindings(style_bindings)
    normalized_emotion_context = normalize_chat_emotion_context(emotion_context)
    normalized_emotion_snapshot = (
        dict(emotion_snapshot)
        if isinstance(emotion_snapshot, Mapping)
        else build_chat_emotion_snapshot(
            emotion_context=normalized_emotion_context,
            source="prompt_payload",
        )
    )
    resolved_render_lang = str(render_target_lang or "").strip().lower()
    if resolved_render_lang not in {"zh", "ja"}:
        resolved_render_lang = infer_render_target_lang(user_message=user_message)
    prompt_context, prompt_context_validation = build_chat_context_pack(
        request_id=request_id,
        session_id=session_id,
        thread_id=thread_id,
        retrieval_summary=retrieval_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        style_bindings=normalized_style_bindings,
        emotion_context=normalized_emotion_context,
        render_target_lang=resolved_render_lang,
        cpb_slots=cpb_slots,
    )
    prompt_context_observability = build_prompt_context_observability(
        prompt_context=prompt_context,
        prompt_context_validation=prompt_context_validation,
        retrieval_summary=retrieval_summary,
        retrieval_evidence_items=retrieval_evidence_items,
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
            "emotion_intensity": normalized_emotion_context.get("emotion_intensity"),
            # FIX P0-瓶颈1：传递完整情感上下文（原实现只传 4 个字段，导致 VAD/回声/独白等管线断裂）
            "vad_vector": normalized_emotion_context.get("vad_vector"),
            "emotion_echo": normalized_emotion_context.get("emotion_echo"),
            "inner_monologue": normalized_emotion_context.get("inner_monologue"),
            "triggered_emotions": normalized_emotion_context.get("triggered_emotions"),
            "user_emotion_profile": normalized_emotion_context.get("user_emotion_profile"),
            "emotion_ts_ms": normalized_emotion_context.get("emotion_ts_ms"),
            "_prev_emotion_tag": normalized_emotion_context.get("_prev_emotion_tag"),
        },
        "emotion_snapshot": normalized_emotion_snapshot,
        "prompt_context": prompt_context,
        "prompt_context_validation": prompt_context_validation,
        "prompt_context_observability": prompt_context_observability,
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


def build_chat_prompt_runtime_payload(
    *,
    runtime_input: Any,
    style_bindings: Mapping[str, Mapping[str, Any]] | None = None,
    cpb_slots: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.app.chat_runtime_input import ChatRuntimeInput  # noqa: PLC0415

    if not isinstance(runtime_input, ChatRuntimeInput):
        raise TypeError("runtime_input must be ChatRuntimeInput")

    render_target_lang = str(runtime_input.render_target_lang or "").strip().lower()
    resolved_style_bindings = (
        normalize_node_style_bindings(style_bindings)
        if style_bindings is not None
        else build_node_style_bindings(render_target_lang=render_target_lang)
    )

    recall_slot_text = dict(runtime_input.recall_slot_text or {})
    merged_slots = dict(cpb_slots or {})
    for slot_name in ("style_canon", "world_canon", "emotion_recall"):
        slot_text = str(recall_slot_text.get(slot_name) or "").strip()
        if slot_text and not str(merged_slots.get(slot_name) or "").strip():
            merged_slots[slot_name] = slot_text

    generation_payload = build_chat_generation_prompt_payload(
        request_id=str(runtime_input.request_id or "").strip(),
        session_id=str(runtime_input.session_id or "").strip(),
        thread_id=runtime_input.thread_id,
        user_message=str(runtime_input.user_message or ""),
        retrieval_summary=dict(runtime_input.retrieval_summary or {}),
        retrieval_evidence_items=list(runtime_input.retrieval_evidence_items or []),
        style_bindings=resolved_style_bindings,
        emotion_context=dict(runtime_input.emotion_state or {}),
        emotion_snapshot=dict(runtime_input.emotion_snapshot or {}),
        render_target_lang=render_target_lang or None,
        cpb_slots=(merged_slots or None),
    )

    prompt_context = dict(generation_payload.get("prompt_context") or {})
    prompt_context_validation = dict(generation_payload.get("prompt_context_validation") or {})
    prompt_context_observability = dict(generation_payload.get("prompt_context_observability") or {})

    runtime_payload = runtime_input.to_runtime_payload(
        prompt_context=prompt_context,
        prompt_context_validation=prompt_context_validation,
        prompt_context_observability=prompt_context_observability,
    )
    runtime_payload["style_bindings"] = resolved_style_bindings
    try:
        from host_brain.app.intelligent_runtime import build_prompt_intelligence  # noqa: PLC0415

        runtime_payload.update(
            build_prompt_intelligence(
                request_id=str(runtime_input.request_id or "").strip(),
                runtime_input=runtime_input,
            )
        )
    except Exception as exc:
        logger.debug("prompt intelligence degraded: %s", exc)

    # ── FIX P0-瓶颈2：将情感子系统数据桥接到 runtime_payload 顶层 ──────────
    # prompt_builder.render_node_generation_prompt() 直接从 runtime_payload 顶层
    # 读取 emotion_vector/emotion_timeline/emotion_echo/inferred_emotion/
    # inner_monologue/user_emotion_profile 等字段。原实现中这些字段从未被写入
    # runtime_payload 顶层，导致 prompt 里对应区块永远为空。
    # 现在从 generation_payload["emotion_context"]（已在 P0-瓶颈1 中修复为完整字段）
    # 和全局单例中提取数据，注入 runtime_payload 顶层。
    _full_emotion_ctx = dict(generation_payload.get("emotion_context") or {})
    if _full_emotion_ctx.get("vad_vector"):
        runtime_payload["emotion_vector"] = _full_emotion_ctx["vad_vector"]
    if _full_emotion_ctx.get("emotion_echo"):
        runtime_payload["emotion_echo"] = _full_emotion_ctx["emotion_echo"]
    if _full_emotion_ctx.get("inner_monologue"):
        runtime_payload["inner_monologue"] = _full_emotion_ctx["inner_monologue"]
    if _full_emotion_ctx.get("user_emotion_profile"):
        runtime_payload["user_emotion_profile"] = _full_emotion_ctx["user_emotion_profile"]
    if _full_emotion_ctx.get("triggered_emotions"):
        runtime_payload["triggered_emotions"] = _full_emotion_ctx["triggered_emotions"]

    # emotion_timeline: 从全局单例获取最新摘要（因为 timeline 是 append-only，
    # 每次 normalize 都会追加，此时已经是最新状态）
    try:
        from host_brain.app.emotion_context import get_emotion_timeline  # noqa: PLC0415
        _tl = get_emotion_timeline()
        _tl_summary = _tl.rebuild_summary(window_ms=300_000)
        if _tl_summary and _tl_summary.get("entry_count", 0) > 0:
            runtime_payload["emotion_timeline"] = _tl_summary
    except Exception:
        pass  # 降级：不注入 timeline

    # ── P1-FIX：接入 PersonaExpressionAdapter ────────────────────────────
    # prompt_builder 通过 runtime_payload.get("persona_expression_hint") 读取
    try:
        from host_brain.persona.expression_adapter import PersonaExpressionAdapter  # noqa: PLC0415
        from host_brain.persona.personality_traits import PersonalityTraits  # noqa: PLC0415
        from host_brain.core.metabolism import EmotionVector  # noqa: PLC0415
        _adapter = PersonaExpressionAdapter()
        _traits = PersonalityTraits.default()
        # 获取当前 EmotionVector
        _vad = _full_emotion_ctx.get("vad_vector") or {}
        _emo_vec = EmotionVector(
            valence=float(_vad.get("valence") or 0.0),
            arousal=float(_vad.get("arousal") or 0.0),
            dominance=float(_vad.get("dominance") or 0.0),
            ts_ms=int(_vad.get("ts_ms") or 0),
        ) if _vad else None
        # 获取 bond_stage
        _bond_stage = "STRANGER"
        try:
            from host_brain.app.dependencies import get_bond_stage_engine  # noqa: PLC0415
            _bse = get_bond_stage_engine()
            if _bse is not None:
                _user_id = get_canonical_bond_user_id()
                _bond_state = _bse.get_state(_user_id)
                _bond_stage = _bond_state.current_stage
        except Exception:
            pass
        _user_tag = str(_full_emotion_ctx.get("emotion_tag") or "NEUTRAL").strip().upper()
        _hint = _adapter.adapt(_traits, _emo_vec, _bond_stage, user_emotion_tag=_user_tag)
        runtime_payload["persona_expression_hint"] = {
            "style_hint": _hint.style_hint,
            "tone": _hint.tone,
            "echo_weight_modifier": _hint.echo_weight_modifier,
            "follow_up_tendency": _hint.追问倾向,
        }
    except (ImportError, AttributeError, Exception) as _pe_exc:
        logger.debug("PersonaExpressionAdapter 接入降级: %s", _pe_exc)

    # ── P1-FIX：接入 SelfReflection ──────────────────────────────────────
    # prompt_builder 通过 runtime_payload.get("self_reflection_pulse") 读取
    try:
        from host_brain.persona.self_reflection import SelfReflection as _SelfReflection  # noqa: PLC0415
        if not hasattr(build_chat_prompt_runtime_payload, "_self_reflection"):
            build_chat_prompt_runtime_payload._self_reflection = _SelfReflection()
        _sr: _SelfReflection = build_chat_prompt_runtime_payload._self_reflection
        # 获取当前轮次（从 session 状态推断，或使用递增计数器）
        if not hasattr(build_chat_prompt_runtime_payload, "_turn_counter"):
            build_chat_prompt_runtime_payload._turn_counter = 0
        build_chat_prompt_runtime_payload._turn_counter += 1
        _turn = build_chat_prompt_runtime_payload._turn_counter
        _tl_summary_for_sr = runtime_payload.get("emotion_timeline") or {}
        _pulse = _sr.maybe_generate(
            turn_count=_turn,
            timeline_summary=_tl_summary_for_sr,
        )
        if _pulse:
            runtime_payload["self_reflection_pulse"] = _pulse
    except (ImportError, AttributeError, Exception) as _sr_exc:
        logger.debug("SelfReflection 接入降级: %s", _sr_exc)

    # ── P1-FIX：接入 BondNarrativeLayer ──────────────────────────────────
    # prompt_builder 通过 runtime_payload.get("bond_narrative") 读取
    try:
        from host_brain.bond.bond_narrative import BondNarrativeLayer  # noqa: PLC0415
        from host_brain.bond.shared_memory_index import SharedMemoryIndex  # noqa: PLC0415
        from host_brain.bond.milestone_tracker import MilestoneTracker  # noqa: PLC0415
        _user_id_bn = get_canonical_bond_user_id()
        # 获取 bond_stage（复用上面已计算的值如果可用）
        _bn_stage = runtime_payload.get("persona_expression_hint", {})
        _bn_bond_stage = "STRANGER"
        try:
            from host_brain.app.dependencies import get_bond_stage_engine  # noqa: PLC0415
            _bse_bn = get_bond_stage_engine()
            if _bse_bn is not None:
                _bn_bond_state = _bse_bn.get_state(_user_id_bn)
                _bn_bond_stage = _bn_bond_state.current_stage
        except Exception:
            pass
        # 尝试获取 SharedMemoryIndex 和 MilestoneTracker
        _smi = None
        _mt = None
        try:
            from host_brain.app.dependencies import get_shared_memory_index, get_milestone_tracker  # noqa: PLC0415
            _smi = get_shared_memory_index()
            _mt = get_milestone_tracker()
        except (ImportError, AttributeError):
            pass
        if _smi is not None or _mt is not None:
            _bnl = BondNarrativeLayer(memory_index=_smi, milestone_tracker=_mt)
            _bn_result = _bnl.build_narrative(user_id=_user_id_bn, bond_stage=_bn_bond_stage)
            _bn_slot = _bn_result.to_prompt_slot()
            if _bn_slot:
                runtime_payload["bond_narrative"] = _bn_slot
    except (ImportError, AttributeError, Exception) as _bn_exc:
        logger.debug("BondNarrativeLayer 接入降级: %s", _bn_exc)

    return runtime_payload


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
