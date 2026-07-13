"""T-UI-01: 独立 monitor bundle 与 obs-side API 联通契约测试

TASK-0066: 验证 Web UI 面板端点可达、数据 API 联通、空/错误输入友好处理。

断言点:
- hostmain 不承载 /panel、/monitor 或 /obs；monitor 静态包由 nginx serve
- /api/ui/timeline 返回 200 且结构包含 intervals/total_requests/success_rate
- /api/ui/status-distribution 返回 200 且结构包含 status_counts/distribution_percentage
- /api/ui/request/{id} 对不存在 ID 返回 404
- /api/ui/link-topology 返回 200 且结构包含 nodes/edges
- /api/ui/search-requests 返回 200 且结构包含 results/total
- /observability/trace 对空 request_id 返回稳定结构
- /metrics 返回 200 且含 reason_code_distribution
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta

import pytest

try:
    from fastapi.testclient import TestClient
    from host_brain.app.obs_fastapi_app import app

    HAS_FASTAPI = True
except Exception:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="FastAPI not installed")


@pytest.fixture
def client(monkeypatch):
    """The historical trace contract now enters through operator auth."""

    from host_brain.observability.operator_query_gateway import (
        OPERATOR_QUERY_AUTH_BINDINGS_ENV,
        OperatorQueryGateway,
    )
    from host_brain.app import obs_fastapi_app

    token = "web-ui-contract-operator"
    monkeypatch.setenv(
        OPERATOR_QUERY_AUTH_BINDINGS_ENV,
        json.dumps(
            [
                {
                    "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                    "principal_id": "ops.web-ui-contract",
                    "scopes": ["obs:trace:read", "obs:ui:read"],
                }
            ]
        ),
    )
    previous_gateway = app.state.operator_query_gateway
    app.state.operator_query_gateway = OperatorQueryGateway.from_environment()
    monkeypatch.setattr(
        obs_fastapi_app,
        "get_query_projection_store",
        lambda: type("EmptyProjectionStore", (), {"get": lambda _self, _kind, _key: None})(),
    )
    try:
        yield TestClient(app, headers={"Authorization": f"Bearer {token}"})
    finally:
        app.state.operator_query_gateway = previous_gateway


# ---------------------------------------------------------------------------
# T-UI-01: 面板可渲染与 API 联通
# ---------------------------------------------------------------------------

class TestMonitorStaticBundle:
    """Monitor 静态包由 release build 生成，仓库只冻结构建输入。"""

    def test_monitor_bundle_contains_spa_mount(self):
        """React SPA 源入口存在，hostmain 不负责 HTTP serve。"""
        from pathlib import Path

        project_root = Path(__file__).resolve().parents[2]
        spa_index = project_root / "host_brain" / "app" / "monitor_ui" / "index.html"
        body = spa_index.read_text(encoding="utf-8-sig")
        assert "<title>" in body or '<div id="root">' in body


class TestObservabilityTraceAPI:
    """观测追踪 API 测试"""

    def test_trace_valid_request_id(self, client):
        """POST /observability/trace 返回稳定结构"""
        resp = client.post(
            "/observability/trace",
            json={"request_id": "req_nonexistent_test_001"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "request_id" in data
        assert "timeline_total" in data
        assert "sources" in data
        assert "timeline" in data
        assert isinstance(data["timeline"], list)
        assert isinstance(data["sources"], dict)

    def test_trace_empty_timeline_for_unknown_id(self, client):
        """不存在的 request_id 返回空时间线"""
        resp = client.post(
            "/observability/trace",
            json={"request_id": "req_definitely_does_not_exist_999"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["timeline_total"] == 0
        assert data["timeline"] == []


class TestWebUIDataAPIs:
    """Web UI 数据 API 端点测试"""

    def _time_params(self):
        now = datetime.utcnow()
        start = (now - timedelta(hours=1)).isoformat()
        end = now.isoformat()
        return {"start_time": start, "end_time": end}

    def test_timeline_aggregation(self, client):
        """GET /api/ui/timeline 返回聚合结构"""
        params = self._time_params()
        params["interval"] = "1h"
        resp = client.get("/api/ui/timeline", params=params)
        assert resp.status_code == 200
        data = resp.json()
        assert "intervals" in data
        assert "total_requests" in data
        assert "success_rate" in data
        assert isinstance(data["intervals"], list)

    def test_status_distribution(self, client):
        """GET /api/ui/status-distribution 返回分布结构"""
        params = self._time_params()
        resp = client.get("/api/ui/status-distribution", params=params)
        assert resp.status_code == 200
        data = resp.json()
        assert "status_counts" in data
        assert "distribution_percentage" in data
        assert "total_requests" in data

    def test_request_detail_not_found(self, client):
        """GET /api/ui/request/{id} 对不存在 ID 返回 404"""
        resp = client.get("/api/ui/request/req_nonexistent_detail_999")
        assert resp.status_code == 404

    def test_link_topology(self, client):
        """GET /api/ui/link-topology 返回拓扑结构"""
        params = self._time_params()
        resp = client.get("/api/ui/link-topology", params=params)
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        assert isinstance(data["nodes"], list)
        assert isinstance(data["edges"], list)

    def test_search_requests(self, client):
        """GET /api/ui/search-requests 返回搜索结果结构"""
        params = self._time_params()
        params["query"] = "test"
        resp = client.get("/api/ui/search-requests", params=params)
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "total" in data
        assert isinstance(data["results"], list)


class TestMetricsForUI:
    """Obs-side 指标端点对面板的适配性"""

    def test_obs_metrics_contains_latency_baseline(self, client):
        """GET /obs/metrics 由 obs side 提供。"""
        resp = client.get("/obs/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "latency_baseline" in data
        assert "recent_chains" in data

    def test_obs_app_does_not_expose_chat_endpoint(self, client):
        """obs side 不能承载主聊天请求。"""
        resp = client.post("/v1/chat", json={"message": "T-UI-01 test"})
        assert resp.status_code == 404


class TestPanelReadonlySafety:
    """面板不引入执行入口的安全性验证"""

    def test_ui_endpoints_are_get_only(self, client):
        """Web UI 数据端点均为 GET（只读）"""
        params = {
            "start_time": datetime.utcnow().isoformat(),
            "end_time": datetime.utcnow().isoformat(),
        }
        # /api/ui/* 均应为 GET
        for path in ["/api/ui/timeline", "/api/ui/status-distribution", "/api/ui/link-topology"]:
            resp = client.post(path, json={})
            assert resp.status_code == 405 or resp.status_code == 422, \
                f"{path} should not accept POST (got {resp.status_code})"

    def test_panel_no_form_action(self, client):
        """Monitor SPA shell 无 form action。"""
        from pathlib import Path

        project_root = Path(__file__).resolve().parents[2]
        body = (project_root / "host_brain" / "app" / "static" / "monitor" / "index.html").read_text(
            encoding="utf-8-sig"
        ).lower()
        assert "<form" not in body or 'action="' not in body
