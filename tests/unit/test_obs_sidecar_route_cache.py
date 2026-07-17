from datetime import datetime, timedelta
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from natsume_contracts.operator.v1 import ProjectionDegradedState

from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.app.obs_routes import router as obs_router
from host_brain.app.obs_routes_core import _clear_obs_route_response_cache
from host_brain.app.web_ui import router as web_ui_router
from host_brain.composition.obs_projection_jobs import _sessions_callback
from tests.fakes.obs_projection_runtime import compose_durable_obs_projection_runtime


class _SessionProjectionStore:
    def __init__(self, request_id: str) -> None:
        self.calls = 0
        now_ms = int(time.time() * 1000)
        self.document = SimpleNamespace(
            schema_version="Natsume.query_projection.v1",
            projection_version=1,
            projection_cursor=1,
            source_cursor=1,
            source_digest=f"digest:{request_id}",
            freshness_ts_ms=now_ms,
            degraded_state=ProjectionDegradedState.HEALTHY,
            degraded_detail=None,
            payload={
                "items": [
                    {
                        "request_id": request_id,
                        "ts_ms": now_ms,
                        "status": "ok",
                    }
                ]
            },
        )

    def get(self, _kind: str, _key: str):
        self.calls += 1
        return self.document


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
        _sessions_callback(runtime.ledger, runtime.store)()

        projection_reads = 0
        original_get = runtime.store.get

        def counted_get(*args, **kwargs):  # noqa: ANN002, ANN003
            nonlocal projection_reads
            projection_reads += 1
            return original_get(*args, **kwargs)

        monkeypatch.setattr(runtime.store, "get", counted_get)
        app = FastAPI()
        app.state.obs_route_ports = ObsRoutePorts(
            query_projection_store_provider=lambda: runtime.store,
        )
        app.include_router(obs_router)
        client = TestClient(app)

        first = client.get("/obs/sessions?page=1&page_size=20&window_minutes=60")
        second = client.get("/obs/sessions?page=1&page_size=20&window_minutes=60")

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["total"] == 1
        assert second.json()["items"][0]["request_id"] == "req-cache"
        assert projection_reads == 1


def test_obs_sessions_cache_and_single_flight_are_isolated_per_app() -> None:
    first_store = _SessionProjectionStore("request-from-app-one")
    second_store = _SessionProjectionStore("request-from-app-two")
    first_ports = ObsRoutePorts(
        query_projection_store_provider=lambda: first_store,
    )
    second_ports = ObsRoutePorts(
        query_projection_store_provider=lambda: second_store,
    )
    assert first_ports.response_cache is not second_ports.response_cache

    first_app = FastAPI()
    first_app.state.obs_route_ports = first_ports
    first_app.include_router(obs_router)
    second_app = FastAPI()
    second_app.state.obs_route_ports = second_ports
    second_app.include_router(obs_router)

    query = "/obs/sessions?page=1&page_size=20&window_minutes=60"
    first_client = TestClient(first_app)
    second_client = TestClient(second_app)
    first = first_client.get(query)
    second = second_client.get(query)
    first_replay = first_client.get(query)
    second_replay = second_client.get(query)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["items"][0]["request_id"] == "request-from-app-one"
    assert second.json()["items"][0]["request_id"] == "request-from-app-two"
    assert first_replay.json() == first.json()
    assert second_replay.json() == second.json()
    assert first_store.calls == 1
    assert second_store.calls == 1


def test_webui_search_requests_never_falls_back_to_ledger() -> None:
    class EmptyProjectionStore:
        @staticmethod
        def get(_kind: str, _key: str):
            return None

    app = FastAPI()
    app.state.obs_route_ports = ObsRoutePorts(
        query_projection_store_provider=EmptyProjectionStore,
    )
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
