from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from host_brain.app.emotion_context import map_emotion_tag_to_vad


_POSITIVE_TAGS = frozenset({"HAPPY", "JOY", "LOVE", "CALM", "CURIOUS", "GRATEFUL", "EXCITED"})
_NEGATIVE_TAGS = frozenset({"SAD", "SADNESS", "ANGRY", "ANGER", "FEAR", "DISGUST", "HURT", "LONELY", "ANXIOUS"})
_IRRITATION_TAGS = frozenset({"ANGRY", "ANGER", "DISGUST", "IRRITATED"})


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, *, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _safe_mapping(raw: Any) -> dict[str, Any]:
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


@dataclass(frozen=True)
class AffectStateBlock:
    emotion_tag: str = "NEUTRAL"
    valence: float = 0.0
    arousal: float = 0.2
    stability: float = 0.7
    social_energy: float = 0.6
    warmth_bias: float = 0.35
    withdraw_bias: float = 0.2
    irritation_bias: float = 0.0
    carryover_turns: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "emotion_tag": self.emotion_tag,
            "valence": round(self.valence, 4),
            "arousal": round(self.arousal, 4),
            "stability": round(self.stability, 4),
            "social_energy": round(self.social_energy, 4),
            "warmth_bias": round(self.warmth_bias, 4),
            "withdraw_bias": round(self.withdraw_bias, 4),
            "irritation_bias": round(self.irritation_bias, 4),
            "carryover_turns": int(self.carryover_turns or 0),
        }


def resolve_affect_state(
    *,
    emotion_context: Mapping[str, Any] | None,
    body_sense: Mapping[str, Any] | None = None,
    offline_state: Mapping[str, Any] | None = None,
    silence_state: Mapping[str, Any] | None = None,
) -> AffectStateBlock:
    emotion = _safe_mapping(emotion_context)
    body = _safe_mapping(body_sense)
    offline = _safe_mapping(offline_state)
    silence = _safe_mapping(silence_state)

    emotion_tag = str(emotion.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
    vad = _safe_mapping(emotion.get("vad_vector"))
    raw_valence = vad.get("valence", emotion.get("valence"))
    raw_arousal = vad.get("arousal", emotion.get("arousal"))
    if raw_valence in (None, "") or raw_arousal in (None, ""):
        valence, arousal, _dominance = map_emotion_tag_to_vad(emotion_tag)
        if raw_valence in (None, ""):
            raw_valence = valence
        if raw_arousal in (None, ""):
            raw_arousal = arousal

    valence = _clamp((_coerce_float(raw_valence, default=0.0) + 1.0) / 2.0)
    arousal = _clamp(_coerce_float(raw_arousal, default=0.2))

    emotion_intensity = _clamp(_coerce_float(emotion.get("emotion_intensity"), default=0.35))
    stability = _clamp(1.0 - emotion_intensity * 0.55)

    fatigue = _clamp(_coerce_float(body.get("fatigue"), default=0.0))
    drowsiness = _clamp(_coerce_float(body.get("drowsiness"), default=0.0))
    loneliness = _clamp(_coerce_float(offline.get("loneliness_level"), default=0.0))
    anticipation = _clamp(_coerce_float(offline.get("anticipation_level"), default=0.0))
    willingness = _clamp(_coerce_float(silence.get("response_willingness"), default=1.0))

    base_warmth = 0.32
    if emotion_tag in _POSITIVE_TAGS:
        base_warmth += 0.16
    if emotion_tag in _NEGATIVE_TAGS:
        base_warmth -= 0.08
    warmth_bias = _clamp(base_warmth + loneliness * 0.18 + anticipation * 0.12 - fatigue * 0.10)

    withdraw_bias = _clamp(
        0.10
        + fatigue * 0.35
        + drowsiness * 0.18
        + (1.0 - willingness) * 0.28
        + (0.12 if emotion_tag in _NEGATIVE_TAGS else 0.0)
    )

    irritation_bias = _clamp(
        (_coerce_float(emotion.get("arousal"), default=0.0) * 0.25 if emotion_tag in _IRRITATION_TAGS else 0.0)
        + (0.18 if emotion_tag in _IRRITATION_TAGS else 0.0)
    )

    social_energy = _clamp(
        0.72
        - fatigue * 0.45
        - drowsiness * 0.20
        + willingness * 0.12
        - withdraw_bias * 0.18
    )

    carryover_turns = int(_coerce_float(emotion.get("turn_count"), default=0.0))
    if carryover_turns <= 0 and emotion_tag != "NEUTRAL":
        carryover_turns = 1

    return AffectStateBlock(
        emotion_tag=emotion_tag,
        valence=valence,
        arousal=arousal,
        stability=stability,
        social_energy=social_energy,
        warmth_bias=warmth_bias,
        withdraw_bias=withdraw_bias,
        irritation_bias=irritation_bias,
        carryover_turns=carryover_turns,
    )


def build_affect_summary(*, affect: AffectStateBlock, render_target_lang: str = "zh") -> str:
    lang = str(render_target_lang or "").strip().lower()
    if lang == "ja":
        if affect.withdraw_bias >= 0.45:
            return "今は少し引き気味で、熱を上げすぎない。"
        if affect.warmth_bias >= 0.5:
            return "今はやや柔らかいが、踏み込みすぎない。"
        return "今は落ち着いていて、静かに返す。"
    if affect.withdraw_bias >= 0.45:
        return "当前偏收着，别把语气抬得太热。"
    if affect.warmth_bias >= 0.5:
        return "当前略柔和，可以温一点，但别说近。"
    return "当前情绪平稳，语气自然收着。"


__all__ = ["AffectStateBlock", "build_affect_summary", "resolve_affect_state"]
