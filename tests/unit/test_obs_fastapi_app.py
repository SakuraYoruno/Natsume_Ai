import os
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import threading

from fastapi.testclient import TestClient
import pytest

import host_brain.app.obs_fastapi_app as obs_fastapi_app_module
import host_brain.composition.obs_application as obs_application_module
from host_brain.app.obs_fastapi_app import app, create_obs_app
from host_brain.app.settings.core import get_core_settings
from host_brain.composition.obs_application import (
    ObsApplicationContainer,
    ObsProjectionJob,
    build_default_obs_application_ports,
)


ROOT = Path(__file__).resolve().parents[2]


def test_obs_fastapi_app_health_ok(tmp_path):
    isolated_app = create_obs_app(
        ports_factory=lambda: build_default_obs_application_ports(
            canonical_db_path=tmp_path / "obs-fastapi-health.sqlite3",
        )
    )
    with TestClient(isolated_app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["status"] == "DEGRADED"
        assert payload["service"] == "obs"
        assert payload["composition"]["projection_coverage"] == "REGISTERED_19_OF_19"
        assert payload["composition"]["count_semantics"] == (
            "producer_registration_and_declared_source_mode_not_runtime_success"
        )
        assert payload["composition"]["registered_jobs"] == [
            "ledger-stats",
            "life-evolution",
            "bio-ops-metadata",
            "capability-forge-operator",
            "device-status",
            "digital-life-overview",
            "health-passive",
            "insights-summary",
            "life-body",
            "operator-live-state",
            "organ-tribe",
            "remaining-operator-queries",
            "resource-manager",
            "runtime-snapshot",
            "security-immune",
            "sessions",
            "skill-pack-catalog",
            "task-runtime",
            "trace",
        ]
        assert payload["composition"]["missing_jobs"] == []
        assert payload["composition"]["registered_count"] == 19
        assert payload["composition"]["declared_non_typed_unavailable_count"] == 16
        assert payload["composition"]["typed_unavailable_job_count"] == 3
        assert payload["composition"]["declared_partial_job_count"] == 7
        assert "remaining-operator-queries" in payload["composition"][
            "declared_non_typed_unavailable_jobs"
        ]
        assert (
            payload["composition"]["source_modes"]["remaining-operator-queries"]
            == "DURABLE_READ_PARTIAL"
        )
        assert payload["composition"]["source_modes"]["trace"] == "DURABLE_READ_PARTIAL"
        assert {
            item["name"] for item in payload["composition"]["unavailable_jobs"]
        } == {
            "capability-forge-operator",
            "life-body",
            "organ-tribe",
        }
        assert all(
            item["reason_code"] == "OBS_HOST_RUNTIME_SOURCE_NOT_INJECTED"
            for item in payload["composition"]["unavailable_jobs"]
        )
        assert {
            item["name"] for item in payload["composition"]["partial_jobs"]
        } == {
            "device-status",
            "digital-life-overview",
            "insights-summary",
            "operator-live-state",
            "remaining-operator-queries",
            "runtime-snapshot",
            "trace",
        }


def test_obs_fastapi_app_exposes_obs_and_ui_routes():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/health" in paths
    assert "/obs/metrics" in paths
    assert "/observability/trace" in paths
    assert "/v1/chat" not in paths


def test_obs_fastapi_app_does_not_construct_task_reader_when_views_are_disabled(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("HOST_NLE_OPERATOR_VIEWS_ENABLED", "false")
    get_core_settings.cache_clear()

    class _MustNotConstruct:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("disabled operator views must not touch Task Runtime")

    monkeypatch.setattr(
        obs_fastapi_app_module,
        "EffectReconciliationQueryRepository",
        _MustNotConstruct,
    )
    monkeypatch.setattr(
        obs_fastapi_app_module,
        "TerminalReconciliationQueryRepository",
        _MustNotConstruct,
    )
    try:
        isolated_app = create_obs_app(
            ports_factory=lambda: build_default_obs_application_ports(
                canonical_db_path=tmp_path / "obs-fastapi-disabled.sqlite3",
            )
        )
        with TestClient(isolated_app) as client:
            assert client.get("/health").status_code == 200
            assert isolated_app.state.task_effect_reconciliation_read_authority is None
            assert isolated_app.state.task_terminal_reconciliation_read_authority is None
    finally:
        get_core_settings.cache_clear()


def test_obs_fastapi_app_does_not_configure_host_ports_in_a_fresh_process(tmp_path):
    program = "\n".join(
        (
            "from host_brain.composition.process_service_container import get_process_service_container",
            "container = get_process_service_container()",
            "assert container.application_ports is None",
            "from host_brain.app.obs_fastapi_app import app",
            "assert container.application_ports is None",
            "from fastapi.testclient import TestClient",
            "with TestClient(app) as client:",
            "    assert client.get('/health').status_code == 200",
            "assert container.application_ports is None",
        )
    )
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["NATSUME_AUDIT_DB"] = str(tmp_path / "obs.sqlite3")

    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_two_obs_apps_own_independent_lifecycles(tmp_path):
    app_one = create_obs_app(
        ports_factory=lambda: build_default_obs_application_ports(
            canonical_db_path=tmp_path / "obs-one.sqlite3",
        )
    )
    app_two = create_obs_app(
        ports_factory=lambda: build_default_obs_application_ports(
            canonical_db_path=tmp_path / "obs-two.sqlite3",
        )
    )
    client_one = TestClient(app_one)
    client_two = TestClient(app_two)

    client_one.__enter__()
    try:
        container_one = app_one.state.obs_application_container
        client_two.__enter__()
        try:
            container_two = app_two.state.obs_application_container
            assert container_one is not container_two
            assert container_one.projection_daemon is not container_two.projection_daemon
            assert container_one.projection_daemon.running is True
            assert container_two.projection_daemon.running is True

            client_one.__exit__(None, None, None)
            assert app_one.state.obs_application_container is None
            assert container_one.closed is True
            assert app_two.state.obs_application_container is container_two
            assert container_two.closed is False
            assert container_two.projection_daemon.running is True
            assert client_two.get("/health").status_code == 200
        finally:
            client_two.__exit__(None, None, None)
    finally:
        if app_one.state.obs_application_container is not None:
            client_one.__exit__(None, None, None)


def test_obs_container_does_not_close_resources_while_projection_thread_runs(
    tmp_path,
):
    entered = threading.Event()
    release = threading.Event()
    closed: list[bool] = []
    base_ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "obs-stop-fence.sqlite3",
    )

    def blocking_job() -> None:
        entered.set()
        release.wait(timeout=5.0)

    def close_resources() -> None:
        assert base_ports.close_owned_resources is not None
        base_ports.close_owned_resources()
        closed.append(True)

    ports = replace(
        base_ports,
        projection_jobs=(ObsProjectionJob("blocking", blocking_job),),
        close_owned_resources=close_resources,
    )
    container = ObsApplicationContainer.compose(ports)
    container.start(interval_ms=10)
    assert entered.wait(timeout=1.0)

    original_stop = container.projection_daemon.stop
    container.projection_daemon.stop = lambda: original_stop(timeout_s=0.01)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="OBS_PROJECTION_STOP_TIMEOUT"):
        container.close()
    assert container.closed is False
    assert container.projection_daemon.running is True
    assert closed == []

    release.set()
    container.projection_daemon.stop = original_stop  # type: ignore[method-assign]
    assert container.close()["closed"] is True
    assert closed == [True]


