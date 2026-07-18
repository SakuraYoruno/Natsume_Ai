from __future__ import annotations

import copy
from typing import Any

import pytest

from host_brain.integrations.runtime_events import runtime_event_payload_digest

from host_brain.runtime.runtime_guardian import (
    GuardianEventV1,
    RuntimeGuardian,
    RuntimeGuardianError,
)
from host_brain.runtime.degradation_authority import (
    evaluate_degradation_capability,
)


class MemoryRuntimeEventStore:
    def __init__(
        self,
        *,
        fail_next_append: bool = False,
        commit_then_raise: bool = False,
        fail_logical_event: str = "",
        fail_logical_count: int = 0,
    ) -> None:
        self.records: list[dict[str, Any]] = []
        self.fail_next_append = fail_next_append
        self.commit_then_raise = commit_then_raise
        self.fail_logical_event = fail_logical_event
        self.fail_logical_count = fail_logical_count

    def append_event(self, event: Any) -> dict[str, Any]:
        existing = next(
            (
                record
                for record in self.records
                if record["event_id"] == event.event_id
            ),
            None,
        )
        if existing is not None:
            return existing
        if self.fail_next_append:
            self.fail_next_append = False
            raise RuntimeError("append failed")
        if (
            self.fail_logical_count > 0
            and str(event.payload.get("event_type") or "")
            == self.fail_logical_event
        ):
            self.fail_logical_count -= 1
            raise RuntimeError("logical append failed")
        record = {
            "cursor": len(self.records) + 1,
            "event_id": event.event_id,
            "schema_version": event.schema_version,
            "event_type": event.event_type,
            "source_kind": event.source_kind,
            "source_ref": event.source_ref,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id,
            "aggregate_version": event.aggregate_version,
            "payload_ref": event.payload_ref,
            "payload": dict(event.payload),
            "payload_digest": runtime_event_payload_digest(event.payload),
            "evidence_refs": list(event.evidence_refs),
            "dedupe_key": event.dedupe_key,
            "redaction_level": event.redaction_level,
            "ts_ms": event.ts_ms,
            "created_ts_ms": event.ts_ms,
            "published_ts_ms": event.ts_ms,
        }
        self.records.append(record)
        if self.commit_then_raise:
            self.commit_then_raise = False
            raise TimeoutError("response lost after commit")
        return record

    def list_events_for_aggregates(self, *, aggregate_refs: tuple[tuple[str, str], ...], after_cursor: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        allowed = set(aggregate_refs)
        return [
            record
            for record in self.records
            if record["cursor"] > after_cursor
            and (record["aggregate_type"], record["aggregate_id"]) in allowed
        ][:limit]


def _replace_guardian_record_event(
    record: dict[str, Any],
    event: GuardianEventV1,
) -> None:
    payload = {
        "event_type": event.event_type,
        "status": event.status,
        "details": dict(event.payload),
        "observed_at_ms": event.observed_at_ms,
        "event_digest": event.event_digest,
    }
    record["event_id"] = event.event_id
    record["payload_ref"] = f"guardian://event/{event.event_digest}"
    record["payload"] = payload
    record["payload_digest"] = runtime_event_payload_digest(payload)
    record["evidence_refs"] = sorted(
        set(event.payload.get("evidence_refs") or ())
    )
    record["ts_ms"] = event.observed_at_ms


def _authority(
    *,
    owner: str = "owner-a",
    lease_id: str = "lease-a",
    fencing_token: int = 1,
    issued_at_ms: int = 1,
) -> dict[str, Any]:
    return {
        "runtime_scope_id": "runtime-scope-a",
        "owner_id": owner,
        "lease_id": lease_id,
        "fencing_token": fencing_token,
        "issued_at_ms": issued_at_ms,
        "expires_at_ms": 10_000,
        "released_at_ms": None,
    }


class FakeReviewStore:
    def __init__(self, *, raise_after_consume: bool = False) -> None:
        self.raise_after_consume = raise_after_consume
        self.record: dict[str, Any] | None = None

    def resume_review(self, review_id: str, **kwargs: Any) -> dict[str, Any]:
        if self.record is None:
            self.record = {
                "review": {
                    "review_id": review_id,
                    "status": "CONSUMED",
                    "subject_ref": kwargs["subject_ref"],
                    "subject_version": kwargs["subject_version"],
                    "resume_phase": kwargs["resume_phase"],
                    "checkpoint_id": kwargs["checkpoint_id"],
                    "resume_result_ref": kwargs["resume_result_ref"],
                    "decision_actor": "operator://reviewer/a",
                }
            }
        if self.raise_after_consume:
            raise TimeoutError("review response lost")
        return {
            "review": self.record,
            "checkpoint_id": kwargs["checkpoint_id"],
            "resume_phase": kwargs["resume_phase"],
            "replay_side_effects": False,
        }

    def get_review(self, review_id: str) -> dict[str, Any]:
        assert self.record is not None
        assert self.record["review"]["review_id"] == review_id
        return self.record


class MultiReviewStore:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}

    def resume_review(self, review_id: str, **kwargs: Any) -> dict[str, Any]:
        self.records.setdefault(
            review_id,
            {
                "review": {
                    "review_id": review_id,
                    "status": "CONSUMED",
                    "subject_ref": kwargs["subject_ref"],
                    "subject_version": kwargs["subject_version"],
                    "resume_phase": kwargs["resume_phase"],
                    "checkpoint_id": kwargs["checkpoint_id"],
                    "resume_result_ref": kwargs["resume_result_ref"],
                    "decision_actor": f"operator://reviewer/{review_id}",
                }
            },
        )
        raise TimeoutError("review response lost after consume")

    def get_review(self, review_id: str) -> dict[str, Any]:
        return self.records[review_id]


