"""Typed application boundary for lifecycle runtime services.

The lifecycle package receives this immutable dependency graph from an
application composition root.  Every collaborator is named and traceable;
there is no string lookup, mutable module resolver, or reverse dependency on
application implementation packages.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


class EmotionTimelinePort(Protocol):
    """Emotion timeline operations consumed by lifecycle projections."""

    def rebuild_summary(self, *, window_ms: int) -> dict[str, Any]: ...

    def build_daily_diary(self, *, window_ms: int) -> dict[str, Any]: ...


ServiceProvider = Callable[[], Any]
KeywordServiceProvider = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class LifecycleRuntimeApplicationPorts:
    """Application-owned collaborators required by lifecycle runtime."""

    get_activity_monitor: KeywordServiceProvider
    get_endocrine_state: KeywordServiceProvider
    get_episodic_store: ServiceProvider
    get_hard_state: ServiceProvider
    get_ledger: ServiceProvider
    get_life_memory_store: ServiceProvider
    get_model_attempt_service: ServiceProvider
    emotion_timeline: EmotionTimelinePort
    get_user_emotion_profile: ServiceProvider | None = None


__all__ = [
    "EmotionTimelinePort",
    "LifecycleRuntimeApplicationPorts",
]
