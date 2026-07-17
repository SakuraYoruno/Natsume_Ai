"""Small background scheduler for projection refresh jobs.

HTTP handlers never call these jobs.  Applications start the scheduler during
router startup; tests and operators may call ``run_projection_jobs_once``.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from host_brain.observability.projection_store import ProjectionLeaderLease, QueryProjectionStore


logger = logging.getLogger("Natsume.obs.projection")


class ProjectionJobDaemon:
    """One application-owned projection scheduler.

    The old module-level scheduler made two observability applications share
    jobs, stop events, threads and leader leases.  Consequently, stopping one
    application could stop (or release the lease owned by) another.  This
    object owns every mutable lifecycle field and can therefore be placed in
    an ``ObsApplicationContainer`` without creating another process locator.
    """

    def __init__(self, *, owner_id: str | None = None) -> None:
        self._jobs: dict[str, Callable[[], object]] = {}
        self._jobs_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._owner_id = str(owner_id or f"projection-daemon:{uuid.uuid4().hex}")
        self._active_store: QueryProjectionStore | None = None
        self._active_lease: ProjectionLeaderLease | None = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def register(self, name: str, callback: Callable[[], object]) -> None:
        key = str(name or "").strip()
        if not key:
            raise ValueError("projection job name is required")
        if not callable(callback):
            raise TypeError("projection job callback must be callable")
        with self._jobs_lock:
            self._jobs[key] = callback

    def run_once(
        self,
        *,
        store: QueryProjectionStore,
        owner_id: str | None = None,
        lease_ms: int = 30_000,
    ) -> dict[str, str]:
        with self._jobs_lock:
            jobs = list(self._jobs.items())
        if not jobs:
            return {}
        results: dict[str, str] = {}
        try:
            lease = store.acquire_leader_lease(
                str(owner_id or self._owner_id),
                lease_ms=lease_ms,
            )
        except Exception as exc:
            detail = f"LEASE_ERROR: {type(exc).__name__}: {exc}"
            logger.warning("projection leader lease failed: error=%s", exc)
            return {name: detail for name, _callback in jobs}
        if lease is None:
            return {name: "SKIPPED_NOT_LEADER" for name, _callback in jobs}
        self._active_store = store
        self._active_lease = lease
        for name, callback in jobs:
            renewed = store.renew_leader_lease(lease, lease_ms=lease_ms)
            if renewed is None:
                results[name] = "SKIPPED_LEASE_LOST"
                continue
            lease = renewed
            self._active_lease = renewed
            try:
                with store.bind_leader_lease(renewed):
                    callback()
                results[name] = "OK"
            except Exception as exc:
                results[name] = f"{type(exc).__name__}: {exc}"
                logger.warning("projection refresh job failed: job=%s error=%s", name, exc)
        return results

    def start(
        self,
        *,
        store: QueryProjectionStore,
        owner_id: str | None = None,
        interval_ms: int = 1000,
        lease_ms: int = 30_000,
    ) -> dict[str, object]:
        if self.running:
            return {"started": False, "running": True}
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                self.run_once(
                    store=store,
                    owner_id=owner_id,
                    lease_ms=max(int(lease_ms), int(interval_ms) * 3),
                )
                self._stop.wait(max(0.1, int(interval_ms) / 1000.0))

        self._thread = threading.Thread(
            target=loop,
            name=f"operator-projection-worker:{self._owner_id}",
            daemon=True,
        )
        self._thread.start()
        return {"started": True, "running": True}

    def stop(self, *, timeout_s: float = 2.0) -> dict[str, object]:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, float(timeout_s)))
            if thread.is_alive():
                return {
                    "running": True,
                    "reason_code": "OBS_PROJECTION_STOP_TIMEOUT",
                }
        if self._active_store is not None and self._active_lease is not None:
            try:
                self._active_store.release_leader_lease(self._active_lease)
            except Exception as exc:
                logger.warning("projection leader lease release failed: error=%s", exc)
        self._active_store = None
        self._active_lease = None
        self._thread = None
        return {"running": False}


_default_daemon = ProjectionJobDaemon()


def register_projection_job(name: str, callback: Callable[[], object]) -> None:
    """Compatibility registration for legacy Host-owned projection jobs."""

    _default_daemon.register(name, callback)


def _default_store() -> QueryProjectionStore:
    from host_brain.composition.process_services import get_query_projection_store

    return get_query_projection_store()


def run_projection_jobs_once(
    *,
    store: QueryProjectionStore | None = None,
    owner_id: str | None = None,
    lease_ms: int = 30_000,
) -> dict[str, str]:
    """Run jobs only while this process holds the durable leader fence."""

    projection_store = store or _default_store()
    return _default_daemon.run_once(
        store=projection_store,
        owner_id=owner_id,
        lease_ms=lease_ms,
    )


def start_projection_jobs(
    *,
    interval_ms: int = 1000,
    store: QueryProjectionStore | None = None,
    owner_id: str | None = None,
    lease_ms: int = 30_000,
) -> dict[str, object]:
    projection_store = store or _default_store()
    return _default_daemon.start(
        store=projection_store,
        owner_id=owner_id,
        interval_ms=interval_ms,
        lease_ms=lease_ms,
    )


def stop_projection_jobs() -> dict[str, object]:
    return _default_daemon.stop()


__all__ = [
    "ProjectionJobDaemon",
    "register_projection_job",
    "run_projection_jobs_once",
    "start_projection_jobs",
    "stop_projection_jobs",
]
