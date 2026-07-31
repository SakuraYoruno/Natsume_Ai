"""Narrow application services explicitly supplied to Tribe agents.

The Tribe domain must not resolve application singletons.  The HTTP process
composition builds this immutable value and attaches it to the long-lived
agents.  Tests may leave a port unset to exercise the agents' documented
degraded paths without booting the application.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


ServiceProvider = Callable[..., Any]

LEGACY_AFFECT_COMPATIBILITY = "LEGACY_AFFECT_COMPATIBILITY_V1"
CANONICAL_AFFECT_LIFE_JOURNAL = "CANONICAL_LIFE_JOURNAL_V1"


@dataclass(frozen=True, slots=True)
class TribeApplicationPorts:
    activity_monitor: ServiceProvider | None = None
    mode_transition_service: ServiceProvider | None = None
    ledger: ServiceProvider | None = None
    life_snapshot_reader: ServiceProvider | None = None
    silence_engine: ServiceProvider | None = None
    state_write_arbiter: ServiceProvider | None = None
    emotion_timeline: ServiceProvider | None = None
    hard_state: ServiceProvider | None = None
    resource_manager: ServiceProvider | None = None
    shared_memory_index: ServiceProvider | None = None
    milestone_tracker: ServiceProvider | None = None
    model_attempt_service: ServiceProvider | None = None
    prompt_registry: ServiceProvider | None = None
    resolve_chat_emotion_context: ServiceProvider | None = None
    canonical_affect_fusion: ServiceProvider | None = None
    affective_life_authority: str = LEGACY_AFFECT_COMPATIBILITY
    build_chat_emotion_snapshot: ServiceProvider | None = None
    build_user_preference_pack: ServiceProvider | None = None
    build_conversation_experience_read_pack: ServiceProvider | None = None
    build_conversation_understanding_pack: ServiceProvider | None = None
    allocate_intent_seq: ServiceProvider | None = None
    evolution_activation_service: ServiceProvider | None = None

    def require(self, name: str) -> ServiceProvider:
        provider = getattr(self, str(name), None)
        if not callable(provider):
            raise RuntimeError(f"TRIBE_APPLICATION_PORT_NOT_CONFIGURED:{name}")
        return provider

    def uses_canonical_affective_life(self) -> bool:
        """Whether legacy Tribe affect writers must remain read-only.

        This is a composition-time invariant, not a process-global feature
        flag.  It lets legacy/offline fixtures keep their isolated behaviour
        while a production Host with Life authority fails closed instead of
        resurrecting another self-affect writer.
        """

        if self.affective_life_authority not in {
            LEGACY_AFFECT_COMPATIBILITY,
            CANONICAL_AFFECT_LIFE_JOURNAL,
        }:
            raise RuntimeError("TRIBE_AFFECTIVE_LIFE_AUTHORITY_INVALID")
        return self.affective_life_authority == CANONICAL_AFFECT_LIFE_JOURNAL


__all__ = [
    "ServiceProvider",
    "CANONICAL_AFFECT_LIFE_JOURNAL",
    "LEGACY_AFFECT_COMPATIBILITY",
    "TribeApplicationPorts",
]
