from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping


_EXPLICIT_IMAGE_QUESTION_RE = re.compile(
    r"(这是什么|图里.*(什么|谁|哪|怎么)|上面.*(写|画|是)|写了什么|"
    r"什么意思|什么梗|表达什么|看懂|识别|内容是什么|what.*(this|image)|"
    r"read.*image|ocr)",
    re.IGNORECASE,
)
_MEDIA_ONLY_PLACEHOLDERS = {
    "",
    "[图片]",
    "[图片消息]",
    "[表情]",
    "[表情消息]",
    "[贴图]",
    "[贴图消息]",
    "[sticker]",
    "[emoji]",
    "[image]",
}

_EMOTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sad", ("委屈", "哭", "难过", "伤心", "泪", "可怜", "呜", "崩溃")),
    ("tease", ("逗", "调侃", "搞怪", "捉弄", "坏笑", "挑衅", "欠揍")),
    ("cute", ("可爱", "卖萌", "猫耳", "装乖", "软萌", "乖", "萌")),
    ("awkward", ("无语", "尴尬", "裂开", "社死", "别扭", "鼓脸", "气鼓鼓", "服了")),
    ("angry", ("生气", "恼", "怒", "不爽", "炸毛")),
    ("greeting", ("打招呼", "hello", "你好", "嗨", "挥手")),
    ("surprised", ("震惊", "惊讶", "吓", "愣住")),
)


def _normalize_text(raw: Any) -> str:
    return str(raw or "").strip()


def _normalize_types(raw: Any) -> tuple[str, ...]:
    values: list[str] = []
    for item in list(raw or []):
        normalized = _normalize_text(item).lower()
        if normalized and normalized not in values:
            values.append(normalized)
    return tuple(values)


def _to_float(raw: Any, default: float = 0.0) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return max(0.0, min(value, 1.0))


def _looks_media_only(user_text: str) -> bool:
    normalized = _normalize_text(user_text).lower()
    return normalized in {item.lower() for item in _MEDIA_ONLY_PLACEHOLDERS}


def _contains_explicit_image_question(user_text: str) -> bool:
    return bool(_EXPLICIT_IMAGE_QUESTION_RE.search(_normalize_text(user_text)))


def _derive_emotion(*, social_signal: str, emotion_signals: tuple[str, ...], visual_facts: str) -> str:
    haystack = " ".join(
        part
        for part in [social_signal, " ".join(emotion_signals), visual_facts]
        if _normalize_text(part)
    )
    for emotion, keywords in _EMOTION_KEYWORDS:
        if any(token in haystack for token in keywords):
            return emotion
    return "unknown"


@dataclass(frozen=True)
class MultimodalAct:
    """Structured multimodal social act, not a reply policy or final text."""

    input_kind: str = ""
    user_intent: str = ""
    emotion: str = "unknown"
    intensity: float = 0.0
    visual_facts: str = ""
    ocr_text: str = ""
    confidence: float = 0.0
    requires_text_answer: bool = True
    raw_signals: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_kind": self.input_kind,
            "user_intent": self.user_intent,
            "emotion": self.emotion,
            "intensity": float(self.intensity or 0.0),
            "visual_facts": self.visual_facts,
            "ocr_text": self.ocr_text,
            "confidence": float(self.confidence or 0.0),
            "requires_text_answer": bool(self.requires_text_answer),
            "raw_signals": dict(self.raw_signals or {}),
        }


def derive_multimodal_act(
    *,
    user_text: str | None = "",
    observations: Mapping[str, Any] | None = None,
) -> MultimodalAct:
    payload = dict(observations or {})
    observed_types = _normalize_types(payload.get("observed_types") or payload.get("modalities") or [])
    primary_modality = _normalize_text(
        payload.get("primary_modality")
        or payload.get("input_kind")
        or payload.get("artifact_type")
        or (observed_types[0] if observed_types else "")
    ).lower()
    artifact_type = _normalize_text(payload.get("artifact_type")).lower()
    input_kind = primary_modality or artifact_type or (observed_types[0] if observed_types else "")
    visual_facts = _normalize_text(
        payload.get("visual_facts")
        or payload.get("visual_summary")
        or payload.get("core_observation")
        or payload.get("summary_for_chat")
    )
    ocr_text = _normalize_text(payload.get("ocr_text") or payload.get("visible_text"))
    social_signal = _normalize_text(payload.get("social_signal") or payload.get("meme_intent"))
    emotion_signals = _normalize_types(payload.get("emotion_signals") or [])
    confidence = _to_float(payload.get("confidence"), 0.0)
    emotion = _derive_emotion(
        social_signal=social_signal,
        emotion_signals=emotion_signals,
        visual_facts=visual_facts,
    )
    explicit_question = _contains_explicit_image_question(user_text or "")
    media_only = _looks_media_only(user_text or "")

    is_sticker_like = input_kind in {"emoji", "sticker"} or artifact_type in {"emoji", "sticker", "meme"}
    is_social_image = (
        input_kind == "image"
        and bool(social_signal or emotion_signals or artifact_type in {"meme", "sticker", "emoji"})
    )

    if explicit_question:
        user_intent = "image_question"
        requires_text_answer = True
    elif is_sticker_like and media_only:
        user_intent = "social_reaction"
        requires_text_answer = False
    elif is_social_image and media_only:
        user_intent = "social_reaction"
        requires_text_answer = False
    elif input_kind == "image":
        user_intent = "visual_share"
        requires_text_answer = True
    elif is_sticker_like:
        user_intent = "social_reaction"
        requires_text_answer = False
    else:
        user_intent = ""
        requires_text_answer = True

    intensity = _to_float(
        payload.get("intensity")
        or payload.get("emotion_intensity")
        or (0.7 if emotion != "unknown" else 0.35),
        0.0,
    )
    return MultimodalAct(
        input_kind=input_kind,
        user_intent=user_intent,
        emotion=emotion,
        intensity=intensity,
        visual_facts=visual_facts,
        ocr_text=ocr_text,
        confidence=confidence,
        requires_text_answer=requires_text_answer,
        raw_signals={
            "observed_types": list(observed_types),
            "artifact_type": artifact_type,
            "social_signal": social_signal,
            "emotion_signals": list(emotion_signals),
            "explicit_question": bool(explicit_question),
            "media_only": bool(media_only),
        },
    )


def coerce_multimodal_act(raw: Any) -> MultimodalAct:
    if isinstance(raw, MultimodalAct):
        return raw
    payload = dict(raw or {}) if isinstance(raw, Mapping) else {}
    nested = payload.get("multimodal_act")
    if isinstance(nested, Mapping):
        payload = {**payload, **dict(nested)}
    return MultimodalAct(
        input_kind=_normalize_text(payload.get("input_kind") or payload.get("primary_modality")).lower(),
        user_intent=_normalize_text(payload.get("user_intent")),
        emotion=_normalize_text(payload.get("emotion") or "unknown").lower() or "unknown",
        intensity=_to_float(payload.get("intensity"), 0.0),
        visual_facts=_normalize_text(payload.get("visual_facts")),
        ocr_text=_normalize_text(payload.get("ocr_text")),
        confidence=_to_float(payload.get("confidence"), 0.0),
        requires_text_answer=bool(payload.get("requires_text_answer", True)),
        raw_signals=dict(payload.get("raw_signals") or {}),
    )


__all__ = [
    "MultimodalAct",
    "coerce_multimodal_act",
    "derive_multimodal_act",
]
