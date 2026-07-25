"""Neutral typed ports shared by Host application and composition layers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


ServiceProvider = Callable[[], Any]
KeywordFactory = Callable[..., Any]


class HostLifecycleScopeError(RuntimeError):
    """Raised when one application lifespan violates lifecycle ownership."""


@dataclass(frozen=True, slots=True)
class HostLifecycleApplicationPorts:
    """Immutable collaborators resolved by the Host composition root."""

    memory_guard: ServiceProvider
    state_arbiter: ServiceProvider
    distillation_engine: ServiceProvider
    semantic_recaller: ServiceProvider
    episodic_store: ServiceProvider
    skill_registry: ServiceProvider
    tool_catalog: ServiceProvider
    capability_broker: ServiceProvider
    model_attempt_service: ServiceProvider
    mode_transition_service_factory: KeywordFactory
    activity_monitor_factory: KeywordFactory
    resource_manager_factory: KeywordFactory
    ledger: ServiceProvider | None = None
    life_memory_store: ServiceProvider | None = None
    bond_update_queue: ServiceProvider | None = None
    life_snapshot_reader: ServiceProvider | None = None
    silence_engine: ServiceProvider | None = None
    state_write_arbiter: ServiceProvider | None = None
    emotion_timeline: ServiceProvider | None = None
    bond_stage_engine: ServiceProvider | None = None
    bond_neo4j_client: ServiceProvider | None = None
    shared_memory_index: ServiceProvider | None = None
    milestone_tracker: ServiceProvider | None = None
    prompt_registry: ServiceProvider | None = None
    user_emotion_profile: ServiceProvider | None = None
    intent_engine: ServiceProvider | None = None
    multimodal_observer: ServiceProvider | None = None
    multimodal_processor: ServiceProvider | None = None
    conversation_history: ServiceProvider | None = None
    life_memory_service: ServiceProvider | None = None
    model_route_catalog: ServiceProvider | None = None
    session_topic_ledger_store: ServiceProvider | None = None
    session_tactic_ledger_store: ServiceProvider | None = None
    tts_context_router: ServiceProvider | None = None
    meta_learning_loop: ServiceProvider | None = None

    def require_provider(self, field_name: str) -> ServiceProvider:
        provider = getattr(self, str(field_name or ""), None)
        if not callable(provider):
            raise HostLifecycleScopeError(
                f"HOST_SERVICE_PROVIDER_NOT_INJECTED:{field_name}"
            )
        return provider


__all__ = [
    "HostLifecycleApplicationPorts",
    "HostLifecycleScopeError",
    "KeywordFactory",
    "ServiceProvider",
]
