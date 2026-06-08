from __future__ import annotations

from host_brain.app.context.conversation_interpretation import ConversationInterpretation
from host_brain.app.context.relationship_state import RelationshipStateBlock
from host_brain.app.reply_modality_router import ReplyModalityRouter


def test_router_routes_pure_sticker_social_input_to_media_action() -> None:
    decision = ReplyModalityRouter().route(
        interpretation=ConversationInterpretation(literal_topic="emoji_social_reply"),
        relationship_state=RelationshipStateBlock(bond_stage="FRIEND"),
        multimodal={
            "primary_modality": "sticker",
            "multimodal_act": {
                "input_kind": "sticker",
                "user_intent": "social_reaction",
                "emotion": "awkward",
                "requires_text_answer": False,
            },
        },
    )

    assert decision.response_form == "sticker"
    assert decision.response_form_reason == "social_reaction"
    assert decision.allow_form_only_reply is True
    assert decision.requires_media_asset is True
    assert decision.allow_text_model is False
    assert decision.render_profile == "media_action"


def test_router_keeps_social_image_textual_when_it_needs_a_natural_reply() -> None:
    decision = ReplyModalityRouter().route(
        interpretation=ConversationInterpretation(literal_topic="emoji_social_reply"),
        relationship_state=RelationshipStateBlock(bond_stage="FRIEND"),
        multimodal={
            "primary_modality": "image",
            "observed_types": ["image"],
        },
    )

    assert decision.response_form == "text"
    assert decision.response_form_reason == "image_social_reply"
    assert decision.allow_text_model is True


def test_router_uses_text_for_explicit_visual_question() -> None:
    decision = ReplyModalityRouter().route(
        interpretation=ConversationInterpretation(literal_topic="literal_description"),
        relationship_state=RelationshipStateBlock(bond_stage="FRIEND"),
        multimodal={
            "primary_modality": "image",
            "multimodal_act": {
                "input_kind": "image",
                "user_intent": "image_question",
                "requires_text_answer": True,
            },
        },
    )

    assert decision.response_form == "text"
    assert decision.response_form_reason == "visual_answer_text"


def test_router_does_not_auto_voice_text_greeting_by_default(monkeypatch) -> None:
    monkeypatch.delenv("HOST_REPLY_MODALITY_AUTO_VOICE_ENABLED", raising=False)

    decision = ReplyModalityRouter().route(
        interpretation=ConversationInterpretation(literal_topic="greeting"),
        relationship_state=RelationshipStateBlock(bond_stage="SOULMATE"),
        multimodal={},
    )

    assert decision.response_form == "text"
    assert decision.response_form_reason == "phatic_text"


def test_router_uses_text_for_auditory_check_without_audio() -> None:
    decision = ReplyModalityRouter().route(
        interpretation=ConversationInterpretation(literal_topic="auditory_check"),
        relationship_state=RelationshipStateBlock(bond_stage="SOULMATE"),
        multimodal={},
    )

    assert decision.response_form == "text"
    assert decision.response_form_reason == "phatic_text"


def test_router_routes_close_inbound_voice_to_voice() -> None:
    decision = ReplyModalityRouter().route(
        interpretation=ConversationInterpretation(literal_topic="presence_ping"),
        relationship_state=RelationshipStateBlock(bond_stage="SOULMATE"),
        multimodal={"primary_modality": "voice"},
    )

    assert decision.response_form == "voice"
    assert decision.response_form_reason == "voice_echo_close"
    assert decision.render_profile == "zh_voice"


def test_router_honors_explicit_voice_request() -> None:
    decision = ReplyModalityRouter().route(
        interpretation=ConversationInterpretation(literal_topic="greeting"),
        relationship_state=RelationshipStateBlock(bond_stage="FRIEND"),
        multimodal={},
        requested_response_form="voice",
    )

    assert decision.response_form == "voice"
    assert decision.response_form_reason == "requested_voice"
