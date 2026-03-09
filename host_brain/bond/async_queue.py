"""Async queue for bond update writes."""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable, Mapping

_logger = logging.getLogger("Natsume.bond_async_queue")


class BondUpdateQueue:
    """Single in-process queue for bond updates."""

    def __init__(
        self,
        *,
        writer_factory: Callable[[], Any],
        max_queue_size: int = 1024,
        worker_enabled: bool = True,
        poll_interval_s: float = 0.1,
    ) -> None:
        self.writer_factory = writer_factory
        self.max_queue_size = max(1, int(max_queue_size))
        self.worker_enabled = bool(worker_enabled)
        self.poll_interval_s = max(0.01, float(poll_interval_s))
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=self.max_queue_size)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._started = False
        self._queued_count = 0
        self._processed_count = 0
        self._failed_count = 0
        self._dropped_count = 0
        self._last_error: str | None = None

    def _ensure_worker_started(self) -> None:
        if not self.worker_enabled:
            return
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="bond-update-queue-worker",
                daemon=True,
            )
            self._worker_thread.start()
            self._started = True

    def enqueue(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        task = dict(payload or {})
        if not task:
            self._dropped_count += 1
            return {
                "status": "DROPPED",
                "reason_code": "BOND_QUEUE_EMPTY_PAYLOAD",
                "queue_size": int(self._queue.qsize()),
            }
        self._ensure_worker_started()
        try:
            self._queue.put_nowait(task)
        except queue.Full:
            self._dropped_count += 1
            return {
                "status": "DEGRADED",
                "reason_code": "BOND_QUEUE_FULL",
                "queue_size": int(self._queue.qsize()),
            }
        self._queued_count += 1
        return {
            "status": "QUEUED",
            "reason_code": None,
            "queue_size": int(self._queue.qsize()),
        }

    def _worker_loop(self) -> None:
        try:
            writer = self.writer_factory()
        except Exception as exc:  # pragma: no cover - defensive guard
            self._last_error = str(exc)
            _logger.error("bond queue worker init failed: %s", exc)
            return

        while not self._stop_event.is_set():
            try:
                task = self._queue.get(timeout=self.poll_interval_s)
            except queue.Empty:
                continue

            try:
                if task is None:
                    break
                writer.apply_update(**dict(task))
                self._processed_count += 1
            except Exception as exc:  # pragma: no cover - defensive guard
                self._failed_count += 1
                self._last_error = str(exc)
                _logger.warning("bond queue task failed: %s", exc)
            finally:
                self._queue.task_done()

    def drain(self, *, timeout_s: float = 3.0) -> bool:
        deadline = time.time() + max(0.1, float(timeout_s))
        while time.time() < deadline:
            unfinished = int(getattr(self._queue, "unfinished_tasks", 0))
            if unfinished <= 0 and self._queue.empty():
                return True
            time.sleep(0.01)
        unfinished = int(getattr(self._queue, "unfinished_tasks", 0))
        return unfinished <= 0 and self._queue.empty()

    def snapshot(self) -> dict[str, Any]:
        return {
            "started": bool(self._started),
            "worker_enabled": bool(self.worker_enabled),
            "queue_size": int(self._queue.qsize()),
            "max_queue_size": int(self.max_queue_size),
            "queued_count": int(self._queued_count),
            "processed_count": int(self._processed_count),
            "failed_count": int(self._failed_count),
            "dropped_count": int(self._dropped_count),
            "last_error": self._last_error,
        }

    def shutdown(self) -> None:
        self._stop_event.set()
        if self.worker_enabled:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
            worker = self._worker_thread
            if worker is not None and worker.is_alive():
                worker.join(timeout=0.5)
