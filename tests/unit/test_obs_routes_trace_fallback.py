"""
Trace fallback tests for GET /obs/trace/{request_id}.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app import obs_routes_core
from host_brain.app.obs_routes import router, set_ledger_provider


def _make_app(ledger):
    app = FastAPI()
    app.include_router(router)
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
    assert data["node_count"] >= 1
    assert data["status"] in ("OK", "FAIL", "DEGRADED")


def test_trace_fallback_not_found_returns_404():
    ledger = _make_ledger_no_trace(gov_rows=[])
    client = TestClient(_make_app(ledger))

    resp = client.get("/obs/trace/nonexistent-req-xyz")
    assert resp.status_code == 404
    detail = resp.json().get("detail", {})
    assert detail.get("error") == "not_found"
    assert detail.get("request_id") == "nonexistent-req-xyz"


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
    calls = [str(c) for c in ledger.fetch_by_request_id.call_args_list]
    assert any("governor_log" in c for c in calls)
    ledger.fetch_by_request_id.assert_called()


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
    assert data["summary"]["degraded"] is True


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
    assert data["summary"]["degraded"] is True
