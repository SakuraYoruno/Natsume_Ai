from __future__ import annotations

import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from natsume_contracts.operator.v1 import ProjectionDegradedState

from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.app.obs_routes_life_body import router
from host_brain.app.observability_runtime.process_state_projection import (
    LIFE_BODY_KIND,
    window_key,
)


class _ProjectionStore:
    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.calls: list[tuple[str, str]] = []

    def get(self, kind: str, key: str):
        self.calls.append((kind, key))
        now = int(time.time() * 1000)
        payload = {
            "window_minutes": 360,
            "timestamp": now,
            "runtimeSnapshot": {"app": self.marker},
            "model": {
                "timelineEventPool": [
                    {
                        "id": f"event-{self.marker}",
                        "type": "request",
                        "organId": "nervous",
                    }
                ],
                "pathologies": [
                    {
                        "id": f"pathology-{self.marker}",
                        "organId": "memory",
                        "severity": "warning",
                    }
                ],
                "primaryPathology": {
                    "id": f"pathology-{self.marker}",
                    "organId": "memory",
                    "severity": "warning",
                },
                "organSystems": [
                    {"id": "memory", "name": f"memory-{self.marker}"}
                ],
            },
        }
        return SimpleNamespace(
            payload=payload,
            schema_version="Natsume.operator.query_projection.v1",
            projection_version=1,
            projection_cursor=1,
            source_cursor=1,
            source_digest=f"sha256:{self.marker}",
            freshness_ts_ms=now,
            degraded_state=ProjectionDegradedState.HEALTHY,
            degraded_detail=None,
        )


def _app(store: _ProjectionStore) -> FastAPI:
    app = FastAPI()
    app.state.obs_route_ports = ObsRoutePorts(
        query_projection_store_provider=lambda: store,
    )
    app.include_router(router)
    return app


def test_life_get_routes_use_only_their_app_local_projection_store() -> None:
    first_store = _ProjectionStore("first")
    second_store = _ProjectionStore("second")
    first = TestClient(_app(first_store))
    second = TestClient(_app(second_store))

    for client, marker in ((first, "first"), (second, "second")):
        life_body = client.get("/obs/life-body?window_minutes=360")
        timeline = client.get("/obs/life-timeline?window_minutes=360")
        pathologies = client.get("/obs/pathologies?window_minutes=360")

        assert life_body.status_code == 200
        assert timeline.status_code == 200
        assert pathologies.status_code == 200
        assert life_body.json()["runtimeSnapshot"]["app"] == marker
        assert timeline.json()["items"][0]["id"] == f"event-{marker}"
        assert pathologies.json()["items"][0]["id"] == f"pathology-{marker}"

    expected_calls = [(LIFE_BODY_KIND, window_key(360))] * 3
    assert first_store.calls == expected_calls
    assert second_store.calls == expected_calls
