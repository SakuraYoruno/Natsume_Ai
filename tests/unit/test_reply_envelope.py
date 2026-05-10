from __future__ import annotations

from host_brain.app.reply_envelope import (
    FINAL_REPLY,
    NO_REPLY,
    ReplyEnvelope,
    can_cache,
    can_persist,
    can_send,
    envelope_from_commit,
)
from host_brain.app.reply_performance import ReplyPerformance


def test_final_reply_envelope_is_the_only_sendable_persistable_cacheable_kind() -> None:
    final = ReplyEnvelope.final_reply(text="哥哥，先接住你这一下。", source="surface")
    media_final = ReplyEnvelope.final_reply(
        text="",
        response_form="sticker",
        source="reaction_catalog",
        cacheable=False,
        media_ref="D:/stickers/cute.webp",
        reaction_id="cute-1",
    )
    no_reply = ReplyEnvelope.no_reply(reason_code="LLM_UNAVAILABLE")
    ack = ReplyEnvelope(kind="transport_ack", text="收到", sendable=True, persistable=True, cacheable=True)
    degraded_final = ReplyEnvelope(kind="final_reply", text="兜底", sendable=True, persistable=True, cacheable=True, degraded=True)

    assert final.kind == FINAL_REPLY
    assert can_send(final) and can_persist(final) and can_cache(final)
    assert media_final.kind == FINAL_REPLY
    assert can_send(media_final) and can_persist(media_final) and not can_cache(media_final)
    assert media_final.text == ""
    assert media_final.media_ref.endswith("cute.webp")
    assert no_reply.kind == NO_REPLY
    assert not can_send(no_reply) and not can_persist(no_reply) and not can_cache(no_reply)
    assert not can_send(ack) and not can_persist(ack) and not can_cache(ack)
    assert not can_send(degraded_final) and not can_persist(degraded_final) and not can_cache(degraded_final)


def test_pure_media_envelope_requires_structured_media_payload() -> None:
    text_labeled_as_sticker = ReplyEnvelope.final_reply(
        text="我在呀。",
        response_form="sticker",
        source="bad_media_contract",
        cacheable=False,
    )
    reaction_without_asset = ReplyEnvelope.final_reply(
        text="",
        response_form="sticker",
        source="bad_media_contract",
        cacheable=False,
        reaction_id="cute-1",
    )
    content_without_media_ref = ReplyEnvelope.final_reply(
        text="",
        response_form="sticker",
        source="reaction_catalog",
        cacheable=False,
        raw_extra={"content": "not-a-media-ref"},
    )

    assert text_labeled_as_sticker.kind == NO_REPLY
    assert not can_send(text_labeled_as_sticker)
    assert reaction_without_asset.kind == NO_REPLY
    assert not can_send(reaction_without_asset)
    assert content_without_media_ref.kind == NO_REPLY
    assert not can_send(content_without_media_ref)


def test_envelope_from_commit_rejects_empty_or_degraded_approved_text() -> None:
    approved = envelope_from_commit(decision="APPROVE", narrative="接到了。", source="full_model")
    media = envelope_from_commit(
        decision="APPROVE",
        narrative="",
        response_form="sticker",
        source="reaction_catalog",
        media_ref="D:/stickers/awkward.webp",
        cacheable=False,
    )
    empty = envelope_from_commit(decision="APPROVE", narrative="", reason_code="EMPTY")
    rejected = envelope_from_commit(decision="REJECT", narrative="不该发", reason_code="BLOCKED")
    degraded = envelope_from_commit(decision="APPROVE", narrative="兜底", degraded=True, reason_code="FALLBACK")

    assert approved.kind == FINAL_REPLY and can_send(approved)
    assert media.kind == FINAL_REPLY and can_send(media) and not can_cache(media)
    assert empty.kind == NO_REPLY and not can_send(empty)
    assert rejected.kind == NO_REPLY and not can_send(rejected)
    assert degraded.kind == NO_REPLY and not can_send(degraded)


def test_no_reply_preserves_response_form_for_observability() -> None:
    no_reply = ReplyEnvelope.no_reply(
        reason_code="QUALITY_REJECTED",
        source="chat_kernel_v2",
        response_form="voice",
    )
    rejected_voice = envelope_from_commit(
        decision="REJECT",
        narrative="",
        reason_code="BLOCKED",
        response_form="voice",
        source="quality_gate",
    )

    assert no_reply.kind == NO_REPLY
    assert no_reply.response_form == "voice"
    assert not can_send(no_reply)
    assert rejected_voice.kind == NO_REPLY
    assert rejected_voice.response_form == "voice"
    assert not can_send(rejected_voice)


def test_final_reply_serializes_first_class_performance() -> None:
    final = ReplyEnvelope.final_reply(
        text="别突然把我往外推啊。",
        source="chat_kernel_v2",
        performance=ReplyPerformance(
            emotion_tag="ANGRY",
            emotion_intensity=0.68,
            action="hands_on_hips",
            source="chat_kernel_v2",
            raw_emotion="hurt_sulk",
        ),
    )

    payload = final.as_dict()

    assert final.kind == FINAL_REPLY
    assert payload["performance"]["emotion_tag"] == "ANGRY"
    assert payload["performance"]["emotion_intensity"] == 0.68
    assert payload["performance"]["action"] == "hands_on_hips"
    assert can_send(final)
