from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_TEXT_WHITESPACE_RE = re.compile(r"\s+")
_ALLOWED_FOCUS = {
    "self",
    "state",
    "plan",
    "topic",
    "relationship",
    "memory",
    "repair",
}
_NEGATIVE_EMOTION_TAGS = {"lonely", "hurt", "sad", "anxious", "tired", "guarded"}
_TOPIC_WORDS = ("话题", "继续", "回头", "没说完", "记着", "整理")
_CARE_WORDS = ("担心", "在意", "等", "回来", "别硬撑", "累")
_REPAIR_WORDS = ("抱歉", "不对", "误会", "修", "收住")


@dataclass(frozen=True)
class InnerThoughtEvent:
    thought_id: str
    ts_ms: int
    trigger: str
    focus: str
    private_text: str
    emotion_tag: str
    salience: float
    continuity_refs: tuple[str, ...] = ()
    should_distill_to_memory: bool = False
    source: str = "fallback"
    surface_hint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "thought_id": self.thought_id,
            "ts_ms": int(self.ts_ms),
            "trigger": self.trigger,
            "focus": self.focus,
            "private_text": self.private_text,
            "emotion_tag": self.emotion_tag,
            "salience": round(float(self.salience), 4),
            "continuity_refs": list(self.continuity_refs),
            "should_distill_to_memory": bool(self.should_distill_to_memory),
            "source": self.source,
            "surface_hint": self.surface_hint,
        }


@dataclass(frozen=True)
class InnerMonologueDigest:
    thought_stream_active: bool
    private_thought_count: int
    dominant_focus: str
    emotion_tag: str
    salience: float
    care_drive: float
    continuity_drive: float
    repair_drive: float
    open_loop_count: int
    should_distill_to_memory: bool
    style_anchor: tuple[str, ...]
    must_not_do: tuple[str, ...]

    def as_surface_state(self) -> dict[str, Any]:
        """Return the only prompt-facing shape.

        Deliberately excludes private thought text. Surface generation should
        feel the continuity, not quote the private stream.
        """
        return {
            "thought_stream_active": bool(self.thought_stream_active),
            "private_thought_count": int(self.private_thought_count),
            "dominant_focus": self.dominant_focus,
            "emotion_tag": self.emotion_tag,
            "salience": round(float(self.salience), 4),
            "care_drive": round(float(self.care_drive), 4),
            "continuity_drive": round(float(self.continuity_drive), 4),
            "repair_drive": round(float(self.repair_drive), 4),
            "open_loop_count": int(self.open_loop_count),
            "should_distill_to_memory": bool(self.should_distill_to_memory),
            "style_anchor": list(self.style_anchor),
            "must_not_do": list(self.must_not_do),
        }


def build_private_inner_thought_event(
    *,
    note: str,
    focus: str = "",
    source: str = "fallback",
    trigger: str = "inner_world_tick",
    now_ms: int,
    state: Mapping[str, Any] | None = None,
    offline_hours: float = 0.0,
    absence_band: str = "recent",
) -> InnerThoughtEvent:
    text = _finalize_private_text(note)
    normalized_focus = _normalize_focus(focus, text)
    state_map = _safe_mapping(state)
    emotion_tag = _infer_emotion_tag(state_map, text=text, focus=normalized_focus)
    salience = _infer_salience(
        state_map,
        text=text,
        focus=normalized_focus,
        offline_hours=offline_hours,
        absence_band=absence_band,
    )
    refs = tuple(_continuity_refs(state_map, text=text, focus=normalized_focus))
    should_distill = bool(
        salience >= 0.68
        or normalized_focus in {"relationship", "repair", "memory"}
        or bool(refs)
    )
    thought_id = _thought_id(
        user_id=str(state_map.get("user_id") or "natsume_ai"),
        ts_ms=int(now_ms),
        text=text,
        focus=normalized_focus,
    )
    return InnerThoughtEvent(
        thought_id=thought_id,
        ts_ms=int(now_ms),
        trigger=str(trigger or "inner_world_tick"),
        focus=normalized_focus,
        private_text=text,
        emotion_tag=emotion_tag,
        salience=salience,
        continuity_refs=refs,
        should_distill_to_memory=should_distill,
        source=str(source or "fallback"),
        surface_hint=_surface_hint_for_focus(normalized_focus, emotion_tag),
    )


