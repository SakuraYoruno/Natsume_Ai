from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from host_brain.core.runtime_helpers import _clean_text, _coerce_mapping as _safe_mapping


VTS_EMOTION_TAGS = frozenset(
    {
        "NEUTRAL",
        "HAPPY",
        "SAD",
        "ANGRY",
        "SURPRISED",
        "THINKING",
        "EXCITED",
        "CALM",
        "FRUSTRATED",
        "ANXIOUS",
        "LOVING",
        "EMBARRASSED",
        "CONFUSED",
        "BORED",
        "PROUD",
        "GRATEFUL",
    }
)


_FINE_EMOTION_ALIASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("hurt", "sulk", "sad", "lonely", "upset", "wounded", "委屈", "伤心", "受伤"), "SAD"),
    (("angry", "anger", "mad", "irritated", "pout", "frustrated", "生气", "不爽", "赌气"), "ANGRY"),
    (("embarrass", "shy", "fluster", "bashful", "害羞", "脸红"), "EMBARRASSED"),
    (("anxious", "worry", "worried", "afraid", "fear", "紧张", "担心", "害怕"), "ANXIOUS"),
    (("sleep", "sleepy", "tired", "calm", "soft", "gentle", "relieved", "困", "温柔", "松口气"), "CALM"),
    (("excited", "bright", "energetic", "surprise", "惊喜", "兴奋", "精神"), "EXCITED"),
    (("happy", "playful", "tease", "smug", "proud", "cute", "开心", "得意", "调侃"), "HAPPY"),
    (("love", "loving", "clingy", "affection", "亲近", "喜欢", "黏"), "LOVING"),
    (("bored", "无聊"), "BORED"),
    (("confused", "疑惑", "困惑"), "CONFUSED"),
    (("thinking", "思考"), "THINKING"),
    (("grateful", "感谢"), "GRATEFUL"),
)


def _clamp_intensity(raw: Any, *, default: float = 0.42) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return max(0.0, min(1.0, round(value, 3)))


def _normalize_standard_tag(raw: Any) -> str:
    tag = _clean_text(raw).upper()
    return tag if tag in VTS_EMOTION_TAGS else ""


def _map_fine_emotion(raw: Any) -> str:
    text = _clean_text(raw).lower()
    if not text:
        return ""
    standard = _normalize_standard_tag(text)
    if standard:
        return standard
    for aliases, tag in _FINE_EMOTION_ALIASES:
        if any(alias.lower() in text for alias in aliases):
            return tag
    return ""


@dataclass(frozen=True)
class ReplyPerformance:
    """First-class performance state consumed by Edge/VTS/Voice.

    `emotion_tag` is the transport-safe VTS enum. `raw_emotion` and
    `raw_action` keep the richer Galgame beat for audit without leaking it into
    user-visible text.
    """

    emotion_tag: str = "NEUTRAL"
    emotion_intensity: float = 0.42
    action: str = ""
    vts_expression_hint: str = ""
    source: str = ""
    raw_emotion: str = ""
    raw_action: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "emotion_tag", _normalize_standard_tag(self.emotion_tag) or "NEUTRAL")
        object.__setattr__(self, "emotion_intensity", _clamp_intensity(self.emotion_intensity))
        object.__setattr__(self, "action", _clean_text(self.action)[:80])
        object.__setattr__(self, "vts_expression_hint", _clean_text(self.vts_expression_hint)[:80])
        object.__setattr__(self, "source", _clean_text(self.source)[:80])
        object.__setattr__(self, "raw_emotion", _clean_text(self.raw_emotion)[:80])
        object.__setattr__(self, "raw_action", _clean_text(self.raw_action)[:80])

    def as_dict(self) -> dict[str, Any]:
        return {
            "emotion_tag": self.emotion_tag,
            "emotion_intensity": self.emotion_intensity,
            "action": self.action,
            "vts_expression_hint": self.vts_expression_hint,
            "source": self.source,
            "raw_emotion": self.raw_emotion,
            "raw_action": self.raw_action,
        }


def coerce_reply_performance(raw: ReplyPerformance | Mapping[str, Any] | None) -> ReplyPerformance:
    if isinstance(raw, ReplyPerformance):
        return raw
    payload = _safe_mapping(raw)
    return ReplyPerformance(
        emotion_tag=payload.get("emotion_tag") or payload.get("emotion") or "NEUTRAL",
        emotion_intensity=payload.get("emotion_intensity", payload.get("intensity", 0.42)),
        action=payload.get("action") or "",
        vts_expression_hint=payload.get("vts_expression_hint") or payload.get("expression_hint") or "",
        source=payload.get("source") or "",
        raw_emotion=payload.get("raw_emotion") or payload.get("emotion") or "",
        raw_action=payload.get("raw_action") or payload.get("action") or "",
    )


def normalize_reply_performance(
    *,
    emotion: Any = "",
    action: Any = "",
    intensity: Any = None,
    facts: Any = None,
    source: str = "",
) -> ReplyPerformance:
    fact_payload = facts.as_dict() if hasattr(facts, "as_dict") else _safe_mapping(facts)
    affect = _safe_mapping(fact_payload.get("affect_state"))
    emotional_act = _safe_mapping(fact_payload.get("emotional_act"))
    social_act = _safe_mapping(fact_payload.get("social_act"))

    raw_emotion = (
        _clean_text(emotion)
        or _clean_text(emotional_act.get("primary_emotion"))
        or _clean_text(affect.get("response_emotion"))
        or _clean_text(affect.get("emotion_tag"))
        or _clean_text(social_act.get("stance"))
    )
    emotion_tag = (
        _map_fine_emotion(raw_emotion)
        or _normalize_standard_tag(affect.get("emotion_tag"))
        or "NEUTRAL"
    )
    raw_action = _clean_text(action) or _clean_text(emotional_act.get("stance")) or _clean_text(social_act.get("act_kind"))
    resolved_intensity = intensity
    if resolved_intensity is None:
        resolved_intensity = emotional_act.get("intensity")
    if resolved_intensity is None:
        resolved_intensity = affect.get("intensity")

    return ReplyPerformance(
        emotion_tag=emotion_tag,
        emotion_intensity=_clamp_intensity(resolved_intensity, default=0.42),
        action=raw_action,
        vts_expression_hint=emotion_tag,
        source=source or "performance_normalizer",
        raw_emotion=raw_emotion,
        raw_action=raw_action,
    )


__all__ = [
    "ReplyPerformance",
    "VTS_EMOTION_TAGS",
    "coerce_reply_performance",
    "normalize_reply_performance",
]
