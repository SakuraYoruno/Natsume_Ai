from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.capability_forge import CapabilityForgeService
from host_brain.app.obs_routes import router
from host_brain.app.capability_gap_telemetry import CapabilityGapTelemetry


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
        executor_target="code_agent",
    )
    telemetry.record_execution(
        runtime_capability_id="runtime_cap_gap_1",
        request_id="req-gap-1",
        success=False,
        executor_route="delegate",
        executor_target="code_agent",
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
        "host_brain.app.dependencies.get_capability_gap_telemetry",
        lambda: telemetry,
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_capability_forge_service",
        lambda: forge_service,
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
    assert payload["by_executor"][0]["key"] == "code_agent"
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
            executor_target="code_agent",
        )
        telemetry.record_execution(
            runtime_capability_id=cap_id,
            request_id=f"req-obs-{index}",
            success=False,
            executor_route="delegate",
            executor_target="code_agent",
            latency_ms=200 + index,
            error_code="FAILED",
        )

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_capability_gap_telemetry",
        lambda: telemetry,
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
