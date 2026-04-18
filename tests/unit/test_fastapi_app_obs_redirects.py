from __future__ import annotations

from fastapi.testclient import TestClient

from host_brain.app.fastapi_app import app


def test_host_main_redirects_obs_to_obs_sidecar(monkeypatch) -> None:
    monkeypatch.setenv("HOST_MAIN_OBS_REDIRECT_ENABLED", "true")
    monkeypatch.setenv("HOST_OBS_APP_HOST", "127.0.0.1")
    monkeypatch.setenv("HOST_OBS_APP_PORT", "18082")

    client = TestClient(app)
    response = client.get("/obs/runtime-snapshot?window_minutes=60", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "http://127.0.0.1:18082/obs/runtime-snapshot?window_minutes=60"


def test_host_main_redirects_monitor_to_proxy(monkeypatch) -> None:
    monkeypatch.setenv("HOST_MAIN_MONITOR_REDIRECT_ENABLED", "true")
    monkeypatch.setenv("HOST_MONITOR_PROXY_HOST", "127.0.0.1")
    monkeypatch.setenv("HOST_MONITOR_PROXY_PORT", "18180")

    client = TestClient(app)
    response = client.get("/monitor/index.html", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "http://127.0.0.1:18180/monitor/index.html"


def test_host_main_redirects_static_monitor_alias_to_proxy(monkeypatch) -> None:
    monkeypatch.setenv("HOST_MAIN_MONITOR_REDIRECT_ENABLED", "true")
    monkeypatch.setenv("HOST_MONITOR_PROXY_HOST", "127.0.0.1")
    monkeypatch.setenv("HOST_MONITOR_PROXY_PORT", "18180")

    client = TestClient(app)
    response = client.get("/static/monitor/index.html", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "http://127.0.0.1:18180/monitor/index.html"

