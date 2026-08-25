from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from natsume_contracts.operator.v1 import ProjectionDegradedState

from host_brain.app.capability_forge import CapabilityForgeService
from host_brain.app.obs_routes import router
from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.app.capability_gap_telemetry import CapabilityGapTelemetry
from host_brain.app.observability_runtime.capability_stats_projection import (
    CAPABILITY_FORGE_OPERATOR_KEY,
    CAPABILITY_FORGE_OPERATOR_KIND,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.observability.projection_store import QueryProjectionStore


class _UnusedProjectionStore:
    @staticmethod
    def get(_kind: str, _key: str):
        return None


def _app_with_projection_ports(store=None) -> FastAPI:
    app = FastAPI()
    projection_store = store or _UnusedProjectionStore()
    app.state.obs_route_ports = ObsRoutePorts(
        query_projection_store_provider=lambda: projection_store,
    )
    app.include_router(router)
    return app


def test_obs_capability_gaps_stats_endpoint(monkeypatch) -> None:
    telemetry = CapabilityGapTelemetry()
    telemetry.record_detected(
        request_id="req-gap-1",
        session_id="sess-gap-1",
        runtime_capability_id="runtime_cap_gap_1",
        reason_code="ACTION_INTENT_FALLBACK_CHAT",
        suggested_family="code",
        missing_capability_tags=["coding"],
        confidence=0.88,
        executor_route="delegate",
        executor_target="agent_worker",
    )
    telemetry.record_execution(
        runtime_capability_id="runtime_cap_gap_1",
        request_id="req-gap-1",
        success=False,
        executor_route="delegate",
        executor_target="agent_worker",
        latency_ms=256,
        error_code="FAILED",
    )
    forge_service = CapabilityForgeService(telemetry=telemetry)
    forge_service.create_proposal(
        session_id="sess-gap-1",
        proposer="tester",
        canary_traffic_percent=10,
    )
    monkeypatch.setattr(
        "host_brain.app.obs_routes_stats_capability_agent.read_capability_forge_projection_from_store",
        lambda _store: (
            {
                "gap": telemetry.snapshot(
                    limit_recent=200,
                    limit_hotspots=50,
                    limit_candidates=200,
                    min_candidate_detected=1,
                    min_candidate_sessions=1,
                    min_candidate_failure_rate=0.0,
                    min_candidate_failed=0,
                ),
                "runtime_quality": forge_service.runtime_quality_stats(limit=200),
            },
            {"available": True},
        ),
    )

    client = TestClient(_app_with_projection_ports())

    response = client.get("/obs/capability-gaps/stats?limit_recent=5")
    assert response.status_code == 200
    payload = response.json()
    assert payload["detected_total"] == 1
    assert payload["executed_total"] == 1
    assert payload["success_total"] == 0
    assert payload["failed_total"] == 1
    assert payload["by_reason"][0]["key"] == "ACTION_INTENT_FALLBACK_CHAT"
    assert payload["by_executor"][0]["key"] == "agent_worker"
    assert len(payload["recent_events"]) == 2
    assert "hotspots" in payload
    assert "forge_candidates" in payload
    assert "forge_runtime_quality" in payload
    assert (
        payload["forge_runtime_quality"]["summary"]["tracked_proposals"]
        == 0
    )
    assert isinstance(payload["forge_runtime_quality"]["items"], list)
    assert "candidate_thresholds" in payload


def test_obs_capability_gap_forge_candidates_endpoint(monkeypatch) -> None:
    telemetry = CapabilityGapTelemetry()
    for index in range(3):
        cap_id = f"runtime_cap_obs_{index}"
        telemetry.record_detected(
            request_id=f"req-obs-{index}",
            session_id=f"sess-obs-{index % 2}",
            runtime_capability_id=cap_id,
            reason_code="ACTION_INTENT_FALLBACK_CHAT",
            suggested_family="code",
            missing_capability_tags=["coding"],
            confidence=0.9,
            executor_route="delegate",
            executor_target="agent_worker",
        )
        telemetry.record_execution(
            runtime_capability_id=cap_id,
            request_id=f"req-obs-{index}",
            success=False,
            executor_route="delegate",
            executor_target="agent_worker",
            latency_ms=200 + index,
            error_code="FAILED",
        )

    monkeypatch.setattr(
        "host_brain.app.obs_routes_stats_capability_agent.read_capability_forge_projection_from_store",
        lambda _store: (
            {
                "gap": telemetry.snapshot(
                    limit_recent=200,
                    limit_hotspots=50,
                    limit_candidates=200,
                    min_candidate_detected=1,
                    min_candidate_sessions=1,
                    min_candidate_failure_rate=0.0,
                    min_candidate_failed=0,
                )
            },
            {"available": True},
        ),
    )
    client = TestClient(_app_with_projection_ports())

    response = client.get(
        "/obs/capability-gaps/forge-candidates"
        "?limit=10&min_candidate_detected=2&min_candidate_sessions=2"
        "&min_candidate_failure_rate=0.3&min_candidate_failed=2"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_candidates"] >= 1
    top = payload["candidates"][0]
    assert top["stage"] == "READY_FOR_FORGE"
    assert top["suggested_family"] == "code"
    assert top["recommended_action"] == "forge_with_reliability_tests"
    assert payload["candidate_thresholds"]["min_detected_count"] == 2


def test_obs_capability_gaps_stats_endpoint_never_calls_live_services(monkeypatch) -> None:
    class _FakeTelemetry:
        def __init__(self) -> None:
            self.calls = 0

        def snapshot(self, **kwargs):
            self.calls += 1
            return {
                "timestamp": 1700000000000,
                "detected_total": 1,
                "executed_total": 1,
                "success_total": 1,
                "failed_total": 0,
                "success_rate": 1.0,
                "active_runtime_capabilities": 1,
                "by_reason": [{"key": "ACTION_INTENT_FALLBACK_CHAT", "count": 1}],
                "by_family": [{"key": "code", "count": 1}],
                "by_executor": [{"key": "agent_worker", "count": 1}],
                "hotspots": [],
                "forge_candidates": [],
                "candidate_thresholds": {
                    "min_detected_count": 3,
                    "min_unique_sessions": 2,
                    "min_failure_rate": 0.35,
                    "min_failed_count": 2,
                },
                "recent_events": [],
            }

    class _FakeForgeService:
        def __init__(self) -> None:
            self.calls = 0

        def runtime_quality_stats(self, limit=8):
            self.calls += 1
            return {
                "timestamp": 1700000000000,
                "summary": {
                    "tracked_proposals": 0,
                    "unhealthy_proposals": 0,
                    "critical_proposals": 0,
                    "avg_health_score": 0.0,
                    "avg_window_failure_rate": 0.0,
                    "avg_window_p95_latency_ms": 0.0,
                },
                "items": [],
            }

    telemetry = _FakeTelemetry()
    forge_service = _FakeForgeService()
    projected_gap = telemetry.snapshot()
    projected_quality = forge_service.runtime_quality_stats()
    telemetry.calls = 0
    forge_service.calls = 0
    monkeypatch.setattr(
        "host_brain.app.obs_routes_stats_capability_agent.read_capability_forge_projection_from_store",
        lambda _store: (
            {"gap": projected_gap, "runtime_quality": projected_quality},
            {"available": True},
        ),
    )

    client = TestClient(_app_with_projection_ports())

    first = client.get("/obs/capability-gaps/stats?limit_recent=5")
    second = client.get("/obs/capability-gaps/stats?limit_recent=5")

    assert first.status_code == 200
    assert second.status_code == 200
    assert telemetry.calls == 0
    assert forge_service.calls == 0


@pytest.mark.parametrize(
    "path",
    (
        "/obs/capability-gaps/stats?limit_recent=5",
        "/obs/capability-gaps/forge-candidates?limit=5",
        "/obs/capability-gaps/forge-proposals?limit=5",
        "/obs/capability-gaps/forge-proposals/missing-proposal",
        "/obs/capability-gaps/forge-runtime/stats",
        "/obs/capability-gaps/forge-runtime/tuning?limit=5",
        "/obs/capability-gaps/forge-runtime/quality?limit=5",
    ),
)
def test_obs_capability_forge_reads_source_unavailable_fail_closed(
    tmp_path,
    path: str,
) -> None:
    db_path = tmp_path / "capability-gap-unavailable.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="capability-gap-source-unavailable",
    )
    assert migrated.success, migrated
    store = QueryProjectionStore(db_path)
    lease = store.acquire_leader_lease(
        "capability-gap-source-unavailable",
        lease_ms=10_000,
    )
    assert lease is not None
    now_ms = int(time.time() * 1000)
    with store.bind_leader_lease(lease):
        store.upsert(
            CAPABILITY_FORGE_OPERATOR_KIND,
            CAPABILITY_FORGE_OPERATOR_KEY,
            {
                "schema_version": "Natsume.operator.projection_source_unavailable.v1",
                "reason_code": "OBS_HOST_RUNTIME_SOURCE_NOT_INJECTED",
            },
            source_cursor=now_ms,
            source_ref="test://capability-forge/source-unavailable",
            freshness_ts_ms=now_ms,
            degraded_state=ProjectionDegradedState.SOURCE_UNAVAILABLE,
        )

    response = TestClient(_app_with_projection_ports(store)).get(path)

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail == {
        "reason_code": "OBS_CAPABILITY_FORGE_PROJECTION_UNAVAILABLE",
        "degraded_state": "SOURCE_UNAVAILABLE",
        "upstream_reason_code": "OPERATOR_PROJECTION_SOURCE_UNAVAILABLE",
        "projection_version": 1,
        "freshness_ts_ms": now_ms,
        "retryable": True,
    }