def test_obs_lifespan_stop_timeout_retains_app_local_retry_handles(tmp_path):
    closed: list[bool] = []
    base_ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "obs-lifespan-stop-timeout.sqlite3",
    )
    original_resource_close = base_ports.close_owned_resources
    assert original_resource_close is not None

    def close_resources() -> None:
        original_resource_close()
        closed.append(True)

    isolated_app = create_obs_app(
        ports=replace(base_ports, close_owned_resources=close_resources),
    )
    client = TestClient(isolated_app)
    client.__enter__()
    container = isolated_app.state.obs_application_container
    route_ports = isolated_app.state.obs_route_ports
    life_reader = isolated_app.state.life_snapshot_reader
    original_stop = container.projection_daemon.stop
    container.projection_daemon.stop = lambda: {  # type: ignore[method-assign]
        "running": True,
        "reason_code": "OBS_PROJECTION_STOP_TIMEOUT",
    }

    with pytest.raises(RuntimeError, match="OBS_PROJECTION_STOP_TIMEOUT"):
        client.__exit__(None, None, None)

    assert isolated_app.state.obs_application_container is container
    assert isolated_app.state.obs_route_ports is route_ports
    assert isolated_app.state.life_snapshot_reader is life_reader
    assert container.closed is False
    assert container.projection_daemon.running is True
    assert closed == []

    container.projection_daemon.stop = original_stop  # type: ignore[method-assign]
    assert container.close()["closed"] is True
    assert closed == [True]


