from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.obs_routes import router as obs_router
from host_brain.app.obs_routes_core import _clear_obs_route_response_cache
from host_brain.app.web_ui import router as web_ui_router


class _SessionsLedger:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_by_time_range(self, table: str, start_ms: int, end_ms: int, limit: int = 100, offset: int = 0):
        self.calls += 1
        if table == "governor_log":
            return [{"request_id": "req-cache", "ts_ms": 1700000000000, "decision": "APPROVE"}]
        if table == "intent_log":
            return [{"request_id": "req-cache", "ts_ms": 1700000000000, "raw_text": "hello cache"}]
        return []


class _SearchLedger:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_by_time_range(self, table: str, start_ms: int, end_ms: int, limit: int = 100, offset: int = 0):
        self.calls += 1
        if table != "intent_log":
            return []
        return [{
            "request_id": "req-search",
            "ts_ms": 1700000000000,
            "decision": "APPROVE",
            "type": "chat",
            "reason_code": None,
            "raw_text": "search me",
        }]


def setup_function() -> None:
    _clear_obs_route_response_cache()


def teardown_function() -> None:
    _clear_obs_route_response_cache()


def test_obs_sessions_route_uses_short_ttl_cache(monkeypatch) -> None:
    ledger = _SessionsLedger()
    monkeypatch.setattr("host_brain.app.obs_routes_core._get_ledger", lambda: ledger)

    app = FastAPI()
    app.include_router(obs_router)
    client = TestClient(app)

    first = client.get("/obs/sessions?page=1&page_size=20&window_minutes=60")
    second = client.get("/obs/sessions?page=1&page_size=20&window_minutes=60")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["total"] == 1
    assert second.json()["items"][0]["request_id"] == "req-cache"
    assert ledger.calls == 4


def test_webui_search_requests_never_falls_back_to_ledger(monkeypatch) -> None:
    ledger = _SearchLedger()
    monkeypatch.setattr("host_brain.app.web_ui.get_ledger", lambda: ledger)
    monkeypatch.setattr(
        "host_brain.app.dependencies.peek_query_projection_store",
        lambda: None,
    )

    app = FastAPI()
    app.include_router(web_ui_router, prefix="/api")
    client = TestClient(app)

    start_time = datetime(2026, 4, 18, 0, 0, 0)
    end_time = start_time + timedelta(hours=1)

    first = client.get(
        "/api/ui/search-requests",
        params={
            "query": "search",
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "page": 1,
            "page_size": 20,
        },
    )
    second = client.get(
        "/api/ui/search-requests",
        params={
            "query": "search",
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "page": 1,
            "page_size": 20,
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["total"] == 0
    assert second.json()["results"] == []
    assert second.json()["degraded_state"] == "REBUILDING"
    assert ledger.calls == 0
