"""T-NLE-19: unified degradation entry, recovery and restriction contract."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

import pytest

from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.persistence.review_repository import ReviewStore
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.core.resource_manager import assess_background_task_pressure
from host_brain.integrations.runtime_events import RuntimeEventStore
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
    degradation_policy_config,
    evaluate_degradation_front_door,
    get_degradation_state,
    reconcile_service_observations,
    reconcile_startup_statuses,
    run_isolated_fault_injection_matrix,
)
from host_brain.tribe.event_bus import InMemoryTribeEventBus


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


def _durable_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "degradation.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="nle-degradation-fixture",
    )
    assert result.success, result.as_dict()
    return db_path


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


def test_t_nle_19_only_bound_one_time_review_can_release_recovered_restrictions(
    tmp_path: Path,
) -> None:
    machine = DegradationStateMachine()
    _enter(machine)
    machine.recover_dependency(
        dependency_id="MODEL",
        status="UP",
        evidence_refs=("probe-evidence://model/up/1001",),
        now_ms=1001,
    )

    db_path = tmp_path / "review.sqlite3"
    migration = (
        Path(__file__).resolve().parents[2]
        / "host_brain"
        / "audit"
        / "migrations"
        / "0055_agent_runtime_review_store.sql"
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(migration.read_text(encoding="utf-8"))
    store = ReviewStore(db_path, clock=lambda: 1_002)
    binding = machine.recovery_review_binding("MODEL")
    store.create_review(
        ReviewItem(
            review_id="review-model-recovery",
            review_type=ReviewType.PRE_EXECUTION_APPROVAL,
            subject_ref=binding["subject_ref"],
            subject_version=binding["subject_version"],
            requester="degradation-state",
            reason="evidence-backed capability recovery",
            risk_level=5,
            side_effects=tuple(binding["side_effects"]),
            evidence_refs=tuple(binding["evidence_refs"]),
            status=ReviewStatus.PENDING,
            expires_at=60_000,
            resume_phase=ReviewResumePhase.PRE_EXECUTION,
            checkpoint_id=binding["checkpoint_id"],
        )
    )
    with pytest.raises(DegradationStateError) as review_required:
        machine.acknowledge_recovery(
            dependency_id="MODEL",
            review_store=store,
            review_id="review-model-recovery",
            resume_token_ref="review_resume::invalid",
            now_ms=1002,
        )
    assert machine.snapshot().status == DEGRADATION_RECOVERY_PENDING
    approved = store.approve_review(
        "review-model-recovery",
        actor_id="operator",
        expected_subject_ref=binding["subject_ref"],
        expected_subject_version=binding["subject_version"],
        expected_resume_phase=ReviewResumePhase.PRE_EXECUTION,
        expected_checkpoint_id=binding["checkpoint_id"],
    )
    transition = machine.acknowledge_recovery(
        dependency_id="MODEL",
        review_store=store,
        review_id="review-model-recovery",
        resume_token_ref=approved["resume_token_ref"],
        now_ms=1003,
    )
    state = machine.snapshot().as_dict()
    assert transition.current_state == DEGRADATION_HEALTHY
    assert state["status"] == DEGRADATION_HEALTHY
    assert state["affected_capabilities"] == ()
    with pytest.raises(DegradationStateError):
        machine.acknowledge_recovery(
            dependency_id="MODEL",
            review_store=store,
            review_id="review-model-recovery",
            resume_token_ref=approved["resume_token_ref"],
            now_ms=1004,
        )


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


def test_t_nle_19_durable_replay_preserves_latch_and_review_ack_across_restart(
    tmp_path: Path,
) -> None:
    db_path = _durable_db(tmp_path)
    first = DegradationStateMachine(event_store=RuntimeEventStore(db_path))
    _enter(first, dependency="MEMORY")
    first.recover_dependency(
        dependency_id="MEMORY",
        status="UP",
        evidence_refs=("probe-evidence://memory/up/1001",),
        source_ref="probe-source://memory/1001",
        now_ms=1001,
    )

    restored = DegradationStateMachine(event_store=RuntimeEventStore(db_path))
    assert restored.snapshot().status == DEGRADATION_RECOVERY_PENDING
    assert restored.snapshot().restored_event_count == 2
    assert restored.evaluate_capability(
        capability_id="memory_write", static_risk_level=4
    )["decision"] == "BLOCK"

    review_store = ReviewStore(db_path, clock=lambda: 1002)
    binding = restored.recovery_review_binding("MEMORY")
    review_store.create_review(
        ReviewItem(
            review_id="review-memory-recovery",
            review_type=ReviewType.PRE_EXECUTION_APPROVAL,
            subject_ref=binding["subject_ref"],
            subject_version=binding["subject_version"],
            requester="degradation-state",
            reason="restart-safe recovery",
            risk_level=5,
            side_effects=tuple(binding["side_effects"]),
            evidence_refs=tuple(binding["evidence_refs"]),
            status=ReviewStatus.PENDING,
            expires_at=60_000,
            resume_phase=ReviewResumePhase.PRE_EXECUTION,
            checkpoint_id=binding["checkpoint_id"],
        )
    )
    approved = review_store.approve_review(
        "review-memory-recovery",
        actor_id="operator",
        expected_subject_ref=binding["subject_ref"],
        expected_subject_version=binding["subject_version"],
        expected_resume_phase=ReviewResumePhase.PRE_EXECUTION,
        expected_checkpoint_id=binding["checkpoint_id"],
    )
    restored.acknowledge_recovery(
        dependency_id="MEMORY",
        review_store=review_store,
        review_id="review-memory-recovery",
        resume_token_ref=approved["resume_token_ref"],
        now_ms=1003,
    )
    final = DegradationStateMachine(event_store=RuntimeEventStore(db_path))
    assert final.snapshot().status == DEGRADATION_HEALTHY
    assert final.snapshot().restored_event_count == 3


def test_t_nle_19_durable_append_precedes_event_bus_notification(tmp_path: Path) -> None:
    db_path = _durable_db(tmp_path)
    store = RuntimeEventStore(db_path)

    class CheckingBus(InMemoryTribeEventBus):
        def publish(self, event):  # type: ignore[no-untyped-def]
            assert len(store.list_events_for_aggregates(
                aggregate_refs=(("DEGRADATION_STATE", "runtime-degradation"),)
            )) == 1
            return super().publish(event)

    bus = CheckingBus()
    machine = DegradationStateMachine(event_store=store, event_bus=bus)
    _enter(machine, dependency="TTS")
    assert bus.recent(limit=1)[0].event_type == DEGRADATION_ENTERED
    assert machine.snapshot().durability_status == "READY"


def test_t_nle_19_corrupt_replay_and_unavailable_state_fail_closed(tmp_path: Path) -> None:
    db_path = _durable_db(tmp_path)
    machine = DegradationStateMachine(event_store=RuntimeEventStore(db_path))
    _enter(machine)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DROP TRIGGER trg_runtime_event_journal_no_update")
        conn.execute(
            "UPDATE runtime_event_timeline SET aggregate_version = 3 "
            "WHERE aggregate_type = 'DEGRADATION_STATE'"
        )
        conn.execute(
            """
            CREATE TRIGGER trg_runtime_event_journal_no_update
            BEFORE UPDATE ON runtime_event_timeline
            BEGIN
                SELECT RAISE(ABORT, 'RUNTIME_EVENT_JOURNAL_IMMUTABLE');
            END
            """
        )
    corrupted = DegradationStateMachine(event_store=RuntimeEventStore(db_path))
    assert corrupted.snapshot().durability_status == "FAILED"
    assert corrupted.evaluate_capability(
        capability_id="high_risk_execution", static_risk_level=5
    )["reason_code"] == "DEGRADATION_STATE_UNAVAILABLE"

    unavailable = DegradationStateMachine(durability_required=True)
    assert unavailable.evaluate_capability(
        capability_id="canon_apply", static_risk_level=5
    )["decision"] == "BLOCK"


def test_t_nle_19_invalid_disabled_flags_and_upstream_policy_are_restrict_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = DegradationStateMachine()
    monkeypatch.setenv("HOST_DEGRADATION_STATE_ENABLED", "garbage")
    assert degradation_policy_config()["status"] == "INVALID_FAIL_CLOSED"
    invalid = evaluate_degradation_front_door(
        capability_id="canon_apply",
        static_risk_level=5,
        machine=machine,
    )
    assert invalid["allowed"] is False
    assert invalid["reason_code"] == "DEGRADATION_POLICY_INVALID"

    monkeypatch.setenv("HOST_DEGRADATION_STATE_ENABLED", "true")
    upstream = evaluate_degradation_front_door(
        capability_id="model_inference",
        static_risk_level=1,
        capability_policy_decision={
            "decision": "BLOCK",
            "allowed": False,
            "reason_code": "CAPABILITY_POLICY_DENIED",
            "effective_risk_level": 4,
        },
        machine=machine,
    )
    assert upstream["allowed"] is False
    assert upstream["reason_code"] == "CAPABILITY_POLICY_DENIED"
    assert upstream["effective_risk_level"] == 4
