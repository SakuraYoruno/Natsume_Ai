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
_jobs: dict[str, Callable[[], object]] = {}
_jobs_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_process_owner_id = f"projection-daemon:{uuid.uuid4().hex}"
_active_store: QueryProjectionStore | None = None
_active_lease: ProjectionLeaderLease | None = None


def register_projection_job(name: str, callback: Callable[[], object]) -> None:
    key = str(name or "").strip()
    if not key:
        raise ValueError("projection job name is required")
    with _jobs_lock:
        _jobs[key] = callback


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

    global _active_lease, _active_store
    with _jobs_lock:
        jobs = list(_jobs.items())
    if not jobs:
        return {}
    results: dict[str, str] = {}
    try:
        projection_store = store or _default_store()
        lease = projection_store.acquire_leader_lease(
            owner_id or _process_owner_id,
            lease_ms=lease_ms,
        )
    except Exception as exc:
        detail = f"LEASE_ERROR: {type(exc).__name__}: {exc}"
        logger.warning("projection leader lease failed: error=%s", exc)
        return {name: detail for name, _callback in jobs}
    if lease is None:
        return {name: "SKIPPED_NOT_LEADER" for name, _callback in jobs}
    _active_store = projection_store
    _active_lease = lease
    for name, callback in jobs:
        renewed = projection_store.renew_leader_lease(lease, lease_ms=lease_ms)
        if renewed is None:
            results[name] = "SKIPPED_LEASE_LOST"
            continue
        lease = renewed
        _active_lease = renewed
        try:
            with projection_store.bind_leader_lease(renewed):
                callback()
            results[name] = "OK"
        except Exception as exc:
            results[name] = f"{type(exc).__name__}: {exc}"
            logger.warning("projection refresh job failed: job=%s error=%s", name, exc)
    return results


def start_projection_jobs(
    *,
    interval_ms: int = 1000,
    store: QueryProjectionStore | None = None,
    owner_id: str | None = None,
    lease_ms: int = 30_000,
) -> dict[str, object]:
    global _thread
    if _thread is not None and _thread.is_alive():
        return {"started": False, "running": True}
    _stop.clear()

    def loop() -> None:
        while not _stop.is_set():
            run_projection_jobs_once(
                store=store,
                owner_id=owner_id,
                lease_ms=max(int(lease_ms), int(interval_ms) * 3),
            )
            _stop.wait(max(0.1, int(interval_ms) / 1000.0))

    _thread = threading.Thread(target=loop, name="operator-projection-worker", daemon=True)
    _thread.start()
    return {"started": True, "running": True}


def stop_projection_jobs() -> dict[str, object]:
    global _active_lease, _active_store, _thread
    _stop.set()
    thread = _thread
    if thread is not None:
        thread.join(timeout=2.0)
    if _active_store is not None and _active_lease is not None:
        try:
            _active_store.release_leader_lease(_active_lease)
        except Exception as exc:
            logger.warning("projection leader lease release failed: error=%s", exc)
    _active_store = None
    _active_lease = None
    _thread = None
    return {"running": False}


__all__ = [
    "register_projection_job",
    "run_projection_jobs_once",
    "start_projection_jobs",
    "stop_projection_jobs",
]
