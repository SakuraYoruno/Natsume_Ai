"""chat_context_memory.py — 记忆/人格信号子模块。

从 chat_context_builder.py 拆分，负责记忆召回信号的构建与人格表达提示的应用。
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from host_brain.app.prompt_builder import _truncate_for_prompt

logger = logging.getLogger(__name__)

_LOW_SIGNAL_PERSONA_INTENTS: frozenset[str] = frozenset({"phatic", "affection"})
_SHORT_CHAT_PERSONA_SCORE_FLOOR = 0.12


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

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


def _load_runtime_guidance_templates() -> dict[str, Any]:
    try:
        from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged  # noqa: PLC0415
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
            return {"absence_band": absence_band, "reconnect_style": reconnect_style}
    except Exception:
        pass
    return {"absence_band": "recent", "reconnect_style": "soft_resume"}


# ---------------------------------------------------------------------------
# 记忆信号构建
# ---------------------------------------------------------------------------

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
        guidance_parts.append(_template_text(runtime_guidance_templates, "memory_signal_guidance", "suppressed"))
    if avg_distortion >= 0.35:
        recall_bias = "uncertain"
        guidance_parts.append(_template_text(runtime_guidance_templates, "memory_signal_guidance", "distortion_high"))
    elif distorted_count > 0:
        guidance_parts.append(_template_text(runtime_guidance_templates, "memory_signal_guidance", "distortion_low"))
    if protective_recoil_count > 0:
        if recall_bias == "neutral":
            recall_bias = "guarded"
        guidance_parts.append(_template_text(runtime_guidance_templates, "memory_signal_guidance", "protective_recoil"))
    if (shared_history_resonance_count > 0 and protective_recoil_count <= 0
            and suppressed_count <= 0 and avg_distortion < 0.35):
        if recall_bias == "neutral":
            recall_bias = "warm"
        guidance_parts.append(_template_text(runtime_guidance_templates, "memory_signal_guidance", "shared_history"))
    if dominant_emotion in warm_tags and suppressed_count <= 0:
        if recall_bias == "neutral":
            recall_bias = "warm"
        guidance_parts.append(_template_text(runtime_guidance_templates, "memory_signal_guidance", "warm_emotion"))
    elif dominant_emotion in fragile_tags:
        if recall_bias == "neutral":
            recall_bias = "fragile"
        guidance_parts.append(_template_text(runtime_guidance_templates, "memory_signal_guidance", "fragile_emotion"))

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
        evidence_rows.append({
            "content": content,
            "emotion_color_tag": emotion_color_tag or None,
            "suppression_flag": suppression_flag,
            "distortion_level": distortion_level,
            "source_ref": str(row.get("source_ref") or "").strip() or None,
            "evidence_ref": str(row.get("evidence_ref") or "").strip() or None,
            "kind": str(row.get("kind") or "episodic").strip() or "episodic",
        })
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
        "warm": "偏温暖承接", "guarded": "偏克制试探",
        "uncertain": "偏留白保守", "fragile": "偏脆弱照看", "neutral": "偏中性回忆",
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


# ---------------------------------------------------------------------------
# 人格表达提示应用
# ---------------------------------------------------------------------------

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
        motive_guidance.append(_template_text(runtime_guidance_templates, "motive_guidance", "care"))
    if continuity_drive >= 0.68 and continuity_drive >= retreat_drive:
        follow_up_tendency = max(follow_up_tendency, 0.12)
        motive_guidance.append(_template_text(runtime_guidance_templates, "motive_guidance", "continuity"))
    if retreat_drive >= 0.72 and retreat_drive >= active_drive + 0.1:
        tone = "gentle"
        follow_up_tendency = min(follow_up_tendency, 0.0)
        echo_weight_modifier = min(echo_weight_modifier, 0.02)
        if not pace_hint:
            pace_hint = "slow"
        motive_guidance.append(_template_text(runtime_guidance_templates, "motive_guidance", "retreat"))

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
        offline_guidance.append(_template_text(runtime_guidance_templates, "offline_guidance", "6h_plus"))
    elif absence_band == "24h_plus":
        follow_up_tendency = max(follow_up_tendency, 0.08)
        echo_weight_modifier = max(echo_weight_modifier, 0.06)
        if max(loneliness, anticipation) >= 0.35:
            tone = "caring"
        if not pace_hint:
            pace_hint = "steady"
        offline_guidance.append(_template_text(runtime_guidance_templates, "offline_guidance", "24h_plus"))
    elif absence_band == "72h_plus":
        tone = "caring"
        follow_up_tendency = max(follow_up_tendency, 0.12)
        echo_weight_modifier = max(echo_weight_modifier, 0.10)
        if not pace_hint:
            pace_hint = "slow"
        offline_guidance.append(_template_text(runtime_guidance_templates, "offline_guidance", "72h_plus"))
    elif absence_band == "7d_plus":
        tone = "caring"
        follow_up_tendency = max(follow_up_tendency, 0.18)
        echo_weight_modifier = max(echo_weight_modifier, 0.14)
        if not pace_hint:
            pace_hint = "slow"
        offline_guidance.append(_template_text(runtime_guidance_templates, "offline_guidance", "7d_plus"))

    if loneliness >= 0.55:
        echo_weight_modifier = max(echo_weight_modifier, 0.08)
        offline_guidance.append(_template_text(runtime_guidance_templates, "offline_guidance", "loneliness"))
    elif anticipation >= 0.6 and absence_band in {"24h_plus", "72h_plus", "7d_plus"}:
        offline_guidance.append(_template_text(runtime_guidance_templates, "offline_guidance", "anticipation"))

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
        activity_guidance.append(_template_text(runtime_guidance_templates, "activity_guidance", "wakeup"))
    elif (is_gaming or recent_event_type == "gaming_detected") and recent_event_age_s <= 900.0:
        if tone == "gentle":
            tone = "playful"
        follow_up_tendency = max(follow_up_tendency, 0.05)
        echo_weight_modifier = max(echo_weight_modifier, 0.04)
        if not pace_hint:
            pace_hint = "brisk"
        activity_guidance.append(_template_text(runtime_guidance_templates, "activity_guidance", "gaming_detected"))
    elif recent_event_type == "gaming_exited" and recent_event_age_s <= 600.0:
        tone = "caring" if tone == "gentle" else tone
        follow_up_tendency = min(max(follow_up_tendency, 0.03), 0.08)
        echo_weight_modifier = max(echo_weight_modifier, 0.05)
        if not pace_hint:
            pace_hint = "steady"
        activity_guidance.append(_template_text(runtime_guidance_templates, "activity_guidance", "gaming_exited"))
    elif idle_minutes >= 20.0:
        if tone == "playful":
            tone = "gentle"
        follow_up_tendency = min(max(follow_up_tendency, 0.02), 0.06)
        echo_weight_modifier = max(echo_weight_modifier, 0.04)
        if not pace_hint:
            pace_hint = "steady"
        activity_guidance.append(_template_text(runtime_guidance_templates, "activity_guidance", "idle_reentry"))

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
        for tag in raw_tags if str(tag or "").strip()
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
        scene_guidance.append(_template_text(runtime_guidance_templates, "scene_guidance", "focus"))
    elif scene_tags & {"gaming", "gameplay", "battle", "lobby"}:
        if tone == "gentle":
            tone = "playful"
        follow_up_tendency = max(follow_up_tendency, 0.04)
        echo_weight_modifier = max(echo_weight_modifier, 0.03)
        if not pace_hint:
            pace_hint = "brisk"
        scene_guidance.append(_template_text(runtime_guidance_templates, "scene_guidance", "gaming"))

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
        mode_guidance.append(_template_text(runtime_guidance_templates, "mode_guidance", "focus"))
    elif sys_mode == "gaming":
        if tone == "gentle":
            tone = "playful"
        follow_up_tendency = max(follow_up_tendency, 0.06)
        echo_weight_modifier = max(echo_weight_modifier, 0.04)
        if not pace_hint:
            pace_hint = "brisk"
        mode_guidance.append(_template_text(runtime_guidance_templates, "mode_guidance", "gaming"))
    elif sys_mode == "sleep":
        tone = "gentle"
        follow_up_tendency = min(follow_up_tendency, 0.0)
        echo_weight_modifier = min(echo_weight_modifier, 0.02)
        pace_hint = "slow"
        mode_guidance.append(_template_text(runtime_guidance_templates, "mode_guidance", "sleep"))
    elif sys_mode in {"maintenance", "locked"}:
        tone = "gentle"
        follow_up_tendency = min(follow_up_tendency, 0.0)
        echo_weight_modifier = min(echo_weight_modifier, 0.02)
        if not pace_hint:
            pace_hint = "steady"
        mode_guidance.append(_template_text(runtime_guidance_templates, "mode_guidance", "minimal"))

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

    warm_tags = {"NOSTALGIC", "WARM", "GRATEFUL", "LOVING", "TOUCHED", "HAPPY"}
    fragile_tags = {"SAD", "HURT", "LONELY", "ANXIOUS", "SCARED", "DEPRESSED", "MELANCHOLY"}

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
    memory_guidance: list[str] = []
    runtime_guidance_templates = _load_runtime_guidance_templates()

    if suppressed_count > 0 or recall_bias == "guarded":
        follow_up_tendency = min(follow_up_tendency, 0.03)
        echo_weight_modifier = min(echo_weight_modifier, 0.05)
        if not pace_hint:
            pace_hint = "steady"
        memory_guidance.append(_template_text(runtime_guidance_templates, "memory_hint_guidance", "suppressed"))
    if avg_distortion >= 0.35 or recall_bias == "uncertain":
        follow_up_tendency = min(follow_up_tendency, 0.04)
        memory_guidance.append(_template_text(runtime_guidance_templates, "memory_hint_guidance", "uncertain"))
    elif distorted_count > 0:
        memory_guidance.append(_template_text(runtime_guidance_templates, "memory_hint_guidance", "distorted"))
    if protective_recoil_count > 0:
        follow_up_tendency = min(follow_up_tendency, 0.02)
        echo_weight_modifier = min(echo_weight_modifier, 0.04)
        if not pace_hint:
            pace_hint = "steady"
        memory_guidance.append(_template_text(runtime_guidance_templates, "memory_hint_guidance", "protective_recoil"))
    if dominant_emotion in warm_tags and suppressed_count <= 0 and avg_distortion < 0.35:
        tone = "caring" if tone == "gentle" else tone
        follow_up_tendency = max(follow_up_tendency, 0.04)
        echo_weight_modifier = max(echo_weight_modifier, 0.05)
        memory_guidance.append(_template_text(runtime_guidance_templates, "memory_hint_guidance", "warm"))
    elif dominant_emotion in fragile_tags:
        follow_up_tendency = min(follow_up_tendency, 0.02)
        memory_guidance.append(_template_text(runtime_guidance_templates, "memory_hint_guidance", "fragile"))
    if (shared_history_resonance_count > 0 and protective_recoil_count <= 0
            and suppressed_count <= 0 and avg_distortion < 0.35):
        tone = "caring" if tone == "gentle" else tone
        follow_up_tendency = max(follow_up_tendency, 0.08)
        echo_weight_modifier = max(echo_weight_modifier, 0.06)
        memory_guidance.append(_template_text(runtime_guidance_templates, "memory_hint_guidance", "shared_history"))

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


__all__ = [
    # 辅助函数（供 chat_context_retrieval.py 共享）
    "_coerce_non_negative_int",
    "_coerce_optional_float",
    "_clamp_float",
    "_coerce_optional_bool",
    "_load_runtime_guidance_templates",
    "_template_text",
    "_resolve_offline_reconnect_profile",
    # 记忆信号
    "_build_memory_persona_signal",
    "_build_memory_persona_evidence",
    "_build_memory_recall_summary",
    # 人格表达提示
    "_apply_motive_state_to_persona_expression_hint",
    "_apply_offline_state_to_persona_expression_hint",
    "_apply_activity_monitor_to_persona_expression_hint",
    "_apply_scene_awareness_to_persona_expression_hint",
    "_apply_runtime_mode_to_persona_expression_hint",
    "_apply_memory_persona_signal_to_persona_expression_hint",
]
