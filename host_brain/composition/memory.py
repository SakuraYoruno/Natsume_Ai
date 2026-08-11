"""Process composition for the governed LifeMemory service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from host_brain.memory.life_memory.projection import (
    LegacyEpisodicProjection,
    LifeMemoryProjectionWorker,
)
from host_brain.memory.life_memory.service import (
    LifeMemoryAdmissionPolicy,
    LifeMemoryService,
)
from host_brain.persistence.life_memory_repository import LifeMemoryRepository


@dataclass
class ProcessMemoryContainer:
    repository: LifeMemoryRepository
    policy: LifeMemoryAdmissionPolicy
    service: LifeMemoryService
    projection_worker: LifeMemoryProjectionWorker | None
    startup_projection_summary: dict[str, int]

    def close(self) -> None:
        # Repository connections are operation-scoped; this lifecycle hook
        # exists so the process composition can close all containers uniformly.
        return None


def build_process_memory_container(
    *,
    ledger: Any,
    canonical_db_path: str | Path,
    worth_evaluator: Any,
    memory_guard: Any,
    episodic_projection_store: Any | None = None,
    startup_projection_limit: int = 100,
) -> ProcessMemoryContainer:
    canonical = _canonical_path(ledger, canonical_db_path)
    repository = LifeMemoryRepository(canonical)
    policy = LifeMemoryAdmissionPolicy(
        worth_evaluator=worth_evaluator,
        guard=memory_guard,
    )
    projection_worker: LifeMemoryProjectionWorker | None = None
    startup_summary = {"published": 0, "failed": 0, "skipped": 0}
    if episodic_projection_store is not None:
        projection_worker = LifeMemoryProjectionWorker(
            repository=repository,
            projector=LegacyEpisodicProjection(episodic_projection_store),
        )
        startup_summary = projection_worker.drain_pending(
            limit=max(1, int(startup_projection_limit))
        )
    service = LifeMemoryService(
        repository=repository,
        policy=policy,
        projection_dispatcher=projection_worker,
    )
    return ProcessMemoryContainer(
        repository=repository,
        policy=policy,
        service=service,
        projection_worker=projection_worker,
        startup_projection_summary=startup_summary,
    )


def _canonical_path(ledger: Any, configured: str | Path) -> Path:
    selected = str(
        getattr(ledger, "canonical_db_path", None)
        or getattr(ledger, "db_path", None)
        or configured
        or ""
    ).strip()
    configured_path = str(configured or "").strip()
    if not selected or selected == ":memory:" or not configured_path:
        raise ValueError("LIFE_MEMORY_COMPOSITION_REQUIRES_DURABLE_DB")
    selected_path = Path(selected).expanduser().resolve()
    if selected_path != Path(configured_path).expanduser().resolve():
        raise ValueError("LIFE_MEMORY_COMPOSITION_DB_MISMATCH")
    return selected_path


__all__ = [
    "ProcessMemoryContainer",
    "build_process_memory_container",
]