def test_guardian_records_health_mode_slo_and_acknowledgement() -> None:
    store = MemoryRuntimeEventStore()
    guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=lambda: _authority(),
        clock=lambda: 100,
    )

    degraded = guardian.observe_dependency(
        dependency_id="MODEL",
        status="DOWN",
        evidence_refs=("health://model/100",),
        source_ref="probe://model",
    )
    assert degraded.status == "DEGRADED"
    assert degraded.active_dependencies == ("MODEL",)

    maintenance = guardian.enter_maintenance(reason_ref="maintenance://window/1")
    assert maintenance.status == "MAINTENANCE"
    guardian.exit_maintenance(acknowledgement_ref="operator://ack/1")
    guardian.enter_sleep(reason_ref="sleep://quiet-hours/1")
    assert guardian.snapshot().status == "SLEEP"
    guardian.exit_sleep(acknowledgement_ref="operator://ack/2")
    slo = guardian.record_slo_breach(
        slo_id="host.p95",
        observed_value=2.0,
        threshold=1.0,
        evidence_ref="metric://host/p95/100",
    )
    assert slo.status == "SLO_BREACH"
    breach_ref = slo.slo_breaches[0]
    resolved = guardian.resolve_slo_breach(
        breach_ref=breach_ref,
        evidence_ref="metric://host/p95/recovered/100",
    )
    assert resolved.slo_breaches == ()
    assert resolved.status == "DEGRADED"

    target_event = guardian.event_history()[0]["event_id"]
    acknowledged = guardian.acknowledge_event(
        event_id=target_event,
        acknowledgement_ref="review://guardian/1",
    )
    assert target_event in acknowledged.acknowledged_events
    assert len(guardian.event_history()) == 8

    restored = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=lambda: _authority(),
        clock=lambda: 900,
    )
    assert restored.snapshot() == guardian.snapshot()


