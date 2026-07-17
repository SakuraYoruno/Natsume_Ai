"""Application-owned background scheduler for projection refresh jobs."""

from __future__ import annotations

import logging
import threading
import time
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

    def __init__(
        self,
        *,
        owner_id: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._jobs: dict[str, tuple[Callable[[], object], int]] = {}
        self._last_started: dict[str, float] = {}
        self._jobs_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lifecycle_state = "READY"
        self._owner_id = str(owner_id or f"projection-daemon:{uuid.uuid4().hex}")
        self._clock = clock
        self._active_store: QueryProjectionStore | None = None
        self._active_lease: ProjectionLeaderLease | None = None

    @property
    def running(self) -> bool:
        with self._lifecycle_lock:
            thread = self._thread
            return bool(thread is not None and thread.is_alive())

    def register(
        self,
        name: str,
        callback: Callable[[], object],
        *,
        min_interval_ms: int = 0,
    ) -> None:
        key = str(name or "").strip()
        if not key:
            raise ValueError("projection job name is required")
        if not callable(callback):
            raise TypeError("projection job callback must be callable")
        cadence = int(min_interval_ms)
        if cadence < 0:
            raise ValueError("projection job min_interval_ms must be non-negative")
        with self._jobs_lock:
            self._jobs[key] = (callback, cadence)

    def run_once(
        self,
        *,
        store: QueryProjectionStore,
        owner_id: str | None = None,
        lease_ms: int = 30_000,
    ) -> dict[str, str]:
        """Run one serialized wave unless shutdown has fenced new work."""

        with self._jobs_lock:
            names = tuple(self._jobs)
        with self._lifecycle_lock:
            state = self._lifecycle_state
        if state in {"STOPPING", "STOPPED"}:
            return {name: f"SKIPPED_{state}" for name in names}

        self._run_lock.acquire()
        try:
            # A stop may have begun while this caller waited behind another
            # wave.  Recheck after acquiring the single-flight fence.
            with self._lifecycle_lock:
                state = self._lifecycle_state
            if state in {"STOPPING", "STOPPED"}:
                return {name: f"SKIPPED_{state}" for name in names}
            return self._run_jobs_once(
                store=store,
                owner_id=owner_id,
                lease_ms=lease_ms,
            )
        finally:
            self._run_lock.release()

    def _run_jobs_once(
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
        now = float(self._clock())
        results: dict[str, str] = {}
        due_jobs: list[tuple[str, Callable[[], object]]] = []
        with self._jobs_lock:
            for name, (callback, min_interval_ms) in jobs:
                previous = self._last_started.get(name)
                if (
                    previous is not None
                    and min_interval_ms > 0
                    and (now - previous) * 1000.0 < min_interval_ms
                ):
                    results[name] = "SKIPPED_NOT_DUE"
                else:
                    due_jobs.append((name, callback))
        if not due_jobs:
            return results
        try:
            lease = store.acquire_leader_lease(
                str(owner_id or self._owner_id),
                lease_ms=lease_ms,
            )
        except Exception as exc:
            detail = f"LEASE_ERROR: {type(exc).__name__}: {exc}"
            logger.warning("projection leader lease failed: error=%s", exc)
            results.update({name: detail for name, _callback in due_jobs})
            return results
        if lease is None:
            results.update(
                {name: "SKIPPED_NOT_LEADER" for name, _callback in due_jobs}
            )
            return results
        self._active_store = store
        self._active_lease = lease
        for name, callback in due_jobs:
            renewed = store.renew_leader_lease(lease, lease_ms=lease_ms)
            if renewed is None:
                results[name] = "SKIPPED_LEASE_LOST"
                continue
            lease = renewed
            self._active_lease = renewed
            with self._jobs_lock:
                self._last_started[name] = float(self._clock())
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
        with self._lifecycle_lock:
            if self.running:
                return {"started": False, "running": True}
            if self._lifecycle_state == "STOPPING":
                return {
                    "started": False,
                    "running": False,
                    "reason_code": "OBS_PROJECTION_STOP_IN_PROGRESS",
                }
            self._stop.clear()
            self._lifecycle_state = "RUNNING"

            def loop() -> None:
                interval_s = max(0.1, int(interval_ms) / 1000.0)
                # Do not begin a potentially slow projection wave during ASGI
                # construction.  The first bounded wait makes immediate
                # shutdown quiescent and leaves readiness free to report
                # REBUILDING until the first scheduled refresh.
                while not self._stop.wait(interval_s):
                    self.run_once(
                        store=store,
                        owner_id=owner_id,
                        lease_ms=max(int(lease_ms), int(interval_ms) * 3),
                    )

            thread = threading.Thread(
                target=loop,
                name=f"operator-projection-worker:{self._owner_id}",
                daemon=True,
            )
            self._thread = thread
            try:
                thread.start()
            except BaseException:
                self._thread = None
                self._stop.set()
                self._lifecycle_state = "STOPPED"
                raise
            return {"started": True, "running": True}

    def stop(self, *, timeout_s: float = 2.0) -> dict[str, object]:
        timeout = max(0.0, float(timeout_s))
        deadline = time.monotonic() + timeout
        with self._lifecycle_lock:
            if self._lifecycle_state == "STOPPED":
                return {"running": False}
            self._lifecycle_state = "STOPPING"
            self._stop.set()
            thread = self._thread

        if thread is threading.current_thread():
            return {
                "running": True,
                "reason_code": "OBS_PROJECTION_STOP_TIMEOUT",
            }
        if thread is not None:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                return {
                    "running": True,
                    "reason_code": "OBS_PROJECTION_STOP_TIMEOUT",
                }

        acquired = self._run_lock.acquire(
            timeout=max(0.0, deadline - time.monotonic())
        )
        if not acquired:
            # A manual or background run still owns the write fence.  Keep the
            # leader lease and backing resources alive until a later stop can
            # prove the callback quiescent.
            return {
                "running": True,
                "reason_code": "OBS_PROJECTION_STOP_TIMEOUT",
            }
        try:
            if self._active_store is not None and self._active_lease is not None:
                try:
                    self._active_store.release_leader_lease(self._active_lease)
                except Exception as exc:
                    logger.warning(
                        "projection leader lease release failed: error=%s",
                        exc,
                    )
            self._active_store = None
            self._active_lease = None
            with self._lifecycle_lock:
                self._thread = None
                self._lifecycle_state = "STOPPED"
            return {"running": False}
        finally:
            self._run_lock.release()


__all__ = ["ProjectionJobDaemon"]
