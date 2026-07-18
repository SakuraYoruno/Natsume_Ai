from __future__ import annotations

from typing import Any

import pytest

from host_brain.runtime.context_contracts import (
    SceneResolutionInputV1,
    WorldFactV2,
    WorldSnapshotV2,
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
            "event_type": event.event_type,
            "source_ref": event.source_ref,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id,
            "aggregate_version": event.aggregate_version,
            "payload": dict(event.payload),
            "evidence_refs": list(event.evidence_refs),
            "ts_ms": event.ts_ms,
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
) -> SceneResolutionInputV1:
    return SceneResolutionInputV1(
        workspace_id="workspace-a",
        subject_id="person-a",
        task_kind=task_kind,
        relationship_state="TRUSTED",
        user_state=user_state,
        activity_state=activity_state,
        local_hour=local_hour,
        risk_level=2,
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

    restored = _resolver(event_store=store, clock=lambda: 800)
    with pytest.raises(Exception, match="fingerprint"):
        restored.transition_history(
            workspace_id="workspace-a",
            subject_id="person-a",
        )


@pytest.mark.parametrize(
    ("context", "expected_scene"),
    [
        (_context(task_kind="RELEASE"), "scene.release-maintenance"),
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


def test_scene_resolver_does_not_accept_caller_selected_scene() -> None:
    with pytest.raises(TypeError):
        _resolver().resolve(  # type: ignore[call-arg]
            snapshot=_snapshot(),
            context=_context(),
            scene_id="scene.caller-controlled",
        )
