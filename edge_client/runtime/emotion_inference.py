"""Lightweight text emotion hinting for Edge ASR events.

This module does not infer acoustic emotion from voice. Current ASR upstreams
only provide transcript text, so this is a conservative text bootstrap hint
used when no dedicated voice-affect model is available.
"""

from __future__ import annotations

import math
from typing import Any

from host_brain.core.metabolism import EmotionVector


_EMOTION_HINTS: dict[str, dict[str, Any]] = {
    "HAPPY": {
        "terms": ("happy", "开心", "高兴", "うれしい", "嬉しい", "楽しい", "好き"),
        "vector": (0.55, 0.20, 0.00),
    },
    "EXCITED": {
        "terms": ("excited", "兴奋", "激动", "最高", "やばい", "すごい"),
        "vector": (0.55, 0.55, 0.10),
    },
    "SAD": {
        "terms": ("sad", "难过", "伤心", "悲しい", "つらい", "寂しい"),
        "vector": (-0.55, -0.25, -0.10),
    },
    "ANGRY": {
        "terms": ("angry", "生气", "烦死", "烦", "むかつく", "腹立つ"),
        "vector": (-0.65, 0.45, 0.20),
    },
    "ANXIOUS": {
        "terms": ("anxious", "紧张", "担心", "不安", "心配", "こわい", "怖い"),
        "vector": (-0.35, 0.35, -0.15),
    },
    "CALM": {
        "terms": ("calm", "安心", "平静", "冷静", "落ち着", "ゆっくり"),
        "vector": (0.15, -0.35, 0.00),
    },
    "THINKING": {
        "terms": ("think", "maybe", "想想", "考虑", "怎么", "为什么", "どう", "かな"),
        "vector": (0.00, 0.05, 0.35),
    },
    "SURPRISED": {
        "terms": ("surprised", "惊讶", "居然", "まさか", "えっ", "真的假的"),
        "vector": (0.10, 0.55, 0.05),
    },
}


def infer_asr_emotion(
    *,
    text: str,
    confidence: float | None = None,
    final: bool | None = None,
) -> dict[str, Any]:
    raw_text = str(text or "").strip()
    if not raw_text:
        return {
            "emotion_tag": "NEUTRAL",
            "emotion_intensity": 0.0,
            "mood_state": "calm",
            "emotion_source": "asr_text_unavailable",
            "emotion_modality": "asr_text",
            "emotion_confidence": 0.0,
            "emotion_inference_degraded": True,
            "acoustic_emotion_available": False,
            "inference_note": "asr_transcript_empty_no_acoustic_emotion_model",
            "matched_terms": [],
        }

    normalized = raw_text.lower()
    valence = 0.0
    arousal = 0.0
    dominance = 0.0
    matched_terms: list[str] = []

    for hint in _EMOTION_HINTS.values():
        vector = hint["vector"]
        for term in hint["terms"]:
            if str(term).lower() not in normalized:
                continue
            matched_terms.append(str(term))
            valence += float(vector[0])
            arousal += float(vector[1])
            dominance += float(vector[2])

    exclamation_count = raw_text.count("!") + raw_text.count("！")
    question_count = raw_text.count("?") + raw_text.count("？")
    if exclamation_count:
        arousal += min(0.35, 0.12 * exclamation_count)
    if question_count:
        dominance += min(0.35, 0.10 * question_count)
        if not matched_terms:
            arousal += 0.08

    if not matched_terms and question_count:
        dominance += 0.22
    elif not matched_terms and exclamation_count:
        arousal += 0.18

    vector = EmotionVector(
        valence=max(-1.0, min(1.0, valence)),
        arousal=max(-1.0, min(1.0, arousal)),
        dominance=max(-1.0, min(1.0, dominance)),
        ts_ms=0,
    )
    emotion_tag = vector.to_emotion_tag()
    mood_state = vector.to_mood_state()
    intensity = math.sqrt((vector.valence ** 2) + (vector.arousal ** 2) + (vector.dominance ** 2))
    if confidence is not None:
        intensity *= max(0.45, min(1.0, float(confidence)))
    if final is False:
        intensity *= 0.9
    intensity = max(0.0, min(1.0, round(intensity / 1.35, 4)))

    if emotion_tag == "NEUTRAL" and intensity < 0.18:
        intensity = 0.0
    confidence_score = _confidence_from_signal(
        matched_terms=matched_terms,
        exclamation_count=exclamation_count,
        question_count=question_count,
        asr_confidence=confidence,
        final=final,
    )
    degraded = not matched_terms

    return {
        "emotion_tag": emotion_tag,
        "emotion_intensity": intensity,
        "mood_state": mood_state,
        "emotion_source": "asr_text_bootstrap",
        "emotion_modality": "asr_text",
        "emotion_confidence": confidence_score,
        "emotion_inference_degraded": degraded,
        "acoustic_emotion_available": False,
        "inference_note": "text_semantic_hint_no_acoustic_emotion_model",
        "inference_level": 1,  # 方向 G：标记为 Level 1（关键词引擎）
        "matched_terms": matched_terms[:6],
    }


def _confidence_from_signal(
    *,
    matched_terms: list[str],
    exclamation_count: int,
    question_count: int,
    asr_confidence: float | None,
    final: bool | None,
) -> float:
    if matched_terms:
        confidence = 0.45 + min(0.30, 0.10 * len(set(matched_terms)))
    elif question_count or exclamation_count:
        confidence = 0.24
    else:
        confidence = 0.0
    if asr_confidence is not None:
        confidence *= max(0.45, min(1.0, float(asr_confidence)))
    if final is False:
        confidence *= 0.9
    return max(0.0, min(0.95, round(confidence, 4)))