def test_guardian_owns_durable_degradation_read_gate_and_service_batch() -> None:
    store = MemoryRuntimeEventStore()
    guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=lambda: _authority(),
        clock=lambda: 100,
    )

    transitions = guardian.observe_service_batch(
        [
            {"service_id": "llm_gateway", "status": "DOWN", "side": "host"},
            {"service_id": "tts", "status": "UP", "side": "edge"},
        ],
        source_ref="service-heartbeat://runtime/100",
        observed_at_ms=100,
    )

    assert len(transitions) == 1
    assert transitions[0]["dependency_id"] == "MODEL"
    assert transitions[0]["guardian_revision"] == 1
    state = guardian.read_degradation_state()
    assert state["status"] == "DEGRADED"
    assert state["durability_required"] is True
    assert state["durability_status"] == "READY"
    assert state["guardian_authority"] == {
        "schema_version": "Natsume.runtime_guardian.degradation_authority.v1",
        "authority": "RUNTIME_GUARDIAN",
        "authority_status": "ACTIVE",
        "aggregate_type": "RUNTIME_GUARDIAN",
        "aggregate_id": guardian._aggregate_id,  # noqa: SLF001
        "aggregate_ref": f"runtime-guardian://sha256/{guardian._aggregate_id}",  # noqa: SLF001
        "runtime_scope_id": "runtime-scope-a",
        "runtime_owner_id": "owner-a",
        "runtime_lease_id": "lease-a",
        "fencing_token": 1,
        "lease_issued_at_ms": 1,
        "lease_expires_at_ms": 10_000,
        "lease_released_at_ms": None,
        "guardian_revision": 1,
        "guardian_status": "DEGRADED",
        "last_event_digest": guardian.event_history()[-1]["event_digest"],
        "durable": True,
    }
    decision = guardian.evaluate_capability(
        capability_id="autonomous_planning",
        static_risk_level=4,
    )
    assert decision["decision"] == "BLOCK"
    assert decision["allowed"] is False
    assert decision["durability_status"] == "READY"
    assert decision["guardian_authority"]["guardian_revision"] == 1


def test_guardian_normalizes_startup_batch_without_global_machine() -> None:
    guardian = RuntimeGuardian(
        event_store=MemoryRuntimeEventStore(),
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=lambda: _authority(),
        clock=lambda: 200,
    )

    transitions = guardian.observe_startup_batch(
        {
            "chat_llm_runtime_warmup": "OK",
            "retrieval_runtime_warmup": "FAILED: chroma unavailable",
        },
        source_ref="startup-probe://runtime/200",
        observed_at_ms=200,
    )

    assert len(transitions) == 1
    assert transitions[0]["dependency_id"] == "MEMORY"
    assert guardian.snapshot().active_dependencies == ("MEMORY",)


def test_guardian_authority_loss_blocks_high_risk_and_marks_low_risk_fallback() -> None:
    now = [100]
    guardian = RuntimeGuardian(
        event_store=MemoryRuntimeEventStore(),
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=lambda: _authority(),
        clock=lambda: now[0],
    )
    now[0] = 10_001

    high_risk = guardian.evaluate_capability(
        capability_id="autonomous_planning",
        static_risk_level=4,
    )
    low_risk = guardian.evaluate_capability(
        capability_id="model_inference",
        static_risk_level=1,
    )

    assert high_risk["decision"] == "BLOCK"
    assert high_risk["allowed"] is False
    assert high_risk["reason_code"] == "DEGRADATION_STATE_UNAVAILABLE"
    assert high_risk["guardian_authority"]["authority_status"] == "UNAVAILABLE"
    assert low_risk["decision"] == "DEGRADE"
    assert low_risk["allowed"] is True
    assert low_risk["authority_granted"] is False
    with pytest.raises(RuntimeGuardianError, match="not active"):
        guardian.read_degradation_state()


def test_guardian_batch_observer_requires_durable_journal() -> None:
    guardian = RuntimeGuardian(
        event_store=None,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=lambda: _authority(),
        clock=lambda: 100,
    )

    with pytest.raises(RuntimeGuardianError, match="durable journal"):
        guardian.observe_service_batch(
            [{"service_id": "llm_gateway", "status": "DOWN"}],
            source_ref="service-heartbeat://runtime/100",
            observed_at_ms=100,
        )


