from __future__ import annotations

from host_brain.app.capability_forge import CapabilityForgeService
from host_brain.app.capability_gap_telemetry import CapabilityGapTelemetry
from host_brain.audit.sqlite_ledger import SQLiteLedger


def _build_seeded_telemetry() -> CapabilityGapTelemetry:
    telemetry = CapabilityGapTelemetry()
    for index in range(4):
        capability_id = f"runtime_cap_forge_{index}"
        telemetry.record_detected(
            request_id=f"req-forge-{index}",
            session_id=f"sess-forge-{index % 2}",
            runtime_capability_id=capability_id,
            reason_code="ACTION_INTENT_FALLBACK_CHAT",
            suggested_family="code",
            missing_capability_tags=["coding"],
            confidence=0.9,
            executor_route="skill",
            executor_target="agent_worker",
        )
        telemetry.record_execution(
            runtime_capability_id=capability_id,
            request_id=f"req-forge-{index}",
            success=index == 3,
            executor_route="skill",
            executor_target="agent_worker",
            latency_ms=120 + index,
            error_code=None if index == 3 else "FAILED",
        )
    return telemetry


def test_capability_forge_is_proposal_only_and_replays_ledger() -> None:
    telemetry = _build_seeded_telemetry()
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    service = CapabilityForgeService(telemetry=telemetry, ledger=ledger)

    proposal = service.create_proposal(
        session_id="sess-proposal-only",
        proposer="tester",
        canary_traffic_percent=10,
    )
    assert proposal["status"] == "PENDING_APPROVAL"
    assert proposal["governor_gate"] == {
        "required": True,
        "authority": "VERSION_ACTIVATION_SERVICE",
        "review_required": True,
        "production_influence_enabled": False,
    }
    assert service.get_proposal(proposal["proposal_id"]) == proposal

    restored = CapabilityForgeService(
        telemetry=telemetry,
        ledger=ledger,
    ).get_proposal(proposal["proposal_id"])
    assert restored == proposal

    for retired in (
        "approve_and_promote",
        "rollback",
        "record_runtime_result",
        "get_runtime_route_override",
    ):
        assert not hasattr(service, retired)


def test_capability_forge_runtime_projection_is_permanently_retired() -> None:
    service = CapabilityForgeService(telemetry=_build_seeded_telemetry())
    stats = service.runtime_match_stats()
    assert stats["authority"] == "PROPOSAL_ONLY"
    assert stats["apply_rate"] == 0.0
    assert stats["counters"]["total"] == 0
    assert stats["counters"]["applied"] == 0
    assert stats["counters"]["retired"] == 1


def test_capability_forge_preview_does_not_mutate_proposals() -> None:
    service = CapabilityForgeService(telemetry=_build_seeded_telemetry())
    preview = service.resolve_candidate()
    assert preview is not None
    assert service.list_proposals() == []
