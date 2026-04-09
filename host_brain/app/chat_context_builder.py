"""Chat 上下文构建辅助函数。

从 chat_handler.py 拆分，负责构建 context pack、style bindings 和 generation prompt payload。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

from host_brain.app.dependencies import get_ledger
from host_brain.app.graph_config_builder import CHAT_DIRECT_NODE_ID
from host_brain.app.prompt_builder import _truncate_for_prompt, infer_render_target_lang
from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged
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

_LOW_SIGNAL_PERSONA_INTENTS: frozenset[str] = frozenset({"phatic", "affection"})
_SHORT_CHAT_PERSONA_SCORE_FLOOR = 0.12

_RUNTIME_GUIDANCE_TEMPLATES_DEFAULT: dict[str, Any] = {}
_LATEST_PROMPT_CONTEXT_SNAPSHOT_LOCK = threading.Lock()
_LATEST_PROMPT_CONTEXT_SNAPSHOT: dict[str, Any] = {}


def _record_latest_prompt_context_snapshot(snapshot: Mapping[str, Any] | None) -> None:
    if not isinstance(snapshot, Mapping):
        return
    with _LATEST_PROMPT_CONTEXT_SNAPSHOT_LOCK:
        _LATEST_PROMPT_CONTEXT_SNAPSHOT.clear()
        _LATEST_PROMPT_CONTEXT_SNAPSHOT.update(copy.deepcopy(dict(snapshot)))


def get_latest_prompt_context_runtime_snapshot() -> dict[str, Any]:
    with _LATEST_PROMPT_CONTEXT_SNAPSHOT_LOCK:
        return copy.deepcopy(_LATEST_PROMPT_CONTEXT_SNAPSHOT)


def _load_runtime_guidance_templates() -> dict[str, Any]:
    try:
        raw = resolve_prompt_template_json_merged("chat_context_builder.runtime_guidance_texts")
    except Exception:
        return {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def _template_text(
    template_data: Mapping[str, Any] | None,
    *keys: str,
    default: str = "",
    **kwargs: Any,
) -> str:
    current: Any = template_data
    for key in keys:
        if not isinstance(current, Mapping):
            current = None
            break
        current = current.get(key)
    text = str(current or default)
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except Exception:
        return text


def _resolve_offline_reconnect_profile(offline_state: Mapping[str, Any] | None) -> dict[str, str]:
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        payload = get_limbic_agent().resolve_offline_reconnect_profile(
            offline_state=dict(offline_state or {}),
        )
        if isinstance(payload, Mapping):
            absence_band = str(payload.get("absence_band") or "recent").strip() or "recent"
            reconnect_style = str(payload.get("reconnect_style") or "").strip()
            return {
                "absence_band": absence_band,
                "reconnect_style": reconnect_style,
            }
    except Exception:
        pass
    return {
        "absence_band": "recent",
        "reconnect_style": "soft_resume",
    }


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
            "top_agent": v5_entry,  # Phase XIV v4：顶级 Agent 使用同一份风格指导
            "core_reasoner_primary": v5_entry,
            "core_reasoner_secondary": v5_entry,
            CHAT_DIRECT_NODE_ID: v5_entry,
            "renderer_ja_c_style": v5_entry,
        }

    # ── Canonical v5 source（host_brain/canon/style/style_profile_v5.json）──
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

    raw_v5_path = str(
        os.environ.get("NATSUME_STYLE_V5_PATH")
        or os.environ.get("STYLE_V5_PATH")
        or os.environ.get("STYLE_V5_ROOT")
        or "host_brain/canon/style/style_profile_v5.json"
    ).strip()
    v5_path = Path(raw_v5_path)
    if v5_path.suffix.lower() != ".json":
        v5_path = v5_path / "style_profile_v5.json"

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
    bond_summary: Mapping[str, Any] | None = None,
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
    from host_brain.bond.summarizer import normalize_bond_summary  # noqa: PLC0415
    normalized_style_bindings = normalize_node_style_bindings(style_bindings)
    normalized_emotion_context = normalize_chat_emotion_context(emotion_context)
    resolved_bond_summary = (
        normalize_bond_summary(dict(bond_summary))
        if isinstance(bond_summary, Mapping) and bond_summary
        else {}
    )

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

    retrieval_rows = _build_retrieval_rows(
        retrieval_summary=retrieval_summary,
        retrieval_evidence_items=retrieval_evidence_items,
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
    memory_persona_signal = _build_memory_persona_signal(retrieval_evidence_items)
    memory_persona_evidence = _build_memory_persona_evidence(retrieval_rows)
    memory_recall_summary = ""
    if memory_persona_signal:
        memory_emotion_hint.update(memory_persona_signal)
        memory_recall_summary = _build_memory_recall_summary(memory_emotion_hint)
        if memory_recall_summary:
            memory_emotion_hint["memory_recall_summary"] = memory_recall_summary
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
    runtime_guidance_templates = _load_runtime_guidance_templates()
    # Phase 4: 注入 BondEmotionalTexture 亲密温度和裂痕状态（让关系有温度）
    try:
        from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415
        _user_id = get_canonical_bond_user_id()
        _social_snapshot = get_social_brain_agent().resolve_bond_snapshot(
            person_id=_user_id,
            bond_summary=resolved_bond_summary,
        )
        _bond_state = _social_snapshot.get("bond_state_obj")
        if _bond_state is not None:
            if not resolved_bond_summary:
                resolved_bond_summary = dict(_social_snapshot.get("bond_summary") or {})
            bond_texture["bond_stage"] = str(_social_snapshot.get("bond_stage") or "")
            bond_texture["bond_trend"] = str(_social_snapshot.get("bond_trend") or "")
            # 亲密温度
            _texture = _bond_state.texture
            bond_texture["intimacy_temperature"] = round(_texture.intimacy_temperature, 3)
            # 亲密温度 → 自然语言描述
            if _texture.intimacy_temperature > 0.8:
                bond_texture["texture_hint"] = _template_text(
                    runtime_guidance_templates,
                    "bond_texture",
                    "temperature_hints",
                    "gt_0_8",
                )
            elif _texture.intimacy_temperature > 0.6:
                bond_texture["texture_hint"] = _template_text(
                    runtime_guidance_templates,
                    "bond_texture",
                    "temperature_hints",
                    "gt_0_6",
                )
            elif _texture.intimacy_temperature > 0.4:
                bond_texture["texture_hint"] = _template_text(
                    runtime_guidance_templates,
                    "bond_texture",
                    "temperature_hints",
                    "gt_0_4",
                )
            elif _texture.intimacy_temperature > 0.2:
                bond_texture["texture_hint"] = _template_text(
                    runtime_guidance_templates,
                    "bond_texture",
                    "temperature_hints",
                    "gt_0_2",
                )
            else:
                bond_texture["texture_hint"] = _template_text(
                    runtime_guidance_templates,
                    "bond_texture",
                    "temperature_hints",
                    "default",
                )
            # 裂痕状态
            _rift = _bond_state.rift
            if _rift is not None and _rift.is_active():
                bond_texture["rift_active"] = True
                bond_texture["rift_cause"] = _rift.rift_cause
                bond_texture["rift_repair_progress"] = round(_rift.repair_progress, 2)
                if _rift.repair_progress > 0.7:
                    bond_texture["rift_hint"] = _template_text(
                        runtime_guidance_templates,
                        "bond_texture",
                        "rift_hints",
                        "repair_high",
                    )
                elif _rift.repair_progress > 0.3:
                    bond_texture["rift_hint"] = _template_text(
                        runtime_guidance_templates,
                        "bond_texture",
                        "rift_hints",
                        "repair_mid",
                    )
                else:
                    bond_texture["rift_hint"] = _template_text(
                        runtime_guidance_templates,
                        "bond_texture",
                        "rift_hints",
                        "repair_low_template",
                        rift_cause=_rift.rift_cause,
                    )
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
        "bond_summary": resolved_bond_summary,
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
    if memory_persona_evidence:
        context_pack["memory_persona_evidence"] = memory_persona_evidence
    if memory_recall_summary:
        context_pack["memory_recall_summary"] = memory_recall_summary

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


def _coerce_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_float(value: float, *, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


def _build_runtime_mode_flags(prompt_context: Mapping[str, Any] | None) -> dict[str, Any]:
    context = dict(prompt_context or {}) if isinstance(prompt_context, Mapping) else {}
    hard_snapshot_raw = context.get("hard_snapshot")
    hard_snapshot = dict(hard_snapshot_raw) if isinstance(hard_snapshot_raw, Mapping) else {}

    sys_mode = str(
        hard_snapshot.get("sys_mode")
        or os.environ.get("HOST_SYS_MODE")
        or "normal"
    ).strip().lower() or "normal"

    runtime_flags: dict[str, Any] = {"sys_mode": sys_mode}
    try:
        from host_brain.app import dependencies as _deps  # noqa: PLC0415

        monitor = getattr(_deps, "_activity_monitor_ref", None)
        if monitor is not None and hasattr(monitor, "get_status"):
            activity_status = monitor.get_status()
            if isinstance(activity_status, Mapping):
                runtime_flags["activity_monitor"] = dict(activity_status)
    except Exception:
        pass

    return runtime_flags


def _coerce_optional_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _build_memory_persona_signal(retrieval_evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    warm_tags = {"NOSTALGIC", "WARM", "GRATEFUL", "LOVING", "TOUCHED", "HAPPY"}
    fragile_tags = {"SAD", "HURT", "LONELY", "ANXIOUS", "SCARED", "DEPRESSED", "MELANCHOLY"}

    humanized_count = 0
    suppressed_count = 0
    distorted_count = 0
    distortion_sum = 0.0
    shared_history_resonance_count = 0
    protective_recoil_count = 0
    emotion_counts: dict[str, int] = {}

    for row in list(retrieval_evidence_items or [])[:12]:
        if not isinstance(row, Mapping):
            continue
        metadata_raw = row.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
        if not metadata:
            continue

        emotion_color_tag = str(
            metadata.get("emotion_color_tag") or metadata.get("emotion_tag") or ""
        ).strip().upper()
        suppression_flag = _coerce_optional_bool(metadata.get("suppression_flag")) is True
        distortion_level = float(_coerce_optional_float(metadata.get("distortion_level")) or 0.0)
        distortion_level = _clamp_float(distortion_level, minimum=0.0, maximum=1.0)
        reason_tags = {
            str(tag).strip().lower()
            for tag in (metadata.get("persona_recall_reason_tags") or [])
            if str(tag).strip()
        }
        has_resonance = "shared_history_resonance" in reason_tags
        has_protective_recoil = "protective_recoil_penalty" in reason_tags

        if not emotion_color_tag and not suppression_flag and distortion_level <= 0.0 and not has_resonance and not has_protective_recoil:
            continue

        humanized_count += 1
        if emotion_color_tag:
            emotion_counts[emotion_color_tag] = emotion_counts.get(emotion_color_tag, 0) + 1
        if suppression_flag:
            suppressed_count += 1
        if distortion_level > 0.0:
            distorted_count += 1
            distortion_sum += distortion_level
        if has_resonance:
            shared_history_resonance_count += 1
        if has_protective_recoil:
            protective_recoil_count += 1

    if humanized_count <= 0:
        return {}

    dominant_emotion = ""
    if emotion_counts:
        dominant_emotion = max(emotion_counts.items(), key=lambda item: (item[1], item[0]))[0]

    avg_distortion = distortion_sum / max(distorted_count, 1) if distorted_count > 0 else 0.0
    guidance_parts: list[str] = []
    recall_bias = "neutral"
    runtime_guidance_templates = _load_runtime_guidance_templates()

    if suppressed_count > 0:
        recall_bias = "guarded"
        guidance_parts.append(
            _template_text(
                runtime_guidance_templates,
                "memory_signal_guidance",
                "suppressed",
            )
        )

    if avg_distortion >= 0.35:
        recall_bias = "uncertain"
        guidance_parts.append(
            _template_text(
                runtime_guidance_templates,
                "memory_signal_guidance",
                "distortion_high",
            )
        )
    elif distorted_count > 0:
        guidance_parts.append(
            _template_text(
                runtime_guidance_templates,
                "memory_signal_guidance",
                "distortion_low",
            )
        )

    if protective_recoil_count > 0:
        if recall_bias == "neutral":
            recall_bias = "guarded"
        guidance_parts.append(
            _template_text(
                runtime_guidance_templates,
                "memory_signal_guidance",
                "protective_recoil",
            )
        )

    if (
        shared_history_resonance_count > 0
        and protective_recoil_count <= 0
        and suppressed_count <= 0
        and avg_distortion < 0.35
    ):
        if recall_bias == "neutral":
            recall_bias = "warm"
        guidance_parts.append(
            _template_text(
                runtime_guidance_templates,
                "memory_signal_guidance",
                "shared_history",
            )
        )

    if dominant_emotion in warm_tags and suppressed_count <= 0:
        if recall_bias == "neutral":
            recall_bias = "warm"
        guidance_parts.append(
            _template_text(
                runtime_guidance_templates,
                "memory_signal_guidance",
                "warm_emotion",
            )
        )
    elif dominant_emotion in fragile_tags:
        if recall_bias == "neutral":
            recall_bias = "fragile"
        guidance_parts.append(
            _template_text(
                runtime_guidance_templates,
                "memory_signal_guidance",
                "fragile_emotion",
            )
        )

    guidance_text = "；".join(dict.fromkeys(part for part in guidance_parts if part))
    return {
        "humanized_memory_count": humanized_count,
        "suppressed_memory_count": suppressed_count,
        "distorted_memory_count": distorted_count,
        "avg_distortion_level": round(avg_distortion, 4),
        "dominant_memory_emotion": dominant_emotion or None,
        "recall_bias": recall_bias,
        "shared_history_resonance_count": shared_history_resonance_count,
        "protective_recoil_count": protective_recoil_count,
        "memory_persona_guidance": guidance_text or None,
    }


def _build_memory_persona_evidence(retrieval_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence_rows: list[dict[str, Any]] = []
    for row in list(retrieval_rows or [])[:8]:
        if not isinstance(row, Mapping):
            continue
        emotion_color_tag = str(row.get("emotion_color_tag") or "").strip().upper()
        suppression_flag = _coerce_optional_bool(row.get("suppression_flag")) is True
        distortion_level = _coerce_optional_float(row.get("distortion_level"))
        if distortion_level is not None:
            distortion_level = round(_clamp_float(float(distortion_level), minimum=0.0, maximum=1.0), 4)
        if not emotion_color_tag and not suppression_flag and (distortion_level is None or distortion_level <= 0.0):
            continue

        content = _truncate_for_prompt(row.get("content"), limit=160)
        if not content:
            continue

        evidence_rows.append(
            {
                "content": content,
                "emotion_color_tag": emotion_color_tag or None,
                "suppression_flag": suppression_flag,
                "distortion_level": distortion_level,
                "source_ref": str(row.get("source_ref") or "").strip() or None,
                "evidence_ref": str(row.get("evidence_ref") or "").strip() or None,
                "kind": str(row.get("kind") or "episodic").strip() or "episodic",
            }
        )
        if len(evidence_rows) >= 4:
            break
    return evidence_rows


def _build_memory_recall_summary(memory_signal_raw: Mapping[str, Any] | None) -> str:
    memory_signal = dict(memory_signal_raw or {}) if isinstance(memory_signal_raw, Mapping) else {}
    if not memory_signal:
        return ""

    humanized_count = _coerce_non_negative_int(memory_signal.get("humanized_memory_count"))
    suppressed_count = _coerce_non_negative_int(memory_signal.get("suppressed_memory_count"))
    distorted_count = _coerce_non_negative_int(memory_signal.get("distorted_memory_count"))
    avg_distortion = float(_coerce_optional_float(memory_signal.get("avg_distortion_level")) or 0.0)
    dominant_emotion = str(memory_signal.get("dominant_memory_emotion") or "").strip().upper()
    recall_bias = str(memory_signal.get("recall_bias") or "").strip().lower()
    shared_history_resonance_count = _coerce_non_negative_int(memory_signal.get("shared_history_resonance_count"))
    protective_recoil_count = _coerce_non_negative_int(memory_signal.get("protective_recoil_count"))
    guidance = str(memory_signal.get("memory_persona_guidance") or "").strip()

    if humanized_count <= 0 and not guidance and not dominant_emotion:
        return ""

    bias_labels = {
        "warm": "偏温暖承接",
        "guarded": "偏克制试探",
        "uncertain": "偏留白保守",
        "fragile": "偏脆弱照看",
        "neutral": "偏中性回忆",
    }

    parts: list[str] = []
    if humanized_count > 0:
        parts.append(f"本次召回命中了 {humanized_count} 条带人格化标记的记忆")
    if dominant_emotion:
        parts.append(f"主导回忆情绪偏向 {dominant_emotion}")
    if shared_history_resonance_count > 0:
        parts.append(f"{shared_history_resonance_count} 条带共享记忆共振")
    if protective_recoil_count > 0:
        parts.append(f"{protective_recoil_count} 条带防御性回缩")
    if suppressed_count > 0:
        parts.append(f"其中 {suppressed_count} 条处于压制状态")
    if distorted_count > 0:
        parts.append(f"{distorted_count} 条带有细节失真，平均失真 {avg_distortion:.2f}")
    if recall_bias:
        parts.append(f"当前回忆倾向 {bias_labels.get(recall_bias, recall_bias)}")
    if guidance:
        parts.append(f"处理建议：{guidance}")
    return "；".join(part for part in parts if part)


def _apply_motive_state_to_persona_expression_hint(
    hint_raw: Mapping[str, Any] | None,
    motive_state_raw: Mapping[str, Any] | None,
) -> dict[str, Any]:
    hint = dict(hint_raw or {}) if isinstance(hint_raw, Mapping) else {}
    motive_state = dict(motive_state_raw or {}) if isinstance(motive_state_raw, Mapping) else {}
    if not hint or not motive_state:
        return hint

    def _score(key: str) -> float:
        value = _coerce_optional_float(motive_state.get(key))
        return float(value or 0.0)

    care_drive = _score("care_drive")
    reach_out_drive = _score("reach_out_drive")
    continuity_drive = _score("continuity_drive")
    withdraw_drive = _score("withdraw_drive")
    self_protect_drive = _score("self_protect_drive")

    active_drive = max(care_drive, reach_out_drive, continuity_drive)
    retreat_drive = max(withdraw_drive, self_protect_drive)

    tone = str(hint.get("tone") or "gentle").strip() or "gentle"
    style_hint = str(hint.get("style_hint") or "").strip()
    follow_up_tendency = _coerce_optional_float(hint.get("follow_up_tendency")) or 0.0
    echo_weight_modifier = _coerce_optional_float(hint.get("echo_weight_modifier")) or 0.0
    pace_hint = str(hint.get("pace_hint") or "").strip()
    motive_guidance: list[str] = []
    runtime_guidance_templates = _load_runtime_guidance_templates()

    if care_drive >= 0.62 and care_drive >= retreat_drive:
        tone = "caring"
        follow_up_tendency = max(follow_up_tendency, 0.08)
        echo_weight_modifier = max(echo_weight_modifier, 0.08)
        if not pace_hint:
            pace_hint = "steady"
        motive_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "motive_guidance",
                "care",
            )
        )

    if continuity_drive >= 0.68 and continuity_drive >= retreat_drive:
        follow_up_tendency = max(follow_up_tendency, 0.12)
        motive_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "motive_guidance",
                "continuity",
            )
        )

    if retreat_drive >= 0.72 and retreat_drive >= active_drive + 0.1:
        tone = "gentle"
        follow_up_tendency = min(follow_up_tendency, 0.0)
        echo_weight_modifier = min(echo_weight_modifier, 0.02)
        if not pace_hint:
            pace_hint = "slow"
        motive_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "motive_guidance",
                "retreat",
            )
        )

    if motive_guidance:
        guidance_text = "；".join(dict.fromkeys(motive_guidance))
        style_hint = f"{style_hint}；{guidance_text}" if style_hint else guidance_text
        hint["motive_guidance"] = guidance_text

    hint["tone"] = tone
    hint["style_hint"] = _truncate_for_prompt(style_hint, limit=240) or style_hint
    hint["follow_up_tendency"] = round(_clamp_float(follow_up_tendency, minimum=-0.3, maximum=0.3), 4)
    hint["echo_weight_modifier"] = round(_clamp_float(echo_weight_modifier, minimum=-0.3, maximum=0.3), 4)
    if pace_hint:
        hint["pace_hint"] = pace_hint
    return hint


def _apply_offline_state_to_persona_expression_hint(
    hint_raw: Mapping[str, Any] | None,
    offline_state_raw: Mapping[str, Any] | None,
) -> dict[str, Any]:
    hint = dict(hint_raw or {}) if isinstance(hint_raw, Mapping) else {}
    offline_state = dict(offline_state_raw or {}) if isinstance(offline_state_raw, Mapping) else {}
    if not hint or not offline_state:
        return hint

    reconnect_profile = _resolve_offline_reconnect_profile(offline_state)
    absence_band = str(reconnect_profile.get("absence_band") or "recent").strip() or "recent"
    if absence_band == "recent":
        return hint

    loneliness = _coerce_optional_float(offline_state.get("loneliness_level")) or 0.0
    anticipation = _coerce_optional_float(offline_state.get("anticipation_level")) or 0.0

    tone = str(hint.get("tone") or "gentle").strip() or "gentle"
    style_hint = str(hint.get("style_hint") or "").strip()
    follow_up_tendency = _coerce_optional_float(hint.get("follow_up_tendency")) or 0.0
    echo_weight_modifier = _coerce_optional_float(hint.get("echo_weight_modifier")) or 0.0
    pace_hint = str(hint.get("pace_hint") or "").strip()
    offline_guidance: list[str] = []
    reconnect_style = str(reconnect_profile.get("reconnect_style") or "").strip()
    runtime_guidance_templates = _load_runtime_guidance_templates()

    if absence_band == "6h_plus":
        follow_up_tendency = max(follow_up_tendency, 0.04)
        echo_weight_modifier = max(echo_weight_modifier, 0.04)
        if not pace_hint:
            pace_hint = "steady"
        offline_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "offline_guidance",
                "6h_plus",
            )
        )
    elif absence_band == "24h_plus":
        follow_up_tendency = max(follow_up_tendency, 0.08)
        echo_weight_modifier = max(echo_weight_modifier, 0.06)
        if max(loneliness, anticipation) >= 0.35:
            tone = "caring"
        if not pace_hint:
            pace_hint = "steady"
        offline_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "offline_guidance",
                "24h_plus",
            )
        )
    elif absence_band == "72h_plus":
        tone = "caring"
        follow_up_tendency = max(follow_up_tendency, 0.12)
        echo_weight_modifier = max(echo_weight_modifier, 0.10)
        if not pace_hint:
            pace_hint = "slow"
        offline_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "offline_guidance",
                "72h_plus",
            )
        )
    elif absence_band == "7d_plus":
        tone = "caring"
        follow_up_tendency = max(follow_up_tendency, 0.18)
        echo_weight_modifier = max(echo_weight_modifier, 0.14)
        if not pace_hint:
            pace_hint = "slow"
        offline_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "offline_guidance",
                "7d_plus",
            )
        )

    if loneliness >= 0.55:
        echo_weight_modifier = max(echo_weight_modifier, 0.08)
        offline_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "offline_guidance",
                "loneliness",
            )
        )
    elif anticipation >= 0.6 and absence_band in {"24h_plus", "72h_plus", "7d_plus"}:
        offline_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "offline_guidance",
                "anticipation",
            )
        )

    if offline_guidance:
        guidance_text = "；".join(dict.fromkeys(part for part in offline_guidance if part))
        style_hint = f"{style_hint}；{guidance_text}" if style_hint else guidance_text
        hint["offline_guidance"] = guidance_text
    if reconnect_style:
        hint["offline_reconnect_style"] = reconnect_style

    hint["tone"] = tone
    hint["style_hint"] = _truncate_for_prompt(style_hint, limit=240) or style_hint
    hint["follow_up_tendency"] = round(_clamp_float(follow_up_tendency, minimum=-0.3, maximum=0.3), 4)
    hint["echo_weight_modifier"] = round(_clamp_float(echo_weight_modifier, minimum=-0.3, maximum=0.3), 4)
    if pace_hint:
        hint["pace_hint"] = pace_hint
    return hint


def _apply_activity_monitor_to_persona_expression_hint(
    hint_raw: Mapping[str, Any] | None,
    activity_monitor_raw: Mapping[str, Any] | None,
) -> dict[str, Any]:
    hint = dict(hint_raw or {}) if isinstance(hint_raw, Mapping) else {}
    activity_monitor = dict(activity_monitor_raw or {}) if isinstance(activity_monitor_raw, Mapping) else {}
    if not hint or not activity_monitor:
        return hint

    tone = str(hint.get("tone") or "gentle").strip() or "gentle"
    style_hint = str(hint.get("style_hint") or "").strip()
    follow_up_tendency = _coerce_optional_float(hint.get("follow_up_tendency")) or 0.0
    echo_weight_modifier = _coerce_optional_float(hint.get("echo_weight_modifier")) or 0.0
    pace_hint = str(hint.get("pace_hint") or "").strip()
    activity_guidance: list[str] = []
    runtime_guidance_templates = _load_runtime_guidance_templates()

    recent_event_type = str(activity_monitor.get("recent_event_type") or "").strip().lower()
    try:
        recent_event_age_s = float(activity_monitor.get("recent_event_age_s"))
    except (TypeError, ValueError):
        recent_event_age_s = float("inf")
    try:
        idle_minutes = float(activity_monitor.get("idle_minutes") or 0.0)
    except (TypeError, ValueError):
        idle_minutes = 0.0
    is_gaming = bool(activity_monitor.get("is_gaming"))

    if recent_event_type == "wakeup" and recent_event_age_s <= 600.0:
        tone = "gentle"
        follow_up_tendency = min(follow_up_tendency, 0.02)
        echo_weight_modifier = min(echo_weight_modifier, 0.03)
        pace_hint = "slow"
        activity_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "activity_guidance",
                "wakeup",
            )
        )
    elif (is_gaming or recent_event_type == "gaming_detected") and recent_event_age_s <= 900.0:
        if tone == "gentle":
            tone = "playful"
        follow_up_tendency = max(follow_up_tendency, 0.05)
        echo_weight_modifier = max(echo_weight_modifier, 0.04)
        if not pace_hint:
            pace_hint = "brisk"
        activity_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "activity_guidance",
                "gaming_detected",
            )
        )
    elif recent_event_type == "gaming_exited" and recent_event_age_s <= 600.0:
        tone = "caring" if tone == "gentle" else tone
        follow_up_tendency = min(max(follow_up_tendency, 0.03), 0.08)
        echo_weight_modifier = max(echo_weight_modifier, 0.05)
        if not pace_hint:
            pace_hint = "steady"
        activity_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "activity_guidance",
                "gaming_exited",
            )
        )
    elif idle_minutes >= 20.0:
        if tone == "playful":
            tone = "gentle"
        follow_up_tendency = min(max(follow_up_tendency, 0.02), 0.06)
        echo_weight_modifier = max(echo_weight_modifier, 0.04)
        if not pace_hint:
            pace_hint = "steady"
        activity_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "activity_guidance",
                "idle_reentry",
            )
        )

    if activity_guidance:
        guidance_text = "；".join(dict.fromkeys(part for part in activity_guidance if part))
        style_hint = f"{style_hint}；{guidance_text}" if style_hint else guidance_text
        hint["activity_guidance"] = guidance_text
        if recent_event_type:
            hint["activity_event_type"] = recent_event_type

    hint["tone"] = tone
    hint["style_hint"] = _truncate_for_prompt(style_hint, limit=240) or style_hint
    hint["follow_up_tendency"] = round(_clamp_float(follow_up_tendency, minimum=-0.3, maximum=0.3), 4)
    hint["echo_weight_modifier"] = round(_clamp_float(echo_weight_modifier, minimum=-0.3, maximum=0.3), 4)
    if pace_hint:
        hint["pace_hint"] = pace_hint
    return hint


def _apply_scene_awareness_to_persona_expression_hint(
    hint_raw: Mapping[str, Any] | None,
    scene_awareness_raw: Mapping[str, Any] | None,
) -> dict[str, Any]:
    hint = dict(hint_raw or {}) if isinstance(hint_raw, Mapping) else {}
    scene_awareness = dict(scene_awareness_raw or {}) if isinstance(scene_awareness_raw, Mapping) else {}
    if not hint or not scene_awareness:
        return hint

    raw_tags = scene_awareness.get("scene_tags") or []
    scene_tags = {
        str(tag or "").strip().lower().replace("-", "_").replace(" ", "_")
        for tag in raw_tags
        if str(tag or "").strip()
    }
    current_activity = str(scene_awareness.get("current_activity") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if current_activity:
        scene_tags.add(current_activity)
    companion_suitable = scene_awareness.get("companion_suitable")

    if not scene_tags and companion_suitable is None:
        return hint

    tone = str(hint.get("tone") or "gentle").strip() or "gentle"
    style_hint = str(hint.get("style_hint") or "").strip()
    follow_up_tendency = _coerce_optional_float(hint.get("follow_up_tendency")) or 0.0
    echo_weight_modifier = _coerce_optional_float(hint.get("echo_weight_modifier")) or 0.0
    pace_hint = str(hint.get("pace_hint") or "").strip()
    scene_guidance: list[str] = []
    runtime_guidance_templates = _load_runtime_guidance_templates()

    if companion_suitable is False or scene_tags & {"focus_work", "fullscreen_video", "sleeping_screen", "meeting"}:
        tone = "gentle"
        follow_up_tendency = min(follow_up_tendency, 0.01)
        echo_weight_modifier = min(echo_weight_modifier, 0.03)
        if not pace_hint:
            pace_hint = "steady"
        scene_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "scene_guidance",
                "focus",
            )
        )
    elif scene_tags & {"gaming", "gameplay", "battle", "lobby"}:
        if tone == "gentle":
            tone = "playful"
        follow_up_tendency = max(follow_up_tendency, 0.04)
        echo_weight_modifier = max(echo_weight_modifier, 0.03)
        if not pace_hint:
            pace_hint = "brisk"
        scene_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "scene_guidance",
                "gaming",
            )
        )

    if scene_guidance:
        guidance_text = "；".join(dict.fromkeys(part for part in scene_guidance if part))
        style_hint = f"{style_hint}；{guidance_text}" if style_hint else guidance_text
        hint["scene_guidance"] = guidance_text
        if scene_tags:
            hint["scene_tags"] = sorted(scene_tags)

    hint["tone"] = tone
    hint["style_hint"] = _truncate_for_prompt(style_hint, limit=240) or style_hint
    hint["follow_up_tendency"] = round(_clamp_float(follow_up_tendency, minimum=-0.3, maximum=0.3), 4)
    hint["echo_weight_modifier"] = round(_clamp_float(echo_weight_modifier, minimum=-0.3, maximum=0.3), 4)
    if pace_hint:
        hint["pace_hint"] = pace_hint
    return hint


def _apply_runtime_mode_to_persona_expression_hint(
    hint_raw: Mapping[str, Any] | None,
    runtime_flags_raw: Mapping[str, Any] | None,
) -> dict[str, Any]:
    hint = dict(hint_raw or {}) if isinstance(hint_raw, Mapping) else {}
    runtime_flags = dict(runtime_flags_raw or {}) if isinstance(runtime_flags_raw, Mapping) else {}
    if not hint or not runtime_flags:
        return hint

    activity_monitor_raw = runtime_flags.get("activity_monitor")
    activity_monitor = dict(activity_monitor_raw) if isinstance(activity_monitor_raw, Mapping) else {}
    sys_mode = str(runtime_flags.get("sys_mode") or "").strip().lower()
    if sys_mode not in {"normal", "focus", "gaming", "sleep", "maintenance", "locked"}:
        sys_mode = "gaming" if bool(activity_monitor.get("is_gaming")) else "normal"
    if sys_mode == "normal":
        return hint

    tone = str(hint.get("tone") or "gentle").strip() or "gentle"
    style_hint = str(hint.get("style_hint") or "").strip()
    follow_up_tendency = _coerce_optional_float(hint.get("follow_up_tendency")) or 0.0
    echo_weight_modifier = _coerce_optional_float(hint.get("echo_weight_modifier")) or 0.0
    pace_hint = str(hint.get("pace_hint") or "").strip()
    mode_guidance: list[str] = []
    runtime_guidance_templates = _load_runtime_guidance_templates()

    if sys_mode == "focus":
        if tone == "playful":
            tone = "gentle"
        follow_up_tendency = min(follow_up_tendency, 0.02)
        echo_weight_modifier = min(echo_weight_modifier, 0.04)
        if not pace_hint:
            pace_hint = "steady"
        mode_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "mode_guidance",
                "focus",
            )
        )
    elif sys_mode == "gaming":
        if tone == "gentle":
            tone = "playful"
        follow_up_tendency = max(follow_up_tendency, 0.06)
        echo_weight_modifier = max(echo_weight_modifier, 0.04)
        if not pace_hint:
            pace_hint = "brisk"
        mode_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "mode_guidance",
                "gaming",
            )
        )
    elif sys_mode == "sleep":
        tone = "gentle"
        follow_up_tendency = min(follow_up_tendency, 0.0)
        echo_weight_modifier = min(echo_weight_modifier, 0.02)
        pace_hint = "slow"
        mode_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "mode_guidance",
                "sleep",
            )
        )
    elif sys_mode in {"maintenance", "locked"}:
        tone = "gentle"
        follow_up_tendency = min(follow_up_tendency, 0.0)
        echo_weight_modifier = min(echo_weight_modifier, 0.02)
        if not pace_hint:
            pace_hint = "steady"
        mode_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "mode_guidance",
                "minimal",
            )
        )

    if mode_guidance:
        guidance_text = "；".join(dict.fromkeys(part for part in mode_guidance if part))
        style_hint = f"{style_hint}；{guidance_text}" if style_hint else guidance_text
        hint["mode_guidance"] = guidance_text
        hint["sys_mode"] = sys_mode

    hint["tone"] = tone
    hint["style_hint"] = _truncate_for_prompt(style_hint, limit=240) or style_hint
    hint["follow_up_tendency"] = round(_clamp_float(follow_up_tendency, minimum=-0.3, maximum=0.3), 4)
    hint["echo_weight_modifier"] = round(_clamp_float(echo_weight_modifier, minimum=-0.3, maximum=0.3), 4)
    if pace_hint:
        hint["pace_hint"] = pace_hint
    return hint


def _apply_memory_persona_signal_to_persona_expression_hint(
    hint_raw: Mapping[str, Any] | None,
    memory_signal_raw: Mapping[str, Any] | None,
) -> dict[str, Any]:
    hint = dict(hint_raw or {}) if isinstance(hint_raw, Mapping) else {}
    memory_signal = dict(memory_signal_raw or {}) if isinstance(memory_signal_raw, Mapping) else {}
    if not hint or not memory_signal:
        return hint

    tone = str(hint.get("tone") or "gentle").strip() or "gentle"
    style_hint = str(hint.get("style_hint") or "").strip()
    follow_up_tendency = _coerce_optional_float(hint.get("follow_up_tendency")) or 0.0
    echo_weight_modifier = _coerce_optional_float(hint.get("echo_weight_modifier")) or 0.0
    pace_hint = str(hint.get("pace_hint") or "").strip()

    suppressed_count = int(memory_signal.get("suppressed_memory_count") or 0)
    distorted_count = int(memory_signal.get("distorted_memory_count") or 0)
    avg_distortion = float(_coerce_optional_float(memory_signal.get("avg_distortion_level")) or 0.0)
    dominant_emotion = str(memory_signal.get("dominant_memory_emotion") or "").strip().upper()
    recall_bias = str(memory_signal.get("recall_bias") or "").strip().lower()
    shared_history_resonance_count = int(memory_signal.get("shared_history_resonance_count") or 0)
    protective_recoil_count = int(memory_signal.get("protective_recoil_count") or 0)

    warm_tags = {"NOSTALGIC", "WARM", "GRATEFUL", "LOVING", "TOUCHED", "HAPPY"}
    fragile_tags = {"SAD", "HURT", "LONELY", "ANXIOUS", "SCARED", "DEPRESSED", "MELANCHOLY"}
    memory_guidance: list[str] = []
    runtime_guidance_templates = _load_runtime_guidance_templates()

    if suppressed_count > 0 or recall_bias == "guarded":
        follow_up_tendency = min(follow_up_tendency, 0.03)
        echo_weight_modifier = min(echo_weight_modifier, 0.05)
        if not pace_hint:
            pace_hint = "steady"
        memory_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "memory_hint_guidance",
                "suppressed",
            )
        )

    if avg_distortion >= 0.35 or recall_bias == "uncertain":
        follow_up_tendency = min(follow_up_tendency, 0.04)
        memory_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "memory_hint_guidance",
                "uncertain",
            )
        )
    elif distorted_count > 0:
        memory_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "memory_hint_guidance",
                "distorted",
            )
        )

    if protective_recoil_count > 0:
        follow_up_tendency = min(follow_up_tendency, 0.02)
        echo_weight_modifier = min(echo_weight_modifier, 0.04)
        if not pace_hint:
            pace_hint = "steady"
        memory_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "memory_hint_guidance",
                "protective_recoil",
            )
        )

    if dominant_emotion in warm_tags and suppressed_count <= 0 and avg_distortion < 0.35:
        tone = "caring" if tone == "gentle" else tone
        follow_up_tendency = max(follow_up_tendency, 0.04)
        echo_weight_modifier = max(echo_weight_modifier, 0.05)
        memory_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "memory_hint_guidance",
                "warm",
            )
        )
    elif dominant_emotion in fragile_tags:
        follow_up_tendency = min(follow_up_tendency, 0.02)
        memory_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "memory_hint_guidance",
                "fragile",
            )
        )

    if (
        shared_history_resonance_count > 0
        and protective_recoil_count <= 0
        and suppressed_count <= 0
        and avg_distortion < 0.35
    ):
        tone = "caring" if tone == "gentle" else tone
        follow_up_tendency = max(follow_up_tendency, 0.08)
        echo_weight_modifier = max(echo_weight_modifier, 0.06)
        memory_guidance.append(
            _template_text(
                runtime_guidance_templates,
                "memory_hint_guidance",
                "shared_history",
            )
        )

    guidance_text = str(memory_signal.get("memory_persona_guidance") or "").strip()
    if guidance_text:
        memory_guidance.append(guidance_text)
    if memory_guidance:
        final_guidance = "；".join(dict.fromkeys(part for part in memory_guidance if part))
        style_hint = f"{style_hint}；{final_guidance}" if style_hint else final_guidance
        hint["memory_guidance"] = final_guidance

    hint["tone"] = tone
    hint["style_hint"] = _truncate_for_prompt(style_hint, limit=260) or style_hint
    hint["follow_up_tendency"] = round(_clamp_float(follow_up_tendency, minimum=-0.3, maximum=0.3), 4)
    hint["echo_weight_modifier"] = round(_clamp_float(echo_weight_modifier, minimum=-0.3, maximum=0.3), 4)
    if pace_hint:
        hint["pace_hint"] = pace_hint
    return hint


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
        store_id = str(
            row.get("store_id")
            or metadata.get("store_id")
            or ""
        ).strip().lower()
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
                "emotion_color_tag": emotion_color_tag,
                "suppression_flag": suppression_flag,
                "distortion_level": distortion_level,
            }
        )
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
    memory_recall_summary_text = str(prompt_context.get("memory_recall_summary") or "").strip()
    memory_persona_evidence_rows = (
        list(prompt_context.get("memory_persona_evidence") or [])
        if isinstance(prompt_context.get("memory_persona_evidence"), list)
        else []
    )

    slot_item_counts = {
        "authoritative": len(authoritative_rows),
        "retrieval": len(retrieval_rows),
        "style_canon": len(style_rows),
        "style_node_bindings": len(node_bindings),
        "tool_meta_pack": len(tool_meta_rows),
        "emotion_recall": (1 if emotion_recall_text else 0),
        "memory_persona_evidence": len(memory_persona_evidence_rows),
        "memory_recall_summary": (1 if memory_recall_summary_text else 0),
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
                "memory_persona_evidence": memory_persona_evidence_rows,
                "memory_recall_summary": memory_recall_summary_text,
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
            "memory_recall_summary_present": bool(memory_recall_summary_text),
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
    bond_summary: Mapping[str, Any] | None = None,
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
        bond_summary=bond_summary,
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


def _copy_mapping_payload(raw: Any) -> dict[str, Any]:
    return dict(raw) if isinstance(raw, Mapping) else {}


def _copy_mapping_list(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _build_multi_agent_context_pack(
    *,
    runtime_payload: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        from host_brain.app.schemas.context_pack import ContextPack  # noqa: PLC0415
    except Exception:
        return {}

    payload = dict(runtime_payload or {})
    request_section = _copy_mapping_payload(payload.get("request"))
    retrieval_section = _copy_mapping_payload(payload.get("retrieval"))
    retrieval_section["evidence_items"] = _copy_mapping_list(retrieval_section.get("evidence_items"))

    planner_section: dict[str, Any] = {
        "graph": _copy_mapping_payload(payload.get("graph")),
        "scene_awareness": _copy_mapping_payload(payload.get("scene_awareness")),
        "intent_draft": _copy_mapping_payload(payload.get("intent_draft")),
    }

    soul_state: dict[str, Any] = {
        "bond_summary": _copy_mapping_payload(payload.get("bond_summary")),
        "offline_state": _copy_mapping_payload(payload.get("offline_state")),
        "self_model_snapshot": _copy_mapping_payload(payload.get("self_model_snapshot")),
        "self_reflection_pulse": _copy_mapping_payload(payload.get("self_reflection_pulse")),
        "persona_expression_hint": _copy_mapping_payload(payload.get("persona_expression_hint")),
    }

    runtime_state: dict[str, Any] = {
        "emotion_vector": _copy_mapping_payload(payload.get("emotion_vector")),
        "emotion_timeline": _copy_mapping_payload(payload.get("emotion_timeline")),
        "triggered_emotions": _copy_mapping_payload(payload.get("triggered_emotions")),
        "endocrine_state": _copy_mapping_payload(payload.get("endocrine_state")),
        "silence_state": _copy_mapping_payload(payload.get("silence_state")),
        "body_sense": _copy_mapping_payload(payload.get("body_sense")),
        "runtime_mode": _copy_mapping_payload(payload.get("runtime_mode")),
        "activity_monitor": _copy_mapping_payload(payload.get("activity_monitor")),
    }

    prompt_context = _copy_mapping_payload(payload.get("prompt_context"))
    prompt_context_payload = _copy_mapping_payload(prompt_context.get("payload"))
    memory_state: dict[str, Any] = {
        "prompt_context_payload": prompt_context_payload,
        "memory_persona_signal": _copy_mapping_payload(payload.get("memory_persona_signal")),
        "memory_persona_evidence": _copy_mapping_list(payload.get("memory_persona_evidence")),
        "memory_recall_summary": str(payload.get("memory_recall_summary") or "").strip(),
        "bond_narrative": _copy_mapping_payload(payload.get("bond_narrative")),
    }

    trace_refs: list[str] = []
    request_id = str(request_section.get("request_id") or "").strip()
    if request_id:
        trace_refs.append(f"request:{request_id}")

    request_meta = {
        "request_id": request_id,
        "person_id": str(request_section.get("person_id") or "").strip(),
        "session_id": str(request_section.get("session_id") or "").strip(),
        "thread_id": str(request_section.get("thread_id") or "").strip(),
        "target_lang": str(_copy_mapping_payload(payload.get("render")).get("target_lang") or "").strip(),
    }

    context_pack = ContextPack(
        request_meta=request_meta,
        planner=planner_section,
        retrieval=retrieval_section,
        motive=_copy_mapping_payload(payload.get("motive_state")),
        response_policy=_copy_mapping_payload(payload.get("response_policy")),
        soul_state=soul_state,
        offline_state=_copy_mapping_payload(payload.get("offline_state")),
        runtime_state=runtime_state,
        memory_state=memory_state,
        trace_refs=trace_refs,
        extensions={
            "version": str(payload.get("version") or "").strip(),
            "sys_mode": str(payload.get("sys_mode") or "").strip(),
            "endocrine_gate": str(
                _copy_mapping_payload(payload.get("endocrine_state")).get("gate_state") or ""
            ).strip(),
        },
    )
    return context_pack.model_dump()


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
        bond_summary=dict(runtime_input.bond_summary or {}),
        emotion_context=dict(runtime_input.emotion_state or {}),
        emotion_snapshot=dict(runtime_input.emotion_snapshot or {}),
        render_target_lang=render_target_lang or None,
        cpb_slots=(merged_slots or None),
    )

    prompt_context = dict(generation_payload.get("prompt_context") or {})
    prompt_context_validation = dict(generation_payload.get("prompt_context_validation") or {})
    prompt_context_observability = dict(generation_payload.get("prompt_context_observability") or {})
    episodic_vector_slot_text = str(recall_slot_text.get("episodic_vector") or "").strip()
    if episodic_vector_slot_text and not str(prompt_context.get("episodic_vector") or "").strip():
        prompt_context["episodic_vector"] = episodic_vector_slot_text

    runtime_payload = runtime_input.to_runtime_payload(
        prompt_context=prompt_context,
        prompt_context_validation=prompt_context_validation,
        prompt_context_observability=prompt_context_observability,
    )
    runtime_payload["style_bindings"] = resolved_style_bindings
    runtime_mode_flags = _build_runtime_mode_flags(prompt_context)
    if runtime_mode_flags:
        runtime_payload["runtime_mode"] = runtime_mode_flags
        runtime_payload["sys_mode"] = str(runtime_mode_flags.get("sys_mode") or "normal").strip().lower() or "normal"
        activity_monitor = runtime_mode_flags.get("activity_monitor")
        if isinstance(activity_monitor, Mapping):
            runtime_payload["activity_monitor"] = dict(activity_monitor)
    graph_payload = dict(runtime_payload.get("graph") or {})
    scene_awareness_raw = graph_payload.get("scene_awareness")
    scene_awareness = (
        dict(scene_awareness_raw)
        if isinstance(scene_awareness_raw, Mapping)
        else {}
    )
    if scene_awareness:
        runtime_payload["scene_awareness"] = scene_awareness
    memory_persona_signal_raw = prompt_context.get("memory_emotion_hint")
    memory_persona_signal = (
        dict(memory_persona_signal_raw)
        if isinstance(memory_persona_signal_raw, Mapping)
        else {}
    )
    if memory_persona_signal:
        runtime_payload["memory_persona_signal"] = memory_persona_signal
    memory_persona_evidence_raw = prompt_context.get("memory_persona_evidence")
    memory_persona_evidence = (
        [dict(item) for item in memory_persona_evidence_raw if isinstance(item, Mapping)]
        if isinstance(memory_persona_evidence_raw, list)
        else []
    )
    if memory_persona_evidence:
        runtime_payload["memory_persona_evidence"] = memory_persona_evidence
    memory_recall_summary = str(
        prompt_context.get("memory_recall_summary")
        or memory_persona_signal.get("memory_recall_summary")
        or ""
    ).strip()
    if memory_recall_summary:
        runtime_payload["memory_recall_summary"] = memory_recall_summary
    _record_latest_prompt_context_snapshot(
        {
            "request_id": str(runtime_input.request_id or "").strip(),
            "session_id": str(runtime_input.session_id or "").strip(),
            "thread_id": str(runtime_input.thread_id or "").strip(),
            "render_target_lang": render_target_lang or None,
            "ts_ms": int(time.time() * 1000),
            "style_bindings": normalize_node_style_bindings(resolved_style_bindings),
            "style_pack": (
                dict(prompt_context.get("style_pack") or {})
                if isinstance(prompt_context.get("style_pack"), Mapping)
                else {}
            ),
            "memory_persona_signal": dict(memory_persona_signal or {}),
            "memory_persona_evidence": [dict(item) for item in memory_persona_evidence],
            "memory_recall_summary": memory_recall_summary,
            "prompt_context_observability": dict(prompt_context_observability or {}),
        }
    )
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
    if _full_emotion_ctx.get("endocrine_state"):
        runtime_payload["endocrine_state"] = dict(_full_emotion_ctx.get("endocrine_state") or {})

    if not isinstance(runtime_payload.get("endocrine_state"), Mapping):
        try:
            from host_brain.app.dependencies import get_endocrine_state  # noqa: PLC0415

            runtime_payload["endocrine_state"] = dict(
                get_endocrine_state(
                    emotion_tag=str(_full_emotion_ctx.get("emotion_tag") or "NEUTRAL"),
                    emotion_intensity=float(_full_emotion_ctx.get("emotion_intensity") or 0.0),
                )
                or {}
            )
        except Exception:
            pass

    if isinstance(runtime_payload.get("endocrine_state"), Mapping):
        merged_emotion_context = dict(runtime_payload.get("emotion_context") or {})
        merged_emotion_context["endocrine_state"] = dict(runtime_payload.get("endocrine_state") or {})
        runtime_payload["emotion_context"] = merged_emotion_context

    # emotion_timeline: 从全局单例获取最新摘要（因为 timeline 是 append-only，
    # 每次 normalize 都会追加，此时已经是最新状态）
    _tl_summary: dict[str, Any] = {}
    _tl_diary: dict[str, Any] = {}
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        _tl_snapshot = get_limbic_agent().resolve_emotion_timeline_snapshot(
            summary_window_ms=300_000,
            diary_window_ms=24 * 3600 * 1000,
        )
        _tl_summary = dict(_tl_snapshot.get("summary") or {})
        _tl_diary = dict(_tl_snapshot.get("diary") or {})
        if _tl_summary and _tl_summary.get("entry_count", 0) > 0:
            runtime_payload["emotion_timeline"] = _tl_summary
    except Exception:
        pass  # 降级：不注入 timeline

    # ── P1-FIX：接入 PersonaExpressionAdapter ────────────────────────────
    # prompt_builder 通过 runtime_payload.get("persona_expression_hint") 读取
    _emotion_reunion_type = ""
    try:
        from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415
        _prefrontal_agent = get_prefrontal_agent()
        _persona_user_id = get_canonical_bond_user_id()
        # 获取当前 EmotionVector
        _vad = _full_emotion_ctx.get("vad_vector") or {}
        _cur_valence = float(_vad.get("valence") or 0.0)
        _cur_arousal = float(_vad.get("arousal") or 0.0)
        _cur_dominance = float(_vad.get("dominance") or 0.0)

        # ── 接入 EmotionPersistence（经 limbic 包装层）──────────────────
        try:
            _ep_payload = dict(
                get_limbic_agent().resolve_persistent_emotion_state(
                    person_id=_persona_user_id,
                    valence=_cur_valence,
                    arousal=_cur_arousal,
                    dominance=_cur_dominance,
                )
                or {}
            )
            _cur_valence = float(_ep_payload.get("valence") or _cur_valence)
            _cur_arousal = float(_ep_payload.get("arousal") or _cur_arousal)
            _cur_dominance = float(_ep_payload.get("dominance") or _cur_dominance)
            _emotion_reunion_type = str(_ep_payload.get("reunion_type") or "").strip()
            if _emotion_reunion_type:
                runtime_payload["emotion_reunion_type"] = _emotion_reunion_type
        except Exception as _ep_exc:
            logger.debug("EmotionPersistence 接入降级: %s", _ep_exc)

        # ── Phase XVI：离线内心世界唤醒快照注入 ──────────────────────────
        try:
            from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

            _iw_snapshot = dict(get_limbic_agent().on_user_awaken(person_id=_persona_user_id) or {})
            _iw_offline_hours = float(_iw_snapshot.get("offline_hours") or 0.0)
            _iw_hint = str(_iw_snapshot.get("awakening_hint") or "")
            if _iw_offline_hours >= 1.0 or _iw_hint:
                runtime_payload["offline_state"] = _iw_snapshot
                logger.debug(
                    "OfflineInnerWorld 唤醒快照注入: user=%s offline_hours=%.1f hint=%s",
                    _persona_user_id,
                    _iw_offline_hours,
                    _iw_hint[:30] if _iw_hint else "",
                )
        except Exception as _iw_exc:
            logger.debug("OfflineInnerWorld 接入降级: %s", _iw_exc)

        # ── Phase XVIII：沉默引擎状态快照注入 ────────────────────────────
        try:
            from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

            _silence_state = dict(get_limbic_agent().resolve_silence_state())
            _silence_level = str(_silence_state.get("willingness_level") or "NORMAL").strip().upper() or "NORMAL"
            _topic_resistance_active = bool(_silence_state.get("topic_resistance_active"))
            if _silence_level != "NORMAL" or _topic_resistance_active:
                runtime_payload["silence_state"] = _silence_state
                logger.debug(
                    "EmotionalSilenceEngine 状态注入: user=%s willingness=%.2f level=%s",
                    _persona_user_id,
                    float(_silence_state.get("response_willingness") or 0.0),
                    _silence_level,
                )
        except Exception as _silence_exc:
            logger.debug("EmotionalSilenceEngine 接入降级: %s", _silence_exc)

        # ── v1.12：身体感知状态快照注入 + 疲劳惩罚接入沉默引擎 ──────────
        try:
            from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

            _limbic_agent = get_limbic_agent()
            _bs_result = _limbic_agent.capture_conversation_body_sense(
                request_id=request_id,
                session_id=session_id,
                person_id=_persona_user_id,
                emotion_tag=_full_emotion_ctx.get("emotion_tag"),
                mood_state=_full_emotion_ctx.get("mood_state"),
                offline_loneliness=(runtime_payload.get("offline_state") or {}).get("loneliness_level"),
            )
            _bs_payload = dict(_bs_result.get("body_sense") or {})
            if bool(_bs_result.get("is_active")):
                runtime_payload["body_sense"] = _bs_payload
                logger.debug(
                    "BodySenseModel 状态注入: user=%s fatigue=%s drowsiness=%s loneliness=%s",
                    _persona_user_id,
                    _bs_payload.get("fatigue_level"),
                    _bs_payload.get("drowsiness_level"),
                    _bs_payload.get("loneliness_level"),
                )
            # 疲劳惩罚接入沉默引擎（断链6修复）
            try:
                _bs_penalty = float(_bs_result.get("willingness_penalty") or 0.0)
            except (TypeError, ValueError):
                _bs_penalty = 0.0
            if _bs_penalty > 0.0:
                try:
                    _limbic_agent.apply_body_sense_penalty(
                        penalty=_bs_penalty,
                    )
                except Exception:
                    pass
        except Exception as _bs_exc:
            logger.debug("BodySenseModel 接入降级: %s", _bs_exc)

        # ── Phase XIX：涌现式成长叙事注入 self_reflection_pulse ──────────
        try:
            from host_brain.skills.impl.emergent_growth_skill import _get_growth_engine  # noqa: PLC0415
            _eg_engine = _get_growth_engine()
            _eg_note = _eg_engine.get_latest_growth_note(_persona_user_id)
            if _eg_note:
                # 注入到 self_reflection_pulse（如果已有则追加 emergent_growth_note 字段）
                _existing_pulse = runtime_payload.get("self_reflection_pulse")
                if isinstance(_existing_pulse, dict):
                    _existing_pulse["emergent_growth_note"] = _eg_note
                else:
                    runtime_payload["emergent_growth_note"] = _eg_note
                logger.debug(
                    "EmergentGrowthEngine 成长叙事注入: user=%s note='%s'",
                    _persona_user_id, _eg_note[:40],
                )
        except Exception as _eg_exc:
            logger.debug("EmergentGrowthEngine 接入降级: %s", _eg_exc)

        # 获取 bond_stage
        _bond_stage = "STRANGER"
        try:
            from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415
            _user_id = get_canonical_bond_user_id()
            _bond_stage = get_social_brain_agent().resolve_bond_stage(person_id=_user_id, default="STRANGER")
        except Exception:
            pass
        _user_tag = str(_full_emotion_ctx.get("emotion_tag") or "NEUTRAL").strip().upper()
        # 获取当前用户消息（用于对话节奏推断）
        _user_msg_for_pace = str(generation_payload.get("user_message") or "").strip()
        _persona_expression_hint = dict(
            _prefrontal_agent.resolve_persona_expression_hint(
                persona_user_id=_persona_user_id,
                bond_stage=_bond_stage,
                user_emotion_tag=_user_tag,
                user_message=_user_msg_for_pace,
                valence=_cur_valence,
                arousal=_cur_arousal,
                dominance=_cur_dominance,
                ts_ms=int(_vad.get("ts_ms") or 0),
            )
            or {}
        )
        runtime_payload["persona_expression_hint"] = _persona_expression_hint
    except (ImportError, AttributeError, Exception) as _pe_exc:
        logger.debug("PersonaExpressionAdapter 接入降级: %s", _pe_exc)

    # ── P1-FIX：接入 SelfReflection ──────────────────────────────────────
    # prompt_builder 通过 runtime_payload.get("self_reflection_pulse") 读取
    _sr_alignment_snapshot: dict[str, Any] = {}
    _prefrontal_agent_for_sr = None
    try:
        from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415
        _prefrontal_agent = get_prefrontal_agent()
        _prefrontal_agent_for_sr = _prefrontal_agent
        # 获取当前轮次（从 session 状态推断，或使用递增计数器）
        if not hasattr(build_chat_prompt_runtime_payload, "_turn_counter"):
            build_chat_prompt_runtime_payload._turn_counter = 0
        build_chat_prompt_runtime_payload._turn_counter += 1
        _turn = build_chat_prompt_runtime_payload._turn_counter
        _sr_user_id = get_canonical_bond_user_id()
        _sr_bond_state = None
        _sr_bond_summary = dict((dict(prompt_context or {}).get("bond_summary") or {}))
        _sr_last_deep_talk_ts_ms = 0
        try:
            from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

            _sr_social = get_social_brain_agent().resolve_bond_snapshot(
                person_id=_sr_user_id,
                bond_summary=_sr_bond_summary,
            )
            _sr_bond_state = _sr_social.get("bond_state_obj")
            if not _sr_bond_summary:
                _sr_bond_summary = dict(_sr_social.get("bond_summary") or {})
            _sr_last_deep_talk_ts_ms = int(_sr_social.get("last_event_ts_ms") or 0)
        except Exception:
            pass
        _sr_current_ts_ms = int(_full_emotion_ctx.get("emotion_ts_ms") or 0) or int(time.time() * 1000)
        if _sr_last_deep_talk_ts_ms <= 0 and _emotion_reunion_type:
            if _emotion_reunion_type == "reunion":
                _sr_last_deep_talk_ts_ms = _sr_current_ts_ms - 8 * 24 * 3600 * 1000
            elif _emotion_reunion_type == "short_absence":
                _sr_last_deep_talk_ts_ms = _sr_current_ts_ms - 4 * 24 * 3600 * 1000
        try:
            _sr_persona_changes = list(
                _prefrontal_agent.resolve_persona_change_log(
                    persona_user_id=_sr_user_id,
                    limit=5,
                )
                or []
            )
        except Exception:
            _sr_persona_changes = []
        try:
            _sr_growth_awareness = dict(_prefrontal_agent.resolve_growth_awareness() or {})
        except Exception:
            _sr_growth_awareness = {}
        try:
            _sr_growth_summary = dict(
                _prefrontal_agent.resolve_growth_summary(
                    persona_user_id=_sr_user_id,
                    now_ts_ms=_sr_current_ts_ms,
                )
                or {}
            )
        except Exception:
            _sr_growth_summary = {}
        _tl_summary_for_sr = dict(runtime_payload.get("emotion_timeline") or _tl_summary or {})
        _tl_diary_for_sr = dict(_tl_diary or {})
        # ── 断链5修复（v1.12）：获取风格演变摘要，注入自省感知 ──────────
        _sr_style_evolution: dict | None = None
        try:
            _sr_style_evolution = dict(_prefrontal_agent.resolve_style_evolution_summary() or {})
        except Exception:
            pass
        _sr_cycle_payload = _prefrontal_agent.run_self_reflection_cycle(
            turn_count=_turn,
            timeline_summary=_tl_summary_for_sr,
            timeline_diary=_tl_diary_for_sr,
            current_emotion=str(_full_emotion_ctx.get("emotion_tag") or ""),
            bond_state=_sr_bond_state,
            bond_summary=_sr_bond_summary,
            persona_changes=_sr_persona_changes,
            last_deep_talk_ts_ms=_sr_last_deep_talk_ts_ms,
            current_ts_ms=_sr_current_ts_ms,
            growth_awareness=_sr_growth_awareness,
            growth_summary=_sr_growth_summary,
            style_evolution=_sr_style_evolution,
        )
        _pulse = dict(_sr_cycle_payload.get("self_reflection_pulse") or {})
        _sr_alignment_snapshot = dict(_sr_cycle_payload.get("snapshot") or {})
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
            from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

            _bn_bond_stage = get_social_brain_agent().resolve_bond_stage(
                person_id=_user_id_bn,
                default="STRANGER",
            )
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

    # ── 接入 BondAwareStyleEngine：Bond 阶段感知风格指令 ──────────────
    # prompt_builder 通过 runtime_payload.get("bond_style_instruction") 读取
    try:
        from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415

        _prefrontal_agent = get_prefrontal_agent()
        _bas_user_id = get_canonical_bond_user_id()
        _bas_bond_stage = "STRANGER"
        _bas_intimacy = 0.0
        _bas_rift_active = False
        _bas_rift_severity = "medium"
        _bas_rift_progress = 0.0
        _bas_bond_trend = ""
        _bas_shared_hints: list[str] = []
        try:
            from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

            _bas_social = get_social_brain_agent().resolve_bond_snapshot(person_id=_bas_user_id)
            _bas_state = _bas_social.get("bond_state_obj")
            _bas_bond_stage = str(_bas_social.get("bond_stage") or "STRANGER")
            _bas_intimacy = float(_bas_social.get("intimacy_score") or 0.0)
            _bas_bond_trend = str(_bas_social.get("bond_trend") or "")
            if True:
                _bas_rift = _bas_social.get("rift")
                if _bas_rift is not None and getattr(_bas_rift, "is_active", lambda: False)():
                    _bas_rift_active = True
                    _bas_rift_severity = str(getattr(_bas_rift, "severity", "medium") or "medium")
                    _bas_rift_progress = float(getattr(_bas_rift, "repair_progress", 0.0) or 0.0)
                # 裂痕状态
                _bas_rift = getattr(_bas_state, "rift", None)
        except Exception:
            pass
        # 从 bond_narrative 中提取共同记忆提示
        _bas_bn = runtime_payload.get("bond_narrative") or {}
        if isinstance(_bas_bn, dict):
            _bas_shared_hints = [str(h) for h in (_bas_bn.get("shared_memory_hints") or []) if h]
        # 用户情绪方向
        _bas_mood = "neutral"
        _bas_vad = _full_emotion_ctx.get("vad_vector") or {}
        if _bas_vad:
            _bas_v = float(_bas_vad.get("valence") or 0.0)
            if _bas_v > 0.15:
                _bas_mood = "positive"
            elif _bas_v < -0.15:
                _bas_mood = "negative"
        # Natsume 自身情绪标签
        _bas_natsume_mood = str(_full_emotion_ctx.get("emotion_tag") or "NEUTRAL").strip().upper()
        _bond_style_instruction = _prefrontal_agent.resolve_bond_style_instruction(
            bond_stage=_bas_bond_stage,
            intimacy_score=_bas_intimacy,
            mood_state=_bas_mood,
            natsume_mood=_bas_natsume_mood,
            rift_active=_bas_rift_active,
            rift_severity=_bas_rift_severity,
            rift_progress=_bas_rift_progress,
            bond_trend=_bas_bond_trend,
            shared_memory_hints=_bas_shared_hints or None,
        )
        if _bond_style_instruction:
            runtime_payload["bond_style_instruction"] = _bond_style_instruction
    except (ImportError, AttributeError, Exception) as _bas_exc:
        logger.debug("BondAwareStyleEngine 接入降级: %s", _bas_exc)

    # ── MotiveEngine + ResponsePolicyLayer 接入 ─────────────────────────────
    # 在所有状态收集完成后，统一计算动机向量与行为约束策略，
    # 注入 runtime_payload["motive_state"] 和 runtime_payload["response_policy"]
    # 供 prompt_builder / post_processor / proactive_voice 消费。
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        _pending_topics = list(
            (runtime_payload.get("offline_state") or {}).get("anticipation_topics") or []
        )
        _shared_count = int(
            (runtime_payload.get("bond_summary") or {}).get("shared_memory_count") or 0
        )

        _limbic_agent = get_limbic_agent()
        runtime_payload["motive_state"] = _limbic_agent.compute_motive_state(
            silence_state=runtime_payload.get("silence_state"),
            body_sense=runtime_payload.get("body_sense"),
            offline_state=runtime_payload.get("offline_state"),
            bond_summary=runtime_payload.get("bond_summary"),
            self_reflection_pulse=runtime_payload.get("self_reflection_pulse"),
            emotion_context=runtime_payload.get("triggered_emotions"),
            pending_followup_topics=_pending_topics,
            shared_memory_count=_shared_count,
            user_message=str(runtime_input.user_message or ""),
            runtime_flags=runtime_payload.get("runtime_mode"),
        )
        runtime_payload["response_policy"] = _limbic_agent.decide_response_policy(
            silence_state=runtime_payload.get("silence_state"),
            body_sense=runtime_payload.get("body_sense"),
            offline_state=runtime_payload.get("offline_state"),
            bond_summary=runtime_payload.get("bond_summary"),
            self_reflection_pulse=runtime_payload.get("self_reflection_pulse"),
            emotion_context=runtime_payload.get("triggered_emotions"),
            motive_state=runtime_payload.get("motive_state"),
            memory_persona_signal=runtime_payload.get("memory_persona_signal"),
            user_message=str(runtime_input.user_message or ""),
            runtime_flags=runtime_payload.get("runtime_mode"),
        )
        runtime_payload["persona_expression_hint"] = _apply_motive_state_to_persona_expression_hint(
            runtime_payload.get("persona_expression_hint"),
            runtime_payload.get("motive_state"),
        )
        if (
            runtime_payload.get("self_reflection_pulse")
            and _prefrontal_agent_for_sr is not None
            and _sr_alignment_snapshot
        ):
            try:
                runtime_payload["self_reflection_pulse"] = _prefrontal_agent_for_sr.align_self_reflection_pulse(
                    pulse=runtime_payload.get("self_reflection_pulse"),
                    snapshot=_sr_alignment_snapshot,
                    offline_state=runtime_payload.get("offline_state"),
                    bond_summary=runtime_payload.get("bond_summary"),
                    motive_state=runtime_payload.get("motive_state"),
                    response_policy=runtime_payload.get("response_policy"),
                    runtime_flags=runtime_payload.get("runtime_mode"),
                )
            except Exception as _sr_align_exc:
                logger.debug("SelfReflection narrative alignment degraded: %s", _sr_align_exc)

    except (ImportError, AttributeError, Exception) as _motive_exc:
        logger.debug("MotiveEngine / ResponsePolicyLayer 接入降级: %s", _motive_exc)

    if runtime_payload.get("persona_expression_hint") and runtime_payload.get("offline_state"):
        runtime_payload["persona_expression_hint"] = _apply_offline_state_to_persona_expression_hint(
            runtime_payload.get("persona_expression_hint"),
            runtime_payload.get("offline_state"),
        )

    if runtime_payload.get("persona_expression_hint") and runtime_payload.get("memory_persona_signal"):
        runtime_payload["persona_expression_hint"] = _apply_memory_persona_signal_to_persona_expression_hint(
            runtime_payload.get("persona_expression_hint"),
            runtime_payload.get("memory_persona_signal"),
        )

    if runtime_payload.get("persona_expression_hint") and runtime_payload.get("activity_monitor"):
        runtime_payload["persona_expression_hint"] = _apply_activity_monitor_to_persona_expression_hint(
            runtime_payload.get("persona_expression_hint"),
            runtime_payload.get("activity_monitor"),
        )

    if runtime_payload.get("persona_expression_hint") and runtime_payload.get("scene_awareness"):
        runtime_payload["persona_expression_hint"] = _apply_scene_awareness_to_persona_expression_hint(
            runtime_payload.get("persona_expression_hint"),
            runtime_payload.get("scene_awareness"),
        )

    if runtime_payload.get("persona_expression_hint") and runtime_payload.get("runtime_mode"):
        runtime_payload["persona_expression_hint"] = _apply_runtime_mode_to_persona_expression_hint(
            runtime_payload.get("persona_expression_hint"),
            runtime_payload.get("runtime_mode"),
        )

    try:
        _agent_context_pack = _build_multi_agent_context_pack(runtime_payload=runtime_payload)
        if _agent_context_pack:
            runtime_payload["context_pack"] = _agent_context_pack
    except Exception as _context_pack_exc:
        logger.debug("build multi-agent context pack degraded: %s", _context_pack_exc)

    # ── T-12：CortexAgent Supervisor 收口 ────────────────────────────────
    # 通过 CortexAgent.collect_tribe_context() 聚合各族 Agent 的运行时状态，
    # 注入 runtime_payload["tribe_vital_signs"] 和 runtime_payload["tribe_route"]
    # 供 prompt_builder 和下游消费者读取。
    try:
        from host_brain.tribe.cortex_agent import get_cortex_agent  # noqa: PLC0415

        _cortex = get_cortex_agent()
        _tribe_ctx = _cortex.collect_tribe_context(
            session_id=str(runtime_input.session_id or "").strip() or None,
            user_id=get_canonical_bond_user_id() or None,
        )
        _vital_signs = dict(_tribe_ctx.get("vital_signs") or {})
        if _vital_signs:
            runtime_payload["tribe_vital_signs"] = _vital_signs
        _tribe_route = _cortex.route_intent(
            message=str(runtime_input.user_message or ""),
            tribe_context=_tribe_ctx,
        )
        runtime_payload["tribe_route"] = _tribe_route
        # T-23：将完整 hedonic_state 快照注入 runtime_payload（T-20 已由 collect_tribe_context 写入）
        # 供 prompt_builder Phase XIX 读取完整享乐状态（pain_level/reward_level/net_valence 等）
        _hedonic_state = dict(_tribe_ctx.get("hedonic_state") or {})
        if _hedonic_state:
            runtime_payload["hedonic_state"] = _hedonic_state
        # 若 voice_emotion 有值，补充到 runtime_payload（T-09 闭环）
        _voice_emotion = dict(_tribe_ctx.get("voice_emotion") or {})
        if _voice_emotion and _voice_emotion.get("emotion_tag"):
            runtime_payload.setdefault("voice_emotion_inference", _voice_emotion)
        # T-97：消费 high_loneliness_flag，孤独感高时注入 loneliness_hint 供 prompt_builder 调整语气
        _high_loneliness = bool(_vital_signs.get("high_loneliness_flag", False))
        _loneliness_level = float(_vital_signs.get("loneliness_level") or 0.0)
        if _high_loneliness:
            runtime_payload["loneliness_hint"] = {
                "high_loneliness": True,
                "loneliness_level": round(_loneliness_level, 4),
                "suggest": "主动关怀，增加陪伴感表达，避免冷漠或过于简短的回复",
            }
        else:
            runtime_payload.setdefault("loneliness_hint", {
                "high_loneliness": False,
                "loneliness_level": round(_loneliness_level, 4),
            })

        # T-98：消费 bond_trend_hint，关系趋势影响话题选择和回复倾向
        _bond_trend = str(_vital_signs.get("bond_trend_hint") or "").strip().lower()
        if _bond_trend:
            runtime_payload["bond_trend_hint"] = _bond_trend
            # progress：关系升温，可适当增加亲密感表达
            # regress_risk：关系面临退步风险，回复需更谨慎温和
            if _bond_trend == "progress":
                runtime_payload.setdefault("bond_tone_adjust", "warmer")
            elif _bond_trend in ("regress_risk", "regress"):
                runtime_payload.setdefault("bond_tone_adjust", "cautious")

        # T-99：消费 immune_risk_level，免疫拦截状态影响 prompt 谨慎度
        _immune_risk = str(_vital_signs.get("immune_risk_level") or "none").strip().lower()
        _immune_block_count = int(_vital_signs.get("immune_block_count") or 0)
        if _immune_risk in ("medium", "high", "critical") or _immune_block_count >= 3:
            runtime_payload["immune_caution"] = {
                "risk_level": _immune_risk,
                "block_count": _immune_block_count,
                "suggest": "当前会话存在内容风险，回复需更加谨慎，避免触碰敏感话题",
            }

        # T-105：接入 LanguageSystemAgent.get_language_style()，注入 runtime_payload["language_style"]
        # 根据当前 bond_stage / emotion_tag / loneliness_level 动态生成语言风格建议，
        # 供 prompt_builder Phase T-106 消费，影响回复的语气/措辞/句式选择。
        try:
            from host_brain.tribe.language_system_agent import get_language_system_agent  # noqa: PLC0415

            _lang_agent = get_language_system_agent()
            _bond_stage_for_lang = str(_vital_signs.get("bond_stage") or "stranger").strip().lower()
            _emotion_tag_for_lang = str(_vital_signs.get("emotion_tag") or "NEUTRAL").strip().upper()
            _loneliness_for_lang = float(_vital_signs.get("loneliness_level") or 0.0)
            _high_loneliness_for_lang = bool(_vital_signs.get("high_loneliness_flag", False))
            _language_style = _lang_agent.get_language_style(
                bond_stage=_bond_stage_for_lang,
                emotion_tag=_emotion_tag_for_lang,
                loneliness_level=_loneliness_for_lang,
                high_loneliness_flag=_high_loneliness_for_lang,
            )
            runtime_payload["language_style"] = _language_style
        except Exception as _lang_exc:
            logger.debug("LanguageSystemAgent.get_language_style 降级: %s", _lang_exc)

        logger.debug(
            "CortexAgent Supervisor 收口完成: gate_state=%s route=%s voice_emotion=%s hedonic_tone=%s "
            "high_loneliness=%s bond_trend=%s immune_risk=%s",
            _vital_signs.get("gate_state"),
            _tribe_route.get("route"),
            _voice_emotion.get("emotion_tag"),
            _hedonic_state.get("hedonic_tone"),
            _high_loneliness,
            _bond_trend,
            _immune_risk,
        )
    except Exception as _cortex_exc:
        logger.debug("CortexAgent Supervisor 收口降级: %s", _cortex_exc)

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
