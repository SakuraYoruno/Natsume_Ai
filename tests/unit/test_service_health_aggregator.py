"""
T-OBS-HEALTH-01~06 — ServiceHealthAggregator 单元测试
TASK-0188 / FR-OBS-008
"""
from __future__ import annotations

import asyncio
import pytest

from host_brain.obs.service_health_aggregator import (
    ServiceHealthAggregator,
    ServiceHealthResult,
    ALL_SERVICES,
)


# ---------------------------------------------------------------------------
# T-OBS-HEALTH-01: 无探测函数时所有服务返回 UNKNOWN
# ---------------------------------------------------------------------------
def test_health_all_unknown_without_probes():
    agg = ServiceHealthAggregator()
    result = asyncio.run(agg.aggregate())
    assert result["total"] == len(ALL_SERVICES)
    assert result["unknown"] == len(ALL_SERVICES)
    assert result["up"] == 0
    assert result["down"] == 0
    assert "timestamp" in result


# ---------------------------------------------------------------------------
# T-OBS-HEALTH-02: 注册探测函数后返回 UP
# ---------------------------------------------------------------------------
def test_health_probe_returns_up():
    async def probe_up(service_id: str) -> ServiceHealthResult:
        return ServiceHealthResult(service_id, service_id, "host", "UP", latency_ms=10)

    agg = ServiceHealthAggregator()
    agg.register_probe("sqlite_ledger", probe_up)
    result = asyncio.run(agg.aggregate())
    svc_map = {s["service_id"]: s for s in result["services"]}
    assert svc_map["sqlite_ledger"]["status"] == "UP"
    assert svc_map["sqlite_ledger"]["latency_ms"] == 10


# ---------------------------------------------------------------------------
# T-OBS-HEALTH-03: 单服务超时时该服务 status=DOWN，其余不受影响
# ---------------------------------------------------------------------------
def test_health_single_service_timeout():
    async def probe_timeout(service_id: str) -> ServiceHealthResult:
        await asyncio.sleep(10)  # 远超超时
        return ServiceHealthResult(service_id, service_id, "host", "UP")

    async def probe_up(service_id: str) -> ServiceHealthResult:
        return ServiceHealthResult(service_id, service_id, "host", "UP", latency_ms=5)

    agg = ServiceHealthAggregator(timeout_ms=50)
    agg.register_probe("sqlite_ledger", probe_timeout)
    agg.register_probe("scheduler", probe_up)
    result = asyncio.run(agg.aggregate())
    svc_map = {s["service_id"]: s for s in result["services"]}
    assert svc_map["sqlite_ledger"]["status"] == "DOWN"
    assert "超时" in (svc_map["sqlite_ledger"]["error_summary"] or "")
    assert svc_map["scheduler"]["status"] == "UP"


# ---------------------------------------------------------------------------
# T-OBS-HEALTH-04: side=host 过滤只返回 host 服务
# ---------------------------------------------------------------------------
def test_health_side_filter_host():
    agg = ServiceHealthAggregator()
    result = asyncio.run(agg.aggregate(side_filter="host"))
    for svc in result["services"]:
        assert svc["side"] == "host"
    assert result["total"] > 0


# ---------------------------------------------------------------------------
# T-OBS-HEALTH-05: side=edge 过滤只返回 edge 服务
# ---------------------------------------------------------------------------
def test_health_side_filter_edge():
    agg = ServiceHealthAggregator()
    result = asyncio.run(agg.aggregate(side_filter="edge"))
    for svc in result["services"]:
        assert svc["side"] == "edge"
    assert result["total"] > 0


# ---------------------------------------------------------------------------
# T-OBS-HEALTH-06: 响应结构稳定（字段不随服务状态变化而缺失）
# ---------------------------------------------------------------------------
def test_health_response_structure_stable():
    async def probe_down(service_id: str) -> ServiceHealthResult:
        return ServiceHealthResult(service_id, service_id, "host", "DOWN", error_summary="连接失败")

    agg = ServiceHealthAggregator()
    agg.register_probe("neo4j", probe_down)
    result = asyncio.run(agg.aggregate())
    required_keys = {"service_id", "name", "side", "status", "latency_ms", "last_check_ts"}
    for svc in result["services"]:
        for key in required_keys:
            assert key in svc, f"缺少字段 {key} in {svc['service_id']}"
