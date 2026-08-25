"""Typed application boundary for lifecycle runtime services.

The lifecycle package receives this immutable dependency graph from an
application composition root.  Every collaborator is named and traceable;
there is no string lookup, mutable module resolver, or reverse dependency on
application implementation packages.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


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
    # OBS reads the same canonical Life snapshot that powers chat and
    # companion delivery.  Isolated compositions may omit it, in which case
    # the canonical reader returns a degraded, fail-closed projection.
    get_life_snapshot_reader: ServiceProvider | None = None


__all__ = ["LifecycleRuntimeApplicationPorts"]