def test_guardian_commit_then_response_loss_exact_reconciles() -> None:
    store = MemoryRuntimeEventStore(commit_then_raise=True)
    guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=lambda: _authority(),
        clock=lambda: 100,
    )

    guardian.observe_dependency(
        dependency_id="MODEL",
        status="DOWN",
        evidence_refs=("health://model/100",),
        source_ref="probe://model",
    )

    snapshot = guardian.snapshot()
    assert snapshot.revision == 1
    assert snapshot.status == "DEGRADED"
    assert snapshot.active_dependencies == ("MODEL",)
    assert guardian.degradation_snapshot().revision == 1
    assert len(store.records) == 1


def test_guardian_append_failure_never_installs_shadow_candidate() -> None:
    store = MemoryRuntimeEventStore(fail_logical_event="", fail_logical_count=0)
    guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=lambda: _authority(),
        clock=lambda: 100,
    )
    observed_states: list[tuple[str, int]] = []

    def fail_before_commit(event: Any) -> dict[str, Any]:
        observed_states.append(
            (guardian._mode, guardian.degradation_snapshot().revision)  # noqa: SLF001
        )
        raise RuntimeError("append failed")

    store.append_event = fail_before_commit  # type: ignore[method-assign]
    with pytest.raises(RuntimeGuardianError, match="durably appended"):
        guardian.enter_maintenance(reason_ref="maintenance://shadow/one")

    assert observed_states == [("ACTIVE", 0), ("ACTIVE", 0)]
    assert guardian.snapshot().mode == "ACTIVE"
    assert guardian.snapshot().revision == 0


def test_runtime_guardian_capability_port_composes_policy_and_degradation() -> None:
    guardian = RuntimeGuardian(
        event_store=MemoryRuntimeEventStore(),
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=lambda: _authority(),
        clock=lambda: 100,
    )
    upstream_block = evaluate_degradation_capability(
        guardian,
        capability_id="general_execution",
        static_risk_level=1,
        capability_policy_decision={
            "decision": "BLOCK",
            "allowed": False,
            "reason_code": "MATURITY_BLOCKED",
        },
    )
    assert upstream_block["allowed"] is False
    assert upstream_block["reason_code"] == "MATURITY_BLOCKED"

    guardian.observe_dependency(
        dependency_id="MODEL",
        status="DOWN",
        evidence_refs=("health://model/down",),
        source_ref="probe://model",
    )
    degraded = evaluate_degradation_capability(
        guardian,
        capability_id="autonomous_planning",
        static_risk_level=4,
    )
    assert degraded["allowed"] is False
    assert degraded["reason_code"] == "DEPENDENCY_DEGRADED_HIGH_RISK_BLOCKED"
    assert degraded["guardian_authority"]["authority"] == "RUNTIME_GUARDIAN"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("source_kind", "TASK"),
        ("source_ref", "runtime-guardian://forged"),
        ("payload_ref", "guardian://event/forged"),
        ("redaction_level", "INTERNAL_REDACTED"),
        ("evidence_refs", []),
    ],
)
def test_guardian_replay_rejects_outer_runtime_event_envelope_tamper(
    field: str,
    replacement: Any,
) -> None:
    store = MemoryRuntimeEventStore()
    guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=lambda: _authority(),
        clock=lambda: 100,
    )
    guardian.observe_dependency(
        dependency_id="MODEL",
        status="DOWN",
        evidence_refs=("health://model/100",),
        source_ref="probe://model",
    )
    store.records[0][field] = replacement

    with pytest.raises(RuntimeGuardianError, match="RuntimeEvent envelope"):
        RuntimeGuardian(
            event_store=store,
            runtime_scope_id="runtime-scope-a",
            runtime_authority_provider=lambda: _authority(),
            clock=lambda: 200,
        )


