from __future__ import annotations

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.capability_forge import CapabilityForgeService
from host_brain.app.capability_gap_telemetry import CapabilityGapTelemetry


def _build_seeded_telemetry() -> CapabilityGapTelemetry:
    telemetry = CapabilityGapTelemetry()
    for index in range(4):
        cap_id = f"runtime_cap_forge_{index}"
        telemetry.record_detected(
            request_id=f"req-forge-{index}",
            session_id=f"sess-forge-{index % 2}",
            runtime_capability_id=cap_id,
            reason_code="ACTION_INTENT_FALLBACK_CHAT",
            suggested_family="code",
            missing_capability_tags=["coding"],
            confidence=0.9,
            executor_route="skill",
            executor_target="agent_worker",
        )
        telemetry.record_execution(
            runtime_capability_id=cap_id,
            request_id=f"req-forge-{index}",
            success=index == 3,
            executor_route="skill",
            executor_target="agent_worker",
            latency_ms=120 + index,
            error_code=None if index == 3 else "FAILED",
        )
    return telemetry


def test_capability_forge_create_approve_and_rollback() -> None:
    telemetry = _build_seeded_telemetry()
    service = CapabilityForgeService(telemetry=telemetry)

    proposal = service.create_proposal(
        session_id="sess-001",
        proposer="tester",
        candidate_key=None,
        canary_traffic_percent=10,
    )
    assert proposal["status"] == "PENDING_APPROVAL"
    assert proposal["governor_gate"]["approval_token_ref"].startswith("approval::")
    assert proposal["release_plan"]["canary"]["traffic_percent"] == 10

    approval_token_ref = proposal["governor_gate"]["approval_token_ref"]
    approved = service.approve_and_promote(
        proposal_id=proposal["proposal_id"],
        approval_token_ref=approval_token_ref,
        approver="reviewer",
        target_stage="canary",
    )
    assert approved["status"] == "APPROVED"
    assert approved["promotion"]["stage"] == "CANARY"
    assert approved["release_plan"]["shadow"]["state"] == "ACTIVE"
    assert approved["release_plan"]["canary"]["state"] == "ACTIVE"
    assert approved["release_plan"]["full"]["state"] == "PENDING"

    rolled_back = service.rollback(
        proposal_id=proposal["proposal_id"],
        operator="ops",
        reason="canary alert",
    )
    assert rolled_back["status"] == "ROLLED_BACK"
    assert rolled_back["promotion"]["stage"] == "ROLLED_BACK"
    assert rolled_back["release_plan"]["shadow"]["state"] == "ROLLED_BACK"
    assert rolled_back["runtime_stats"]["auto_rollback_triggered"] is False


def test_capability_forge_approve_requires_valid_token() -> None:
    telemetry = _build_seeded_telemetry()
    service = CapabilityForgeService(telemetry=telemetry)
    proposal = service.create_proposal(session_id="sess-002", proposer="tester")

    with pytest.raises(PermissionError):
        service.approve_and_promote(
            proposal_id=proposal["proposal_id"],
            approval_token_ref="approval::invalid",
            approver="reviewer",
            target_stage="shadow",
        )


def test_capability_forge_replay_and_runtime_override() -> None:
    telemetry = _build_seeded_telemetry()
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    service = CapabilityForgeService(telemetry=telemetry, ledger=ledger)
    proposal = service.create_proposal(session_id="sess-replay", proposer="tester", canary_traffic_percent=100)
    proposal_id = proposal["proposal_id"]
    approval_token_ref = proposal["governor_gate"]["approval_token_ref"]
    service.approve_and_promote(
        proposal_id=proposal_id,
        approval_token_ref=approval_token_ref,
        approver="reviewer",
        target_stage="canary",
    )
    assert len(ledger.capability_forge_proposal_log) >= 2

    # 模拟进程重启：新服务实例从 ledger 回放恢复 proposal 状态
    restored_service = CapabilityForgeService(
        telemetry=telemetry,
        ledger=ledger,
    )
    restored = restored_service.get_proposal(proposal_id)
    assert restored is not None
    assert restored["status"] == "APPROVED"
    assert restored["promotion"]["stage"] == "CANARY"

    decision = restored_service.get_runtime_route_override(
        request_id="req-route-canary",
        suggested_family="code",
        missing_capability_tags=["coding"],
        reason_code="ACTION_INTENT_FALLBACK_CHAT",
    )
    assert decision["apply"] is True
    assert decision["stage"] in {"CANARY", "FULL"}
    assert decision["executor_target"] == "agent_worker"

    stats = restored_service.runtime_match_stats()
    assert stats["counters"]["total"] >= 1
    assert stats["counters"]["applied"] >= 1


