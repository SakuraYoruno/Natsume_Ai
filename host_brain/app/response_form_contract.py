from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class ResponseFormClass(str, Enum):
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
    form = normalize_response_form(raw)
    return form in {"text", "voice", "text_plus_sticker", "text_plus_image", "text_plus_gif"}


def response_form_requires_media(raw: str | None) -> bool:
    return normalize_response_form(raw) in MEDIA_RESPONSE_FORMS


@dataclass(frozen=True)
class MediaReplyAction:
    response_form: str
    media_ref: str = ""
    reaction_id: str = ""
    content: str = ""
    raw_extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def has_media_payload(self) -> bool:
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
    form_class = classify_response_form(response_form)
    if form_class == ResponseFormClass.VOICE:
        return "ja_voice"
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