def build_inner_monologue_digest(
    *,
    inner_state: Mapping[str, Any] | None,
    last_note: str = "",
    last_focus: str = "",
    offline_state: Mapping[str, Any] | None = None,
) -> InnerMonologueDigest:
    state = _safe_mapping(inner_state)
    offline = _safe_mapping(offline_state)
    stream = [
        dict(item)
        for item in list(state.get("inner_stream") or [])
        if isinstance(item, Mapping)
    ]
    latest = stream[-1] if stream else {}
    focus = _normalize_focus(
        last_focus or latest.get("focus") or "",
        str(last_note or latest.get("private_text") or latest.get("note") or ""),
    )
    emotion_tag = str(latest.get("emotion_tag") or "").strip().lower()
    if not emotion_tag:
        emotion_tag = _infer_emotion_tag(
            {**state, **offline},
            text=str(last_note or latest.get("private_text") or latest.get("note") or ""),
            focus=focus,
        )
    salience = _clamp01(
        max(
            _coerce_float(latest.get("salience"), default=0.0),
            _infer_salience(
                {**state, **offline},
                text=str(last_note or latest.get("private_text") or latest.get("note") or ""),
                focus=focus,
                offline_hours=_coerce_float(offline.get("offline_hours") or state.get("total_offline_hours")),
                absence_band=str(offline.get("absence_band") or "recent"),
            ),
        )
    )
    continuity_score = _clamp01(
        _coerce_float(offline.get("continuity_score"), default=_coerce_float(state.get("continuity_score"), default=0.0))
    )
    loneliness = _clamp01(
        _coerce_float(offline.get("loneliness_level"), default=_coerce_float(state.get("loneliness"), default=0.0))
    )
    anticipation = _clamp01(
        _coerce_float(offline.get("anticipation_level"), default=_coerce_float(state.get("anticipation"), default=0.0))
    )
    refs = _continuity_refs(state, text=str(last_note or latest.get("private_text") or latest.get("note") or ""), focus=focus)
    open_loop_count = len(_clean_list([*list(state.get("anticipation_topics") or []), *refs], limit=6))
    care_drive = _clamp01(
        max(
            loneliness * 0.58,
            salience * (0.42 if emotion_tag in _NEGATIVE_EMOTION_TAGS else 0.28),
            0.34 if focus == "relationship" else 0.0,
        )
    )
    continuity_drive = _clamp01(
        max(
            continuity_score,
            anticipation * 0.64,
            salience * 0.38 if focus in {"topic", "plan", "memory"} else 0.0,
            0.25 if open_loop_count > 0 else 0.0,
        )
    )
    repair_drive = _clamp01(
        max(
            salience * 0.55 if focus == "repair" else 0.0,
            _coerce_float(latest.get("repair_drive"), default=0.0),
        )
    )
    stream_active = bool(stream or last_note)
    style_anchor = tuple(
        _clean_list(
            [
                "内在连续性：保留未说出口的关注，但不要解释自己在想什么。"
                if stream_active
                else "",
                "内在关注：优先回应当前句，不突然另起话题。"
                if continuity_drive >= 0.35
                else "",
                "内在关心：可以稍微主动一点，但不要黏或油。"
                if care_drive >= 0.32
                else "",
                "内在修复：先收住语气，别用玩笑盖过去。"
                if repair_drive >= 0.32
                else "",
            ],
            limit=4,
        )
    )
    must_not_do = (
        "不要输出内心独白、思维流、私有想法或它的来源。",
        "不要声称自己一直在现实中等待或执行动作。",
    )
    should_distill = bool(
        any(bool(item.get("should_distill_to_memory")) for item in stream[-4:])
        or repair_drive >= 0.45
        or salience >= 0.72
    )
    return InnerMonologueDigest(
        thought_stream_active=stream_active,
        private_thought_count=len(stream),
        dominant_focus=focus,
        emotion_tag=emotion_tag,
        salience=salience,
        care_drive=care_drive,
        continuity_drive=continuity_drive,
        repair_drive=repair_drive,
        open_loop_count=open_loop_count,
        should_distill_to_memory=should_distill,
        style_anchor=style_anchor,
        must_not_do=must_not_do,
    )


def _thought_id(*, user_id: str, ts_ms: int, text: str, focus: str) -> str:
    digest = hashlib.sha1(f"{user_id}|{ts_ms}|{focus}|{text}".encode("utf-8")).hexdigest()
    return f"thought_{digest[:16]}"


