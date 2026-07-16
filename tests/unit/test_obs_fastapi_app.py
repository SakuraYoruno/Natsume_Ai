import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient

import host_brain.app.obs_fastapi_app as obs_fastapi_app_module
from host_brain.app.obs_fastapi_app import app
from host_brain.app.settings.core import get_core_settings
from tests.fakes.obs_projection_runtime import compose_durable_obs_projection_runtime


ROOT = Path(__file__).resolve().parents[2]


def test_obs_fastapi_app_health_ok(tmp_path):
    with compose_durable_obs_projection_runtime(
        tmp_path,
        request_id="obs-fastapi-health",
    ):
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["status"] == "OK"
        assert payload["service"] == "obs"
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
    try:
        with compose_durable_obs_projection_runtime(
            tmp_path,
            request_id="obs-fastapi-disabled-task-reader",
        ):
            with TestClient(app) as client:
                assert client.get("/health").status_code == 200
                assert app.state.task_effect_reconciliation_read_authority is None
    finally:
        get_core_settings.cache_clear()


def test_obs_fastapi_app_configures_process_ports_in_a_fresh_process(tmp_path):
    program = "\n".join(
        (
            "from host_brain.composition.process_service_container import get_process_service_container",
            "container = get_process_service_container()",
            "assert container.application_ports is None",
            "from host_brain.app.obs_fastapi_app import app",
            "assert container.application_ports is not None",
            "from fastapi.testclient import TestClient",
            "with TestClient(app) as client:",
            "    assert client.get('/health').status_code == 200",
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
