from __future__ import annotations

from host_brain.persona.fallback_policy import (
    FallbackRequest,
    SURFACE_CACHE_POLICY_VERSION,
    build_fallback_reply,
    compact_forbidden_text,
    is_degraded_reply_metadata,
    is_forbidden_surface_text,
    is_surface_cacheable_reply,
    is_transport_ack_metadata,
    metadata_message_kind,
)


def test_forbidden_surface_text_blocks_old_template_corpus() -> None:
    assert compact_forbidden_text("在。怎么了。") == "在怎么了"
    assert is_forbidden_surface_text("")
    assert is_forbidden_surface_text("唔。")
    assert is_forbidden_surface_text("哥哥，突然这么可爱，是想让我哄你吗？")
    assert is_forbidden_surface_text("哥哥，你真正想问什么？")
    assert is_forbidden_surface_text("哥哥，好嘛，那我就当自己刚才多想了一点。")
    assert not is_forbidden_surface_text("哥哥，猫耳那张我接住了。")


def test_metadata_kind_and_transport_ack_detection() -> None:
    assert metadata_message_kind({"message_kind": "FINAL_REPLY"}) == "final_reply"
    assert is_transport_ack_metadata({"message_kind": "transport_ack"})
    assert is_transport_ack_metadata({"reply_source": "quick_ack"})
    assert not is_transport_ack_metadata({"message_kind": "final_reply", "reply_source": "surface"})


def test_degraded_metadata_blocks_host_fallback_sources() -> None:
    assert is_degraded_reply_metadata({"fallback": True})
    assert is_degraded_reply_metadata({"degraded": True})
    assert is_degraded_reply_metadata({"reply_used_fallback": True})
    assert is_degraded_reply_metadata({"provider_name": "host"})
    assert is_degraded_reply_metadata({"model_id": "surface-emergency-fallback"})
    assert is_degraded_reply_metadata({"source": "fallback_policy"})
    assert not is_degraded_reply_metadata({"provider_name": "gemini", "model_id": "gemini-2.5-flash-lite"})


def test_surface_cacheable_reply_requires_final_non_degraded_text() -> None:
    assert is_surface_cacheable_reply(
        text="哥哥，猫耳那张我接住了。",
        metadata={"message_kind": "final_reply", "provider_name": "gemini", "model_id": "gemini-2.5-flash-lite"},
    )
    assert not is_surface_cacheable_reply(text="哥哥，你真正想问什么？")
    assert not is_surface_cacheable_reply(text="哥哥，猫耳那张我接住了。", source="transport_ack")
    assert not is_surface_cacheable_reply(text="哥哥，猫耳那张我接住了。", provider_name="host")
    assert not is_surface_cacheable_reply(text="哥哥，猫耳那张我接住了。", model_id="fallback-policy")
    assert not is_surface_cacheable_reply(text="哥哥，猫耳那张我接住了。", fallback=True)
    assert not is_surface_cacheable_reply(
        text="哥哥，猫耳那张我接住了。",
        metadata={"message_kind": "transport_ack"},
    )


def test_fallback_reply_is_degraded_and_topic_aware() -> None:
    assert SURFACE_CACHE_POLICY_VERSION.startswith("surface-v6")

    image = build_fallback_reply(FallbackRequest(primary_topic="emoji_social_reply", reason_code="x"))
    followup = build_fallback_reply(FallbackRequest(primary_topic="assistant_reply_followup", reason_code="y"))
    identity = build_fallback_reply(FallbackRequest(primary_topic="identity", reason_code="z"))
    generic = build_fallback_reply(FallbackRequest(primary_topic="other", reason_code="q"))
    ja_image = build_fallback_reply(
        FallbackRequest(primary_topic="emoji_social_followup", render_target_lang="ja", reason_code="ja-image")
    )
    ja_followup = build_fallback_reply(
        FallbackRequest(primary_topic="assistant_reply_followup", render_target_lang="ja", reason_code="ja-follow")
    )
    ja_generic = build_fallback_reply(FallbackRequest(render_target_lang="ja", reason_code="ja"))

    assert all(
        result.text == ""
        and result.sendable is False
        and result.persistable is False
        and result.cacheable is False
        and result.degraded
        for result in (image, followup, identity, generic, ja_image, ja_followup, ja_generic)
    )