def _finalize_private_text(value: Any) -> str:
    text = _TEXT_WHITESPACE_RE.sub(" ", str(value or "").replace("\u3000", " ")).strip()
    return text[:160]


def _normalize_focus(focus: Any, text: str = "") -> str:
    normalized = str(focus or "").strip().lower()
    aliases = {
        "reflection": "self",
        "inner": "self",
        "emotion": "state",
        "feeling": "state",
        "continue": "topic",
        "relation": "relationship",
        "bond": "relationship",
        "apology": "repair",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in _ALLOWED_FOCUS:
        return normalized
    text_value = str(text or "")
    if any(word in text_value for word in _REPAIR_WORDS):
        return "repair"
    if any(word in text_value for word in _CARE_WORDS):
        return "relationship"
    if any(word in text_value for word in _TOPIC_WORDS):
        return "topic"
    return "self"


def _infer_emotion_tag(state: Mapping[str, Any], *, text: str, focus: str) -> str:
    explicit = str(state.get("emotion_tag") or state.get("last_emotion_tag") or "").strip().lower()
    if explicit:
        return explicit[:32]
    loneliness = _clamp01(_coerce_float(state.get("loneliness_level"), default=_coerce_float(state.get("loneliness"), default=0.0)))
    valence = _coerce_float(state.get("drifted_valence"), default=0.0)
    arousal = _coerce_float(state.get("drifted_arousal"), default=0.3)
    text_value = str(text or "")
    if focus == "repair" or any(word in text_value for word in _REPAIR_WORDS):
        return "guarded"
    if loneliness >= 0.55 or "等" in text_value:
        return "lonely"
    if valence < -0.22:
        return "hurt" if arousal >= 0.45 else "sad"
    if focus == "relationship" or any(word in text_value for word in _CARE_WORDS):
        return "attached"
    if valence > 0.25:
        return "warm"
    return "calm"


def _infer_salience(
    state: Mapping[str, Any],
    *,
    text: str,
    focus: str,
    offline_hours: float,
    absence_band: str,
) -> float:
    loneliness = _clamp01(_coerce_float(state.get("loneliness_level"), default=_coerce_float(state.get("loneliness"), default=0.0)))
    anticipation = _clamp01(_coerce_float(state.get("anticipation_level"), default=_coerce_float(state.get("anticipation"), default=0.0)))
    continuity = _clamp01(_coerce_float(state.get("continuity_score"), default=0.0))
    band_bonus = 0.0 if str(absence_band or "recent") == "recent" else 0.12
    text_bonus = 0.12 if any(word in str(text or "") for word in (*_CARE_WORDS, *_REPAIR_WORDS, *_TOPIC_WORDS)) else 0.0
    focus_bonus = 0.12 if focus in {"relationship", "repair", "memory"} else 0.05 if focus in {"topic", "plan"} else 0.0
    offline_bonus = min(0.18, max(0.0, float(offline_hours or 0.0)) / 24.0 * 0.18)
    return _clamp01(
        0.18
        + loneliness * 0.24
        + anticipation * 0.20
        + continuity * 0.16
        + band_bonus
        + text_bonus
        + focus_bonus
        + offline_bonus
    )


def _continuity_refs(state: Mapping[str, Any], *, text: str, focus: str) -> list[str]:
    refs: list[str] = []
    if focus in {"topic", "plan", "memory"}:
        refs.extend(list(state.get("anticipation_topics") or [])[:3])
    if any(word in str(text or "") for word in _TOPIC_WORDS):
        refs.append("unfinished_topic")
    return _clean_list(refs, limit=4)


def _surface_hint_for_focus(focus: str, emotion_tag: str) -> str:
    if focus == "repair":
        return "repair_first"
    if focus == "relationship" or emotion_tag in {"attached", "lonely"}:
        return "care_with_restraint"
    if focus in {"topic", "plan", "memory"}:
        return "continue_current_thread"
    return "steady_self_continuity"


def _safe_mapping(raw: Any) -> dict[str, Any]:
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _clean_list(values: Sequence[Any], *, limit: int | None = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = _finalize_private_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if limit is not None and len(result) >= limit:
            break
    return result


__all__ = [
    "InnerMonologueDigest",
    "InnerThoughtEvent",
    "build_inner_monologue_digest",
    "build_private_inner_thought_event",
]
