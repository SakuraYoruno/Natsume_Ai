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
    "猫猫精",
    "赖在你身边",
    "赖着",
    "考虑考虑",
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
_SELF_IMAGE_PARTICLE_RE = r"(?:吗|吧|哦|啦|呀|啊|嘛|呢)?"
_SELF_IMAGE_SUFFIX_RE = (
    rf"(?:的(?:样子|表情|感觉|模样|时候)|"
    rf"(?:死|惨|爆|坏|疯)了{_SELF_IMAGE_PARTICLE_RE}|"
    rf"(?:死|惨|爆|坏|疯){_SELF_IMAGE_PARTICLE_RE}(?![\u4e00-\u9fff])|"
    r"得(?:不行|要命|很)|"
    r"(?:吗|吧|哦|啦|呀|啊|嘛|呢|了)?(?![\u4e00-\u9fff]))"
)
_SELF_IMAGE_PREDICATE_RE = rf"(?:可爱|萌|好看){_SELF_IMAGE_SUFFIX_RE}"
_SELF_AS_IMAGE_RE = re.compile(
    rf"(看我.{{0,24}}{_SELF_IMAGE_PREDICATE_RE}|"
    rf"我是不是.{{0,24}}{_SELF_IMAGE_PREDICATE_RE}|"
    rf"我可不{_SELF_IMAGE_PREDICATE_RE}|"
    rf"(?:看|盯|盯着|又在看|在看).{{0,6}}我的(?:猫耳|耳朵|脸|眼睛|表情|样子|模样)|"
    rf"(?:我|人家).{{0,18}}(?:比|还要|更|最).{{0,16}}(?:可爱|萌|好看)|"
    rf"(?:我|人家)(?:今天|现在)?(?:也)?(?:很|超|好|太)?{_SELF_IMAGE_PREDICATE_RE})"
)
_IMAGE_SOCIAL_QUESTION_BACK_RE = re.compile(
    r"^(?:哥哥)?(?:你)?(?:是不是|是否|是不是也|是不是觉得|觉得|是觉得).{0,24}"
    r"(?:这张|这图|图里|猫猫|小猫|猫耳|表情|可爱|萌|好看).{0,12}(?:吗|嘛|呀|吧|呢)?$"
)
_WEAK_IMAGE_ANCHORS = {"可爱", "卖萌", "软萌", "萌", "表情"}
_VISUAL_REFERENCE_MARKERS = (
    "这张",
    "这图",
    "图里",
    "画面",
    "上面",
    "这里",
    "这个表情",
    "小表情",
    "表情包",
    "猫耳",
    "脸",
    "眼睛",
)


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
    if _IMAGE_SOCIAL_QUESTION_BACK_RE.search(compact):
        return True
    if "这张图" in compact and any(marker in compact for marker in ("内容大概是", "我看到的内容", "画面里")):
        return True
    return False


def candidate_uses_image_observation(candidate_text: str, multimodal: Mapping[str, Any] | None) -> bool:
    compact = compact_image_social_text(candidate_text)
    if not compact:
        return False
    if _SELF_AS_IMAGE_RE.search(compact):
        return False
    if any(marker in compact for marker in _VISUAL_REFERENCE_MARKERS):
        return True
    anchors = image_social_anchor_terms(multimodal)
    strong_anchors = [anchor for anchor in anchors if anchor not in _WEAK_IMAGE_ANCHORS]
    if strong_anchors and any(anchor in compact for anchor in strong_anchors):
        return True
    return False


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
