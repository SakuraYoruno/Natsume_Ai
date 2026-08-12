from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class ResponseFormClass(str, Enum):
    """渲染器实际需要处理的三类表层回复形态。"""
    TEXT = "text"
    VOICE = "voice"
    MEDIA = "media"


TEXT_RESPONSE_FORMS = frozenset({"", "text"})
VOICE_RESPONSE_FORMS = frozenset({"voice"})
PURE_MEDIA_RESPONSE_FORMS = frozenset({"sticker", "image", "gif"})
MIXED_MEDIA_RESPONSE_FORMS = frozenset({"text_plus_sticker", "text_plus_image", "text_plus_gif"})
MEDIA_RESPONSE_FORMS = PURE_MEDIA_RESPONSE_FORMS | MIXED_MEDIA_RESPONSE_FORMS
ALLOWED_RESPONSE_FORMS = TEXT_RESPONSE_FORMS | VOICE_RESPONSE_FORMS | MEDIA_RESPONSE_FORMS


def normalize_response_form(raw: str | None, *, default: str = "text") -> str:
    # 外部通道/旧客户端可能传入未知形态；只接受白名单别名，默认值也需二次校验，
    # 从而让后续渲染、质量门和投递层共享同一个 fail-safe 的文本回退。
    value = str(raw or "").strip().lower()
    aliases = {
        "": "",
        "text": "text",
        "voice": "voice",
        "sticker": "sticker",
        "gif": "gif",
        "image": "image",
        "text_plus_sticker": "text_plus_sticker",
        "text_plus_image": "text_plus_image",
        "text_plus_gif": "text_plus_gif",
    }
    normalized = aliases.get(value)
    if normalized is not None:
        return normalized or str(default or "text").strip().lower() or "text"
    fallback = str(default or "text").strip().lower() or "text"
    return fallback if fallback in ALLOWED_RESPONSE_FORMS else "text"


def classify_response_form(raw: str | None) -> ResponseFormClass:
    form = normalize_response_form(raw)
    if form in VOICE_RESPONSE_FORMS:
        return ResponseFormClass.VOICE
    if form in MEDIA_RESPONSE_FORMS:
        return ResponseFormClass.MEDIA
    return ResponseFormClass.TEXT


def is_voice_response_form(raw: str | None) -> bool:
    return classify_response_form(raw) == ResponseFormClass.VOICE


def is_media_response_form(raw: str | None) -> bool:
    return classify_response_form(raw) == ResponseFormClass.MEDIA


def is_pure_media_response_form(raw: str | None) -> bool:
    return normalize_response_form(raw) in PURE_MEDIA_RESPONSE_FORMS


def is_mixed_media_response_form(raw: str | None) -> bool:
    return normalize_response_form(raw) in MIXED_MEDIA_RESPONSE_FORMS


def response_form_requires_text(raw: str | None) -> bool:
    # voice 与“文字加媒体”都仍需可见文本；纯媒体才可不生成正文，避免通道层
    # 将混合形态误当成只投递附件的空回复。
    form = normalize_response_form(raw)
    return form in {"text", "voice", "text_plus_sticker", "text_plus_image", "text_plus_gif"}


def response_form_requires_media(raw: str | None) -> bool:
    return normalize_response_form(raw) in MEDIA_RESPONSE_FORMS


@dataclass(frozen=True)
class MediaReplyAction:
    """媒体投递所需的最小结构化动作，不把附件字段混入自然语言正文。"""
    response_form: str
    media_ref: str = ""
    reaction_id: str = ""
    content: str = ""
    raw_extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def has_media_payload(self) -> bool:
        # 支持历史字段名，但只检查是否存在明确载荷引用；reaction/content 本身不能
        # 证明有可投递媒体，防止模型选了媒体形态却只输出一段文字。
        raw = dict(self.raw_extra or {})
        return bool(
            str(self.media_ref or "").strip()
            or str(raw.get("media_ref") or raw.get("media_path") or "").strip()
            or str(raw.get("sticker") or raw.get("gif") or raw.get("image") or "").strip()
        )


def build_media_reply_action(
    *,
    response_form: str | None,
    media_ref: str = "",
    reaction_id: str = "",
    content: str = "",
    raw_extra: Mapping[str, Any] | None = None,
) -> MediaReplyAction:
    # 在单一入口归一化所有用户可见字段，保持适配器构造与运行时构造得到相同动作。
    return MediaReplyAction(
        response_form=normalize_response_form(response_form),
        media_ref=str(media_ref or "").strip(),
        reaction_id=str(reaction_id or "").strip(),
        content=str(content or "").strip(),
        raw_extra=dict(raw_extra or {}),
    )


def has_structured_media_payload(
    *,
    response_form: str | None,
    media_ref: str = "",
    reaction_id: str = "",
    content: str = "",
    raw_extra: Mapping[str, Any] | None = None,
) -> bool:
    if not response_form_requires_media(response_form):
        return False
    return build_media_reply_action(
        response_form=response_form,
        media_ref=media_ref,
        reaction_id=reaction_id,
        content=content,
        raw_extra=raw_extra,
    ).has_media_payload


def derive_render_profile_for_response_form(response_form: str | None) -> str:
    # profile 是质量/渲染策略选择，不是模型内容权限：媒体动作走结构化处理，语音
    # 使用语音文本规范，其余一律按中文文本守卫。
    form_class = classify_response_form(response_form)
    if form_class == ResponseFormClass.VOICE:
        return "zh_voice"
    if form_class == ResponseFormClass.MEDIA:
        return "media_action"
    return "zh_text"


__all__ = [
    "ALLOWED_RESPONSE_FORMS",
    "MEDIA_RESPONSE_FORMS",
    "MIXED_MEDIA_RESPONSE_FORMS",
    "PURE_MEDIA_RESPONSE_FORMS",
    "TEXT_RESPONSE_FORMS",
    "VOICE_RESPONSE_FORMS",
    "MediaReplyAction",
    "ResponseFormClass",
    "build_media_reply_action",
    "classify_response_form",
    "derive_render_profile_for_response_form",
    "has_structured_media_payload",
    "is_media_response_form",
    "is_mixed_media_response_form",
    "is_pure_media_response_form",
    "is_voice_response_form",
    "normalize_response_form",
    "response_form_requires_media",
    "response_form_requires_text",
]
