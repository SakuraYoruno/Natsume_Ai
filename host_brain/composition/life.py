"""Process composition for the single Digital Life command authority."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any

from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.service import LifeCommandService
from host_brain.persistence.life_command_repository import LifeCommandRepository


@dataclass(frozen=True, slots=True)
class ProcessLifeContainer:
    repository: LifeCommandRepository
    command_service: LifeCommandService
    snapshot_reader: LifeSnapshotReader


class ProcessLifeProvider:
    """Explicit process-owned provider for the one composed Life authority."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._container: ProcessLifeContainer | None = None

    def install(self, container: ProcessLifeContainer) -> None:
        if not isinstance(container, ProcessLifeContainer):
            raise TypeError("container must be a ProcessLifeContainer")
        with self._lock:
            current = self._container
            if current is not None and current is not container:
                raise RuntimeError("PROCESS_LIFE_AUTHORITY_ALREADY_COMPOSED")
            self._container = container

    def require(self) -> ProcessLifeContainer:
        with self._lock:
            container = self._container
        if container is None:
            raise RuntimeError("PROCESS_LIFE_AUTHORITY_NOT_COMPOSED")
        return container

    def clear(self, container: ProcessLifeContainer | None = None) -> None:
        with self._lock:
            if container is not None and self._container is not container:
                return
            self._container = None


PROCESS_LIFE_PROVIDER = ProcessLifeProvider()


def build_process_life_container(*, ledger: Any, adopted_at_ms: int) -> ProcessLifeContainer:
    repository = LifeCommandRepository.from_ledger(ledger)
    command_service = LifeCommandService(repository)
    command_service.adopt_all_legacy_projections(adopted_at_ms=int(adopted_at_ms))
    return ProcessLifeContainer(
        repository=repository,
        command_service=command_service,
        snapshot_reader=LifeSnapshotReader(repository),
    )


__all__ = [
    "PROCESS_LIFE_PROVIDER",
    "ProcessLifeContainer",
    "ProcessLifeProvider",
    "build_process_life_container",
]
