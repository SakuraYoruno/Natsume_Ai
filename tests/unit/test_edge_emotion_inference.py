from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_infer_asr_emotion():
    module_path = Path(__file__).resolve().parents[2] / "edge_client" / "runtime" / "emotion_inference.py"
    spec = importlib.util.spec_from_file_location("edge_runtime_emotion_inference", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.infer_asr_emotion


infer_asr_emotion = _load_infer_asr_emotion()


def test_happy_text_maps_to_happy() -> None:
    result = infer_asr_emotion(
        text="今天真的好开心！",
        confidence=0.95,
        final=True,
    )
    assert result["emotion_tag"] == "HAPPY"
    assert float(result["emotion_intensity"]) > 0.2
    assert result["emotion_source"] == "asr_text_bootstrap"
    assert result["emotion_modality"] == "asr_text"
    assert result["acoustic_emotion_available"] is False
    assert result["emotion_inference_degraded"] is False
    assert "开心" in list(result["matched_terms"])


def test_question_text_maps_to_thinking() -> None:
    result = infer_asr_emotion(
        text="这是怎么回事？",
        confidence=0.90,
        final=False,
    )
    assert result["emotion_tag"] == "THINKING"
    assert float(result["emotion_intensity"]) > 0.1
    assert result["mood_state"] in {"focused", "steady", "engaged", "calm"}


def test_empty_text_degrades_to_neutral() -> None:
    result = infer_asr_emotion(
        text="",
        confidence=0.50,
        final=False,
    )
    assert result["emotion_tag"] == "NEUTRAL"
    assert float(result["emotion_intensity"]) == 0.0
    assert result["emotion_source"] == "asr_text_unavailable"
    assert result["emotion_modality"] == "asr_text"
    assert result["acoustic_emotion_available"] is False
    assert result["emotion_inference_degraded"] is True
