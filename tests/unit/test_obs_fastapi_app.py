from fastapi.testclient import TestClient

from host_brain.app.obs_fastapi_app import app
from tests.fakes.obs_projection_runtime import compose_durable_obs_projection_runtime


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
