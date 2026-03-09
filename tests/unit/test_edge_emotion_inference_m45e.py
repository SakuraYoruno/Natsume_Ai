from __future__ import annotations

from edge_client.runtime.emotion_inference import infer_asr_emotion


def test_happy_text_maps_to_happy() -> None:
    result = infer_asr_emotion(
        text="今天真的好开心！",
        confidence=0.95,
        final=True,
    )
    assert result["emotion_tag"] == "HAPPY"
    assert float(result["emotion_intensity"]) > 0.2
    assert "开心" in list(result["matched_terms"])


def test_question_text_maps_to_thinking() -> None:
    result = infer_asr_emotion(
        text="这是怎么回事？",
        confidence=0.90,
        final=False,
    )
    assert result["emotion_tag"] == "THINKING"
    assert float(result["emotion_intensity"]) > 0.2


def test_empty_text_degrades_to_neutral() -> None:
    result = infer_asr_emotion(
        text="",
        confidence=0.50,
        final=False,
    )
    assert result["emotion_tag"] == "NEUTRAL"
    assert float(result["emotion_intensity"]) == 0.0
