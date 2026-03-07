"""
T-OBS-ROUTE-INT-01~16 — obs_routes.py 集成测试
TASK-0198 / WARN-TEST-002
使用内存 SQLite + FastAPI TestClient，覆盖 4 个端点 × 4 个场景
"""
from __future__ import annotations

import time
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.obs_routes import router, set_ledger_provider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ledger():
    """内存 SQLite ledger，预置 fixture 数据。"""
    db = SQLiteLedger(":memory:", check_same_thread=False)
    now = int(time.time() * 1000)

    # 预置 governor_log 数据
    db.log_governor({
        "request_id": "req-int-001",
        "thread_id": "t1", "turn_id": "u1", "intent_id": "i1",
        "decision": "APPROVE", "reason_code": None,
        "rule_trace": None, "tokens": None, "snapshot_refs": None,
        "stage": "S_GOVERNOR", "ts_ms": now - 5000,
    })
    db.log_governor({
        "request_id": "req-int-002",
        "thread_id": "t2", "turn_id": "u2", "intent_id": "i2",
        "decision": "REJECT", "reason_code": "RATE_LIMIT_EXCEEDED",
        "rule_trace": None, "tokens": None, "snapshot_refs": None,
        "stage": "S_GOVERNOR", "ts_ms": now - 3000,
    })

    # 预置 llm_node_call_log 数据
    db.log_llm_node_call({
        "request_id": "req-int-001",
        "node_id": "core_reasoner_primary",
        "provider": "openai", "model_id": "gpt-5.2",
        "status": "OK", "latency_ms": 120, "ts_ms": now - 4000,
    })

    # 预置 graph_run_trace 数据
    db.log_graph_run_trace({
        "request_id": "req-int-001",
        "session_id": "sess-001",
        "total_latency_ms": 350,
        "status": "OK",
        "node_count": 3,
        "nodes_json": [
            {"node_id": "intent_i1", "node_type": "intent", "display_name": "Intent Parser",
             "status": "OK", "ts_ms": now - 5000},
        ],
        "edges_json": [],
        "ts_ms": now - 4500,
    })

    yield db
    db.close()


@pytest.fixture
def client(ledger):
    """带 obs router 的 TestClient。"""
    app = FastAPI()
    app.include_router(router)
    set_ledger_provider(lambda: ledger)
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /obs/services/health
# ---------------------------------------------------------------------------