def test_obs_startup_rollback_close_failure_retains_exact_retry_handles(
    tmp_path,
    monkeypatch,
):
    base_ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "obs-startup-rollback-retry.sqlite3",
    )
    original_resource_close = base_ports.close_owned_resources
    assert original_resource_close is not None
    close_calls = 0

    def retryable_close() -> None:
        nonlocal close_calls
        close_calls += 1
        if close_calls == 1:
            raise RuntimeError("rollback close failed")
        original_resource_close()

    ports = replace(base_ports, close_owned_resources=retryable_close)
    isolated_app = create_obs_app(ports=ports)
    monkeypatch.setattr(
        obs_fastapi_app_module,
        "_build_effect_reconciliation_read_authority",
        lambda _path: (_ for _ in ()).throw(RuntimeError("reader failed")),
    )

    with pytest.raises(RuntimeError, match="rollback close failed"):
        with TestClient(isolated_app):
            pass

    container = isolated_app.state.obs_application_container
    route_ports = isolated_app.state.obs_route_ports
    assert isinstance(container, ObsApplicationContainer)
    assert container.ports is ports
    assert route_ports.require_query_projection_store() is ports.query_projection_store
    assert isolated_app.state.life_snapshot_reader is ports.life_snapshot_reader
    assert isolated_app.state.task_effect_reconciliation_read_authority is None
    assert isolated_app.state.task_terminal_reconciliation_read_authority is None
    assert container.closed is False
    assert close_calls == 1

    assert container.close()["closed"] is True
    assert close_calls == 2


def test_direct_obs_ports_are_single_use_across_lifespans(tmp_path):
    ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / "obs-direct-ports-single-use.sqlite3",
    )
    isolated_app = create_obs_app(ports=ports)

    with TestClient(isolated_app) as client:
        assert client.get("/health").status_code == 200

    with pytest.raises(RuntimeError, match="OBS_APPLICATION_PORTS_ALREADY_CONSUMED"):
        with TestClient(isolated_app):
            pass


def test_obs_ports_factory_can_supply_fresh_bundle_for_each_lifespan(tmp_path):
    created = []

    def factory():
        ports = build_default_obs_application_ports(
            canonical_db_path=tmp_path / f"obs-factory-{len(created)}.sqlite3",
        )
        created.append(ports)
        return ports

    isolated_app = create_obs_app(ports_factory=factory)
    with TestClient(isolated_app) as first:
        assert first.get("/health").status_code == 200
    with TestClient(isolated_app) as second:
        assert second.get("/health").status_code == 200

    assert len(created) == 2
    assert created[0] is not created[1]


def test_obs_app_module_has_no_host_locator_or_life_provider_fallback() -> None:
    source = (ROOT / "host_brain" / "app" / "obs_fastapi_app.py").read_text(
        encoding="utf-8"
    )
    assert "configure_process_service_ports" not in source
    assert "PROCESS_LIFE_PROVIDER" not in source
    assert "composition.process_services" not in source


@pytest.mark.parametrize("failure_site", ("reader", "start"))
def test_obs_app_startup_failure_closes_owned_ports_and_clears_state(
    tmp_path,
    monkeypatch,
    failure_site,
):
    base_ports = build_default_obs_application_ports(
        canonical_db_path=tmp_path / f"obs-startup-failure-{failure_site}.sqlite3",
    )
    closed: list[bool] = []
    original_close = base_ports.close_owned_resources
    assert original_close is not None

    def close_resources() -> None:
        original_close()
        closed.append(True)

    ports = replace(base_ports, close_owned_resources=close_resources)
    isolated_app = create_obs_app(ports=ports)

    if failure_site == "reader":
        monkeypatch.setattr(
            obs_fastapi_app_module,
            "_build_effect_reconciliation_read_authority",
            lambda _path: (_ for _ in ()).throw(RuntimeError("reader failed")),
        )
    else:
        monkeypatch.setattr(
            ObsApplicationContainer,
            "start",
            lambda self, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("start failed")
            ),
        )

    with pytest.raises(RuntimeError, match=f"{failure_site} failed"):
        with TestClient(isolated_app):
            pass

    assert closed == [True]
    assert isolated_app.state.obs_application_container is None
    assert isolated_app.state.obs_route_ports is None
    assert isolated_app.state.life_snapshot_reader is None
    assert isolated_app.state.task_effect_reconciliation_read_authority is None
    assert isolated_app.state.task_terminal_reconciliation_read_authority is None


def test_default_obs_ports_close_ledger_when_job_composition_fails(
    tmp_path,
    monkeypatch,
):
    closed: list[object] = []
    original_close = obs_application_module.SQLiteLedger.close

    def tracked_close(ledger) -> None:  # noqa: ANN001
        closed.append(ledger)
        original_close(ledger)

    def fail_jobs(**_kwargs):  # noqa: ANN003
        raise RuntimeError("job composition failed")

    monkeypatch.setattr(obs_application_module.SQLiteLedger, "close", tracked_close)
    monkeypatch.setattr(
        obs_application_module,
        "build_explicit_obs_projection_callbacks",
        fail_jobs,
    )

    with pytest.raises(RuntimeError, match="job composition failed"):
        build_default_obs_application_ports(
            canonical_db_path=tmp_path / "obs-default-ports-failure.sqlite3",
        )

    assert len(closed) == 1
