from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.bond.async_queue import BondUpdateQueue, RetiredBondUpdateQueue


class _FakeWriter:
    def __init__(self, sink: list[dict], *, raise_error: bool = False, delay_s: float = 0.0) -> None:
        self._sink = sink
        self._raise_error = raise_error
        self._delay_s = delay_s

    def apply_update(self, **kwargs):  # noqa: ANN003
        if self._delay_s > 0:
            time.sleep(self._delay_s)
        if self._raise_error:
            raise RuntimeError("bond writer failed")
        self._sink.append(dict(kwargs))
        return {"status": "OK"}


def test_bond_async_queue_processes_enqueued_task() -> None:
    sink: list[dict] = []
    queue = BondUpdateQueue(
        writer_factory=lambda: _FakeWriter(sink),
        max_queue_size=8,
        worker_enabled=True,
    )
    try:
        result = queue.enqueue(
            {
                "request_id": "req-bond-1",
                "intent_id": "intent-bond-1",
                "session_id": "sess-bond-1",
                "seq": 1,
                "user_id": "u1",
                "topic_id": "topic",
                "update_kind": "BOND_DEEPEN",
                "evidence_refs": ["evd://1"],
                "emotion_tag": "HAPPY",
                "emotion_intensity": 0.8,
                "ts_ms": 1,
            }
        )
        assert result["status"] == "QUEUED"
        assert queue.drain(timeout_s=2.0) is True
        assert len(sink) == 1
        snapshot = queue.snapshot()
        assert snapshot["processed_count"] >= 1
        assert snapshot["failed_count"] == 0
    finally:
        queue.shutdown()


def test_bond_async_queue_full_returns_degraded_when_worker_disabled() -> None:
    sink: list[dict] = []
    queue = BondUpdateQueue(
        writer_factory=lambda: _FakeWriter(sink),
        max_queue_size=1,
        worker_enabled=False,
    )
    try:
        first = queue.enqueue({"request_id": "req-1"})
        second = queue.enqueue({"request_id": "req-2"})
        assert first["status"] == "QUEUED"
        assert second["status"] == "DEGRADED"
        assert second["reason_code"] == "BOND_QUEUE_FULL"
        snapshot = queue.snapshot()
        assert snapshot["dropped_count"] >= 1
    finally:
        queue.shutdown()


def test_bond_async_queue_task_failure_updates_failed_count() -> None:
    sink: list[dict] = []
    queue = BondUpdateQueue(
        writer_factory=lambda: _FakeWriter(sink, raise_error=True),
        max_queue_size=8,
        worker_enabled=True,
    )
    try:
        result = queue.enqueue({"request_id": "req-fail"})
        assert result["status"] == "QUEUED"
        assert queue.drain(timeout_s=2.0) is True
        snapshot = queue.snapshot()
        assert snapshot["failed_count"] >= 1
        assert snapshot["last_error"] is not None
        assert sink == []
    finally:
        queue.shutdown()


def test_bond_queue_rejects_enqueue_after_exact_shutdown() -> None:
    queue = BondUpdateQueue(
        writer_factory=lambda: object(),
        max_queue_size=2,
        worker_enabled=False,
    )

    assert queue.shutdown() is True
    result = queue.enqueue({"request_id": "req-after-close"})

    assert result["status"] == "DEGRADED"
    assert result["reason_code"] == "BOND_QUEUE_CLOSED"
    assert queue.snapshot()["closed"] is True
    assert queue.snapshot()["queue_size"] == 0


def test_retired_bond_queue_has_no_worker_or_process_backlog() -> None:
    queue = RetiredBondUpdateQueue()

    result = queue.enqueue({"request_id": "legacy-queue-attempt"})

    assert result["status"] == "RETIRED_CANONICAL_LIFE"
    assert result["reason_code"] == "LEGACY_BOND_QUEUE_RETIRED"
    assert result["accepted"] is False
    assert queue.snapshot()["worker_alive"] is False
    assert queue.snapshot()["queue_size"] == 0
    assert queue.drain() is True
    assert queue.shutdown() is True