def test_capability_forge_auto_rollback_triggered_by_canary_failures() -> None:
    telemetry = _build_seeded_telemetry()
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    service = CapabilityForgeService(telemetry=telemetry, ledger=ledger)
    proposal = service.create_proposal(session_id="sess-auto-rb", proposer="tester", canary_traffic_percent=100)
    proposal_id = proposal["proposal_id"]
    service.approve_and_promote(
        proposal_id=proposal_id,
        approval_token_ref=proposal["governor_gate"]["approval_token_ref"],
        approver="reviewer",
        target_stage="canary",
    )

    # 连续失败触发自动回滚
    for _ in range(3):
        service.record_runtime_result(
            proposal_id=proposal_id,
            success=False,
            latency_ms=250,
            error_code="FAILED",
        )

    updated = service.get_proposal(proposal_id)
    assert updated is not None
    assert updated["status"] == "ROLLED_BACK"
    assert updated["runtime_stats"]["auto_rollback_triggered"] is True

    route_decision = service.get_runtime_route_override(
        request_id="req-after-rollback",
        suggested_family="code",
        missing_capability_tags=["coding"],
        reason_code="ACTION_INTENT_FALLBACK_CHAT",
    )
    assert route_decision["apply"] is False


def test_capability_forge_auto_rollback_triggered_by_window_failure_rate() -> None:
    telemetry = _build_seeded_telemetry()
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    service = CapabilityForgeService(telemetry=telemetry, ledger=ledger)
    proposal = service.create_proposal(session_id="sess-auto-rate", proposer="tester", canary_traffic_percent=100)
    proposal_id = proposal["proposal_id"]
    service.approve_and_promote(
        proposal_id=proposal_id,
        approval_token_ref=proposal["governor_gate"]["approval_token_ref"],
        approver="reviewer",
        target_stage="canary",
    )
    with service._lock:
        stored = service._proposals[proposal_id]
        rollback_cfg = dict(dict(stored.get("release_plan") or {}).get("rollback") or {})
        rollback_cfg["observe_min_samples"] = 4
        rollback_cfg["auto_trigger_failure_rate"] = 0.7
        rollback_cfg["auto_trigger_consecutive_failures"] = 99
        release_plan = dict(stored.get("release_plan") or {})
        release_plan["rollback"] = rollback_cfg
        stored["release_plan"] = release_plan
        service._proposals[proposal_id] = stored

    # 1 success + 3 failures => window failure rate = 0.75, should trigger by rate (not consecutive).
    service.record_runtime_result(proposal_id=proposal_id, success=True, latency_ms=120)
    service.record_runtime_result(proposal_id=proposal_id, success=False, latency_ms=200, error_code="FAILED")
    service.record_runtime_result(proposal_id=proposal_id, success=False, latency_ms=210, error_code="FAILED")
    service.record_runtime_result(proposal_id=proposal_id, success=False, latency_ms=220, error_code="FAILED")

    updated = service.get_proposal(proposal_id)
    assert updated is not None
    assert updated["status"] == "ROLLED_BACK"
    assert updated["runtime_stats"]["auto_rollback_triggered"] is True
    assert updated["runtime_stats"]["window_total"] >= 4
    assert float(updated["runtime_stats"]["window_failure_rate"]) >= 0.7


