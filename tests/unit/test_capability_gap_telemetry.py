from __future__ import annotations

from host_brain.app.capability_gap_telemetry import CapabilityGapTelemetry


def test_capability_gap_telemetry_counts_detected_and_execution() -> None:
    telemetry = CapabilityGapTelemetry()
    telemetry.record_detected(
        request_id="req-1",
        session_id="sess-1",
        runtime_capability_id="runtime_cap_1",
        reason_code="ACTION_INTENT_FALLBACK_CHAT",
        suggested_family="code",
        missing_capability_tags=["coding"],
        confidence=0.91,
        executor_route="delegate",
        executor_target="code_agent",
    )
    telemetry.record_execution(
        runtime_capability_id="runtime_cap_1",
        request_id="req-1",
        success=True,
        executor_route="delegate",
        executor_target="code_agent",
        latency_ms=123,
        error_code=None,
    )

    snapshot = telemetry.snapshot(limit_recent=10)
    assert snapshot["detected_total"] == 1
    assert snapshot["executed_total"] == 1
    assert snapshot["success_total"] == 1
    assert snapshot["failed_total"] == 0
    assert snapshot["success_rate"] == 1.0
    assert snapshot["active_runtime_capabilities"] == 1
    assert snapshot["by_reason"][0]["key"] == "ACTION_INTENT_FALLBACK_CHAT"
    assert snapshot["by_family"][0]["key"] == "code"
    assert snapshot["by_executor"][0]["key"] == "code_agent"


def test_capability_gap_telemetry_recent_limit_and_reset() -> None:
    telemetry = CapabilityGapTelemetry(max_recent_events=10)
    for index in range(6):
        telemetry.record_detected(
            request_id=f"req-{index}",
            session_id=f"sess-{index}",
            runtime_capability_id=f"runtime_cap_{index}",
            reason_code="ACTION_INTENT_FALLBACK_CHAT",
            suggested_family="reasoning",
            missing_capability_tags=["reasoning"],
            confidence=0.6,
            executor_route="delegate",
            executor_target="intent_agent",
        )

    snapshot = telemetry.snapshot(limit_recent=3)
    assert len(snapshot["recent_events"]) == 3
    assert snapshot["recent_events"][-1]["runtime_capability_id"] == "runtime_cap_5"

    telemetry.reset_for_tests()
    reset_snapshot = telemetry.snapshot(limit_recent=5)
    assert reset_snapshot["detected_total"] == 0
    assert reset_snapshot["executed_total"] == 0
    assert reset_snapshot["recent_events"] == []


def test_capability_gap_telemetry_builds_forge_candidates() -> None:
    telemetry = CapabilityGapTelemetry()
    for index in range(4):
        cap_id = f"runtime_cap_codegen_{index}"
        telemetry.record_detected(
            request_id=f"req-{index}",
            session_id=f"sess-{index % 2}",
            runtime_capability_id=cap_id,
            reason_code="ACTION_INTENT_FALLBACK_CHAT",
            suggested_family="code",
            missing_capability_tags=["coding"],
            confidence=0.92,
            executor_route="delegate",
            executor_target="code_agent",
        )
        telemetry.record_execution(
            runtime_capability_id=cap_id,
            request_id=f"req-{index}",
            success=index == 3,
            executor_route="delegate",
            executor_target="code_agent",
            latency_ms=100 + index,
            error_code=None if index == 3 else "FAILED",
        )

    snapshot = telemetry.snapshot(
        limit_recent=20,
        limit_hotspots=5,
        limit_candidates=5,
        min_candidate_detected=3,
        min_candidate_sessions=2,
        min_candidate_failure_rate=0.4,
        min_candidate_failed=2,
    )

    hotspots = list(snapshot.get("hotspots") or [])
    assert hotspots
    assert hotspots[0]["suggested_family"] == "code"
    assert hotspots[0]["detected_count"] == 4

    candidates = list(snapshot.get("forge_candidates") or [])
    assert candidates
    top_candidate = candidates[0]
    assert top_candidate["stage"] == "READY_FOR_FORGE"
    assert top_candidate["detected_count"] == 4
    assert top_candidate["failed_count"] == 3
    assert top_candidate["impact_sessions"] == 2
    assert top_candidate["recommended_action"] == "forge_with_reliability_tests"
    assert snapshot["candidate_thresholds"]["min_detected_count"] == 3
    assert snapshot["candidate_thresholds"]["min_unique_sessions"] == 2
