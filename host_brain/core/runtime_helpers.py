"""runtime_helpers.py — 运行时纯工具函数（无业务依赖）。

从 agent_runtime_shared.py 提取的通用工具函数，可被任意层安全导入。
不依赖 app 层、tribe 层、ledger 等业务模块。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged


# ---------------------------------------------------------------------------
# 模板工具
# ---------------------------------------------------------------------------

class _SafeTemplateDict(dict[str, Any]):
    def __missing__(self, key: object) -> str:
        return "{" + str(key) + "}"


def _format_template_text(template_text: Any, **kwargs: Any) -> str:
    text = str(template_text or "")
    if not kwargs:
        return text
    payload = _SafeTemplateDict(
        {str(key): "" if value is None else str(value) for key, value in kwargs.items()}
    )
    try:
        return text.format_map(payload)
    except Exception:
        return text


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_section(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    raw = mapping.get(key)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    return _format_template_text(mapping.get(key, default), **kwargs)


# ---------------------------------------------------------------------------
# 环境变量 / SSE / Airlock 工具
# ---------------------------------------------------------------------------

def _parse_bool_env(key: str, default: bool = False) -> bool:
    raw = str(os.environ.get(key, "")).strip().lower()
    return raw in ("1", "true", "yes", "on") if raw else default


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _airlock_sanitize_input(message: str) -> tuple[str, bool]:
    sanitized = message.replace("\x00", "").strip()
    return sanitized, bool(sanitized)


def _airlock_sanitize_output(text: str) -> str:
    """出口净化：null byte + 长度截断。"""
    text = text.replace("\x00", "").strip()
    max_chars = int(os.environ.get("HOST_QA_MAX_RESPONSE_CHARS") or "4000")
    if len(text) > max_chars:
        truncated = text[:max_chars]
        for ch in ("。", "！", "？", ".", "!", "?"):
            last = truncated.rfind(ch)
            if last > max_chars * 0.7:
                truncated = truncated[: last + 1]
                break
        text = truncated
    return text


def _build_reply_preview(text: str, *, limit: int = 120) -> str:
    normalized = str(text or "").strip().replace("\r", " ").replace("\n", " ")
    if len(normalized) <= int(limit):
        return normalized
    return normalized[: max(1, int(limit) - 3)].rstrip() + "..."


# ---------------------------------------------------------------------------
# Trace 数据类型强制转换工具
# ---------------------------------------------------------------------------

def _coerce_trace_mapping(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    return dict(raw)


def _coerce_trace_score(raw: Any) -> float | None:
    try:
        return round(float(raw), 4)
    except (TypeError, ValueError):
        return None


def _coerce_trace_bool(raw: Any) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


# ---------------------------------------------------------------------------
# Trace 摘要工具
# ---------------------------------------------------------------------------

def _normalize_trace_stage_meta(trace_stage_meta: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(trace_stage_meta, Mapping):
        return {}
    normalized: dict[str, Any] = {}
    for key, value in dict(trace_stage_meta).items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        normalized[str(key)] = value
    return normalized


def _resolve_envelope_confidence(status: str) -> float:
    normalized = str(status or "").strip().upper()
    if normalized == "OK":
        return 1.0
    if normalized == "SKIPPED":
        return 0.6
    if normalized == "DEGRADED":
        return 0.35
    if normalized in {"FAILED", "ERROR"}:
        return 0.0
    return 0.5


def _build_trace_output_digest(output_data: Any) -> dict[str, Any]:
    if isinstance(output_data, Mapping):
        digest: dict[str, Any] = {"field_count": len(output_data)}
        scalar_count = 0
        for key, value in list(output_data.items())[:8]:
            key_text = str(key).strip()
            if not key_text:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                digest[key_text] = value
                scalar_count += 1
                continue
            if isinstance(value, list):
                digest[f"{key_text}_count"] = len(value)
                continue
            if isinstance(value, Mapping):
                digest[f"{key_text}_keys"] = list(value.keys())[:5]
        if scalar_count == 0 and len(digest) == 1:
            digest["kind"] = "mapping"
        return digest
    if isinstance(output_data, list):
        return {"kind": "list", "count": len(output_data)}
    if isinstance(output_data, str):
        text = output_data.strip()
        return {"kind": "text", "length": len(text), "preview": text[:120]}
    if output_data is None:
        return {}
    return {"kind": type(output_data).__name__}


def _detect_trace_fallback_used(
    *,
    reason_code: str | None,
    degrade_code: str | None,
    output_data: Any,
) -> bool:
    for token in (str(reason_code or ""), str(degrade_code or "")):
        if "FALLBACK" in token.upper():
            return True
    if isinstance(output_data, Mapping):
        for key, value in output_data.items():
            key_text = str(key).strip().lower()
            if "fallback" in key_text and bool(value):
                return True
    return False


def _summarize_motive_state_for_trace(motive_state: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _coerce_trace_mapping(motive_state)
    if not raw:
        return {}
    summary: dict[str, Any] = {}
    dominant_motive = str(raw.get("dominant_motive") or "").strip()
    if dominant_motive:
        summary["dominant_motive"] = dominant_motive
    drives: dict[str, float] = {}
    for key in ("reach_out_drive", "care_drive", "withdraw_drive", "continuity_drive", "self_protect_drive"):
        score = _coerce_trace_score(raw.get(key))
        if score is not None:
            drives[key] = score
    if drives:
        summary["drives"] = drives
    reason_tags = [str(tag).strip() for tag in list(raw.get("motive_reason_tags") or []) if str(tag).strip()]
    if reason_tags:
        summary["reason_tags"] = reason_tags[:8]
    return summary


def _summarize_reply_plan_for_trace(reply_plan: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _coerce_trace_mapping(reply_plan)
    if not raw:
        return {}
    summary: dict[str, Any] = {}
    for key in ("max_chars", "max_sentences"):
        try:
            summary[key] = int(raw.get(key))
        except (TypeError, ValueError):
            continue
    for key in ("allow_followup", "allow_proactive_after_reply", "should_be_brief", "should_be_withdrawn", "requires_validation", "force_buffering"):
        value = _coerce_trace_bool(raw.get(key))
        if value is not None:
            summary[key] = value
    for key in ("warmth_floor", "warmth_ceiling", "intimacy_ceiling", "humor_ceiling"):
        value = _coerce_trace_score(raw.get(key))
        if value is not None:
            summary[key] = value
    for key in ("withdrawal_guard",):
        value = _coerce_trace_bool(raw.get(key))
        if value is not None:
            summary[key] = value
    reason_tags = [str(tag).strip() for tag in list(raw.get("reason_tags") or []) if str(tag).strip()]
    if reason_tags:
        summary["reason_tags"] = reason_tags[:8]
    for key in (
        "primary_topic",
        "interaction_move",
        "response_goal",
        "tactic_family",
        "tactic_variant",
        "trajectory_stage",
        "restate_level",
        "tone_band",
        "followup_kind",
        "reply_contract",
    ):
        value = str(raw.get(key) or "").strip()
        if value:
            summary[key] = value
    used_tactics_recent = [str(item).strip() for item in list(raw.get("used_tactics_recent") or []) if str(item).strip()]
    if used_tactics_recent:
        summary["used_tactics_recent"] = used_tactics_recent[:8]
    forbidden_tactics = [str(item).strip() for item in list(raw.get("forbidden_tactics") or []) if str(item).strip()]
    if forbidden_tactics:
        summary["forbidden_tactics"] = forbidden_tactics[:8]
    forbidden_patterns = [str(item).strip() for item in list(raw.get("forbidden_patterns") or []) if str(item).strip()]
    if forbidden_patterns:
        summary["forbidden_patterns"] = forbidden_patterns[:8]
    return summary


def _summarize_conversation_interpretation_for_trace(conversation_interpretation: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _coerce_trace_mapping(conversation_interpretation)
    if not raw:
        return {}
    summary: dict[str, Any] = {}
    for key in (
        "literal_topic",
        "interaction_move",
        "freshness",
        "user_stance",
        "repeat_surface_group",
        "last_user_act",
        "last_assistant_act",
    ):
        value = str(raw.get(key) or "").strip()
        if value:
            summary[key] = value
    try:
        summary["repeat_question_count"] = int(raw.get("repeat_question_count") or 0)
    except (TypeError, ValueError):
        pass
    for key in ("recent_identity_answered", "recent_boundary_answered"):
        value = _coerce_trace_bool(raw.get(key))
        if value is not None:
            summary[key] = value
    answered_topics = [str(item).strip() for item in list(raw.get("answered_topics_recent") or []) if str(item).strip()]
    if answered_topics:
        summary["answered_topics_recent"] = answered_topics[:8]
    reason_tags = [str(item).strip() for item in list(raw.get("reason_tags") or []) if str(item).strip()]
    if reason_tags:
        summary["reason_tags"] = reason_tags[:8]
    return summary


def _summarize_topic_ledger_for_trace(topic_ledger: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _coerce_trace_mapping(topic_ledger)
    if not raw:
        return {}
    summary: dict[str, Any] = {}
    active_topics = [str(item).strip() for item in list(raw.get("active_topics") or []) if str(item).strip()]
    if active_topics:
        summary["active_topics"] = active_topics[:8]
    recent_topics = [str(item).strip() for item in list(raw.get("recently_answered_topics") or []) if str(item).strip()]
    if recent_topics:
        summary["recently_answered_topics"] = recent_topics[:8]
    try:
        summary["entry_count"] = len(list(raw.get("entries") or []))
    except Exception:
        pass
    return summary


def _summarize_tactic_ledger_for_trace(tactic_ledger: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _coerce_trace_mapping(tactic_ledger)
    if not raw:
        return {}
    summary: dict[str, Any] = {}
    recent_topics = [str(item).strip() for item in list(raw.get("recent_topics") or []) if str(item).strip()]
    if recent_topics:
        summary["recent_topics"] = recent_topics[:8]
    entries = list(raw.get("entries") or [])
    if entries:
        summary["entry_count"] = len(entries)
        sample = []
        for item in entries[:5]:
            if not isinstance(item, Mapping):
                continue
            sample.append(
                {
                    "topic_key": str(item.get("topic_key") or "").strip(),
                    "tactic_family": str(item.get("tactic_family") or "").strip(),
                    "trajectory_stage": str(item.get("trajectory_stage") or "").strip(),
                }
            )
        if sample:
            summary["recent_entries"] = sample
    return summary


def _summarize_affect_state_for_trace(affect_state: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _coerce_trace_mapping(affect_state)
    if not raw:
        return {}
    summary: dict[str, Any] = {}
    for key in ("emotion_tag",):
        value = str(raw.get(key) or "").strip()
        if value:
            summary[key] = value
    for key in ("valence", "arousal", "stability", "social_energy", "warmth_bias", "withdraw_bias", "irritation_bias"):
        value = _coerce_trace_score(raw.get(key))
        if value is not None:
            summary[key] = value
    try:
        summary["carryover_turns"] = int(raw.get("carryover_turns") or 0)
    except (TypeError, ValueError):
        pass
    return summary


def _summarize_relationship_state_for_trace(relationship_state: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _coerce_trace_mapping(relationship_state)
    if not raw:
        return {}
    summary: dict[str, Any] = {}
    stage = str(raw.get("bond_stage") or "").strip()
    if stage:
        summary["bond_stage"] = stage
    for key in ("stage_label", "relationship_phase", "relationship_substage"):
        value = str(raw.get(key) or "").strip()
        if value:
            summary[key] = value
    for key in ("familiarity", "trust", "comfort", "openness", "dependency_tolerance", "protectiveness", "boundary_softness", "affection_signal", "playfulness", "romantic_tension", "attachment_depth", "conflict_rift"):
        value = _coerce_trace_score(raw.get(key))
        if value is not None:
            summary[key] = value
    return summary


def _summarize_drive_state_for_trace(drive_state: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _coerce_trace_mapping(drive_state)
    if not raw:
        return {}
    summary: dict[str, Any] = {}
    for key in ("reach_out_drive", "withdraw_drive", "continuity_drive", "self_protection_drive", "care_drive", "repair_drive", "playfulness_drive"):
        value = _coerce_trace_score(raw.get(key))
        if value is not None:
            summary[key] = value
    return summary


def _summarize_emotion_context_for_trace(emotion_context: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _coerce_trace_mapping(emotion_context)
    if not raw:
        return {}
    summary: dict[str, Any] = {}
    for key in ("emotion_tag", "mood_state", "emotion_range_hint"):
        value = str(raw.get(key) or "").strip()
        if value:
            summary[key] = value
    for key in ("emotion_intensity", "valence", "arousal"):
        score = _coerce_trace_score(raw.get(key))
        if score is not None:
            summary[key] = score
    vad_vector = raw.get("vad_vector")
    if isinstance(vad_vector, Mapping):
        vad_summary: dict[str, float] = {}
        for key in ("valence", "arousal", "dominance"):
            score = _coerce_trace_score(vad_vector.get(key))
            if score is not None:
                vad_summary[key] = score
        if vad_summary:
            summary["vad_vector"] = vad_summary
    triggered = [str(item).strip() for item in list(raw.get("triggered_emotions") or []) if str(item).strip()]
    if triggered:
        summary["triggered_emotions"] = triggered[:8]
    return summary


def _summarize_persona_resident_memory_for_trace(
    *,
    bond_summary: Mapping[str, Any] | None,
    retrieval_summary: Mapping[str, Any] | None,
    retrieval_evidence_items: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    bond_raw = _coerce_trace_mapping(bond_summary)
    if bond_raw:
        bond_info: dict[str, Any] = {}
        for key in ("stage", "trend", "user_id"):
            value = str(bond_raw.get(key) or "").strip()
            if value:
                bond_info[key] = value
        score = _coerce_trace_score(bond_raw.get("score"))
        if score is not None:
            bond_info["score"] = score
        if bond_info:
            summary["bond"] = bond_info
    retrieval_raw = _coerce_trace_mapping(retrieval_summary)
    if retrieval_raw:
        retrieval_info: dict[str, Any] = {}
        for key in ("status", "reason_code", "pack_id"):
            value = str(retrieval_raw.get(key) or "").strip()
            if value:
                retrieval_info[key] = value
        for key in ("candidate_count", "route_count", "rerank_topk"):
            try:
                retrieval_info[key] = int(retrieval_raw.get(key) or 0)
            except (TypeError, ValueError):
                continue
        target_stores = [str(item).strip() for item in list(retrieval_raw.get("target_stores") or []) if str(item).strip()]
        if target_stores:
            retrieval_info["target_stores"] = target_stores
        if retrieval_info:
            summary["retrieval"] = retrieval_info
    evidence_items = [item for item in list(retrieval_evidence_items or []) if isinstance(item, Mapping)]
    if evidence_items:
        summary["evidence_count"] = len(evidence_items)
        store_ids = sorted({
            str(item.get("store_id") or item.get("source_type") or item.get("kind") or "").strip()
            for item in evidence_items
            if str(item.get("store_id") or item.get("source_type") or item.get("kind") or "").strip()
        })
        if store_ids:
            summary["evidence_store_ids"] = store_ids
    return summary


__all__ = [
    "_format_template_text",
    "_load_template_mapping",
    "_mapping_section",
    "_mapping_text",
    "_parse_bool_env",
    "_sse",
    "_airlock_sanitize_input",
    "_airlock_sanitize_output",
    "_build_reply_preview",
    "_coerce_trace_mapping",
    "_coerce_trace_score",
    "_coerce_trace_bool",
    "_normalize_trace_stage_meta",
    "_resolve_envelope_confidence",
    "_build_trace_output_digest",
    "_detect_trace_fallback_used",
    "_summarize_motive_state_for_trace",
    "_summarize_conversation_interpretation_for_trace",
    "_summarize_reply_plan_for_trace",
    "_summarize_tactic_ledger_for_trace",
    "_summarize_topic_ledger_for_trace",
    "_summarize_affect_state_for_trace",
    "_summarize_relationship_state_for_trace",
    "_summarize_drive_state_for_trace",
    "_summarize_emotion_context_for_trace",
    "_summarize_persona_resident_memory_for_trace",
]