def test_capability_forge_adaptive_tuning_scale_up_and_down() -> None:
    telemetry = _build_seeded_telemetry()
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    service = CapabilityForgeService(telemetry=telemetry, ledger=ledger)
    proposal = service.create_proposal(session_id="sess-adapt", proposer="tester", canary_traffic_percent=10)
    proposal_id = proposal["proposal_id"]
    service.approve_and_promote(
        proposal_id=proposal_id,
        approval_token_ref=proposal["governor_gate"]["approval_token_ref"],
        approver="reviewer",
        target_stage="canary",
    )
    with service._lock:
        stored = dict(service._proposals[proposal_id])
        release_plan = dict(stored.get("release_plan") or {})
        rollback_cfg = dict(release_plan.get("rollback") or {})
        rollback_cfg["observe_min_samples"] = 50
        rollback_cfg["auto_trigger_consecutive_failures"] = 99
        rollback_cfg["auto_trigger_failure_rate"] = 0.6
        adaptive_cfg = dict(release_plan.get("adaptive") or {})
        adaptive_cfg["min_samples"] = 3
        adaptive_cfg["cooldown_samples"] = 3
        adaptive_cfg["target_failure_rate"] = 0.25
        adaptive_cfg["target_health_score"] = 72.0
        adaptive_cfg["latency_guard_ms"] = 1000
        adaptive_cfg["increase_step_percent"] = 10
        adaptive_cfg["decrease_step_percent"] = 10
        adaptive_cfg["max_canary_percent"] = 50
        adaptive_cfg["min_canary_percent"] = 1
        release_plan["rollback"] = rollback_cfg
        release_plan["adaptive"] = adaptive_cfg
        stored["release_plan"] = release_plan
        service._proposals[proposal_id] = stored

    service.record_runtime_result(proposal_id=proposal_id, success=True, latency_ms=100)
    service.record_runtime_result(proposal_id=proposal_id, success=True, latency_ms=105)
    service.record_runtime_result(proposal_id=proposal_id, success=True, latency_ms=110)

    after_scale_up = service.get_proposal(proposal_id)
    assert after_scale_up is not None
    assert int(after_scale_up["release_plan"]["canary"]["traffic_percent"]) == 20
    assert str(after_scale_up["runtime_stats"]["last_tune_action"]).upper() == "SCALE_UP"
    assert float(after_scale_up["runtime_stats"]["health_score"]) > 70.0
    assert int(after_scale_up["runtime_stats"]["window_p95_latency_ms"]) > 0

    service.record_runtime_result(proposal_id=proposal_id, success=False, latency_ms=210, error_code="FAILED")
    service.record_runtime_result(proposal_id=proposal_id, success=False, latency_ms=220, error_code="FAILED")
    service.record_runtime_result(proposal_id=proposal_id, success=False, latency_ms=230, error_code="FAILED")

    after_scale_down = service.get_proposal(proposal_id)
    assert after_scale_down is not None
    assert after_scale_down["status"] == "APPROVED"
    assert int(after_scale_down["release_plan"]["canary"]["traffic_percent"]) == 10
    assert str(after_scale_down["runtime_stats"]["last_tune_action"]).upper() == "SCALE_DOWN"
    assert int(after_scale_down["runtime_stats"]["adaptive_tune_count"]) >= 2
    assert float(after_scale_down["runtime_stats"]["health_score"]) < float(after_scale_up["runtime_stats"]["health_score"])

    tuning_stats = service.runtime_tuning_stats(limit=10)
    assert int(dict(tuning_stats["summary"]).get("last_action_scale_down") or 0) >= 1
    quality_stats = service.runtime_quality_stats(limit=10)
    assert int(dict(quality_stats["summary"]).get("tracked_proposals") or 0) >= 1
    assert float(dict(quality_stats["summary"]).get("avg_health_score") or 0.0) >= 0.0


def test_capability_forge_auto_plan_creates_proposal(monkeypatch) -> None:
    telemetry = _build_seeded_telemetry()
    service = CapabilityForgeService(telemetry=telemetry)
    monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_PROPOSAL_ENABLED", "true")
    monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_MIN_DETECTED", "3")
    monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_MIN_SESSIONS", "2")
    monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_MIN_FAILURE_RATE", "0.3")
    monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_MIN_FAILED", "2")
    monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_APPROVE_ENABLED", "false")

    decision = service.maybe_auto_plan_for_gap(
        request_id="req-auto-plan-1",
        session_id="sess-auto-plan-1",
        reason_code="ACTION_INTENT_FALLBACK_CHAT",
        suggested_family="code",
        missing_capability_tags=["coding"],
        proposer="runtime-test",
    )
    assert decision["triggered"] is True
    assert decision["reason"] == "PROPOSAL_CREATED"
    assert str(decision.get("proposal_id") or "").startswith("forge_prop_")
    assert decision["status"] == "PENDING_APPROVAL"

    # Dedup should skip when an active proposal for the same candidate already exists.
    decision2 = service.maybe_auto_plan_for_gap(
        request_id="req-auto-plan-2",
        session_id="sess-auto-plan-2",
        reason_code="ACTION_INTENT_FALLBACK_CHAT",
        suggested_family="code",
        missing_capability_tags=["coding"],
        proposer="runtime-test",
    )
    assert decision2["triggered"] is False
    assert decision2["reason"] == "EXISTING_ACTIVE_PROPOSAL"
    assert decision2["proposal_id"] == decision["proposal_id"]


def test_capability_forge_auto_plan_auto_approve(monkeypatch) -> None:
    from host_brain.app.settings import get_capability_forge_settings

    telemetry = _build_seeded_telemetry()
    service = CapabilityForgeService(telemetry=telemetry)
    monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_PROPOSAL_ENABLED", "true")
    monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_APPROVE_ENABLED", "true")
    monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_APPROVE_STAGE", "canary")
    monkeypatch.setenv("HOST_CAPABILITY_FORGE_AUTO_APPROVER", "auto-pilot")
    get_capability_forge_settings.cache_clear()

    decision = service.maybe_auto_plan_for_gap(
        request_id="req-auto-approve-1",
        session_id="sess-auto-approve-1",
        reason_code="ACTION_INTENT_FALLBACK_CHAT",
        suggested_family="code",
        missing_capability_tags=["coding"],
        proposer="runtime-test",
    )
    assert decision["triggered"] is True
    assert decision["auto_approved"] is True
    assert decision["reason"] == "PROPOSAL_CREATED_AND_APPROVED"
    assert decision["status"] == "APPROVED"
    assert decision["stage"] == "CANARY"
    get_capability_forge_settings.cache_clear()

