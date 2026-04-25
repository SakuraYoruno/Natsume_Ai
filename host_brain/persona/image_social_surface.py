from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from host_brain.persona.fallback_policy import (
    FallbackRequest,
    build_fallback_reply,
    is_forbidden_surface_text,
)


_PUNCT_RE = re.compile(r"[\s\u3000。！？?!，,、~～…；;：:\"“”'‘’（）()\[\]【】]+")
_OLD_TEMPLATE_MARKERS = (
    "突然这么可爱",
    "想让我哄",
    "想跟我撒娇",
    "委屈成这样",
    "一下子软成这样",
    "情绪这么低",
    "软乎乎的表情",
    "拿这个小表情",
    "拿这种软乎乎",
    "闹别扭",
    "会错意",
    "多想了一点",
    "怎么突然丢",
    "突然发这个给我看",
    "突然这个样子",
)
_VAGUE_SHAPES = (
    "怎么啦",
    "怎么了",
    "想让我",
    "是不是想",
    "是想",
)
_SELF_AS_IMAGE_RE = re.compile(r"(看我|我今天|我是不是|我可不可|我可爱|我萌|我好看).{0,8}(可爱|萌|好看)")


def compact_image_social_text(raw_text: str) -> str:
    return _PUNCT_RE.sub("", str(raw_text or "").strip())


def image_social_observation_text(multimodal: Mapping[str, Any] | None) -> str:
    payload = dict(multimodal or {})
    parts: list[str] = []
    for key in ("summary_for_chat", "core_observation", "visible_text", "artifact_type"):
        value = str(payload.get(key) or "").strip()
        if value:
            parts.append(value)
    emotion = "、".join(str(item).strip() for item in list(payload.get("emotion_signals") or []) if str(item).strip())
    if emotion:
        parts.insert(0, emotion)
    return "；".join(dict.fromkeys(parts))


def image_social_anchor_terms(multimodal: Mapping[str, Any] | None) -> list[str]:
    observation = image_social_observation_text(multimodal)
    compact = compact_image_social_text(observation)
    anchors: list[str] = []
    for marker in (
        "猫耳",
        "灰发",
        "蓝眼",
        "脸颊",
        "特写",
        "动漫",
        "表情",
        "可爱",
        "卖萌",
        "软萌",
        "哭",
        "泪",
        "委屈",
        "生气",
        "无语",
        "笑",
        "开心",
    ):
        if marker in compact and marker not in anchors:
            anchors.append(marker)
    return anchors[:5]


def is_image_social_surface_sample_disallowed(text: str) -> bool:
    compact = compact_image_social_text(text)
    if not compact:
        return True
    if is_forbidden_surface_text(text):
        return True
    if len(compact) <= 1:
        return True
    if any(marker in compact for marker in _OLD_TEMPLATE_MARKERS):
        return True
    if compact.startswith(("怎么啦", "怎么了")):
        return True
    if any(marker in compact for marker in _VAGUE_SHAPES) and compact.endswith("吗"):
        return True
    if _SELF_AS_IMAGE_RE.search(compact):
        return True
    if "这张图" in compact and any(marker in compact for marker in ("内容大概是", "我看到的内容", "画面里")):
        return True
    return False


def candidate_uses_image_observation(candidate_text: str, multimodal: Mapping[str, Any] | None) -> bool:
    compact = compact_image_social_text(candidate_text)
    if not compact:
        return False
    if any(marker in compact for marker in ("这张", "这图", "这个表情", "小表情", "表情包", "猫耳", "脸", "眼睛")):
        return True
    anchors = image_social_anchor_terms(multimodal)
    if anchors and any(anchor in compact for anchor in anchors):
        return True
    return not anchors


def build_emergency_image_social_reply(multimodal: Mapping[str, Any] | None) -> str:
    result = build_fallback_reply(
        FallbackRequest(
            reason_code="image_social_surface_unavailable",
            primary_topic="emoji_social_reply",
            response_form_reason="image_social_reply",
            observation=image_social_observation_text(multimodal),
        )
    )
    return result.text


__all__ = [
    "build_emergency_image_social_reply",
    "candidate_uses_image_observation",
    "compact_image_social_text",
    "image_social_anchor_terms",
    "image_social_observation_text",
    "is_image_social_surface_sample_disallowed",
]
