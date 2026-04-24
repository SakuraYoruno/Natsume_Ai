from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def _normalize_text(raw: Any) -> str:
    return str(raw or "").strip()


def _normalize_types(raw: Any) -> tuple[str, ...]:
    values: list[str] = []
    for item in list(raw or []):
        normalized = _normalize_text(item).lower()
        if normalized and normalized not in values:
            values.append(normalized)
    return tuple(values)


@dataclass(frozen=True)
class PerceptionPacket:
    """Canonical perception facts for the normal chat chain.

    This packet is intentionally factual only. It may summarize what the system
    observed, but it must not encode reply policy such as `reply_focus` or
    `conversation_hint`.
    """

    observed_types: tuple[str, ...] = ()
    summary_for_chat: str = ""
    core_observation: str = ""
    visible_text: str = ""
    artifact_type: str = ""
    primary_modality: str = ""
    emotion_signals: tuple[str, ...] = ()
    confidence: float = 0.0
    followup_required: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "observed_types": list(self.observed_types),
            "summary_for_chat": self.summary_for_chat,
            "core_observation": self.core_observation,
            "visible_text": self.visible_text,
            "artifact_type": self.artifact_type,
            "primary_modality": self.primary_modality,
            "emotion_signals": list(self.emotion_signals),
            "confidence": float(self.confidence or 0.0),
            "followup_required": bool(self.followup_required),
        }


def build_perception_packet(
    *,
    multimodal_observations: Mapping[str, Any] | None = None,
    multimodal_payload: Mapping[str, Any] | None = None,
    attachment_summary: str | None = None,
) -> PerceptionPacket:
    observations = dict(multimodal_observations or {})
    payload = dict(multimodal_payload or {})

    observed_types = _normalize_types(
        observations.get("observed_types") or payload.get("modalities") or []
    )
    primary_modality = _normalize_text(
        observations.get("primary_modality")
        or payload.get("primary_modality")
        or (observed_types[0] if observed_types else "")
    ).lower()
    visible_text = _normalize_text(observations.get("visible_text"))
    artifact_type = _normalize_text(observations.get("artifact_type")).lower()
    core_observation = _normalize_text(observations.get("core_observation"))
    summary_for_chat = _normalize_text(observations.get("summary_for_chat"))
    if not summary_for_chat:
        summary_for_chat = core_observation or visible_text or _normalize_text(attachment_summary)
    emotion_signals = _normalize_types(observations.get("emotion_signals") or [])

    confidence_raw = observations.get("confidence")
    try:
        confidence = float(confidence_raw) if confidence_raw is not None else 0.0
    except (TypeError, ValueError):
        confidence = 0.0

    return PerceptionPacket(
        observed_types=observed_types,
        summary_for_chat=summary_for_chat,
        core_observation=core_observation,
        visible_text=visible_text,
        artifact_type=artifact_type,
        primary_modality=primary_modality,
        emotion_signals=emotion_signals,
        confidence=max(0.0, min(confidence, 1.0)),
        followup_required=bool(observations.get("followup_required") or False),
    )


def coerce_perception_packet(raw: Mapping[str, Any] | None) -> PerceptionPacket:
    payload = dict(raw or {}) if isinstance(raw, Mapping) else {}
    return build_perception_packet(multimodal_observations=payload)


__all__ = [
    "PerceptionPacket",
    "build_perception_packet",
    "coerce_perception_packet",
]
