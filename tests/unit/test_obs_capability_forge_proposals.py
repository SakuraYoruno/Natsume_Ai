from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.capability_forge import CapabilityForgeService
from host_brain.app.capability_gap_telemetry import CapabilityGapTelemetry
from host_brain.app.obs_routes import router


def _build_service() -> CapabilityForgeService:
    telemetry = CapabilityGapTelemetry()
    for index in range(3):
        cap_id = f"runtime_cap_obs_forge_{index}"
        telemetry.record_detected(
            request_id=f"req-obs-forge-{index}",
            session_id=f"sess-obs-forge-{index % 2}",
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
            request_id=f"req-obs-forge-{index}",
            success=False,
            executor_route="delegate",
            executor_target="code_agent",
            latency_ms=200 + index,
            error_code="FAILED",
        )
    return CapabilityForgeService(telemetry=telemetry)


def test_obs_capability_forge_proposal_endpoints(monkeypatch) -> None:
    service = _build_service()
    monkeypatch.setattr("host_brain.app.dependencies.get_capability_forge_service", lambda: service)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    preview_resp = client.post(
        "/obs/capability-gaps/forge-proposals/preview",
        json={"canary_traffic_percent": 8},
    )
    assert preview_resp.status_code == 200
    preview_payload = preview_resp.json()
    assert preview_payload["candidate"]["suggested_family"] == "code"

    create_resp = client.post(
        "/obs/capability-gaps/forge-proposals",
        json={
            "session_id": "sess-api",
            "proposer": "api-user",
            "canary_traffic_percent": 6,
        },
    )
    assert create_resp.status_code == 200
    create_payload = create_resp.json()["proposal"]
    proposal_id = create_payload["proposal_id"]
    approval_token_ref = create_payload["governor_gate"]["approval_token_ref"]

    bad_approve = client.post(
        f"/obs/capability-gaps/forge-proposals/{proposal_id}/approve",
        json={
            "approval_token_ref": "approval::bad-token",
            "approver": "reviewer",
            "target_stage": "shadow",
        },
    )
    assert bad_approve.status_code == 403

    approve_resp = client.post(
        f"/obs/capability-gaps/forge-proposals/{proposal_id}/approve",
        json={
            "approval_token_ref": approval_token_ref,
            "approver": "reviewer",
            "target_stage": "full",
        },
    )
    assert approve_resp.status_code == 200
    approve_payload = approve_resp.json()["proposal"]
    assert approve_payload["status"] == "APPROVED"
    assert approve_payload["promotion"]["stage"] == "FULL"

    list_resp = client.get("/obs/capability-gaps/forge-proposals?status=APPROVED&limit=10")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] >= 1

    get_resp = client.get(f"/obs/capability-gaps/forge-proposals/{proposal_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["proposal"]["proposal_id"] == proposal_id

    rollback_resp = client.post(
        f"/obs/capability-gaps/forge-proposals/{proposal_id}/rollback",
        json={"operator": "ops", "reason": "manual rollback"},
    )
    assert rollback_resp.status_code == 200
    assert rollback_resp.json()["proposal"]["status"] == "ROLLED_BACK"

    runtime_match_resp = client.post(
        "/obs/capability-gaps/forge-proposals/runtime-match",
        json={
            "request_id": "req-runtime-match",
            "suggested_family": "code",
            "missing_capability_tags": ["coding"],
            "reason_code": "ACTION_INTENT_FALLBACK_CHAT",
        },
    )
    assert runtime_match_resp.status_code == 200
    runtime_payload = runtime_match_resp.json()
    assert "decision" in runtime_payload

    runtime_stats_resp = client.get("/obs/capability-gaps/forge-runtime/stats")
    assert runtime_stats_resp.status_code == 200
    runtime_stats_payload = runtime_stats_resp.json()
    assert runtime_stats_payload["counters"]["total"] >= 1

    tuning_stats_resp = client.get("/obs/capability-gaps/forge-runtime/tuning?limit=5")
    assert tuning_stats_resp.status_code == 200
    tuning_payload = tuning_stats_resp.json()
    assert int(dict(tuning_payload["summary"]).get("total_proposals") or 0) >= 1
    assert isinstance(tuning_payload["items"], list)

    quality_stats_resp = client.get("/obs/capability-gaps/forge-runtime/quality?limit=5")
    assert quality_stats_resp.status_code == 200
    quality_payload = quality_stats_resp.json()
    assert "summary" in quality_payload
    assert isinstance(quality_payload["items"], list)
