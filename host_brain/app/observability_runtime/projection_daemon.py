"""Application-owned background scheduler for projection refresh jobs."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from host_brain.observability.projection_store import ProjectionLeaderLease, QueryProjectionStore


logger = logging.getLogger("Natsume.obs.projection")

_RESULT_OK = "OK"
_RESULT_SKIPPED_PREFIX = "SKIPPED_"


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
        wall_clock_ms: Callable[[], int] | None = None,
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
        self._wall_clock_ms = wall_clock_ms or (lambda: int(time.time() * 1000))
        self._active_store: QueryProjectionStore | None = None
        self._active_lease: ProjectionLeaderLease | None = None
        self._generation = 0
        self._wave_in_flight = False
        self._wave_count = 0
        self._consecutive_failure_waves = 0
        self._last_wave: dict[str, object] | None = None
        self._last_failure: dict[str, object] | None = None
        self._last_success_ts_ms: int | None = None
        self._last_success_wave_count: int | None = None
        self._last_failure_wave_count: int | None = None

    @property
    def running(self) -> bool:
        with self._lifecycle_lock:
            thread = self._thread
            return bool(thread is not None and thread.is_alive())

    def health_snapshot(self) -> dict[str, object]:
        """Return a secret-free app-local supervision snapshot."""

        with self._lifecycle_lock:
            thread = self._thread
            running = bool(thread is not None and thread.is_alive())
            lifecycle_state = self._lifecycle_state
            if lifecycle_state == "RUNNING" and not running:
                status = "FAILED"
            elif lifecycle_state == "RUNNING" and self._consecutive_failure_waves > 0:
                status = "DEGRADED"
            else:
                status = lifecycle_state
            last_failure = deepcopy(self._last_failure)
            last_wave = deepcopy(self._last_wave)
            return {
                "status": status,
                "lifecycle_state": lifecycle_state,
                "running": running,
                "generation": self._generation,
                "wave_in_flight": self._wave_in_flight,
                "wave_count": self._wave_count,
                "consecutive_failure_waves": self._consecutive_failure_waves,
                "last_success_ts_ms": self._last_success_ts_ms,
                "recovered_after_last_failure": bool(
                    self._last_failure_wave_count is not None
                    and self._last_success_wave_count is not None
                    and self._last_success_wave_count
                    > self._last_failure_wave_count
                ),
                "last_wave": last_wave,
                "last_failure": last_failure,
            }

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
            with self._lifecycle_lock:
                self._wave_in_flight = True
            try:
                results = self._run_jobs_once(
                    store=store,
                    owner_id=owner_id,
                    lease_ms=lease_ms,
                )
            except BaseException as exc:
                self._record_unexpected_wave_failure(exc)
                raise
            else:
                self._record_wave_results(results)
                return results
            finally:
                with self._lifecycle_lock:
                    self._wave_in_flight = False
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
            detail = f"LEASE_ACQUIRE_ERROR: {type(exc).__name__}"
            logger.warning(
                "projection leader lease failed: error_type=%s",
                type(exc).__name__,
            )
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
            try:
                renewed = store.renew_leader_lease(lease, lease_ms=lease_ms)
            except Exception as exc:
                results[name] = f"LEASE_RENEW_ERROR: {type(exc).__name__}"
                logger.warning(
                    "projection leader lease renew failed: job=%s error_type=%s",
                    name,
                    type(exc).__name__,
                )
                continue
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
                results[name] = _RESULT_OK
            except Exception as exc:
                results[name] = f"JOB_ERROR: {type(exc).__name__}"
                logger.warning(
                    "projection refresh job failed: job=%s error_type=%s",
                    name,
                    type(exc).__name__,
                )
        return results

    @staticmethod
    def _result_error_type(result: str) -> str:
        parts = [
            part.strip()
            for part in str(result or "").split(":")
            if part.strip()
        ]
        if len(parts) >= 2:
            return parts[-1]
        return "ProjectionJobError"

    @staticmethod
    def _summarize_results(results: Mapping[str, str]) -> dict[str, object]:
        ok_jobs: list[str] = []
        skipped_jobs: list[str] = []
        failed_jobs: list[str] = []
        error_types: set[str] = set()
        for name, raw_result in results.items():
            result = str(raw_result or "")
            if result == _RESULT_OK:
                ok_jobs.append(str(name))
            elif result.startswith(_RESULT_SKIPPED_PREFIX):
                skipped_jobs.append(str(name))
            else:
                failed_jobs.append(str(name))
                error_types.add(ProjectionJobDaemon._result_error_type(result))
        return {
            "status": (
                "FAILED"
                if failed_jobs
                else "SUCCESS"
                if ok_jobs
                else "SKIPPED"
            ),
            "job_count": len(results),
            "ok_count": len(ok_jobs),
            "skipped_count": len(skipped_jobs),
            "failed_count": len(failed_jobs),
            "failed_jobs": sorted(failed_jobs),
            "error_types": sorted(error_types),
        }

    def _record_wave_results(self, results: Mapping[str, str]) -> None:
        summary = self._summarize_results(results)
        observed_ts_ms = int(self._wall_clock_ms())
        summary["observed_ts_ms"] = observed_ts_ms
        with self._lifecycle_lock:
            self._wave_count += 1
            summary["wave_sequence"] = self._wave_count
            self._last_wave = summary
            failed_count = int(summary["failed_count"])
            ok_count = int(summary["ok_count"])
            if failed_count > 0:
                self._consecutive_failure_waves += 1
                self._last_failure_wave_count = self._wave_count
                self._last_failure = {
                    "reason_code": "OBS_PROJECTION_WAVE_FAILED",
                    "incident_id": f"obs-projection:{uuid.uuid4().hex}",
                    "observed_ts_ms": observed_ts_ms,
                    "wave_sequence": self._wave_count,
                    "failed_jobs": list(summary["failed_jobs"]),
                    "error_types": list(summary["error_types"]),
                }
            elif ok_count > 0:
                self._consecutive_failure_waves = 0
                self._last_success_ts_ms = observed_ts_ms
                self._last_success_wave_count = self._wave_count

    def _record_unexpected_wave_failure(self, exc: BaseException) -> None:
        observed_ts_ms = int(self._wall_clock_ms())
        error_type = type(exc).__name__
        with self._lifecycle_lock:
            self._wave_count += 1
            self._consecutive_failure_waves += 1
            self._last_failure_wave_count = self._wave_count
            self._last_wave = {
                "status": "FAILED",
                "job_count": 0,
                "ok_count": 0,
                "skipped_count": 0,
                "failed_count": 1,
                "failed_jobs": [],
                "error_types": [error_type],
                "observed_ts_ms": observed_ts_ms,
                "wave_sequence": self._wave_count,
            }
            self._last_failure = {
                "reason_code": "OBS_PROJECTION_WAVE_EXCEPTION",
                "incident_id": f"obs-projection:{uuid.uuid4().hex}",
                "observed_ts_ms": observed_ts_ms,
                "wave_sequence": self._wave_count,
                "failed_jobs": [],
                "error_types": [error_type],
            }

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
            if self._lifecycle_state == "FAILED":
                return {
                    "started": False,
                    "running": False,
                    "reason_code": "OBS_PROJECTION_FAILED_REQUIRES_CLOSE",
                }
            self._stop.clear()
            self._lifecycle_state = "RUNNING"
            self._generation += 1

            def loop() -> None:
                interval_s = max(0.1, int(interval_ms) / 1000.0)
                # Do not begin a potentially slow projection wave during ASGI
                # construction.  The first bounded wait makes immediate
                # shutdown quiescent and leaves readiness free to report
                # REBUILDING until the first scheduled refresh.
                try:
                    while not self._stop.wait(interval_s):
                        try:
                            self.run_once(
                                store=store,
                                owner_id=owner_id,
                                lease_ms=max(
                                    int(lease_ms),
                                    int(interval_ms) * 3,
                                ),
                            )
                        except Exception as exc:
                            logger.error(
                                "projection daemon wave raised unexpectedly: "
                                "error_type=%s",
                                type(exc).__name__,
                            )
                            continue
                        except BaseException:
                            return
                finally:
                    with self._lifecycle_lock:
                        if (
                            self._lifecycle_state == "RUNNING"
                            and not self._stop.is_set()
                        ):
                            self._lifecycle_state = "FAILED"

            thread = threading.Thread(
                target=loop,
                name=f"operator-projection-worker:{self._owner_id}",
                daemon=True,
            )
            self._thread = thread
            try:
                thread.start()
            except BaseException as exc:
                self._thread = None
                self._stop.set()
                self._lifecycle_state = "FAILED"
                self._record_unexpected_wave_failure(exc)
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
