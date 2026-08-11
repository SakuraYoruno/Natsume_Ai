from __future__ import annotations

import json
import threading
import time
from dataclasses import replace

from fastapi.testclient import TestClient

from host_brain.app.obs_application import (
    ObsProjectionJob,
    build_default_obs_application_ports,
)
from host_brain.app.obs_fastapi_app import create_obs_app
from host_brain.app.observability_runtime.projection_daemon import (
    ProjectionJobDaemon,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.observability.projection_store import QueryProjectionStore


def _store(tmp_path, name: str) -> QueryProjectionStore:
    db_path = tmp_path / f"{name}.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"obs-projection-supervision:{name}",
    )
    assert migrated.success, migrated
    return QueryProjectionStore(db_path)


def _wait_until(predicate, *, timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before timeout")


class _FlakyRenewStore:
    def __init__(self, store: QueryProjectionStore) -> None:
        self._store = store
        self.allow_renew = threading.Event()
        self.renew_failed = threading.Event()

    def __getattr__(self, name: str):
        return getattr(self._store, name)

    def renew_leader_lease(self, lease, *, lease_ms: int):  # noqa: ANN001
        if not self.allow_renew.is_set():
            self.renew_failed.set()
            raise RuntimeError("secret-provider-detail")
        return self._store.renew_leader_lease(lease, lease_ms=lease_ms)


class _LeaseExpiresBetweenJobsStore:
    def __init__(self, store: QueryProjectionStore) -> None:
        self._store = store
        self._renew_count = 0

    def __getattr__(self, name: str):
        return getattr(self._store, name)

    def renew_leader_lease(self, lease, *, lease_ms: int):  # noqa: ANN001
        self._renew_count += 1
        if self._renew_count == 2:
            self._store.release_leader_lease(lease)
            return None
        return self._store.renew_leader_lease(lease, lease_ms=lease_ms)


def test_background_renew_failure_is_reported_and_recovers_without_thread_exit(
    tmp_path,
) -> None:
    store = _FlakyRenewStore(_store(tmp_path, "renew-recovery"))
    callback_ran = threading.Event()
    daemon = ProjectionJobDaemon(owner_id="obs-supervision-renew")
    daemon.register("projection", callback_ran.set)

    try:
        assert daemon.start(store=store, interval_ms=10)["started"] is True
        assert store.renew_failed.wait(timeout=2.0)
        _wait_until(
            lambda: daemon.health_snapshot()["consecutive_failure_waves"] >= 1
        )

        degraded = daemon.health_snapshot()
        assert degraded["status"] == "DEGRADED"
        assert degraded["running"] is True
        assert degraded["last_failure"]["reason_code"] == (
            "OBS_PROJECTION_WAVE_FAILED"
        )
        assert degraded["last_failure"]["failed_jobs"] == ["projection"]
        assert degraded["last_failure"]["error_types"] == ["RuntimeError"]
        assert "secret-provider-detail" not in json.dumps(degraded)
        tampered_snapshot = daemon.health_snapshot()
        tampered_snapshot["last_failure"]["failed_jobs"].append("forged")
        assert daemon.health_snapshot()["last_failure"]["failed_jobs"] == [
            "projection"
        ]

        store.allow_renew.set()
        assert callback_ran.wait(timeout=2.0)
        _wait_until(lambda: daemon.health_snapshot()["status"] == "RUNNING")
        recovered = daemon.health_snapshot()
        assert recovered["running"] is True
        assert recovered["consecutive_failure_waves"] == 0
        assert recovered["recovered_after_last_failure"] is True
        assert recovered["last_failure"] == degraded["last_failure"]
    finally:
        daemon.stop(timeout_s=2.0)


def test_process_control_exit_is_visible_and_requires_close_before_restart(
    tmp_path,
) -> None:
    store = _store(tmp_path, "fatal-exit")
    daemon = ProjectionJobDaemon(owner_id="obs-supervision-fatal")

    def stop_background_thread() -> None:
        raise SystemExit("secret-process-control-detail")

    daemon.register("fatal", stop_background_thread)
    assert daemon.start(store=store, interval_ms=10)["started"] is True
    _wait_until(lambda: daemon.health_snapshot()["status"] == "FAILED")

    failed = daemon.health_snapshot()
    assert failed["running"] is False
    assert failed["lifecycle_state"] == "FAILED"
    assert failed["last_failure"]["reason_code"] == (
        "OBS_PROJECTION_WAVE_EXCEPTION"
    )
    assert failed["last_failure"]["error_types"] == ["SystemExit"]
    assert "secret-process-control-detail" not in json.dumps(failed)
    assert daemon.start(store=store, interval_ms=10) == {
        "started": False,
        "running": False,
        "reason_code": "OBS_PROJECTION_FAILED_REQUIRES_CLOSE",
    }

    assert daemon.stop(timeout_s=2.0)["running"] is False
    stopped = daemon.health_snapshot()
    assert stopped["status"] == "STOPPED"
    assert stopped["last_failure"] == failed["last_failure"]


def test_wave_reacquires_expired_lease_before_later_live_projection(tmp_path) -> None:
    store = _LeaseExpiresBetweenJobsStore(_store(tmp_path, "lease-reacquire"))
    calls: list[str] = []
    daemon = ProjectionJobDaemon(owner_id="obs-supervision-reacquire")
    daemon.register("slow-earlier-job", lambda: calls.append("slow"))
    daemon.register("live-cockpit-job", lambda: calls.append("live"))

    result = daemon.run_once(
        store=store,
        owner_id="obs-supervision-reacquire",
        lease_ms=30_000,
    )

    assert result == {
        "slow-earlier-job": "OK",
        "live-cockpit-job": "OK",
    }
    assert calls == ["slow", "live"]


def test_obs_health_exposes_secret_free_daemon_failure(tmp_path) -> None:
    base_ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "obs-health-supervision.sqlite3",
    )

    def fail_projection() -> None:
        raise RuntimeError("secret-projection-source-detail")

    ports = replace(
        base_ports,
        projection_jobs=(ObsProjectionJob("failing", fail_projection),),
        projection_coverage="REGISTERED_1_OF_1",
        missing_projection_jobs=(),
    )
    application = create_obs_app(ports=ports)

    with TestClient(application) as client:
        container = application.state.obs_application_container
        assert container.projection_daemon.run_once(
            store=container.ports.query_projection_store,
            owner_id="obs-health-supervision",
            lease_ms=120_000,
        ) == {"failing": "JOB_ERROR: RuntimeError"}

        response = client.get("/health")
        assert response.status_code == 200
        payload = response.json()
        daemon_health = payload["composition"]["projection_daemon"]
        assert payload["status"] == "DEGRADED"
        assert daemon_health["status"] == "DEGRADED"
        assert daemon_health["running"] is True
        assert daemon_health["last_failure"]["failed_jobs"] == ["failing"]
        assert daemon_health["last_failure"]["error_types"] == ["RuntimeError"]
        assert "secret-projection-source-detail" not in response.text