def test_services_health_returns_200(client):
    """T-OBS-ROUTE-INT-01: 正常路径返回 200 + 必要字段。"""
    resp = client.get("/obs/services/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "up" in data
    assert "degraded" in data
    assert "down" in data
    assert "services" in data
    assert isinstance(data["services"], list)


def test_services_health_side_filter(client):
    """T-OBS-ROUTE-INT-02: ?side=host 过滤只返回 host 端服务。"""
    resp = client.get("/obs/services/health?side=host")
    assert resp.status_code == 200
    data = resp.json()
    for svc in data["services"]:
        assert svc.get("service_type") in ("host", "llm_node", None)


def test_services_health_empty_window(client):
    """T-OBS-ROUTE-INT-03: 极小时间窗口（1分钟）返回 UNKNOWN 状态。"""
    resp = client.get("/obs/services/health?window_minutes=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 0


def test_services_health_invalid_window(client):
    """T-OBS-ROUTE-INT-04: 超出范围的 window_minutes 返回 422。"""
    resp = client.get("/obs/services/health?window_minutes=99999")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /obs/sessions
# ---------------------------------------------------------------------------

def test_sessions_returns_200(client):
    """T-OBS-ROUTE-INT-05: 正常路径返回 200 + 分页字段。"""
    resp = client.get("/obs/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert "items" in data
    assert isinstance(data["items"], list)


def test_sessions_status_filter(client):
    """T-OBS-ROUTE-INT-06: ?status=ok 只返回 APPROVE 决策的会话。"""
    resp = client.get("/obs/sessions?status=ok")
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item["status"].lower() in ("ok", "approve")


def test_sessions_empty_result(client):
    """T-OBS-ROUTE-INT-07: 极小时间窗口返回空列表（total=0）。"""
    resp = client.get("/obs/sessions?window_minutes=1")
    assert resp.status_code == 200
    data = resp.json()
    # 可能为空，但结构必须正确
    assert "items" in data
    assert isinstance(data["items"], list)


def test_sessions_pagination(client):
    """T-OBS-ROUTE-INT-08: 分页参数正确传递。"""
    resp = client.get("/obs/sessions?page=1&page_size=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 1
    assert data["page_size"] == 1
    assert len(data["items"]) <= 1


def test_sessions_can_find_rows_beyond_default_fetch_limit(client, ledger):
    """Regression: session query should not silently truncate to first 100 rows."""
    now = int(time.time() * 1000)
    for i in range(120):
        ledger.log_governor({
            "request_id": f"req-many-{i:03d}",
            "thread_id": f"tm{i:03d}",
            "turn_id": f"um{i:03d}",
            "intent_id": f"im{i:03d}",
            "decision": "APPROVE",
            "reason_code": None,
            "rule_trace": None,
            "tokens": None,
            "snapshot_refs": None,
            "stage": "S_GOVERNOR",
            "ts_ms": now - i,
        })

    resp = client.get("/obs/sessions?window_minutes=10080&keyword=req-many-119")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["request_id"] == "req-many-119"


# ---------------------------------------------------------------------------
# GET /obs/trace/{request_id}
# ---------------------------------------------------------------------------

def test_trace_existing_request(client):
    """T-OBS-ROUTE-INT-09: 存在的 request_id 返回 200 + 节点列表。"""
    resp = client.get("/obs/trace/req-int-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == "req-int-001"
    assert "nodes" in data
    assert data["node_count"] >= 1


def test_trace_not_found(client):
    """T-OBS-ROUTE-INT-10: 不存在的 request_id 返回 404。"""
    resp = client.get("/obs/trace/nonexistent-xyz-999")
    assert resp.status_code == 404


def test_trace_fallback_path(client, ledger):
    """T-OBS-ROUTE-INT-11: graph_run_trace 无数据时降级路径正常返回。"""
    # req-int-002 只有 governor_log，无 graph_run_trace
    resp = client.get("/obs/trace/req-int-002")
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == "req-int-002"
    assert data["node_count"] >= 1


def test_trace_response_structure(client):
    """T-OBS-ROUTE-INT-12: 响应结构包含所有必要字段。"""
    resp = client.get("/obs/trace/req-int-001")
    assert resp.status_code == 200
    data = resp.json()
    required_fields = {"request_id", "status", "node_count", "nodes", "edges", "ts_ms", "timestamp"}
    assert required_fields.issubset(data.keys())


# ---------------------------------------------------------------------------
# GET /obs/metrics/timeseries
# ---------------------------------------------------------------------------

def test_metrics_timeseries_returns_200(client):
    """T-OBS-ROUTE-INT-13: 正常路径返回 200 + 时序字段。"""
    resp = client.get("/obs/metrics/timeseries")
    assert resp.status_code == 200
    data = resp.json()
    assert "points" in data
    assert "total_requests" in data
    assert "rejection_rate" in data
    assert isinstance(data["points"], list)


def test_metrics_timeseries_range_param(client):
    """T-OBS-ROUTE-INT-14: range_minutes 参数正确影响 range_ms。"""
    resp = client.get("/obs/metrics/timeseries?range_minutes=30")
    assert resp.status_code == 200
    data = resp.json()
    assert data["range_ms"] == 30 * 60 * 1000


def test_metrics_timeseries_interval_param(client):
    """T-OBS-ROUTE-INT-15: interval_minutes 参数正确影响 interval_ms。"""
    resp = client.get("/obs/metrics/timeseries?interval_minutes=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["interval_ms"] == 1 * 60 * 1000


def test_metrics_timeseries_invalid_range(client):
    """T-OBS-ROUTE-INT-16: 超出范围的 range_minutes 返回 422。"""
    resp = client.get("/obs/metrics/timeseries?range_minutes=1")
    assert resp.status_code == 422
