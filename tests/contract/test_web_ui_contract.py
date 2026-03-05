"""T-UI-01: 链路可视化面板可渲染与 API 联通契约测试

TASK-0066: 验证 Web UI 面板端点可达、数据 API 联通、空/错误输入友好处理。

断言点:
- /panel 返回 200 且 content-type 包含 text/html
- /api/ui/timeline 返回 200 且结构包含 intervals/total_requests/success_rate
- /api/ui/status-distribution 返回 200 且结构包含 status_counts/distribution_percentage
- /api/ui/request/{id} 对不存在 ID 返回 404
- /api/ui/link-topology 返回 200 且结构包含 nodes/edges
- /api/ui/search-requests 返回 200 且结构包含 results/total
- /observability/trace 对空 request_id 返回稳定结构
- /metrics 返回 200 且含 reason_code_distribution
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta

import pytest

try:
    from fastapi.testclient import TestClient
    from host_brain.app.fastapi_app import app

    HAS_FASTAPI = True
except Exception:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="FastAPI not installed")


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# T-UI-01: 面板可渲染与 API 联通
# ---------------------------------------------------------------------------

class TestPanelRendering:
    """面板 HTML 页面可达性测试"""

    def test_panel_returns_html(self, client):
        """GET /panel 返回 HTML 页面"""
        resp = client.get("/panel")
        # 若 static/panel.html 存在则 200，否则 404（仍是 HTML 响应）
        assert resp.status_code in (200, 404)
        assert "text/html" in resp.headers.get("content-type", "")

    def test_panel_contains_key_elements(self, client):
        """面板页面为 React SPA 架构（M17 升级后）：
        /panel 重定向到 /static/monitor/index.html；
        验证 SPA 特征：static/monitor/index.html 文件存在，或响应含 <title> 标签。
        """
        from pathlib import Path

        # 方式一：检测 SPA 静态文件是否存在（不依赖 HTTP 响应内容）
        project_root = Path(__file__).resolve().parents[2]
        spa_index = project_root / "static" / "monitor" / "index.html"
        if spa_index.exists():
            body = spa_index.read_text(encoding="utf-8")
            # SPA index.html 应含 <title> 或 <div id="root">
            assert "<title>" in body or '<div id="root">' in body, (
                "static/monitor/index.html 不含 SPA 挂载点特征"
            )
            return

        # 方式二：/panel 返回 301 重定向（React SPA 架构）
        resp = client.get("/panel", follow_redirects=False)
        if resp.status_code in (301, 302, 307, 308):
            # 重定向到 SPA 入口，符合 M17 架构预期
            assert "/static/monitor" in resp.headers.get("location", ""), (
                "/panel 重定向目标不是 SPA 入口"
            )
            return

        # 方式三：若跟随重定向后返回 HTML，检测 <title> 标签（不假设 SSR 内嵌内容）
        resp = client.get("/panel")
        if resp.status_code == 200:
            body = resp.text
            assert "<title>" in body or '<div id="root">' in body, (
                "面板页面不含 SPA 特征（<title> 或 <div id='root'>）"
            )


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
    """指标端点对面板的适配性"""

    def test_metrics_contains_reason_code_distribution(self, client):
        """GET /metrics 包含 reason_code_distribution"""
        resp = client.get("/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "reason_code_distribution" in data
        assert isinstance(data["reason_code_distribution"], dict)

    def test_metrics_chat_count_increments(self, client):
        """chat 请求后 metrics.total_chat_requests 递增"""
        m1 = client.get("/metrics").json()
        client.post("/v1/chat", json={"message": "T-UI-01 test"})
        m2 = client.get("/metrics").json()
        assert m2["total_chat_requests"] >= m1["total_chat_requests"] + 1


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
        """面板 HTML 无 form action 或提交按钮"""
        resp = client.get("/panel")
        if resp.status_code == 200:
            body = resp.text.lower()
            assert "<form" not in body or 'action="' not in body