def test_guardian_modes_are_mutually_exclusive_and_fenced() -> None:
    store = MemoryRuntimeEventStore()
    guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=lambda: _authority(),
        clock=lambda: 100,
    )
    guardian.enter_sleep(reason_ref="sleep://quiet-hours/1")
    maintenance = guardian.enter_maintenance(reason_ref="maintenance://window/1")

    assert maintenance.mode == "MAINTENANCE"
    assert maintenance.sleep_ref is None
    assert maintenance.maintenance_ref == "maintenance://window/1"

    try:
        RuntimeGuardian(
            event_store=store,
            runtime_scope_id="runtime-scope-a",
            runtime_authority_provider=lambda: _authority(
                owner="owner-b",
                lease_id="lease-b",
                fencing_token=1,
            ),
            clock=lambda: 200,
        )
    except Exception as exc:
        assert "fencing token" in str(exc)
    else:  # pragma: no cover - explicit fencing assertion
        raise AssertionError("same fence with another owner must fail")

    takeover = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=lambda: _authority(
            owner="owner-b",
            lease_id="lease-b",
            fencing_token=2,
            issued_at_ms=200,
        ),
        clock=lambda: 200,
    )
    assert takeover.snapshot().mode == "MAINTENANCE"


def test_guardian_requires_complete_exact_runtime_authority_identity() -> None:
    incomplete = _authority()
    incomplete.pop("runtime_scope_id")
    with pytest.raises(RuntimeGuardianError, match="identity is incomplete"):
        RuntimeGuardian(
            event_store=MemoryRuntimeEventStore(),
            runtime_scope_id="runtime-scope-a",
            runtime_authority_provider=lambda: incomplete,
            clock=lambda: 100,
        )


def test_guardian_replay_rejects_higher_fence_reusing_same_lease_id() -> None:
    store = MemoryRuntimeEventStore()
    RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=lambda: _authority(),
        clock=lambda: 100,
    ).enter_maintenance(reason_ref="maintenance://lease/one")

    with pytest.raises(RuntimeGuardianError, match="lease rotation is invalid"):
        RuntimeGuardian(
            event_store=store,
            runtime_scope_id="runtime-scope-a",
            runtime_authority_provider=lambda: _authority(
                lease_id="lease-a",
                fencing_token=2,
                issued_at_ms=100,
            ),
            clock=lambda: 200,
        )


def test_guardian_rejects_backdated_health_mode_and_slo_mutations() -> None:
    guardian = RuntimeGuardian(
        event_store=MemoryRuntimeEventStore(),
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=lambda: _authority(),
        clock=lambda: 200,
    )
    guardian.enter_maintenance(
        reason_ref="maintenance://timeline/one",
        observed_at_ms=200,
    )
    with pytest.raises(RuntimeGuardianError, match="must be monotonic"):
        guardian.observe_dependency(
            dependency_id="MODEL",
            status="UP",
            evidence_refs=("health://model/backdated",),
            source_ref="probe://model",
            observed_at_ms=199,
        )
    with pytest.raises(RuntimeGuardianError, match="must be monotonic"):
        guardian.enter_sleep(
            reason_ref="sleep://timeline/backdated",
            observed_at_ms=199,
        )
    with pytest.raises(RuntimeGuardianError, match="must be monotonic"):
        guardian.record_slo_breach(
            slo_id="host.p95",
            observed_value=2.0,
            threshold=1.0,
            evidence_ref="metric://host/p95/backdated",
            observed_at_ms=199,
        )


def test_guardian_replay_rejects_backdated_digest_valid_event() -> None:
    store = MemoryRuntimeEventStore()
    guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=lambda: _authority(),
        clock=lambda: 200,
    )
    guardian.enter_maintenance(
        reason_ref="maintenance://timeline/first",
        observed_at_ms=200,
    )
    guardian.enter_sleep(
        reason_ref="sleep://timeline/second",
        observed_at_ms=201,
    )
    details = copy.deepcopy(store.records[1]["payload"]["details"])
    forged = GuardianEventV1(
        event_type="GUARDIAN_SLEEP_ENTERED",
        revision=2,
        status="SLEEP",
        payload=details,
        observed_at_ms=199,
    )
    _replace_guardian_record_event(store.records[1], forged)

    with pytest.raises(RuntimeGuardianError, match="owner/fence lineage"):
        RuntimeGuardian(
            event_store=store,
            runtime_scope_id="runtime-scope-a",
            runtime_authority_provider=lambda: _authority(),
            clock=lambda: 300,
        )


