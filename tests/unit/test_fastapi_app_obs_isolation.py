from __future__ import annotations

from fastapi.testclient import TestClient

from host_brain.app.asgi import app as host_app
from host_brain.app.obs_fastapi_app import app as obs_app


def test_host_main_does_not_mount_obs_or_monitor_routes() -> None:
    host_paths = {route.path for route in host_app.routes if hasattr(route, "path")}

    assert "/v1/chat" in host_paths
    assert "/metrics" in host_paths
    assert "/obs/metrics" not in host_paths
    assert "/obs/codex-bio-ops" not in host_paths
    assert "/api/ui/search-requests" not in host_paths
    assert "/monitor" not in host_paths
    assert "/static" not in host_paths
    assert "/panel" not in host_paths


def test_host_main_rejects_monitor_and_obs_requests_without_redirecting() -> None:
    client = TestClient(host_app)

    for path in (
        "/obs/runtime-snapshot?window_minutes=60",
        "/obs/codex-bio-ops",
        "/api/ui/search-requests",
        "/monitor/index.html",
        "/static/monitor/index.html",
        "/panel",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 404
        assert "location" not in response.headers


def test_obs_side_owns_observability_and_monitor_api_routes() -> None:
    obs_paths = {route.path for route in obs_app.routes if hasattr(route, "path")}

    assert "/v1/chat" not in obs_paths
    assert "/obs/metrics" in obs_paths
    assert "/obs/codex-bio-ops" in obs_paths
    assert "/api/ui/search-requests" in obs_paths
    assert "/observability/trace" in obs_paths
