"""Small sync/async lease heartbeats for fenced Life dispatch handlers."""

from __future__ import annotations

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
        # Lease renewal must not depend on the handler's event loop or its
        # shared default executor.  A busy loop/executor can otherwise starve
        # ``asyncio.to_thread`` beyond the lease and fence an otherwise healthy
        # handler.  The synchronous heartbeat already owns a dedicated daemon
        # thread and provides the required fail-closed shutdown semantics.
        self._heartbeat = SyncLifeLeaseHeartbeat(renew, lease_ms=lease_ms)

    async def __aenter__(self) -> "AsyncLifeLeaseHeartbeat":
        self._heartbeat.__enter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return self._heartbeat.__exit__(exc_type, exc, traceback)


__all__ = [
    "AsyncLifeLeaseHeartbeat",
    "LifeLeaseHeartbeatError",
    "SyncLifeLeaseHeartbeat",
    "heartbeat_interval_seconds",
]
