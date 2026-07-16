from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from host_brain.eventing.runtime_event_projection import RuntimeEventProjectionWorker
from host_brain.integrations.runtime_events import (
    RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED,
    RuntimeEventError,
    RuntimeOutboxPublisher,
)


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
    def claim_pending_outbox(
        self,
        *,
        worker_id: str,
        lease_ms: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        assert worker_id == "publisher-test"
        assert lease_ms == 50
        assert limit == 1
        return []

    def inspect_outbox_delivery_backlog(self) -> dict[str, int | None]:
        return {
            "total_pending": 0,
            "delivery_ready_count": 0,
            "next_wakeup_ts_ms": None,
        }


class CrashingProjectionWorker:
    def drain(self, *, limit: int) -> dict[str, Any]:
        raise OSError("simulated projection process failure")


def test_aw03_committed_journal_publish_is_not_reported_failed_by_projection_crash() -> None:
    publisher = RuntimeOutboxPublisher(
        task_store=EmptyTaskStore(),
        event_store=object(),  # type: ignore[arg-type]
        projection_worker=CrashingProjectionWorker(),
        worker_id="publisher-test",
        claim_lease_ms=50,
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


class ClaimedTaskStore:
    def __init__(self) -> None:
        self.completed: list[dict[str, Any]] = []
        self.released: list[dict[str, Any]] = []
        self.claim_available = True

    @staticmethod
    def _claim() -> dict[str, Any]:
        return {
            "outbox_id": "outbox-1",
            "status": "PENDING",
            "status_version": 1,
            "claim_owner": "publisher-1",
            "claim_token": "claim-token-1",
            "claim_fencing_token": 1,
        }

    def claim_pending_outbox(
        self,
        *,
        worker_id: str,
        lease_ms: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        assert (worker_id, lease_ms, limit) == ("publisher-1", 500, 1)
        if not self.claim_available:
            return []
        self.claim_available = False
        return [self._claim()]

    def complete_outbox_claim(self, outbox_id: str, **values: Any) -> dict[str, Any]:
        self.completed.append({"outbox_id": outbox_id, **values})
        return {"status": "PUBLISHED", "status_version": 2}

    def inspect_outbox_delivery_backlog(self) -> dict[str, int | None]:
        if self.completed:
            return {
                "total_pending": 0,
                "delivery_ready_count": 0,
                "next_wakeup_ts_ms": None,
            }
        return {
            "total_pending": 1,
            "delivery_ready_count": 0,
            "next_wakeup_ts_ms": 1_000,
        }

    def release_outbox_claim(self, outbox_id: str, **values: Any) -> dict[str, Any]:
        self.released.append({"outbox_id": outbox_id, **values})
        return {"status": "PENDING", "status_version": 2}

    def list_pending_outbox(self, **_values: Any) -> Any:
        raise AssertionError("publisher must not use the unfenced legacy list path")

    def mark_outbox_published(self, *_args: Any, **_values: Any) -> Any:
        raise AssertionError("publisher must not use the unfenced legacy mark path")


class RecordingEventStore:
    def append_task_outbox(self, row: dict[str, Any]) -> dict[str, Any]:
        assert row["claim_token"] == "claim-token-1"
        return {
            "cursor": 7,
            "event_id": "runtimeevent://outbox/1",
            "dedupe_key": "sha256:" + "a" * 64,
        }


class HealthyProjectionWorker:
    def drain(self, *, limit: int) -> dict[str, Any]:
        return {"status": "OK", "processed_count": 0, "limit": limit}


def test_aw03_task_outbox_publisher_uses_only_durable_claim_fences() -> None:
    store = ClaimedTaskStore()
    publisher = RuntimeOutboxPublisher(
        task_store=store,
        event_store=RecordingEventStore(),  # type: ignore[arg-type]
        projection_worker=HealthyProjectionWorker(),
        worker_id="publisher-1",
        claim_lease_ms=500,
    )

    result = publisher.publish_pending(limit=3)

    assert result["pending_scanned"] == 1
    assert result["published_count"] == 1
    assert result["published"][0]["outbox_status"] == "PUBLISHED"
    assert store.released == []
    assert store.completed == [
        {
            "outbox_id": "outbox-1",
            "worker_id": "publisher-1",
            "claim_token": "claim-token-1",
            "fencing_token": 1,
            "expected_version": 1,
        }
    ]


class BacklogReadFailureStore(ClaimedTaskStore):
    def inspect_outbox_delivery_backlog(self) -> dict[str, int | None]:
        raise OSError("private backlog read detail")


def test_committed_outbox_publish_is_not_falsely_failed_by_backlog_read() -> None:
    store = BacklogReadFailureStore()
    publisher = RuntimeOutboxPublisher(
        task_store=store,
        event_store=RecordingEventStore(),  # type: ignore[arg-type]
        projection_worker=HealthyProjectionWorker(),
        worker_id="publisher-1",
        claim_lease_ms=500,
    )

    result = publisher.publish_pending(limit=1)

    assert result["status"] == "DEGRADED"
    assert result["published_count"] == 1
    assert result["backlog"] is None
    assert result["backlog_error"] == {
        "reason_code": "RUNTIME_EVENT_OUTBOX_BACKLOG_UNAVAILABLE"
    }
    assert "private backlog read detail" not in str(result)


class BlockingEventStore(RecordingEventStore):
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def append_task_outbox(self, row: dict[str, Any]) -> dict[str, Any]:
        self.entered.set()
        assert self.release.wait(timeout=2)
        return super().append_task_outbox(row)


def test_close_waits_for_inflight_foreground_publish_before_returning() -> None:
    store = ClaimedTaskStore()
    event_store = BlockingEventStore()
    publisher = RuntimeOutboxPublisher(
        task_store=store,
        event_store=event_store,  # type: ignore[arg-type]
        projection_worker=HealthyProjectionWorker(),
        worker_id="publisher-1",
        claim_lease_ms=500,
    )
    publish_result: list[dict[str, Any]] = []
    publish_failure: list[BaseException] = []

    def publish() -> None:
        try:
            publish_result.append(publisher.publish_pending(limit=1))
        except BaseException as exc:  # pragma: no cover - assertion reports detail
            publish_failure.append(exc)

    foreground = threading.Thread(target=publish)
    foreground.start()
    assert event_store.entered.wait(timeout=1)

    closer = threading.Thread(target=publisher.close)
    closer.start()
    time.sleep(0.02)
    assert closer.is_alive()

    event_store.release.set()
    foreground.join(timeout=2)
    closer.join(timeout=2)

    assert not foreground.is_alive()
    assert not closer.is_alive()
    assert publish_failure == []
    assert publish_result[0]["published_count"] == 1
    with pytest.raises(RuntimeEventError):
        publisher.publish_pending(limit=1)


class FailingEventStore:
    def append_task_outbox(self, _row: dict[str, Any]) -> dict[str, Any]:
        raise OSError("private transport detail")


def test_aw03_publish_failure_releases_exact_claim_without_persisting_detail() -> None:
    store = ClaimedTaskStore()
    publisher = RuntimeOutboxPublisher(
        task_store=store,
        event_store=FailingEventStore(),  # type: ignore[arg-type]
        projection_worker=HealthyProjectionWorker(),
        worker_id="publisher-1",
        claim_lease_ms=500,
        retry_delay_ms=17,
    )

    with pytest.raises(RuntimeEventError) as caught:
        publisher.publish_pending(limit=3)

    assert caught.value.reason_code == RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED
    assert "private transport detail" not in caught.value.detail
    assert store.completed == []
    assert store.released == [
        {
            "outbox_id": "outbox-1",
            "worker_id": "publisher-1",
            "claim_token": "claim-token-1",
            "fencing_token": 1,
            "expected_version": 1,
            "retry_delay_ms": 17,
            "last_error": "OSError",
        }
    ]


class _MutableClock:
    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


class _ExpiringClaimStore:
    def __init__(self, clock: _MutableClock) -> None:
        self.clock = clock
        self.published = threading.Event()
        self.claimed = False

    def claim_pending_outbox(
        self,
        *,
        worker_id: str,
        lease_ms: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        assert (worker_id, lease_ms, limit) == ("publisher-restart", 50, 1)
        if self.claimed or self.clock() < 10:
            return []
        self.claimed = True
        return [
            {
                "outbox_id": "outbox-expired",
                "status": "PENDING",
                "status_version": 2,
                "claim_owner": "publisher-restart",
                "claim_token": "claim-token-restart",
                "claim_fencing_token": 2,
            }
        ]

    def complete_outbox_claim(self, outbox_id: str, **values: Any) -> dict[str, Any]:
        assert outbox_id == "outbox-expired"
        assert values == {
            "worker_id": "publisher-restart",
            "claim_token": "claim-token-restart",
            "fencing_token": 2,
            "expected_version": 2,
        }
        self.published.set()
        return {"status": "PUBLISHED", "status_version": 3}

    def inspect_outbox_delivery_backlog(self) -> dict[str, int | None]:
        if self.published.is_set():
            return {
                "total_pending": 0,
                "delivery_ready_count": 0,
                "next_wakeup_ts_ms": None,
            }
        return {
            "total_pending": 1,
            "delivery_ready_count": 1 if self.clock() >= 10 else 0,
            "next_wakeup_ts_ms": max(10, self.clock()),
        }


class _RestartEventStore:
    def append_task_outbox(self, row: dict[str, Any]) -> dict[str, Any]:
        assert row["claim_token"] == "claim-token-restart"
        return {
            "cursor": 8,
            "event_id": "runtimeevent://outbox/restart",
            "dedupe_key": "sha256:" + "b" * 64,
        }


def test_background_retry_takes_over_after_old_claim_expires_and_closes_cleanly() -> None:
    clock = _MutableClock(0)
    store = _ExpiringClaimStore(clock)
    publisher = RuntimeOutboxPublisher(
        task_store=store,
        event_store=_RestartEventStore(),  # type: ignore[arg-type]
        projection_worker=HealthyProjectionWorker(),
        worker_id="publisher-restart",
        claim_lease_ms=50,
        background_batch_limit=1,
        max_poll_interval_ms=5,
        clock=clock,
    )

    publisher.start()
    time.sleep(0.02)
    assert not store.published.is_set()

    # No command and no explicit wake follows the clock advance.  The bounded
    # poll must discover expiry and take over the durable claim by itself.
    clock.value = 10
    assert store.published.wait(timeout=1)

    publisher.close()
    assert not publisher.background_running
