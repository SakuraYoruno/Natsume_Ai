"""T-NLE-19: unified degradation entry, recovery and restriction contract."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from host_brain.core.resource_manager import assess_background_task_pressure
from host_brain.observability.degradation_state import (
    DEGRADATION_DEGRADED,
    DEGRADATION_ENTERED,
    DEGRADATION_FAULT_MATRIX_SCHEMA_VERSION,
    DEGRADATION_HEALTHY,
    DEGRADATION_RECOVERY_OBSERVED,
    DEGRADATION_RECOVERY_PENDING,
    DEGRADATION_STATE_SCHEMA_VERSION,
    DegradationStateError,
    DegradationStateMachine,
    FAULT_POLICIES,
    build_fault_injection_matrix,
    get_degradation_state,
    reconcile_service_observations,
    reconcile_startup_statuses,
    run_isolated_fault_injection_matrix,
)


def _enter(machine: DegradationStateMachine, dependency: str = "MODEL", now_ms: int = 1000):
    return machine.enter_degradation(
        dependency_id=dependency,
        status="DOWN",
        evidence_refs=(f"probe-evidence://{dependency.lower()}/down/{now_ms}",),
        source_ref=f"probe-source://{dependency.lower()}/{now_ms}",
        now_ms=now_ms,
        detail="fake failure",
        injected=True,
    )


def test_t_nle_19_initial_read_model_is_versioned_and_read_only() -> None:
    state = DegradationStateMachine().snapshot().as_dict()

    assert state["schema_version"] == DEGRADATION_STATE_SCHEMA_VERSION
    assert state["status"] == DEGRADATION_HEALTHY
    assert state["read_only"] is True
    assert state["permission_effect"] == "RESTRICT_ONLY"
    assert state["capability_expansion_requires_review"] is True
    assert state["affected_capabilities"] == ()


def test_t_nle_19_fault_matrix_covers_six_core_dependency_families() -> None:
    matrix = build_fault_injection_matrix()
    dependencies = {row["dependency_id"] for row in matrix["cases"]}

    assert matrix["schema_version"] == DEGRADATION_FAULT_MATRIX_SCHEMA_VERSION
    assert matrix["mode"] == "ISOLATED_FAKE_ONLY"
    assert matrix["external_services_allowed"] is False
    assert matrix["production_writes_allowed"] is False
    assert dependencies == {"MODEL", "MEMORY", "VTS", "TTS", "SSE", "DOMAIN"}
    assert all(row["automatic_capability_expansion"] is False for row in matrix["cases"])


def test_t_nle_19_failure_enters_degraded_state_and_emits_runtime_event() -> None:
    machine = DegradationStateMachine()
    transition = _enter(machine)
    state = machine.snapshot().as_dict()
    event = machine.runtime_events()[0]

    assert transition is not None
    assert transition.event_type == DEGRADATION_ENTERED
    assert transition.current_state == DEGRADATION_DEGRADED
    assert state["status"] == DEGRADATION_DEGRADED
    assert "high_risk_execution" in state["affected_capabilities"]
    assert state["evidence_refs"] == ("probe-evidence://model/down/1000",)
    event.validate()
    assert event.event_type == DEGRADATION_ENTERED
    assert event.payload["external_side_effects"] is False
    assert event.evidence_refs == ("probe-evidence://model/down/1000",)


def test_t_nle_19_recovery_requires_healthy_evidence() -> None:
    machine = DegradationStateMachine()
    _enter(machine)

    with pytest.raises(DegradationStateError) as missing:
        machine.recover_dependency(
            dependency_id="MODEL",
            status="UP",
            evidence_refs=(),
            now_ms=1001,
        )
    assert missing.value.reason_code == "DEGRADATION_EVIDENCE_REQUIRED"

    with pytest.raises(DegradationStateError) as unhealthy:
        machine.recover_dependency(
            dependency_id="MODEL",
            status="UNKNOWN",
            evidence_refs=("probe-evidence://model/unknown/1001",),
            now_ms=1001,
        )
    assert unhealthy.value.reason_code == "DEGRADATION_HEALTH_EVIDENCE_INVALID"
    assert machine.snapshot().status == DEGRADATION_DEGRADED

    with pytest.raises(DegradationStateError) as non_durable:
        machine.recover_dependency(
            dependency_id="MODEL",
            status="UP",
            evidence_refs=("not-a-durable-reference",),
            now_ms=1001,
        )
    assert non_durable.value.reason_code == "DEGRADATION_EVIDENCE_INVALID"


def test_t_nle_19_healthy_probe_recovers_dependency_but_does_not_expand_capability() -> None:
    machine = DegradationStateMachine()
    _enter(machine)
    recovered = machine.recover_dependency(
        dependency_id="MODEL",
        status="UP",
        evidence_refs=("probe-evidence://model/up/1001",),
        source_ref="probe-source://model/1001",
        now_ms=1001,
    )
    state = machine.snapshot().as_dict()
    gate = machine.evaluate_capability(
        capability_id="high_risk_execution",
        static_risk_level=5,
    )

    assert recovered is not None
    assert recovered.event_type == DEGRADATION_RECOVERY_OBSERVED
    assert state["status"] == DEGRADATION_RECOVERY_PENDING
    assert state["active_dependencies"] == ()
    assert state["recovery_evidence_refs"] == ("probe-evidence://model/up/1001",)
    assert gate["decision"] == "BLOCK"
    assert gate["allowed_by_degradation"] is False
    assert gate["authority_granted"] is False
    assert gate["can_expand_scope"] is False
    assert gate["can_reduce_risk"] is False


def test_t_nle_19_only_approved_review_can_release_recovered_restrictions() -> None:
    machine = DegradationStateMachine()
    _enter(machine)
    machine.recover_dependency(
        dependency_id="MODEL",
        status="UP",
        evidence_refs=("probe-evidence://model/up/1001",),
        now_ms=1001,
    )

    with pytest.raises(DegradationStateError) as review_required:
        machine.acknowledge_recovery(
            dependency_id="MODEL",
            review_approved=False,
            evidence_refs=("review-evidence://model/rejected",),
            reviewer_ref="reviewer://monitor/operator",
            now_ms=1002,
        )
    assert review_required.value.reason_code == "DEGRADATION_REVIEW_REQUIRED"

    transition = machine.acknowledge_recovery(
        dependency_id="MODEL",
        review_approved=True,
        evidence_refs=("review-evidence://model/approved",),
        reviewer_ref="reviewer://monitor/operator",
        now_ms=1003,
    )
    state = machine.snapshot().as_dict()
    assert transition.current_state == DEGRADATION_HEALTHY
    assert state["status"] == DEGRADATION_HEALTHY
    assert state["affected_capabilities"] == ()


def test_t_nle_19_service_observation_matrix_enters_and_recovers_without_external_io() -> None:
    machine = DegradationStateMachine()
    failure_rows = [
        {"service_id": policy.service_ids[0], "status": "DOWN", "side": "fake"}
        for policy in FAULT_POLICIES.values()
    ]
    recovery_rows = [
        {"service_id": policy.service_ids[0], "status": "UP", "side": "fake"}
        for policy in FAULT_POLICIES.values()
    ]

    entered = reconcile_service_observations(
        failure_rows,
        machine=machine,
        source_ref="fault-matrix://isolated/failure",
        now_ms=2000,
        injected=True,
    )
    assert len(entered) == 6
    assert machine.snapshot().status == DEGRADATION_DEGRADED

    recovered = reconcile_service_observations(
        recovery_rows,
        machine=machine,
        source_ref="fault-matrix://isolated/recovery",
        now_ms=2001,
        injected=True,
    )
    assert len(recovered) == 6
    assert machine.snapshot().status == DEGRADATION_RECOVERY_PENDING
    assert all(row["event_type"] == DEGRADATION_RECOVERY_OBSERVED for row in recovered)
    for dependency, policy in FAULT_POLICIES.items():
        for capability in policy.high_risk_capabilities:
            assert machine.evaluate_capability(
                capability_id=capability,
                static_risk_level=5,
            )["decision"] == "BLOCK", dependency


def test_t_nle_19_isolated_harness_reports_zero_calls_and_writes() -> None:
    report = run_isolated_fault_injection_matrix(now_ms=3000)

    assert report["status"] == "PASSED"
    assert report["mode"] == "ISOLATED_FAKE_ONLY"
    assert report["external_calls"] == 0
    assert report["production_writes"] == 0
    assert len(report["cases"]) == 6
    assert all(case["high_risk_still_restricted"] for case in report["cases"])

    with pytest.raises(DegradationStateError) as not_isolated:
        run_isolated_fault_injection_matrix(isolated=False)
    assert not_isolated.value.reason_code == "DEGRADATION_FAULT_INJECTION_NOT_ISOLATED"


def test_t_nle_19_resource_gate_cannot_be_disabled_to_bypass_degradation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = DegradationStateMachine()
    _enter(machine, dependency="MEMORY")
    monkeypatch.setenv("HOST_RESOURCE_MANAGER_GATE_ENABLED", "false")

    decision = assess_background_task_pressure(
        task_id="long_term_memory_distillation",
        pressure={"status": "NORMAL", "reasons": []},
        task_type="memory_heavy",
        degradation_state=machine.snapshot().as_dict(),
        capability_id="memory_write",
        static_risk_level=4,
    )

    assert decision["allowed"] is False
    assert decision["decision"] == "defer"
    assert decision["reason_code"] == "DEPENDENCY_DEGRADED_HIGH_RISK_BLOCKED"
    assert decision["degradation_restriction"]["automatic_release"] is False


def test_t_nle_19_startup_adapter_reuses_same_state_machine() -> None:
    machine = DegradationStateMachine()
    entered = reconcile_startup_statuses(
        {
            "chat_llm_runtime_warmup": "DEGRADED: fake model error",
            "retrieval_runtime_warmup": "OK (fake)",
            "unrelated_module": "DEGRADED: ignored",
        },
        machine=machine,
        source_ref="startup-probe://isolated/test",
        now_ms=4000,
    )

    assert len(entered) == 1
    assert entered[0]["dependency_id"] == "MODEL"
    assert machine.snapshot().status == DEGRADATION_DEGRADED


def test_t_nle_19_feature_flag_stops_projection_without_clearing_latched_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = DegradationStateMachine()
    _enter(machine, dependency="DOMAIN")
    monkeypatch.setenv("HOST_DEGRADATION_STATE_ENABLED", "false")

    transitions = reconcile_service_observations(
        [{"service_id": "domain_store", "status": "UP"}],
        machine=machine,
        source_ref="health-report://rollback/test",
        now_ms=5000,
    )
    state = get_degradation_state(machine=machine)

    assert transitions == ()
    assert state["policy_enabled"] is False
    assert state["status"] == DEGRADATION_DEGRADED
    assert machine.evaluate_capability(
        capability_id="pentest_execution",
        static_risk_level=5,
    )["decision"] == "BLOCK"


def test_t_nle_19_config_disabled_signal_is_not_accepted_as_recovery_evidence() -> None:
    machine = DegradationStateMachine()
    _enter(machine, dependency="TTS")

    transitions = reconcile_service_observations(
        [
            {
                "service_id": "edge.rpc.tts",
                "status": "UP",
                "detail": "TTS disabled by config",
            }
        ],
        machine=machine,
        source_ref="health-report://config-disabled/tts",
        now_ms=5500,
    )

    assert transitions == ()
    assert machine.snapshot().status == DEGRADATION_DEGRADED


def test_t_nle_19_duplicate_concurrent_failure_is_one_transition() -> None:
    machine = DegradationStateMachine()

    def enter_once(index: int):
        return machine.enter_degradation(
            dependency_id="SSE",
            status="DOWN",
            evidence_refs=(f"probe-evidence://sse/down/{index}",),
            source_ref=f"probe-source://sse/{index}",
            now_ms=6000 + index,
            injected=True,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        transitions = list(pool.map(enter_once, range(20)))

    assert sum(item is not None for item in transitions) == 1
    assert machine.snapshot().revision == 1
    assert len(machine.runtime_events()) == 1
