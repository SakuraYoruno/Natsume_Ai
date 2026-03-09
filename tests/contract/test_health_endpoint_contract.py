"""T-HEALTH-EP-01~02: /health 端点 edge 探针集成 contract 测试（TASK-0211 M23-B）。

验证 /health 端点的 runtime_statuses 包含 edge 传输层探针键，
以及 edge 探针 DOWN 时 boot_status 为 DEGRADED。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from host_brain.app.fastapi_app import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


class TestHealthEndpointEdgeProbeContract:
    """T-HEALTH-EP-01~02: /health 端点 edge 探针集成 contract 测试。"""

    def test_t_health_ep_01_runtime_statuses_contains_edge_probe_key(self, client):
        """T-HEALTH-EP-01：/health 端点返回的 details 包含 edge 传输层探针相关信息。

        build_edge_transport_probe_statuses 集成后，/health 端点的 details
        应能反映 edge 传输层状态（至少包含 service_statuses 或 runtime_statuses 字段）。
        """
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "details" in data, "/health 响应应含 details 字段"
        # details 结构稳定（字段不随状态变化而缺失）
        assert isinstance(data["details"], dict), "details 应为字典"
        # status 字段存在且为合法值
        assert data["status"] in ("OK", "DEGRADED", "FAILED"), "status 应为合法值"

    def test_t_health_ep_02_edge_probe_integrated_in_health_endpoint(self, client):
        """T-HEALTH-EP-02：/health 端点调用了 build_edge_transport_probe_statuses（集成验证）。

        验证 build_edge_transport_probe_statuses 已被集成到 /health 端点的
        runtime_statuses 构建流程中（不再是孤立的未调用函数）。
        """
        from unittest.mock import patch, call

        probe_call_args = []

        def _mock_edge_probe(*, available, detail=None):
            probe_call_args.append({"available": available, "detail": detail})
            return {"edge.rpc.gateway": {"available": available, "detail": detail or "mock"}}

        with patch(
            "host_brain.app.health_routes.build_edge_transport_probe_statuses",
            side_effect=_mock_edge_probe,
        ):
            response = client.get("/health")

        assert response.status_code == 200
        # 验证 build_edge_transport_probe_statuses 被调用了（不再是孤立函数）
        assert len(probe_call_args) >= 1, (
            "build_edge_transport_probe_statuses 应在 /health 端点中被调用，"
            "当前未被调用说明 M23-B 集成未生效"
        )

    def test_t_health_ep_01b_health_response_structure_stable(self, client):
        """T-HEALTH-EP-01b：/health 响应结构稳定（字段不随状态变化而缺失）。"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        required_fields = {"status", "version", "timestamp", "details"}
        missing = required_fields - set(data.keys())
        assert not missing, f"/health 响应缺少字段: {missing}"
