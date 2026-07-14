from __future__ import annotations

from typing import Any

from host_brain.eventing.runtime_event_projection import RuntimeEventProjectionWorker
from host_brain.integrations.runtime_events import RuntimeOutboxPublisher


class FakeProjectionStore:
    def __init__(self) -> None:
        self.pending = [
            {
                "delivery_id": "delivery://1",
                "event_cursor": 1,
                "claim_token": "claim-1",
            },
            {
                "delivery_id": "delivery://2",
                "event_cursor": 2,
                "claim_token": "claim-2",
            },
        ]
        self.completed: list[str] = []

    def claim_projection_delivery(
        self,
        *,
        worker_id: str,
        lease_ms: int,
    ) -> dict[str, Any] | None:
        assert worker_id == "worker-1"
        assert lease_ms == 50
        return self.pending.pop(0) if self.pending else None

    def complete_projection_delivery(
        self,
        *,
        delivery_id: str,
        worker_id: str,
        claim_token: str,
    ) -> dict[str, Any]:
        assert worker_id == "worker-1"
        assert claim_token == f"claim-{delivery_id[-1]}"
        self.completed.append(delivery_id)
        return {
            "delivery_id": delivery_id,
            "event_cursor": int(delivery_id[-1]),
            "applied": True,
            "projection": {"last_event_cursor": int(delivery_id[-1])},
        }


def test_aw03_projection_worker_owns_no_cursor_and_drains_durable_claims() -> None:
    store = FakeProjectionStore()
    worker = RuntimeEventProjectionWorker(
        store,
        worker_id="worker-1",
        lease_ms=50,
    )

    result = worker.drain(limit=10)

    assert result["processed_count"] == 2
    assert store.completed == ["delivery://1", "delivery://2"]
    assert not hasattr(worker, "cursor")
    assert not hasattr(worker, "queue")


class EmptyTaskStore:
    def list_pending_outbox(self, *, limit: int) -> list[dict[str, Any]]:
        assert limit == 10
        return []


class CrashingProjectionWorker:
    def drain(self, *, limit: int) -> dict[str, Any]:
        raise OSError("simulated projection process failure")


def test_aw03_committed_journal_publish_is_not_reported_failed_by_projection_crash() -> None:
    publisher = RuntimeOutboxPublisher(
        task_store=EmptyTaskStore(),
        event_store=object(),  # type: ignore[arg-type]
        projection_worker=CrashingProjectionWorker(),
    )

    result = publisher.publish_pending(limit=10)

    assert result["status"] == "OK"
    assert result["published_count"] == 0
    assert result["projection"] == {
        "status": "DEGRADED",
        "reason_code": "RUNTIME_EVENT_PROJECTION_FAILED",
        "detail": "OSError: projection delivery remains durable",
        "processed_count": 0,
    }