def test_guardian_recovery_review_saga_reconciles_consumed_token_after_restart() -> None:
    store = MemoryRuntimeEventStore(
        fail_logical_event="GUARDIAN_RECOVERY_ACK_COMMITTED",
        fail_logical_count=2,
    )
    authority_provider = lambda: _authority()
    guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=authority_provider,
        clock=lambda: 100,
    )
    guardian.observe_dependency(
        dependency_id="MODEL",
        status="DOWN",
        evidence_refs=("health://model/down",),
        source_ref="probe://model",
    )
    guardian.observe_dependency(
        dependency_id="MODEL",
        status="UP",
        evidence_refs=("health://model/up",),
        source_ref="probe://model",
    )
    review_store = FakeReviewStore(raise_after_consume=True)

    try:
        guardian.acknowledge_recovery(
            dependency_id="MODEL",
            review_store=review_store,
            review_id="review-model",
            resume_token_ref="review-resume-token://secret",
        )
    except Exception as exc:
        assert "durably appended" in str(exc)
    else:  # pragma: no cover - failure injection must stop the first commit
        raise AssertionError("committed recovery event should fail twice")
    assert guardian.snapshot().status == "RECOVERY_PENDING"

    restarted = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=authority_provider,
        clock=lambda: 200,
    )
    reconciled = restarted.reconcile_prepared_recoveries(
        review_store=review_store,
        observed_at_ms=200,
    )

    assert reconciled.status == "READY"
    assert reconciled.recovery_pending_dependencies == ()
    logical_events = [item["event_type"] for item in restarted.event_history()]
    assert logical_events[-2:] == [
        "GUARDIAN_RECOVERY_ACK_PREPARED",
        "GUARDIAN_RECOVERY_ACK_COMMITTED",
    ]


def test_guardian_replay_rejects_digest_valid_semantically_illegal_delta() -> None:
    store = MemoryRuntimeEventStore()
    guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=lambda: _authority(),
        clock=lambda: 100,
    )
    guardian.enter_maintenance(reason_ref="maintenance://semantic/one")
    details = dict(store.records[0]["payload"]["details"])
    forged = GuardianEventV1(
        event_type="GUARDIAN_EVENT_ACKNOWLEDGED",
        revision=1,
        status="MAINTENANCE",
        payload={
            **details,
            "target_event_id": "guardian-event://unknown",
        },
        observed_at_ms=100,
    )
    _replace_guardian_record_event(store.records[0], forged)

    try:
        RuntimeGuardian(
            event_store=store,
            runtime_scope_id="runtime-scope-a",
            runtime_authority_provider=lambda: _authority(),
            clock=lambda: 200,
        )
    except Exception as exc:
        assert "delta is invalid" in str(exc)
    else:  # pragma: no cover - semantic tamper must fail closed
        raise AssertionError("digest-valid illegal delta must be rejected")


