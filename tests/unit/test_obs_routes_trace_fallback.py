"""
Projection-readiness tests for GET /obs/trace/{request_id}.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.app.obs_routes import router
from host_brain.app.obs_routes_trace import router as trace_router
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.observability.projection_store import QueryProjectionStore


@pytest.fixture
def projection_store(tmp_path) -> QueryProjectionStore:
    db_path = tmp_path / "projection.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="trace-route-unit")
    assert migrated.success, migrated
    return QueryProjectionStore(db_path)


def _make_app(ledger, projection_store: QueryProjectionStore) -> FastAPI:
    app = FastAPI()
    app.state.obs_route_ports = ObsRoutePorts(
        query_projection_store_provider=lambda: projection_store,
    )
    app.include_router(router)
    app.include_router(trace_router)
    return app


def _make_ledger_no_trace(gov_rows=None):
    ledger = MagicMock()

    def fetch_by_request_id(table, request_id, **kwargs):
        _ = request_id
        _ = kwargs
        if table == "governor_log":
            return gov_rows or []
        return []

    ledger.fetch_by_request_id.side_effect = fetch_by_request_id
    return ledger


def test_trace_projection_not_ready_returns_degraded(projection_store):
    gov_row = {
        "request_id": "req-fallback-01",
        "decision": "APPROVE",
        "ts_ms": 1700000000000,
        "reason_code": None,
        "session_id": "sess-01",
    }
    ledger = _make_ledger_no_trace(gov_rows=[gov_row])
    client = TestClient(_make_app(ledger, projection_store))

    resp = client.get("/obs/trace/req-fallback-01")
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == "req-fallback-01"
    assert data["node_count"] == 0
    assert data["status"] == "DEGRADED"
    assert data["projection"]["reason_code"] == "OPERATOR_PROJECTION_NOT_READY"
    ledger.fetch_by_request_id.assert_not_called()


def test_trace_projection_not_ready_accepts_unknown_request(projection_store):
    ledger = _make_ledger_no_trace(gov_rows=[])
    client = TestClient(_make_app(ledger, projection_store))

    resp = client.get("/obs/trace/nonexistent-req-xyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == "nonexistent-req-xyz"
    assert data["projection"]["reason_code"] == "OPERATOR_PROJECTION_NOT_READY"


def test_trace_projection_not_ready_does_not_touch_source_ledger(projection_store):
    gov_row = {
        "request_id": "req-fallback-03",
        "decision": "REJECT",
        "ts_ms": 1700000001000,
        "reason_code": "RATE_LIMIT_EXCEEDED",
        "session_id": None,
    }
    ledger = _make_ledger_no_trace(gov_rows=[gov_row])
    client = TestClient(_make_app(ledger, projection_store))

    resp = client.get("/obs/trace/req-fallback-03")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "DEGRADED"
    ledger.fetch_by_request_id.assert_not_called()


def test_trace_projection_unavailable_returns_degraded(projection_store):
    ledger = _make_ledger_no_trace(gov_rows=[])
    client = TestClient(_make_app(ledger, projection_store))

    resp = client.get("/obs/trace/req-error-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == "req-error-001"
    assert data["projection"]["reason_code"] == "OPERATOR_PROJECTION_NOT_READY"
