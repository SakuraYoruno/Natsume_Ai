from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.capability_forge import CapabilityForgeService
from host_brain.app.obs_routes import router
from host_brain.app.capability_gap_telemetry import CapabilityGapTelemetry
from host_brain.app.obs_routes_core import _clear_obs_route_response_cache


def setup_function() -> None:
    _clear_obs_route_response_cache()


def teardown_function() -> None:
    _clear_obs_route_response_cache()


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
    proposal = forge_service.create_proposal(
        session_id="sess-gap-1",
        proposer="tester",
        canary_traffic_percent=10,
    )
    forge_service.approve_and_promote(
        proposal_id=proposal["proposal_id"],
        approval_token_ref=proposal["governor_gate"]["approval_token_ref"],
        approver="reviewer",
        target_stage="canary",
    )
    forge_service.record_runtime_result(
        proposal_id=proposal["proposal_id"],
        success=False,
        latency_ms=300,
        error_code="FAILED",
    )
    monkeypatch.setattr(
        "host_brain.app.obs_routes_stats_capability_agent.read_capability_forge_projection",
        lambda: (
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
            {},
        ),
    )

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

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
    assert payload["forge_runtime_quality"]["summary"]["tracked_proposals"] >= 1
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
        "host_brain.app.obs_routes_stats_capability_agent.read_capability_forge_projection",
        lambda: (
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
            {},
        ),
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

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
    monkeypatch.setattr("host_brain.composition.process_services.get_capability_gap_telemetry", lambda: telemetry)
    monkeypatch.setattr("host_brain.composition.process_services.get_capability_forge_service", lambda: forge_service)
    monkeypatch.setattr(
        "host_brain.app.obs_routes_stats_capability_agent.read_capability_forge_projection",
        lambda: ({"gap": projected_gap, "runtime_quality": projected_quality}, {}),
    )

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    first = client.get("/obs/capability-gaps/stats?limit_recent=5")
    second = client.get("/obs/capability-gaps/stats?limit_recent=5")

    assert first.status_code == 200
    assert second.status_code == 200
    assert telemetry.calls == 0
    assert forge_service.calls == 0