def test_guardian_replay_rejects_forged_health_and_recovery_reducer_states() -> None:
    store = MemoryRuntimeEventStore()
    guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=lambda: _authority(),
        clock=lambda: 100,
    )
    guardian.observe_dependency(
        dependency_id="MODEL",
        status="DOWN",
        evidence_refs=("health://model/down",),
        source_ref="probe://model",
    )
    guardian.observe_dependency(
        dependency_id="MODEL",
        status="UP",
        evidence_refs=("health://model/up",),
        source_ref="probe://model",
    )
    guardian.acknowledge_recovery(
        dependency_id="MODEL",
        review_store=FakeReviewStore(),
        review_id="review-model",
        resume_token_ref="review-resume-token://model",
    )

    # Replace the legitimate UP transition with a syntactically valid READY
    # reducer state.  Revision-only validation used to accept this direct
    # capability re-enable without the Review transition.
    forged_health_store = MemoryRuntimeEventStore()
    forged_health_store.records = copy.deepcopy(store.records[:2])
    health_details = copy.deepcopy(
        forged_health_store.records[1]["payload"]["details"]
    )
    widened_state = copy.deepcopy(
        store.records[3]["payload"]["details"]["degradation_state"]
    )
    widened_state["revision"] = 2
    widened_state["last_transition"]["revision"] = 2
    health_details["degradation_state"] = widened_state
    forged_health = GuardianEventV1(
        event_type="GUARDIAN_HEALTH_OBSERVED",
        revision=2,
        status="READY",
        payload=health_details,
        observed_at_ms=100,
    )
    _replace_guardian_record_event(
        forged_health_store.records[1],
        forged_health,
    )
    try:
        RuntimeGuardian(
            event_store=forged_health_store,
            runtime_scope_id="runtime-scope-a",
            runtime_authority_provider=lambda: _authority(),
            clock=lambda: 200,
        )
    except RuntimeGuardianError as exc:
        assert "guardian" in str(exc).lower()
    else:  # pragma: no cover - forged widening must fail closed
        raise AssertionError("forged health reducer state must be rejected")

    # Keep the real reducer result but forge the Review binding.  Replay must
    # reconstruct the exact consumed operation, not trust the embedded state.
    forged_commit_store = MemoryRuntimeEventStore()
    forged_commit_store.records = copy.deepcopy(store.records)
    commit_record = forged_commit_store.records[3]
    commit_details = copy.deepcopy(commit_record["payload"]["details"])
    commit_details["binding"]["dependency_id"] = "DATABASE"
    forged_commit = GuardianEventV1(
        event_type="GUARDIAN_RECOVERY_ACK_COMMITTED",
        revision=4,
        status="READY",
        payload=commit_details,
        observed_at_ms=100,
    )
    _replace_guardian_record_event(commit_record, forged_commit)
    try:
        RuntimeGuardian(
            event_store=forged_commit_store,
            runtime_scope_id="runtime-scope-a",
            runtime_authority_provider=lambda: _authority(),
            clock=lambda: 200,
        )
    except RuntimeGuardianError as exc:
        assert "recovery" in str(exc).lower()
    else:  # pragma: no cover - forged Review binding must fail closed
        raise AssertionError("forged recovery reducer state must be rejected")


def test_guardian_reconciles_multiple_consumed_prepared_recoveries_in_order() -> None:
    store = MemoryRuntimeEventStore(
        fail_logical_event="GUARDIAN_RECOVERY_ACK_COMMITTED",
        fail_logical_count=4,
    )
    authority_provider = lambda: _authority()
    guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=authority_provider,
        clock=lambda: 100,
    )
    for dependency in ("MODEL", "MEMORY"):
        guardian.observe_dependency(
            dependency_id=dependency,
            status="DOWN",
            evidence_refs=(f"health://{dependency.lower()}/down",),
            source_ref=f"probe://{dependency.lower()}",
        )
    for dependency in ("MODEL", "MEMORY"):
        guardian.observe_dependency(
            dependency_id=dependency,
            status="UP",
            evidence_refs=(f"health://{dependency.lower()}/up",),
            source_ref=f"probe://{dependency.lower()}",
        )

    review_store = MultiReviewStore()
    for dependency in ("MODEL", "MEMORY"):
        try:
            guardian.acknowledge_recovery(
                dependency_id=dependency,
                review_store=review_store,
                review_id=f"review-{dependency.lower()}",
                resume_token_ref=f"review-resume-token://{dependency.lower()}",
            )
        except RuntimeGuardianError as exc:
            assert "durably appended" in str(exc)
        else:  # pragma: no cover - both injected commit failures are required
            raise AssertionError("recovery commit should remain PREPARED")

    restarted = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=authority_provider,
        clock=lambda: 200,
    )
    reconciled = restarted.reconcile_prepared_recoveries(
        review_store=review_store,
        observed_at_ms=200,
    )
    assert reconciled.status == "READY"
    assert reconciled.recovery_pending_dependencies == ()
    logical_events = [item["event_type"] for item in restarted.event_history()]
    assert logical_events.count("GUARDIAN_RECOVERY_ACK_PREPARED") == 2
    assert logical_events.count("GUARDIAN_RECOVERY_ACK_COMMITTED") == 2

    replayed = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=authority_provider,
        clock=lambda: 300,
    )
    assert replayed.snapshot() == reconciled
