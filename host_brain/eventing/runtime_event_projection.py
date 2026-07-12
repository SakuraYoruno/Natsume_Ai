"""Durable Task Runtime projection worker.

The worker has no in-memory queue or cursor.  Every unit of work is claimed
from the 0068 outbox, fenced by a lease token, and deduplicated by the durable
projection inbox.  A process crash therefore leaves recoverable durable state
instead of an in-process delivery truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4


class RuntimeEventProjectionStore(Protocol):
    def claim_projection_delivery(
        self,
        *,
        worker_id: str,
        lease_ms: int,
    ) -> dict[str, Any] | None: ...

    def complete_projection_delivery(
        self,
        *,
        delivery_id: str,
        worker_id: str,
        claim_token: str,
    ) -> dict[str, Any]: ...


@dataclass(slots=True)
class RuntimeEventProjectionWorker:
    """Synchronous claim/apply loop suitable for Task Runtime publish points."""

    event_store: RuntimeEventProjectionStore
    worker_id: str = field(
        default_factory=lambda: f"runtime-event-projection::{uuid4().hex}"
    )
    lease_ms: int = 30_000

    def __post_init__(self) -> None:
        self.worker_id = str(self.worker_id or "").strip()
        if not self.worker_id:
            raise ValueError("worker_id is required")
        self.lease_ms = max(1, int(self.lease_ms))

    def run_once(self) -> dict[str, Any]:
        claimed = self.event_store.claim_projection_delivery(
            worker_id=self.worker_id,
            lease_ms=self.lease_ms,
        )
        if claimed is None:
            return {"status": "IDLE", "processed_count": 0}
        completed = self.event_store.complete_projection_delivery(
            delivery_id=str(claimed["delivery_id"]),
            worker_id=self.worker_id,
            claim_token=str(claimed["claim_token"]),
        )
        return {
            "status": "COMPLETED",
            "processed_count": 1,
            "delivery_id": str(completed["delivery_id"]),
            "event_cursor": int(completed["event_cursor"]),
            "applied": bool(completed["applied"]),
            "projection": completed.get("projection"),
        }

    def drain(self, *, limit: int = 100) -> dict[str, Any]:
        size = min(10_000, max(1, int(limit)))
        processed: list[dict[str, Any]] = []
        for _ in range(size):
            result = self.run_once()
            if result["status"] == "IDLE":
                break
            processed.append(result)
        return {
            "status": "OK",
            "processed_count": len(processed),
            "processed": processed,
        }


__all__ = ["RuntimeEventProjectionStore", "RuntimeEventProjectionWorker"]
