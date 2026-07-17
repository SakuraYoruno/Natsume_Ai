import os
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import threading

from fastapi.testclient import TestClient
import pytest

import host_brain.app.obs_fastapi_app as obs_fastapi_app_module
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
        assert payload["composition"]["projection_coverage"] == "EXPLICIT_CORE_ONLY"
        assert payload["composition"]["active_jobs"] == [
            "ledger-stats",
            "life-evolution",
        ]
        assert len(payload["composition"]["missing_jobs"]) == 15
        assert payload["projection"]["available"] is False
        assert payload["projection"]["degraded_state"] == "REBUILDING"


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


def test_obs_app_module_has_no_host_locator_or_life_provider_fallback() -> None:
    source = (ROOT / "host_brain" / "app" / "obs_fastapi_app.py").read_text(
        encoding="utf-8"
    )
    assert "configure_process_service_ports" not in source
    assert "PROCESS_LIFE_PROVIDER" not in source
    assert "composition.process_services" not in source
