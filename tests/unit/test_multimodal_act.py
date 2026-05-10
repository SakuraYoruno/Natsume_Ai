from __future__ import annotations

from host_brain.app.context.multimodal_act import derive_multimodal_act


def test_sticker_without_text_is_social_reaction_not_text_answer() -> None:
    act = derive_multimodal_act(
        user_text="[表情消息]",
        observations={
            "primary_modality": "emoji",
            "artifact_type": "sticker",
            "emotion_signals": ["无语"],
            "confidence": 0.82,
        },
    )

    assert act.user_intent == "social_reaction"
    assert act.requires_text_answer is False
    assert act.emotion == "awkward"
    assert act.confidence == 0.82


def test_explicit_image_question_requires_text_answer() -> None:
    act = derive_multimodal_act(
        user_text="这张图是什么意思？",
        observations={
            "primary_modality": "image",
            "artifact_type": "meme",
            "social_signal": "调侃",
        },
    )

    assert act.user_intent == "image_question"
    assert act.requires_text_answer is True
    assert act.emotion == "tease"
