"""Pure read facade for Digital Life snapshots and journals."""

from __future__ import annotations

from natsume_contracts.life import (
    LifeCommandReceipt,
    LifeEvent,
    LifeSnapshot,
)
from host_brain.digital_life.repository_port import LifeCommandRepositoryPort


class LifeSnapshotReader:
    def __init__(self, repository: LifeCommandRepositoryPort) -> None:
        self.repository = repository

    def get_snapshot(self, person_id: str) -> LifeSnapshot:
        return self.repository.get_snapshot(person_id)

    def list_events(
        self,
        person_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[LifeEvent]:
        return self.repository.list_events(
            person_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    def get_receipt(self, command_id: str) -> LifeCommandReceipt | None:
        return self.repository.get_receipt(command_id)

    def list_unadopted_projection_person_ids(self, *, limit: int = 1_000) -> list[str]:
        return self.repository.list_unadopted_projection_person_ids(limit=limit)


__all__ = ["LifeSnapshotReader"]
