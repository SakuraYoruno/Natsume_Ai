from __future__ import annotations

import asyncio

from host_brain.obs.service_health_aggregator import (
    ALL_SERVICES,
    ServiceHealthAggregator,
    ServiceHealthResult,
    validate_health_response,
)


def test_health_all_unknown_without_probes():
    agg = ServiceHealthAggregator()
    result = asyncio.run(agg.aggregate())
    assert result["total"] == len(ALL_SERVICES)
    assert result["unknown"] == len(ALL_SERVICES)
    assert result["up"] == 0
    assert result["down"] == 0
    assert "timestamp" in result


def test_health_probe_returns_up():
    async def probe_up(service_id: str) -> ServiceHealthResult:
        return ServiceHealthResult(service_id, service_id, "host", "UP", latency_ms=10)

    agg = ServiceHealthAggregator()
    agg.register_probe("sqlite_ledger", probe_up)
    result = asyncio.run(agg.aggregate())
    svc_map = {svc["service_id"]: svc for svc in result["services"]}
    assert svc_map["sqlite_ledger"]["status"] == "UP"
    assert svc_map["sqlite_ledger"]["latency_ms"] == 10


def test_health_single_service_timeout():
    async def probe_timeout(service_id: str) -> ServiceHealthResult:
        await asyncio.sleep(10)
        return ServiceHealthResult(service_id, service_id, "host", "UP")

    async def probe_up(service_id: str) -> ServiceHealthResult:
        return ServiceHealthResult(service_id, service_id, "host", "UP", latency_ms=5)

    agg = ServiceHealthAggregator(timeout_ms=50)
    agg.register_probe("sqlite_ledger", probe_timeout)
    agg.register_probe("scheduler", probe_up)
    result = asyncio.run(agg.aggregate())
    svc_map = {svc["service_id"]: svc for svc in result["services"]}
    assert svc_map["sqlite_ledger"]["status"] == "DOWN"
    assert "超时" in (svc_map["sqlite_ledger"]["error_summary"] or "")
    assert svc_map["scheduler"]["status"] == "UP"


def test_health_side_filter_host():
    agg = ServiceHealthAggregator()
    result = asyncio.run(agg.aggregate(side_filter="host"))
    for svc in result["services"]:
        assert svc["side"] == "host"
    assert result["total"] > 0


def test_health_side_filter_edge():
    agg = ServiceHealthAggregator()
    result = asyncio.run(agg.aggregate(side_filter="edge"))
    for svc in result["services"]:
        assert svc["side"] == "edge"
    assert result["total"] > 0


def test_health_response_structure_stable():
    async def probe_down(service_id: str) -> ServiceHealthResult:
        return ServiceHealthResult(service_id, service_id, "host", "DOWN", error_summary="杩炴帴澶辫触")

    agg = ServiceHealthAggregator()
    agg.register_probe("neo4j", probe_down)
    result = asyncio.run(agg.aggregate())
    required_keys = {"service_id", "name", "side", "status", "latency_ms", "last_check_ts"}
    for svc in result["services"]:
        for key in required_keys:
            assert key in svc, f"missing key {key} in {svc['service_id']}"


def test_health_response_lists_all_registered_services():
    agg = ServiceHealthAggregator()
    result = asyncio.run(agg.aggregate())
    service_ids = {svc["service_id"] for svc in result["services"]}

    assert result["total"] == len(ALL_SERVICES)
    for svc in ALL_SERVICES:
        assert svc["service_id"] in service_ids


def test_validate_health_response_rejects_incomplete_payloads():
    assert validate_health_response({
        "up": 0,
        "down": 0,
        "unknown": 0,
        "services": [],
        "timestamp": 0,
    }) is False
    assert validate_health_response({
        "total": 0,
        "up": 0,
        "down": 0,
        "unknown": 0,
        "timestamp": 0,
    }) is False
    assert validate_health_response({
        "total": 1,
        "up": 0,
        "down": 0,
        "unknown": 1,
        "timestamp": 0,
        "services": [{"service_id": "x", "name": "X", "side": "host"}],
    }) is False
    assert validate_health_response({
        "total": 0,
        "up": 0,
        "down": 0,
        "unknown": 0,
        "services": "not_a_list",
        "timestamp": 0,
    }) is False
    assert validate_health_response(None) is False  # type: ignore[arg-type]
    assert validate_health_response("string") is False  # type: ignore[arg-type]


def test_validate_health_response_accepts_complete_payload():
    agg = ServiceHealthAggregator()
    result = asyncio.run(agg.aggregate())
    assert validate_health_response(result) is True
