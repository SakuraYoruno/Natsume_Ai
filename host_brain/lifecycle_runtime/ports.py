"""Typed application boundary for lifecycle runtime services.

The lifecycle package receives this immutable dependency graph from an
application composition root.  Every collaborator is named and traceable;
there is no string lookup, mutable module resolver, or reverse dependency on
application implementation packages.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Protocol


class EmotionTimelinePort(Protocol):
    """Emotion timeline operations consumed by lifecycle projections."""

    def rebuild_summary(self, *, window_ms: int) -> dict[str, Any]: ...

    def build_daily_diary(self, *, window_ms: int) -> dict[str, Any]: ...


ServiceProvider = Callable[[], Any]
KeywordServiceProvider = Callable[..., Any]


class LifecycleCompatibilityUsageMeter:
    """Bounded, app-local evidence for the legacy snapshot rollback window.

    The meter deliberately contains only fixed projection identifiers and
    counters.  It never receives a topic, person, workspace, session, raw
    emotion or any other user-derived value.  A production ASGI composition
    owns one ``LifecycleRuntimeApplicationPorts`` instance for its lifespan,
    so its snapshot is an app-local audit signal rather than another durable
    Digital Life authority.
    """

    _SCHEMA_VERSION = "Natsume.lifecycle_runtime.compatibility_usage.v1"
    _LEGACY_ASSEMBLER = "legacy_runtime_snapshot_assembler"

    def __init__(self) -> None:
        self._lock = RLock()
        self._legacy_runtime_snapshot_reads = 0
        self._legacy_runtime_snapshot_fallbacks_suppressed = 0

    def record_legacy_runtime_snapshot_read(self) -> None:
        """Record an explicit rollback-window read of the old assembler."""

        with self._lock:
            self._legacy_runtime_snapshot_reads += 1

    def record_legacy_runtime_snapshot_fallback_suppressed(self) -> None:
        """Record that production refused to revive the old assembler."""

        with self._lock:
            self._legacy_runtime_snapshot_fallbacks_suppressed += 1

    def snapshot(self) -> dict[str, Any]:
        """Return a privacy-safe, deterministic audit projection."""

        with self._lock:
            reads = self._legacy_runtime_snapshot_reads
            suppressed = self._legacy_runtime_snapshot_fallbacks_suppressed
        return {
            "schema_version": self._SCHEMA_VERSION,
            "scope": "port_instance",
            "projection_id": self._LEGACY_ASSEMBLER,
            "legacy_runtime_snapshot_read_count": reads,
            "legacy_runtime_snapshot_fallback_suppressed_count": suppressed,
        }


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
    # Production OBS must read the same canonical Life snapshot that powers
    # chat and companion delivery.  ``None`` exists only for isolated legacy
    # compatibility fixtures; a composed provider that fails must degrade
    # closed instead of reviving Bond/EmotionTimeline readers.
    get_life_snapshot_reader: ServiceProvider | None = None
    # Only the application composition root may require the canonical reader.
    # Direct isolated fixtures retain an explicit rollback window until their
    # callers migrate, but an actual Host must never recover old affect or
    # relationship authority when that reader is unavailable.
    require_canonical_life_snapshot: bool = False
    # This mutable object is deliberately app-local rather than a module
    # singleton.  It provides observability for the rollback window without
    # becoming a state or relationship authority.
    compatibility_usage_meter: LifecycleCompatibilityUsageMeter = field(
        default_factory=LifecycleCompatibilityUsageMeter,
        repr=False,
    )


__all__ = [
    "EmotionTimelinePort",
    "LifecycleCompatibilityUsageMeter",
    "LifecycleRuntimeApplicationPorts",
]
