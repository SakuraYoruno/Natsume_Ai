"""Small sync/async lease heartbeats for fenced Life dispatch handlers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import threading
from types import TracebackType


class LifeLeaseHeartbeatError(RuntimeError):
    """The handler must not commit because its lease heartbeat failed."""


def heartbeat_interval_seconds(lease_ms: int) -> float:
    # Default leases heartbeat at 1s; short test/repair leases remain practical.
    return max(0.01, min(1.0, max(1, int(lease_ms)) / 3_000.0))


class SyncLifeLeaseHeartbeat:
    def __init__(self, renew: Callable[[], None], *, lease_ms: int) -> None:
        self._renew = renew
        self._interval = heartbeat_interval_seconds(lease_ms)
        self._stop = threading.Event()
        self._failure: BaseException | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="life-lease-heartbeat",
            daemon=True,
        )

    def __enter__(self) -> "SyncLifeLeaseHeartbeat":
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_type, exc, traceback
        self._stop.set()
        self._thread.join(timeout=max(1.0, self._interval * 3.0))
        if self._thread.is_alive():
            raise LifeLeaseHeartbeatError("Life lease heartbeat did not stop")
        if self._failure is not None:
            raise LifeLeaseHeartbeatError("Life lease heartbeat renewal failed") from self._failure
        return False

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._renew()
            except BaseException as exc:  # propagate on the handler thread
                self._failure = exc
                self._stop.set()
                return


class AsyncLifeLeaseHeartbeat:
    def __init__(self, renew: Callable[[], None], *, lease_ms: int) -> None:
        self._renew = renew
        self._interval = heartbeat_interval_seconds(lease_ms)
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "AsyncLifeLeaseHeartbeat":
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_type, exc, traceback
        self._stop.set()
        task = self._task
        if task is not None:
            try:
                await task
            except BaseException as heartbeat_error:
                raise LifeLeaseHeartbeatError(
                    "Life lease heartbeat renewal failed"
                ) from heartbeat_error
        return False

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except TimeoutError:
                await asyncio.to_thread(self._renew)


__all__ = [
    "AsyncLifeLeaseHeartbeat",
    "LifeLeaseHeartbeatError",
    "SyncLifeLeaseHeartbeat",
    "heartbeat_interval_seconds",
]
