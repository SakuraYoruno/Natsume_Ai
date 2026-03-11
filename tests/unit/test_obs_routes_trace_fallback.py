"""
T-OBS-ROUTE-TRACE-FALLBACK-01~03 — Trace 降级路径测试
TASK-0193 / BUG-BE-001
验证 GET /obs/trace/{request_id} 降级路径不再抛出 AttributeError
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

from host_brain.app.obs_routes import router, set_ledger_provider


def _make_app(ledger):
    """构造带 obs router 的最小 FastAPI 应用。"""
    app = FastAPI()
    app.include_router(router)
    set_ledger_provider(lambda: ledger)
    return app


def _make_ledger_no_trace(gov_rows=None):
    """构造 mock ledger：graph_run_trace 无数据，governor_log 返回指定行。"""
    ledger = MagicMock()

    def fetch_by_request_id(table, request_id, **kwargs):
        if table == "governor_log":
            return gov_rows or []
        return []

    ledger.fetch_by_request_id.side_effect = fetch_by_request_id
    return ledger


# ---------------------------------------------------------------------------
# T-OBS-ROUTE-TRACE-FALLBACK-01: 降级路径正常返回（governor_log 有数据）
# ---------------------------------------------------------------------------
def test_trace_fallback_returns_ok():
    """BUG-BE-001: 降级路径不抛 AttributeError，正常返回 200 + JSON。"""
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
    assert data["status"] in ("OK", "FAIL")


# ---------------------------------------------------------------------------
# T-OBS-ROUTE-TRACE-FALLBACK-02: 不存在的 request_id 返回 404
# ---------------------------------------------------------------------------
def test_trace_fallback_not_found_returns_404():
    """BUG-BE-001: 不存在的 request_id 返回 404 + error 字段。"""
    ledger = _make_ledger_no_trace(gov_rows=[])
    client = TestClient(_make_app(ledger))

    resp = client.get("/obs/trace/nonexistent-req-xyz")
    assert resp.status_code == 404
    data = resp.json()
    # FastAPI 将 detail 包装在 {"detail": ...}
    detail = data.get("detail", {})
    assert detail.get("error") == "not_found"
    assert detail.get("request_id") == "nonexistent-req-xyz"


# ---------------------------------------------------------------------------
# T-OBS-ROUTE-TRACE-FALLBACK-03: 降级路径调用 fetch_by_time_range 而非属性访问
# ---------------------------------------------------------------------------
def test_trace_fallback_uses_method_not_attribute():
    """BUG-BE-001: 降级路径使用 ledger.fetch_by_request_id('governor_log', ...) 而非 ledger.governor_log。"""
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

    # 验证调用了 fetch_by_request_id 而非属性访问
    calls = [str(c) for c in ledger.fetch_by_request_id.call_args_list]
    assert any("governor_log" in c for c in calls), (
        "降级路径应调用 fetch_by_request_id('governor_log', ...) 而非属性访问"
    )
    ledger.fetch_by_request_id.assert_called()
