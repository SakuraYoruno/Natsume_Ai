"""
Trace fallback tests for GET /obs/trace/{request_id}.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app import obs_routes_core
from host_brain.app.obs_routes import router, set_ledger_provider
from host_brain.app.obs_routes_trace import router as trace_router
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.observability.projection_store import QueryProjectionStore


@pytest.fixture(autouse=True)
def _projection_only_runtime(tmp_path, monkeypatch):
    db_path = tmp_path / "projection.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="trace-route-unit")
    assert migrated.success, migrated
    store = QueryProjectionStore(db_path)
    monkeypatch.setattr(
        "host_brain.composition.process_services.get_query_projection_store",
        lambda: store,
    )


def _make_app(ledger):
    app = FastAPI()
    app.include_router(router)
    app.include_router(trace_router)
    set_ledger_provider(lambda: ledger)
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


def _minimal_trace_payload(request_id: str, *, degraded: bool) -> dict:
    return {
        "request_id": request_id,
        "session_id": "sess-timeout",
        "status": "DEGRADED" if degraded else "OK",
        "total_latency_ms": 123,
        "node_count": 1,
        "nodes": [
            {
                "node_id": "host_input",
                "node_type": "intent",
                "display_name": "Host Input",
                "status": "OK",
            }
        ],
        "edges": [],
        "summary": {"degraded": degraded},
        "subgraphs": [],
        "stream_events": [],
        "ts_ms": 1700000000000,
        "timestamp": 1700000001000,
    }


def test_trace_fallback_returns_ok():
    gov_row = {
        "request_id": "req-fallback-01",
        "decision": "APPROVE",
        "ts_ms": 1700000000000,
        "reason_code": None,
        "session_id": "sess-01",
    }
    ledger = _make_ledger_no_trace(gov_rows=[gov_row])
    client = TestClient(_make_app(ledger))

    resp = client.get("/obs/trace/req-fallback-01")
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == "req-fallback-01"
    assert data["node_count"] == 0
    assert data["status"] == "DEGRADED"
    assert data["projection"]["reason_code"] == "OPERATOR_PROJECTION_NOT_READY"
    ledger.fetch_by_request_id.assert_not_called()


def test_trace_fallback_not_found_returns_404():
    ledger = _make_ledger_no_trace(gov_rows=[])
    client = TestClient(_make_app(ledger))

    resp = client.get("/obs/trace/nonexistent-req-xyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == "nonexistent-req-xyz"
    assert data["projection"]["reason_code"] == "OPERATOR_PROJECTION_NOT_READY"


def test_trace_fallback_uses_method_not_attribute():
    gov_row = {
        "request_id": "req-fallback-03",
        "decision": "REJECT",
        "ts_ms": 1700000001000,
        "reason_code": "RATE_LIMIT_EXCEEDED",
        "session_id": None,
    }
    ledger = _make_ledger_no_trace(gov_rows=[gov_row])
    client = TestClient(_make_app(ledger))

    resp = client.get("/obs/trace/req-fallback-03")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "DEGRADED"
    ledger.fetch_by_request_id.assert_not_called()


def test_trace_timeout_degrades_to_lightweight(monkeypatch):
    ledger = _make_ledger_no_trace(gov_rows=[])
    client = TestClient(_make_app(ledger))

    def _slow_build(_ledger, request_id):
        time.sleep(0.2)
        return _minimal_trace_payload(request_id, degraded=False)

    def _light_build(_ledger, request_id, *, reason, message):
        _ = reason
        _ = message
        return _minimal_trace_payload(request_id, degraded=True)

    monkeypatch.setattr(obs_routes_core, "_TRACE_BUILD_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(obs_routes_core, "_TRACE_BUILD_FALLBACK_TIMEOUT_SEC", 0.5)
    monkeypatch.setattr(obs_routes_core, "_build_trace_response", _slow_build)
    monkeypatch.setattr(obs_routes_core, "_build_trace_response_lightweight", _light_build)

    resp = client.get("/obs/trace/req-timeout-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == "req-timeout-001"
    assert data["projection"]["reason_code"] == "OPERATOR_PROJECTION_NOT_READY"


def test_trace_error_degrades_to_lightweight(monkeypatch):
    ledger = _make_ledger_no_trace(gov_rows=[])
    client = TestClient(_make_app(ledger))

    def _raise_build(_ledger, _request_id):
        raise RuntimeError("simulated trace build failure")

    def _light_build(_ledger, request_id, *, reason, message):
        _ = reason
        _ = message
        return _minimal_trace_payload(request_id, degraded=True)

    monkeypatch.setattr(obs_routes_core, "_TRACE_BUILD_TIMEOUT_SEC", 0.2)
    monkeypatch.setattr(obs_routes_core, "_TRACE_BUILD_FALLBACK_TIMEOUT_SEC", 0.5)
    monkeypatch.setattr(obs_routes_core, "_build_trace_response", _raise_build)
    monkeypatch.setattr(obs_routes_core, "_build_trace_response_lightweight", _light_build)

    resp = client.get("/obs/trace/req-error-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == "req-error-001"
    assert data["projection"]["reason_code"] == "OPERATOR_PROJECTION_NOT_READY"
