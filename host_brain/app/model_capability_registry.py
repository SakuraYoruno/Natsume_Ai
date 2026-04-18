"""Model capability profile registry for routing governance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelCapabilityProfile:
    provider_name: str
    model_id: str
    modality: str
    reasoning_tier: str
    context_tier: str
    cost_tier: str
    stability_tier: str


_KNOWN_PROFILES: tuple[ModelCapabilityProfile, ...] = (
    ModelCapabilityProfile(
        provider_name="gemini",
        model_id="gemini-2.5-pro",
        modality="multimodal",
        reasoning_tier="high",
        context_tier="large",
        cost_tier="high",
        stability_tier="high",
    ),
    ModelCapabilityProfile(
        provider_name="gemini",
        model_id="gemini-2.5-flash-lite",
        modality="multimodal",
        reasoning_tier="medium",
        context_tier="medium",
        cost_tier="low",
        stability_tier="high",
    ),
)


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def resolve_model_capability_profile(*, provider_name: str, model_id: str, role: str | None = None) -> dict[str, Any]:
    normalized_provider = _normalize(provider_name)
    normalized_model_id = _normalize(model_id)
    normalized_role = _normalize(role or "")

    for profile in _KNOWN_PROFILES:
        if _normalize(profile.provider_name) == normalized_provider and _normalize(profile.model_id) == normalized_model_id:
            return {
                "provider_name": profile.provider_name,
                "model_id": profile.model_id,
                "modality": profile.modality,
                "reasoning_tier": profile.reasoning_tier,
                "context_tier": profile.context_tier,
                "cost_tier": profile.cost_tier,
                "stability_tier": profile.stability_tier,
                "role": normalized_role or None,
                "source": "registry",
            }

    return {
        "provider_name": normalized_provider or "unknown",
        "model_id": model_id,
        "modality": "text",
        "reasoning_tier": "medium" if normalized_role in {"chat_agent", "cortex_orchestrator"} else "low",
        "context_tier": "unknown",
        "cost_tier": "unknown",
        "stability_tier": "unknown",
        "role": normalized_role or None,
        "source": "default",
    }


__all__ = ["resolve_model_capability_profile"]
