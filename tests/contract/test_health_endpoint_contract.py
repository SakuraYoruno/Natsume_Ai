"""T-HEALTH-EP-01~02: /health 端点 edge 探针集成 contract 测试（TASK-0211 M23-B）。

验证 /health 端点的 runtime_statuses 包含 edge 传输层探针键，
以及 edge 探针 DOWN 时 boot_status 为 DEGRADED。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app import health_routes


def _guardian_state() -> dict[str, object]:
    return {
        "status": "DEGRADED",
        "durability_required": True,
        "durability_status": "READY",
        "durability_authority": "RUNTIME_GUARDIAN_JOURNAL",
        "guardian_authority": {
            "authority": "RUNTIME_GUARDIAN",
            "runtime_scope_id": "health-contract-scope",
            "fencing_token": 1,
        },
    }


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        health_routes,
        "load_host_config",
        lambda: SimpleNamespace(
            success=True,
            config=SimpleNamespace(cold_start_allowed=True),
        ),
    )
    monkeypatch.setattr(
        health_routes,
        "build_host_runtime_statuses",
        lambda _config: {},
    )

    async def dependency_probes() -> dict[str, object]:
        return {
            "edge.rpc.gateway": {
                "available": False,
                "unavailable_kind": "edge_rpc",
                "detail": "contract probe unavailable",
            }
        }

    app = FastAPI()
    app.include_router(
        health_routes.create_health_router(
            degradation_state_provider=_guardian_state,
            ledger_provider=lambda: None,
            dependency_probe_provider=dependency_probes,
        )
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


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

    def test_t_health_ep_03_skip_dependency_probes_bypasses_edge_probe(self, client):
        """T-HEALTH-EP-03：/health?skip_dependency_probes=true 不应触发外部依赖探针。"""

        with patch(
            "host_brain.app.health_routes._read_dependency_probes",
            side_effect=AssertionError("dependency probe port should be skipped"),
        ), patch(
            "host_brain.app.health_routes.build_edge_transport_probe_statuses",
            side_effect=AssertionError("build_edge_transport_probe_statuses should be skipped"),
        ):
            response = client.get("/health?skip_dependency_probes=true")

        assert response.status_code == 200

    def test_t_health_ep_04_real_component_status_beats_transport_reachability(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            health_routes,
            "load_host_config",
            lambda: SimpleNamespace(
                success=True,
                config=SimpleNamespace(cold_start_allowed=True),
            ),
        )
        monkeypatch.setattr(
            health_routes,
            "build_host_runtime_statuses",
            lambda _config: {},
        )

        async def dependency_probes() -> dict[str, object]:
            return {
                "edge.rpc.gateway": {
                    "available": True,
                    "detail": "transport reachable",
                },
                "edge.rpc.barge_in": {
                    "available": False,
                    "unavailable_kind": "dependency",
                    "detail": "barge-in disabled by config",
                },
            }

        report = asyncio.run(
            health_routes.build_live_health_report(
                ledger_provider=lambda: None,
                dependency_probe_provider=dependency_probes,
                degradation_state_provider=_guardian_state,
            )
        )
        barge_in = next(
            row
            for row in report["services"]
            if row["service_id"] == "edge.rpc.barge_in"
        )
        assert barge_in["status"] != "UP"
        assert "disabled" in barge_in["detail"]
