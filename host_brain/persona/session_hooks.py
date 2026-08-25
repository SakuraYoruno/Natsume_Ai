"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping
from host_brain.core.orchestration_settings import get_orchestration_settings


def _resolve_persona_session_emotion(emotion_context: Mapping[str, Any]) -> tuple[float, float]:
    """为人格 session_end 提取可用的 valence / arousal。"""
    payload = dict(emotion_context or {})
    vad_payload = payload.get("vad") if isinstance(payload.get("vad"), Mapping) else {}

    raw_valence = payload.get("valence")
    if raw_valence is None:
        raw_valence = vad_payload.get("valence")

    raw_arousal = payload.get("arousal")
    if raw_arousal is None:
        raw_arousal = vad_payload.get("arousal")

    def _coerce(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    if raw_valence is not None or raw_arousal is not None:
        valence = max(-1.0, min(1.0, _coerce(raw_valence, 0.0)))
        arousal = max(0.0, min(1.0, _coerce(raw_arousal, 0.3)))
        return valence, arousal

    emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper()
    intensity = max(0.0, min(1.0, _coerce(payload.get("emotion_intensity"), 0.5)))
    fallback_vad = {
        "HAPPY": (0.7, 0.65),
        "LOVING": (0.8, 0.45),
        "EXCITED": (0.75, 0.85),
        "SURPRISED": (0.2, 0.8),
        "SAD": (-0.6, 0.25),
        "ANGRY": (-0.7, 0.8),
        "NEUTRAL": (0.0, 0.3),
    }
    base_valence, base_arousal = fallback_vad.get(emotion_tag, (0.0, 0.3))
    valence = max(-1.0, min(1.0, float(base_valence) * max(0.4, intensity)))
    arousal = max(0.0, min(1.0, max(float(base_arousal), intensity)))
    return valence, arousal


def _resolve_retrieval_timeout_seconds() -> float:
    return get_orchestration_settings().top_agent_retrieval_timeout_ms / 1000.0


def _resolve_selection_followup_retrieval_timeout_seconds() -> float:
    return (
        get_orchestration_settings().selection_followup_retrieval_timeout_ms
        / 1000.0
    )
