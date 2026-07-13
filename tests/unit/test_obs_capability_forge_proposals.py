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
            executor_target="agent_worker",
        )
        telemetry.record_execution(
            runtime_capability_id=cap_id,
            request_id=f"req-obs-forge-{index}",
            success=False,
            executor_route="delegate",
            executor_target="agent_worker",
            latency_ms=200 + index,
            error_code="FAILED",
        )
    return CapabilityForgeService(telemetry=telemetry)


def test_obs_capability_forge_proposal_endpoints(monkeypatch) -> None:
    service = _build_service()
    seeded = service.create_proposal(
        session_id="sess-direct-seed",
        proposer="pytest-fixture",
        canary_traffic_percent=6,
    )
    proposal_id = seeded["proposal_id"]
    baseline = service.list_proposals(limit=20)
    monkeypatch.setattr("host_brain.composition.process_services.get_capability_forge_service", lambda: service)
    monkeypatch.setattr(
        "host_brain.app.obs_routes_stats_capability_agent.read_capability_forge_projection",
        lambda: (
            {
                "timestamp": 1700000000000,
                "proposals": service.list_proposals(limit=200),
                "runtime_stats": service.runtime_match_stats(),
                "runtime_tuning": service.runtime_tuning_stats(limit=200),
                "runtime_quality": service.runtime_quality_stats(limit=200),
            },
            {},
        ),
    )

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
    assert service.list_proposals(limit=20) == baseline

    list_resp = client.get("/obs/capability-gaps/forge-proposals?limit=10")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1

    get_resp = client.get(f"/obs/capability-gaps/forge-proposals/{proposal_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["proposal"]["proposal_id"] == proposal_id

    removed_mutations = (
        (
            "/obs/capability-gaps/forge-proposals",
            {"session_id": "sess-api", "proposer": "api-user"},
        ),
        (
            f"/obs/capability-gaps/forge-proposals/{proposal_id}/approve",
            {"approval_token_ref": seeded["governor_gate"]["approval_token_ref"]},
        ),
        (
            f"/obs/capability-gaps/forge-proposals/{proposal_id}/rollback",
            {"operator": "ops", "reason": "manual rollback"},
        ),
        (
            "/obs/capability-gaps/forge-proposals/runtime-match",
            {
                "request_id": "req-runtime-match",
                "suggested_family": "code",
                "missing_capability_tags": ["coding"],
                "reason_code": "ACTION_INTENT_FALLBACK_CHAT",
            },
        ),
    )
    for path, payload in removed_mutations:
        response = client.post(path, json=payload)
        assert response.status_code in {404, 405}, path
    assert service.list_proposals(limit=20) == baseline

    runtime_stats_resp = client.get("/obs/capability-gaps/forge-runtime/stats")
    assert runtime_stats_resp.status_code == 200
    assert runtime_stats_resp.json()["counters"]["total"] == 0

    tuning_stats_resp = client.get("/obs/capability-gaps/forge-runtime/tuning?limit=5")
    assert tuning_stats_resp.status_code == 200
    tuning_payload = tuning_stats_resp.json()
    assert int(dict(tuning_payload["summary"]).get("total_proposals") or 0) == 1
    assert isinstance(tuning_payload["items"], list)

    quality_stats_resp = client.get("/obs/capability-gaps/forge-runtime/quality?limit=5")
    assert quality_stats_resp.status_code == 200
    quality_payload = quality_stats_resp.json()
    assert "summary" in quality_payload
    assert isinstance(quality_payload["items"], list)
