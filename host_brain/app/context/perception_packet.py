from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .multimodal_act import MultimodalAct, derive_multimodal_act


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
    visual_facts: str = ""
    ocr_text: str = ""
    social_signal: str = ""
    artifact_type: str = ""
    primary_modality: str = ""
    emotion_signals: tuple[str, ...] = ()
    confidence: float = 0.0
    followup_required: bool = False
    multimodal_act: MultimodalAct = field(default_factory=MultimodalAct)

    def as_dict(self) -> dict[str, Any]:
        return {
            "observed_types": list(self.observed_types),
            "summary_for_chat": self.summary_for_chat,
            "core_observation": self.core_observation,
            "visible_text": self.visible_text,
            "visual_facts": self.visual_facts,
            "ocr_text": self.ocr_text,
            "social_signal": self.social_signal,
            "artifact_type": self.artifact_type,
            "primary_modality": self.primary_modality,
            "emotion_signals": list(self.emotion_signals),
            "confidence": float(self.confidence or 0.0),
            "followup_required": bool(self.followup_required),
            "multimodal_act": self.multimodal_act.as_dict(),
            "user_intent": self.multimodal_act.user_intent,
            "requires_text_answer": bool(self.multimodal_act.requires_text_answer),
            "social_emotion": self.multimodal_act.emotion,
        }


def build_perception_packet(
    *,
    multimodal_observations: Mapping[str, Any] | None = None,
    multimodal_payload: Mapping[str, Any] | None = None,
    attachment_summary: str | None = None,
    user_text: str | None = "",
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
    ocr_text = _normalize_text(observations.get("ocr_text")) or visible_text
    visual_facts = _normalize_text(observations.get("visual_facts") or observations.get("visual_summary"))
    social_signal = _normalize_text(observations.get("social_signal") or observations.get("meme_intent"))
    artifact_type = _normalize_text(observations.get("artifact_type")).lower()
    core_observation = _normalize_text(observations.get("core_observation"))
    summary_for_chat = _normalize_text(observations.get("summary_for_chat"))
    if not summary_for_chat:
        summary_for_chat = social_signal or core_observation or visual_facts or visible_text or _normalize_text(attachment_summary)
    emotion_signals = _normalize_types(observations.get("emotion_signals") or [])
    multimodal_act = derive_multimodal_act(
        user_text=user_text,
        observations={
            **observations,
            "observed_types": list(observed_types),
            "primary_modality": primary_modality,
            "visual_facts": visual_facts,
            "ocr_text": ocr_text,
            "social_signal": social_signal,
            "artifact_type": artifact_type,
            "emotion_signals": list(emotion_signals),
            "summary_for_chat": summary_for_chat,
        },
    )

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
        visual_facts=visual_facts,
        ocr_text=ocr_text,
        social_signal=social_signal,
        artifact_type=artifact_type,
        primary_modality=primary_modality,
        emotion_signals=emotion_signals,
        confidence=max(0.0, min(confidence, 1.0)),
        followup_required=bool(observations.get("followup_required") or False),
        multimodal_act=multimodal_act,
    )


def coerce_perception_packet(raw: Mapping[str, Any] | None) -> PerceptionPacket:
    payload = dict(raw or {}) if isinstance(raw, Mapping) else {}
    return build_perception_packet(multimodal_observations=payload)


__all__ = [
    "MultimodalAct",
    "PerceptionPacket",
    "build_perception_packet",
    "coerce_perception_packet",
]
