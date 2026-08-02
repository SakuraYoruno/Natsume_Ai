from __future__ import annotations

import asyncio

from host_brain.app import startup
from host_brain.app.health_routes import _apply_host_health_status
from host_brain.app.observability_runtime import service_health_heartbeat as heartbeat
from host_brain.core.errors import ACCEPTANCE_PENDING_EXTERNAL
from host_brain.core.services import (
    ServiceStartupOrchestrator,
    build_default_service_registry,
    build_node_probe_report,
)
from host_brain.observability.degradation_state import normalize_service_observations


def test_disabled_remote_edge_is_not_tested_without_network(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOST_ENABLE_EDGE_RPC", "false")

    probe = asyncio.run(startup.probe_edge_rpc_health())
    rows = startup.build_edge_dependency_probe_statuses(probe)

    assert probe["status"] == "NOT_TESTED"
    assert probe["available"] is False
    assert probe["reason_code"] == ACCEPTANCE_PENDING_EXTERNAL
    assert len(rows) == 5
    assert {row["status"] for row in rows.values()} == {"NOT_TESTED"}
    assert all(row["not_tested"] for row in rows.values())


def test_not_tested_edge_makes_startup_acceptance_pending() -> None:
    registry = build_default_service_registry()
    host_statuses = {
        spec.service_id: {"available": True, "detail": "local service healthy"}
        for spec in registry.all()
        if spec.owner == "host"
    }
    edge_rows = startup.build_edge_dependency_probe_statuses(
        {
            "available": False,
            "status": "NOT_TESTED",
            "not_tested": True,
            "reason_code": ACCEPTANCE_PENDING_EXTERNAL,
            "detail": "remote Edge intentionally not started",
        }
    )

    report = ServiceStartupOrchestrator(registry=registry).build_startup_report(
        request_id="req_dl07_external_pending",
        runtime_statuses=host_statuses,
        dependency_probes=edge_rows,
        ts_ms=1_800_300_000_000,
    )

    assert report["summary"]["boot_status"] == ACCEPTANCE_PENDING_EXTERNAL
    assert report["summary"]["required_unavailable"] == 0
    assert report["summary"]["required_not_tested"] == 5
    assert report["summary"]["probe_unavailable"] == 0
    assert report["summary"]["probe_not_tested"] == 5


def test_missing_node_probe_is_not_reported_up() -> None:
    report = build_node_probe_report(
        nodes=[
            {
                "node_id": "personal-edge-vts",
                "region": "lan",
                "role": "edge_realtime",
                "services": ["edge.rpc.vts"],
                "health_endpoint": "http://edge.invalid/healthz",
                "key_profile_id": "kpf-edge-vts",
            }
        ],
        runtime_statuses={},
        request_id="req_unprobed_node",
        ts_ms=1_800_300_000_001,
    )

    assert report["status"] == ACCEPTANCE_PENDING_EXTERNAL
    assert report["summary"] == {
        "node_total": 1,
        "node_down": 0,
        "node_not_tested": 1,
        "node_up": 0,
    }
    assert report["nodes"][0]["node_status"] == "NOT_TESTED"


def test_health_status_keeps_external_pending_separate_from_failure() -> None:
    report = {
        "summary": {
            "required_unavailable": 0,
            "optional_unavailable": 0,
            "required_not_tested": 5,
            "optional_not_tested": 0,
            "runtime_component_unavailable": 0,
        },
        "services": [],
        "reason_code": ACCEPTANCE_PENDING_EXTERNAL,
    }

    _apply_host_health_status(report)

    assert report["status"] == ACCEPTANCE_PENDING_EXTERNAL
    assert report["summary"]["overall_status"] == ACCEPTANCE_PENDING_EXTERNAL


def test_heartbeat_preserves_not_tested_through_cache(monkeypatch) -> None:
    monkeypatch.setenv("HOST_ENABLE_EDGE_RPC", "false")
    state = heartbeat.ServiceHealthProbeState(
        edge_probe=startup.probe_edge_rpc_health,
        edge_dependency_status_builder=startup.build_edge_dependency_probe_statuses,
    )

    result = asyncio.run(heartbeat._probe_edge(probe_state=state))
    assert result["status"] == "NOT_TESTED"
    assert result["sub_services"]
    assert {
        row["status"] for row in result["sub_services"].values()
    } == {"NOT_TESTED"}

    heartbeat._write_core_service_cache(
        now_ts=1_800_300_000_002,
        edge_result=result,
        host_result={"overall_status": "UP", "latency_ms": 1, "sub_services": {}},
        probe_state=state,
    )
    assert {
        state.get_cached(service_id)["status"]
        for service_id in heartbeat.EDGE_SERVICE_IDS
    } == {"NOT_TESTED"}


def test_not_tested_does_not_mutate_guardian_degradation_state() -> None:
    observations = normalize_service_observations(
        [
            {
                "service_id": "edge.rpc.tts",
                "status": "NOT_TESTED",
                "detail": "remote Edge intentionally not started",
            }
        ],
        source_ref="service-heartbeat://runtime/dl07-test",
        now_ms=1_800_300_000_003,
    )

    assert observations == ()
