"""Composition-ready durable Host embodiment outbox and single dispatcher."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from natsume_contracts.edge.embodiment_v1 import (
    EmbodimentCommand,
    EmbodimentCommandPort,
    parse_embodiment_command,
)

from host_brain.channels.embodiment import (
    EmbodimentOutboxAck,
    submit_embodiment_command,
)
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)


class DurableEmbodimentOutbox:
    def __init__(self, repository: EmbodimentOutboxRepository) -> None:
        self._repository = repository

    def enqueue(self, command: EmbodimentCommand) -> EmbodimentOutboxAck:
        row = self._repository.enqueue(command.as_dict())
        return EmbodimentOutboxAck(
            outbox_id=str(row["outbox_id"]),
            command_id=str(row["command_id"]),
            idempotency_key=str(row["idempotency_key"]),
            status=str(row["status"]),
            reason_code=(str(row["reason_code"]) if row.get("reason_code") else None),
        )


@dataclass(frozen=True, slots=True)
class EmbodimentDispatchBatch:
    claimed: int
    applied: int
    rejected: int
    in_doubt: int

    def as_dict(self) -> dict[str, int | str]:
        return {
            "status": "SUCCEEDED" if self.rejected == 0 and self.in_doubt == 0 else "DEGRADED",
            "claimed": self.claimed,
            "applied": self.applied,
            "rejected": self.rejected,
            "in_doubt": self.in_doubt,
        }


class EmbodimentOutboxDispatcher:
    def __init__(
        self,
        repository: EmbodimentOutboxRepository,
        *,
        worker_id: str,
        command_port: EmbodimentCommandPort | None = None,
    ) -> None:
        self._repository = repository
        self._worker_id = str(worker_id or "host-embodiment-dispatcher")
        self._command_port = command_port

    async def dispatch_pending_async(self, *, limit: int = 100) -> EmbodimentDispatchBatch:
        claimed = applied = rejected = in_doubt = 0
        for _ in range(max(0, int(limit))):
            row = self._repository.claim_next(worker_id=self._worker_id)
            if row is None:
                break
            claimed += 1
            command = parse_embodiment_command(dict(row["command"]))
            receipt = await submit_embodiment_command(
                command,
                command_port=self._command_port,
            )
            self._repository.complete(
                outbox_id=str(row["outbox_id"]),
                claim_token=str(row["claim_token"]),
                receipt=receipt.as_dict(),
            )
            status = str(receipt.status or "").strip().upper()
            if receipt.accepted and status in {"APPLIED", "COMPLETED", "SUCCEEDED"}:
                applied += 1
            elif status == "IN_DOUBT":
                in_doubt += 1
            else:
                rejected += 1
        return EmbodimentDispatchBatch(claimed, applied, rejected, in_doubt)


__all__ = [
    "DurableEmbodimentOutbox",
    "EmbodimentDispatchBatch",
    "EmbodimentOutboxDispatcher",
]
