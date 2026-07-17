from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from host_brain.app.obs_fastapi_app import create_obs_app
from host_brain.app.observability_runtime.projection_runtime import (
    SESSION_INDEX_KEY,
    SESSION_KIND,
    TRACE_KIND,
)
from host_brain.app.obs_application import (
    ObsApplicationPorts,
    build_default_obs_application_ports,
)
from host_brain.observability.operator_query_gateway import (
    OPERATOR_QUERY_AUTH_BINDINGS_ENV,
)


def _seed_isolated_ports(
    path: Path,
    *,
    label: str,
    status: str,
    channel: str,
    now_ms: int,
) -> ObsApplicationPorts:
    base_ports = build_default_obs_application_ports(canonical_db_path=path)
    ports = replace(
        base_ports,
        projection_jobs=(),
        projection_coverage="TEST_WEB_UI_APP_LOCAL_STORE",
    )
    request_id = f"req-{label}"
    ports.query_projection_store.upsert(
        SESSION_KIND,
        SESSION_INDEX_KEY,
        {
            "items": [
                {
                    "request_id": request_id,
                    "session_id": f"session-{label}",
                    "ts_ms": now_ms,
                    "status": status,
                    "channel": channel,
                    "duration_ms": 10,
                }
            ]
        },
        source_cursor=1,
        freshness_ts_ms=now_ms,
    )
    ports.query_projection_store.upsert(
        TRACE_KIND,
        request_id,
        {
            "request_id": request_id,
            "ts_ms": now_ms,
            "status": status,
            "duration_ms": 10,
            "summary": {"request_type": channel, "total_latency_ms": 10},
            "nodes": [
                {"source": f"{label}_ingress"},
                {"source": f"{label}_model"},
            ],
        },
        source_cursor=2,
        freshness_ts_ms=now_ms,
    )
    return ports


def test_five_web_ui_gets_read_only_their_create_obs_app_sqlite_store(
    tmp_path,
    monkeypatch,
) -> None:
    token = "web-ui-app-local-store"
    monkeypatch.setenv(
        OPERATOR_QUERY_AUTH_BINDINGS_ENV,
        json.dumps(
            [
                {
                    "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                    "principal_id": "ops.web-ui-app-local-store",
                    "scopes": ["obs:ui:read"],
                }
            ]
        ),
    )
    now_ms = int(time.time() * 1000)
    ports_one = _seed_isolated_ports(
        tmp_path / "web-ui-one.sqlite3",
        label="one",
        status="OK",
        channel="chat",
        now_ms=now_ms,
    )
    ports_two = _seed_isolated_ports(
        tmp_path / "web-ui-two.sqlite3",
        label="two",
        status="FAILED",
        channel="delegate",
        now_ms=now_ms,
    )
    assert ports_one.query_projection_store.db_path != ports_two.query_projection_store.db_path

    app_one = create_obs_app(ports=ports_one)
    app_two = create_obs_app(ports=ports_two)
    headers = {"Authorization": f"Bearer {token}"}
    start = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc) - timedelta(minutes=1)
    end = start + timedelta(minutes=2)
    time_params = {"start_time": start.isoformat(), "end_time": end.isoformat()}

    with TestClient(app_one, headers=headers) as client_one, TestClient(
        app_two,
        headers=headers,
    ) as client_two:
        timeline_one = client_one.get(
            "/api/ui/timeline",
            params={**time_params, "interval": "1m"},
        )
        timeline_two = client_two.get(
            "/api/ui/timeline",
            params={**time_params, "interval": "1m"},
        )
        assert timeline_one.status_code == timeline_two.status_code == 200
        assert timeline_one.json()["total_requests"] == 1
        assert timeline_two.json()["total_requests"] == 1

        status_one = client_one.get("/api/ui/status-distribution", params=time_params)
        status_two = client_two.get("/api/ui/status-distribution", params=time_params)
        assert status_one.json()["status_counts"] == {"OK": 1}
        assert status_two.json()["status_counts"] == {"FAILED": 1}

        detail_one = client_one.get("/api/ui/request/req-one")
        detail_two = client_two.get("/api/ui/request/req-two")
        assert detail_one.status_code == detail_two.status_code == 200
        assert detail_one.json()["request_id"] == "req-one"
        assert detail_two.json()["request_id"] == "req-two"
        assert client_one.get("/api/ui/request/req-two").status_code == 404
        assert client_two.get("/api/ui/request/req-one").status_code == 404

        topology_one = client_one.get("/api/ui/link-topology", params=time_params)
        topology_two = client_two.get("/api/ui/link-topology", params=time_params)
        assert {node["id"] for node in topology_one.json()["nodes"]} == {
            "one_ingress",
            "one_model",
        }
        assert {node["id"] for node in topology_two.json()["nodes"]} == {
            "two_ingress",
            "two_model",
        }

        search_one = client_one.get(
            "/api/ui/search-requests",
            params={**time_params, "query": "req"},
        )
        search_two = client_two.get(
            "/api/ui/search-requests",
            params={**time_params, "query": "req"},
        )
        assert [item["request_id"] for item in search_one.json()["results"]] == [
            "req-one"
        ]
        assert [item["request_id"] for item in search_two.json()["results"]] == [
            "req-two"
        ]
