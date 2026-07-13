from __future__ import annotations

from host_brain.persona.response_form_contract import (
    ResponseFormClass,
    classify_response_form,
    derive_render_profile_for_response_form,
    has_structured_media_payload,
    is_pure_media_response_form,
    normalize_response_form,
    response_form_requires_media,
    response_form_requires_text,
)


def test_response_form_classifies_text_voice_and_media() -> None:
    assert classify_response_form("") == ResponseFormClass.TEXT
    assert classify_response_form("text") == ResponseFormClass.TEXT
    assert classify_response_form("voice") == ResponseFormClass.VOICE
    assert classify_response_form("sticker") == ResponseFormClass.MEDIA
    assert classify_response_form("gif") == ResponseFormClass.MEDIA
    assert classify_response_form("text_plus_gif") == ResponseFormClass.MEDIA


def test_response_form_derives_render_profile_from_single_voice_switch() -> None:
    assert derive_render_profile_for_response_form("") == "zh_text"
    assert derive_render_profile_for_response_form("text") == "zh_text"
    assert derive_render_profile_for_response_form("voice") == "zh_voice"
    assert derive_render_profile_for_response_form("sticker") == "media_action"


def test_media_payload_contract_requires_structured_payload() -> None:
    assert is_pure_media_response_form("sticker")
    assert response_form_requires_media("sticker")
    assert response_form_requires_media("text_plus_sticker")
    assert response_form_requires_text("text_plus_sticker")

    assert has_structured_media_payload(response_form="sticker", media_ref="D:/stickers/cute.webp")
    assert has_structured_media_payload(response_form="gif", raw_extra={"gif": "D:/stickers/cute.gif"})
    assert not has_structured_media_payload(response_form="sticker", reaction_id="cute-1")
    assert not has_structured_media_payload(response_form="text")
