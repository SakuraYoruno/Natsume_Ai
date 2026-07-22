"""Explicit TTS route projection for one accepted Agent Runtime response.

This module deliberately has no planner, SkillComposer, executor, or process
service lookup.  The canonical Agent Runtime receives its two collaborators
from the Host-lifespan application bundle, after TaskGraph acceptance has
authorized the response effect.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


TTS_ROUTE_DEPENDENCY_UNAVAILABLE = "TTS_ROUTE_DEPENDENCY_UNAVAILABLE"

_TTS_EMOTION_MAP: dict[str, str] = {
    "NEUTRAL": "neutral",
    "HAPPY": "happy",
    "JOY": "happy",
    "GRATEFUL": "grateful",
    "PROUD": "proud",
    "SURPRISED": "surprised",
    "EXCITED": "excited",
    "PLAYFUL": "happy",
    "SAD": "sad",
    "ANXIOUS": "anxious",
    "BORED": "bored",
    "CALM": "calm",
    "COMFORT": "calm",
    "THINKING": "thinking",
    "CONFUSED": "confused",
    "ANGRY": "angry",
    "ANGER": "angry",
    "FRUSTRATED": "frustrated",
    "SERIOUS": "thinking",
    "LOVING": "loving",
    "EMBARRASSED": "embarrassed",
    "AFFECTION": "loving",
    "COQUETTISH": "loving",
}


def resolve_tts_route(
    *,
    text: str,
    emotion_context: Mapping[str, Any] | None,
    bond_summary: Mapping[str, Any] | None,
    tts_context_router: Any,
    meta_learning_loop: Any,
) -> dict[str, Any]:
    """Project accepted text to TTS parameters using explicit collaborators.

    There is intentionally no compatibility fallback to ``process_services``:
    creating another service locator path after Agent Runtime acceptance would
    undermine the Host-scope ownership boundary.
    """

    if not callable(getattr(tts_context_router, "route", None)):
        raise RuntimeError(TTS_ROUTE_DEPENDENCY_UNAVAILABLE)
    if not callable(getattr(meta_learning_loop, "record", None)):
        raise RuntimeError(TTS_ROUTE_DEPENDENCY_UNAVAILABLE)

    emotion_tag = str((emotion_context or {}).get("emotion_tag") or "").strip().upper()
    bond_stage = (
        str((bond_summary or {}).get("stage") or "FRIEND").strip().upper()
        or "FRIEND"
    )
    vad = dict((emotion_context or {}).get("vad_vector") or {})
    valence = _coerce_optional_float(vad.get("valence"))
    arousal = _coerce_optional_float(vad.get("arousal"))
    routed = tts_context_router.route(
        text=text,
        emotion_tag=_normalize_emotion_tag(emotion_tag),
        bond_stage=bond_stage,
        valence=valence,
        arousal=arousal,
    )
    meta_learning_loop.record(
        "tts_context_router",
        success=True,
        latency_ms=0.0,
        custom={"speed_scale": float(routed.speed_scale)},
    )
    return {
        "speed": max(0.7, min(1.3, float(routed.speed_scale))),
        "pitch": max(-1.0, min(1.0, float(routed.pitch_shift) / 2.0)),
        "emotion_tag": (
            str(routed.emotion_tag or _normalize_emotion_tag(emotion_tag)).strip()
            or "neutral"
        ),
        "style_note": str(routed.style_note or "").strip(),
        "lang_hint": str(routed.lang_hint or "zh").strip().lower() or "zh",
        "route": routed.as_dict(),
    }


def _normalize_emotion_tag(emotion_tag: str) -> str:
    normalized = str(emotion_tag or "").strip().upper()
    if not normalized:
        return "neutral"
    return _TTS_EMOTION_MAP.get(normalized, normalized.lower())


def _coerce_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "TTS_ROUTE_DEPENDENCY_UNAVAILABLE",
    "resolve_tts_route",
]
