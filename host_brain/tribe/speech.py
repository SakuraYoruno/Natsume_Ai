"""Pure speech-expression mappings owned by the Tribe language domain."""

from __future__ import annotations

from typing import Any


_BROCA_TONE_TO_EMOTION_TAG: dict[str, str] = {
    "warm": "LOVING",
    "caring": "LOVING",
    "gentle": "CALM",
    "supportive": "CALM",
    "playful": "HAPPY",
    "cheerful": "HAPPY",
    "excited": "HAPPY",
    "serious": "NEUTRAL",
    "direct": "NEUTRAL",
    "balanced": "NEUTRAL",
    "cautious": "NEUTRAL",
    "empathetic": "SAD",
    "sad": "SAD",
    "concerned": "SAD",
    "angry": "ANGRY",
    "firm": "ANGRY",
    "surprised": "SURPRISED",
    "fearful": "FEAR",
}


def broca_to_tts_params(
    *,
    tone: str = "balanced",
    warmth_weight: float = 0.5,
    emotion_tag: str = "NEUTRAL",
) -> dict[str, Any]:
    resolved_tag = str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    if resolved_tag == "NEUTRAL":
        resolved_tag = _BROCA_TONE_TO_EMOTION_TAG.get(
            str(tone or "balanced").strip().lower(),
            "NEUTRAL",
        )
    clamped = max(0.0, min(1.0, float(warmth_weight or 0.5)))
    return {
        "emotion_tag": resolved_tag,
        "emotion_intensity": round(0.2 + clamped * 0.7, 3),
    }


__all__ = ["broca_to_tts_params"]
