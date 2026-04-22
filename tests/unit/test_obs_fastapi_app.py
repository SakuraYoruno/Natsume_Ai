from fastapi.testclient import TestClient

from host_brain.app.obs_fastapi_app import app


def test_obs_fastapi_app_health_ok():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "OK"
    assert payload["service"] == "obs"
    assert "runtime_snapshot_warm_cache" in payload
    assert "trace_feed_warm_cache" in payload
    assert "insights_summary_warm_cache" in payload
    assert "digital_life_overview_warm_cache" in payload
    assert "life_body_warm_cache" in payload


def test_obs_fastapi_app_exposes_obs_and_ui_routes():
    paths = {route.path for route in app.routes}
    assert "/health" in paths
    assert "/obs/metrics" in paths
    assert "/obs/runtime-snapshot" in paths
    assert "/obs/trace-feed/snapshot" in paths
    assert "/obs/insights/summary-snapshot" in paths
    assert "/obs/digital-life/overview-snapshot" in paths
    assert "/api/ui/search-requests" in paths
    assert "/v1/chat" not in paths
