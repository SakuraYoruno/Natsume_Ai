from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from host_brain.integrations.runtime_events import runtime_event_payload_digest

from host_brain.contracts.runtime_context import (
    SceneResolutionInputV1,
    WorldFactV2,
    WorldSnapshotV2,
)
from host_brain.runtime.scene_consumer_projection import (
    SCENE_PROJECTION_BLOCK,
    SceneIdentityV2,
    project_scene_decision_v2,
)
from host_brain.runtime.scene_resolver import SceneResolver
from host_brain.runtime.runtime_guardian import GuardianSnapshotV1


class MemoryRuntimeEventStore:
    def __init__(self, *, fail_next_append: bool = False) -> None:
        self.records: list[dict[str, Any]] = []
        self.fail_next_append = fail_next_append

    def append_event(self, event: Any) -> dict[str, Any]:
        if self.fail_next_append:
            self.fail_next_append = False
            raise RuntimeError("append failed")
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
        return record

    def list_events_for_aggregates(
        self,
        *,
        aggregate_refs: tuple[tuple[str, str], ...],
        after_cursor: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        allowed = set(aggregate_refs)
        return [
            record
            for record in self.records
            if record["cursor"] > after_cursor
            and (record["aggregate_type"], record["aggregate_id"]) in allowed
        ][:limit]


class CountingRuntimeEventStore(MemoryRuntimeEventStore):
    def __init__(self) -> None:
        super().__init__()
        self.after_cursors: list[int] = []

    def list_events_for_aggregates(
        self,
        *,
        aggregate_refs: tuple[tuple[str, str], ...],
        after_cursor: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.after_cursors.append(after_cursor)
        return super().list_events_for_aggregates(
            aggregate_refs=aggregate_refs,
            after_cursor=after_cursor,
            limit=limit,
        )


def _snapshot(value: str = "calm") -> WorldSnapshotV2:
    fact = WorldFactV2(
        workspace_id="workspace-a",
        subject_id="person-a",
        key="user.mood",
        value=value,
        source_ref="observation://life",
        confidence=0.8,
        observed_at_ms=100,
        expires_at_ms=1000,
    )
    return WorldSnapshotV2(
        workspace_id="workspace-a",
        subject_id="person-a",
        revision=1,
        facts=(fact,),
    )


def _context(
    *,
    task_kind: str = "ARCHITECTURE_REFACTOR",
    user_state: str = "CALM",
    activity_state: str = "WORKING",
    local_hour: int = 14,
    risk_level: int = 2,
) -> SceneResolutionInputV1:
    return SceneResolutionInputV1(
        workspace_id="workspace-a",
        subject_id="person-a",
        task_kind=task_kind,
        relationship_state="TRUSTED",
        user_state=user_state,
        activity_state=activity_state,
        local_hour=local_hour,
        risk_level=risk_level,
    )


def _authority() -> dict[str, Any]:
    return {
        "runtime_scope_id": "runtime-scope-a",
        "owner_id": "owner-a",
        "lease_id": "lease-a",
        "fencing_token": 1,
        "issued_at_ms": 1,
        "expires_at_ms": 10_000,
        "released_at_ms": None,
    }


def _guardian(status: str = "READY") -> GuardianSnapshotV1:
    mode = status if status in {"MAINTENANCE", "SLEEP"} else "ACTIVE"
    return GuardianSnapshotV1(
        runtime_scope_id="runtime-scope-a",
        runtime_owner_id="owner-a",
        runtime_lease_id="lease-a",
        fencing_token=1,
        revision=0,
        status=status,
        mode=mode,
        active_dependencies=(),
        recovery_pending_dependencies=(),
        maintenance_ref=("maintenance://test" if mode == "MAINTENANCE" else None),
        sleep_ref=("sleep://test" if mode == "SLEEP" else None),
        slo_breaches=(),
        acknowledged_events=(),
        last_event_digest=None,
    )


def _resolver(
    *,
    event_store: Any | None = None,
    clock=lambda: 500,
    guardian_status: str = "READY",
) -> SceneResolver:
    return SceneResolver(
        event_store=event_store,
        runtime_scope_id="runtime-scope-a",
        runtime_authority_provider=_authority,
        guardian_snapshot_provider=lambda: _guardian(guardian_status),
        clock=clock,
    )


def test_scene_reads_do_not_append_duplicate_transition() -> None:
    resolver = _resolver(clock=lambda: 500)
    first = resolver.resolve(snapshot=_snapshot(), context=_context(), observed_at_ms=500)
    second = resolver.resolve(snapshot=_snapshot(), context=_context(), observed_at_ms=900)

    assert first == second
    assert first.scene_id == "scene.architecture-refactor"
    assert first.revision == 1
    assert len(
        resolver.transition_history(
            workspace_id="workspace-a", subject_id="person-a"
        )
    ) == 1


def test_scene_change_advances_revision_and_survives_restart() -> None:
    store = MemoryRuntimeEventStore()
    resolver = _resolver(event_store=store, clock=lambda: 700)
    first = resolver.resolve(snapshot=_snapshot(), context=_context())
    second = resolver.resolve(snapshot=_snapshot("busy"), context=_context())
    assert first.revision == 1
    assert second.revision == 2
    assert len(store.records) == 2

    restored = _resolver(event_store=store, clock=lambda: 999)
    same = restored.resolve(snapshot=_snapshot("busy"), context=_context())
    assert same == second
    assert len(store.records) == 2


def test_scene_exact_read_reuses_verified_projection_and_checks_only_tail() -> None:
    store = CountingRuntimeEventStore()
    resolver = _resolver(event_store=store, clock=lambda: 700)
    emitted = resolver.resolve(snapshot=_snapshot(), context=_context())

    first = resolver.require_decision(
        workspace_id=emitted.workspace_id,
        subject_id=emitted.subject_id,
        scene_id=emitted.scene_id,
        revision=emitted.revision,
        decision_digest=emitted.decision_digest,
    )
    second = resolver.require_decision(
        workspace_id=emitted.workspace_id,
        subject_id=emitted.subject_id,
        scene_id=emitted.scene_id,
        revision=emitted.revision,
        decision_digest=emitted.decision_digest,
    )

    assert first == second == emitted
    assert store.after_cursors == [0, 1, 1]


def test_scene_append_failure_does_not_advance_memory_state() -> None:
    store = MemoryRuntimeEventStore(fail_next_append=True)
    resolver = _resolver(event_store=store, clock=lambda: 700)

    with pytest.raises(Exception, match="durably appended"):
        resolver.resolve(snapshot=_snapshot(), context=_context())

    assert resolver.transition_history(
        workspace_id="workspace-a",
        subject_id="person-a",
    ) == ()


def test_scene_replay_recomputes_fingerprint_and_digest_chain() -> None:
    store = MemoryRuntimeEventStore()
    _resolver(event_store=store, clock=lambda: 700).resolve(
        snapshot=_snapshot(),
        context=_context(),
    )
    store.records[0]["payload"]["fingerprint"] = "f" * 64
    store.records[0]["payload_digest"] = runtime_event_payload_digest(
        store.records[0]["payload"]
    )

    restored = _resolver(event_store=store, clock=lambda: 800)
    with pytest.raises(Exception, match="fingerprint"):
        restored.transition_history(
            workspace_id="workspace-a",
            subject_id="person-a",
        )


def test_scene_incremental_replay_installs_tail_atomically_and_recovers_after_repair() -> None:
    store = MemoryRuntimeEventStore()
    writer = _resolver(event_store=store, clock=lambda: 700)
    writer.resolve(snapshot=_snapshot("first"), context=_context())

    reader = _resolver(event_store=store, clock=lambda: 800)
    assert len(
        reader.transition_history(
            workspace_id="workspace-a",
            subject_id="person-a",
        )
    ) == 1

    writer.resolve(snapshot=_snapshot("second"), context=_context())
    writer.resolve(snapshot=_snapshot("third"), context=_context())
    durable_tail = deepcopy(store.records[-1])
    store.records[-1]["payload"]["fingerprint"] = "f" * 64
    store.records[-1]["payload_digest"] = runtime_event_payload_digest(
        store.records[-1]["payload"]
    )

    with pytest.raises(Exception, match="fingerprint"):
        reader.transition_history(
            workspace_id="workspace-a",
            subject_id="person-a",
        )

    # Repairing the durable tail must be sufficient.  The failed replay did
    # not partially install revision 2 while retaining cursor 1.
    store.records[-1] = durable_tail
    repaired = reader.transition_history(
        workspace_id="workspace-a",
        subject_id="person-a",
    )
    assert len(repaired) == 3
    assert [item["to_revision"] for item in repaired] == [1, 2, 3]


@pytest.mark.parametrize(
    ("context", "expected_scene"),
    [
        (_context(task_kind="RELEASE"), "scene.release-maintenance"),
        (_context(activity_state="MAINTENANCE"), "scene.release-maintenance"),
        (_context(activity_state="SLEEP"), "scene.sleep-low-interruption"),
        (_context(activity_state="GAMING"), "scene.game-low-interruption"),
        (_context(local_hour=1), "scene.late-night"),
        (_context(user_state="FRUSTRATED"), "scene.user-frustrated"),
        (_context(task_kind="ARCHITECTURE_REFACTOR"), "scene.architecture-refactor"),
        (_context(task_kind="DEBUGGING"), "scene.debugging"),
        (_context(task_kind="RESEARCH"), "scene.research"),
        (_context(task_kind="CHAT"), "scene.casual-chat"),
    ],
)
def test_scene_resolver_derives_eight_required_scenarios(
    context: SceneResolutionInputV1,
    expected_scene: str,
) -> None:
    decision = _resolver(clock=lambda: 500).resolve(
        snapshot=_snapshot(),
        context=context,
    )

    assert decision.scene_id == expected_scene


def test_guardian_degradation_restricts_but_does_not_review_low_risk_chat() -> None:
    decision = _resolver(
        clock=lambda: 500,
        guardian_status="RECOVERY_PENDING",
    ).resolve(
        snapshot=_snapshot(),
        context=_context(task_kind="CHAT", risk_level=2),
    )

    constraints = {item.code: item.disposition for item in decision.constraints}
    assert constraints["GUARDIAN_DEGRADED_RESTRICTION"] == "RESTRICT"
    assert "GUARDIAN_DEGRADED_REVIEW" not in constraints


def test_guardian_degradation_does_not_review_low_risk_read_only_delegate() -> None:
    decision = _resolver(
        clock=lambda: 500,
        guardian_status="DEGRADED",
    ).resolve(
        snapshot=_snapshot(),
        context=_context(task_kind="DELEGATE", risk_level=2),
    )

    constraints = {item.code: item.disposition for item in decision.constraints}
    assert constraints["GUARDIAN_DEGRADED_RESTRICTION"] == "RESTRICT"
    assert "GUARDIAN_DEGRADED_REVIEW" not in constraints


def test_guardian_degradation_does_not_block_low_risk_personal_skill() -> None:
    decision = _resolver(
        clock=lambda: 500,
        guardian_status="SLO_BREACH",
    ).resolve(
        snapshot=_snapshot(),
        context=_context(task_kind="SKILL", risk_level=1),
    )

    constraints = {item.code: item.disposition for item in decision.constraints}
    assert constraints["GUARDIAN_DEGRADED_RESTRICTION"] == "RESTRICT"
    assert "GUARDIAN_DEGRADED_REVIEW" not in constraints


def test_guardian_degradation_does_not_review_low_risk_research_plan() -> None:
    decision = _resolver(
        clock=lambda: 500,
        guardian_status="DEGRADED",
    ).resolve(
        snapshot=_snapshot(),
        context=_context(task_kind="RESEARCH", risk_level=2),
    )

    constraints = {item.code: item.disposition for item in decision.constraints}
    assert constraints["SOURCE_EVIDENCE_REQUIRED"] == "RESTRICT"
    assert constraints["GUARDIAN_DEGRADED_RESTRICTION"] == "RESTRICT"
    assert "GUARDIAN_DEGRADED_REVIEW" not in constraints


def test_guardian_degradation_still_requires_review_for_higher_risk_chat() -> None:
    decision = _resolver(
        clock=lambda: 500,
        guardian_status="DEGRADED",
    ).resolve(
        snapshot=_snapshot(),
        context=_context(task_kind="CHAT", risk_level=3),
    )

    constraints = {item.code: item.disposition for item in decision.constraints}
    assert constraints["GUARDIAN_DEGRADED_REVIEW"] == "REVIEW"


def test_scene_sleep_activity_restricts_intent_and_blocks_proactive_projection() -> None:
    decision = _resolver(clock=lambda: 500).resolve(
        snapshot=_snapshot(),
        context=_context(activity_state="SLEEP"),
    )

    constraints = {item.code: item.disposition for item in decision.constraints}
    assert constraints["LOW_INTERRUPTION"] == "RESTRICT"
    assert constraints["PROACTIVE_ACTION_BLOCKED"] == "RESTRICT"
    projection = project_scene_decision_v2(
        decision,
        expected_identity=SceneIdentityV2.from_decision(decision),
    )
    assert projection.proactive_policy == SCENE_PROJECTION_BLOCK


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("source_kind", "TASK"),
        ("source_ref", "runtime://scene-resolver/forged"),
        ("payload_ref", "scene://transition/forged"),
        ("redaction_level", "INTERNAL_REDACTED"),
        ("evidence_refs", []),
    ],
)
def test_scene_replay_rejects_outer_runtime_event_envelope_tamper(
    field: str,
    replacement: Any,
) -> None:
    store = MemoryRuntimeEventStore()
    _resolver(event_store=store, clock=lambda: 700).resolve(
        snapshot=_snapshot(),
        context=_context(),
    )
    store.records[0][field] = replacement

    restored = _resolver(event_store=store, clock=lambda: 800)
    with pytest.raises(Exception, match="RuntimeEvent envelope"):
        restored.transition_history(
            workspace_id="workspace-a",
            subject_id="person-a",
        )


def test_scene_resolver_does_not_accept_caller_selected_scene() -> None:
    with pytest.raises(TypeError):
        _resolver().resolve(  # type: ignore[call-arg]
            snapshot=_snapshot(),
            context=_context(),
            scene_id="scene.caller-controlled",
        )
