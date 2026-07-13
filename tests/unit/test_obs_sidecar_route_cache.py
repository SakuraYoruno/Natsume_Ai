from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.obs_routes import router as obs_router
from host_brain.app.obs_routes_core import _clear_obs_route_response_cache
from host_brain.app.observability_runtime.session_query import refresh_session_query_projection
from host_brain.app.web_ui import router as web_ui_router
from tests.fakes.obs_projection_runtime import compose_durable_obs_projection_runtime


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


def test_obs_sessions_route_uses_short_ttl_cache(tmp_path, monkeypatch) -> None:
    with compose_durable_obs_projection_runtime(
        tmp_path,
        request_id="obs-sessions-cache",
    ) as runtime:
        now_ms = int(datetime.now().timestamp() * 1000)
        runtime.ledger.log_governor(
            {
                "request_id": "req-cache",
                "thread_id": "thread-cache",
                "turn_id": "turn-cache",
                "intent_id": "intent-cache",
                "decision": "APPROVE",
                "reason_code": None,
                "stage": "S_GOVERNOR",
                "ts_ms": now_ms,
            }
        )
        refresh_session_query_projection()

        projection_reads = 0
        original_get = runtime.store.get

        def counted_get(*args, **kwargs):  # noqa: ANN002, ANN003
            nonlocal projection_reads
            projection_reads += 1
            return original_get(*args, **kwargs)

        monkeypatch.setattr(runtime.store, "get", counted_get)
        app = FastAPI()
        app.include_router(obs_router)
        client = TestClient(app)

        first = client.get("/obs/sessions?page=1&page_size=20&window_minutes=60")
        second = client.get("/obs/sessions?page=1&page_size=20&window_minutes=60")

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["total"] == 1
        assert second.json()["items"][0]["request_id"] == "req-cache"
        assert projection_reads == 1


def test_webui_search_requests_never_falls_back_to_ledger(monkeypatch) -> None:
    ledger = _SearchLedger()
    monkeypatch.setattr("host_brain.app.web_ui.get_ledger", lambda: ledger)
    monkeypatch.setattr(
        "host_brain.composition.process_services.peek_query_projection_store",
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
