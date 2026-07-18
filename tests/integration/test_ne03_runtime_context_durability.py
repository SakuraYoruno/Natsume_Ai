from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.integrations.runtime_events import RuntimeEventStore
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.runtime.context_contracts import (
    SceneResolutionInputV1,
    WorldFactV2,
    WorldObservationHeadV1,
    WorldSnapshotV2,
)
from host_brain.runtime.intent_authority import IntentPolicyIdentityV1
from host_brain.runtime.production_composition import (
    build_runtime_context_composition,
)
from host_brain.runtime.runtime_guardian import (
    GuardianSnapshotV1,
    RuntimeGuardian,
    RuntimeGuardianError,
)
from host_brain.runtime.scene_resolver import SceneResolver, SceneResolverError
from host_brain.runtime.context_authority_schema import (
    RuntimeContextSchemaError,
    assert_runtime_context_authority_schema,
)


NOW = 1_900_000_000_000


def _store(tmp_path: Path, name: str, *, clock=lambda: NOW) -> RuntimeEventStore:
    db_path = tmp_path / name
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"ne03-{name}",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    return RuntimeEventStore(db_path, clock=clock)


def _snapshot(index: int) -> WorldSnapshotV2:
    fact = WorldFactV2(
        workspace_id="workspace-ne03",
        subject_id="person-ne03",
        key="runtime.sequence",
        value=index,
        source_ref=f"observation://ne03/{index}",
        confidence=1.0,
        observed_at_ms=NOW + index,
        expires_at_ms=NOW + 10_000 + index,
        revision=index + 1,
    )
    return WorldSnapshotV2(
        workspace_id="workspace-ne03",
        subject_id="person-ne03",
        revision=index + 1,
        facts=(fact,),
    )


def _context(*, task_kind: str = "ARCHITECTURE_REFACTOR") -> SceneResolutionInputV1:
    return SceneResolutionInputV1(
        workspace_id="workspace-ne03",
        subject_id="person-ne03",
        task_kind=task_kind,
        relationship_state="TRUSTED",
        user_state="CALM",
        activity_state="WORKING",
        local_hour=14,
        risk_level=2,
    )


def _runtime_authority_provider(
    store: RuntimeEventStore,
    *,
    runtime_scope_id: str,
    owner_id: str,
):
    repository = EmbodimentOutboxRepository(
        store.db_path,
        runtime_scope_id=runtime_scope_id,
    )
    lease = repository.acquire_runtime_lease(
        worker_id=owner_id,
        lease_ms=30_000,
        now_ms=NOW,
    )
    assert lease is not None

    def provider():
        current = repository.validate_runtime_lease(
            worker_id=owner_id,
            lease_id=str(lease["lease_id"]),
            fencing_token=int(lease["fencing_token"]),
            now_ms=NOW,
        )
        return (
            {**current, "runtime_scope_id": runtime_scope_id}
            if current is not None
            else None
        )

    return provider


def _scene_resolver(
    *,
    event_store,
    authority_provider,
    clock=lambda: NOW,
) -> SceneResolver:
    def guardian_snapshot() -> GuardianSnapshotV1:
        authority = authority_provider()
        assert authority is not None
        return GuardianSnapshotV1(
            runtime_scope_id=authority["runtime_scope_id"],
            runtime_owner_id=authority["owner_id"],
            runtime_lease_id=authority["lease_id"],
            fencing_token=authority["fencing_token"],
            revision=0,
            status="READY",
            mode="ACTIVE",
            active_dependencies=(),
            recovery_pending_dependencies=(),
            maintenance_ref=None,
            sleep_ref=None,
            slo_breaches=(),
            acknowledged_events=(),
            last_event_digest=None,
        )

    authority = authority_provider()
    assert authority is not None
    return SceneResolver(
        event_store=event_store,
        runtime_scope_id=authority["runtime_scope_id"],
        runtime_authority_provider=authority_provider,
        guardian_snapshot_provider=guardian_snapshot,
        clock=clock,
    )


def test_scene_sqlite_replays_more_than_one_page(tmp_path: Path) -> None:
    store = _store(tmp_path, "scene.sqlite3")
    authority_provider = _runtime_authority_provider(
        store,
        runtime_scope_id="runtime-scope-scene-ne03",
        owner_id="owner-scene",
    )
    resolver = _scene_resolver(
        event_store=store,
        authority_provider=authority_provider,
        clock=lambda: NOW,
    )
    latest = None
    for index in range(1001):
        latest = resolver.resolve(
            snapshot=_snapshot(index),
            context=_context(),
            observed_at_ms=NOW + index,
        )

    assert latest is not None and latest.revision == 1001
    restored = _scene_resolver(
        event_store=store,
        authority_provider=authority_provider,
        clock=lambda: NOW + 20_000,
    )
    history = restored.transition_history(
        workspace_id="workspace-ne03",
        subject_id="person-ne03",
    )
    assert len(history) == 1001
    assert history[-1]["to_digest"] == latest.decision_digest


def test_runtime_context_schema_gate_rejects_missing_scene_fence(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, "context-schema.sqlite3")
    assert_runtime_context_authority_schema(store)
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute("DROP TRIGGER scene_state_requires_active_host_lease")
    with pytest.raises(RuntimeContextSchemaError, match="object missing"):
        assert_runtime_context_authority_schema(store)


def test_runtime_context_composition_requires_real_schema_and_typed_ports(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, "context-composition.sqlite3")
    authority_provider = _runtime_authority_provider(
        store,
        runtime_scope_id="runtime-scope-context",
        owner_id="owner-context",
    )

    class ObservationPort:
        def read_observation_head(self, *, workspace_id: str, subject_id: str):
            return WorldObservationHeadV1(
                workspace_id=workspace_id,
                subject_id=subject_id,
                revision=0,
                batch_digest="",
            )

        def list_observation_batches(self, **_kwargs):
            return ()

        def append_observation_batch(self, **_kwargs):
            raise AssertionError("composition construction must not write World")

        def current_expiry_authority_ref(self):
            return "runtime-authority://sha256/" + "1" * 64

    class IntentPort:
        def resolve_intent_authority(self, **_kwargs):
            raise AssertionError("composition construction must not fabricate authority")

    composition = build_runtime_context_composition(
        event_store=store,
        observation_port=ObservationPort(),
        runtime_scope_id="runtime-scope-context",
        runtime_authority_provider=authority_provider,
        intent_authority_port=IntentPort(),
        intent_policy_identity=IntentPolicyIdentityV1(
            policy_ref="policy://intent-arbiter/production-v1",
            policy_digest="1" * 64,
        ),
    )

    assert composition.durable is True
    assert composition.scene_resolver is not None
    assert composition.guardian.snapshot().status == "READY"


def test_guardian_sqlite_replays_more_than_one_page(tmp_path: Path) -> None:
    store = _store(tmp_path, "guardian.sqlite3")
    authority_provider = _runtime_authority_provider(
        store,
        runtime_scope_id="runtime-scope-ne03",
        owner_id="owner-a",
    )
    guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-ne03",
        runtime_authority_provider=authority_provider,
        clock=lambda: NOW,
    )
    for index in range(501):
        guardian.enter_maintenance(
            reason_ref=f"maintenance://ne03/{index}",
            observed_at_ms=NOW + index * 2,
        )
        guardian.exit_maintenance(
            acknowledgement_ref=f"operator://ne03/{index}",
            observed_at_ms=NOW + index * 2 + 1,
        )

    assert guardian.snapshot().revision == 1002
    restored = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-ne03",
        runtime_authority_provider=authority_provider,
        clock=lambda: NOW + 20_000,
    )
    assert restored.snapshot() == guardian.snapshot()
    assert len(restored.event_history()) == 1002


def test_two_scene_and_guardian_instances_fail_closed_on_stale_head(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, "competition.sqlite3")
    authority_provider = _runtime_authority_provider(
        store,
        runtime_scope_id="runtime-scope-ne03",
        owner_id="owner-a",
    )
    first_scene = _scene_resolver(
        event_store=store,
        authority_provider=authority_provider,
        clock=lambda: NOW,
    )
    stale_scene = _scene_resolver(
        event_store=store,
        authority_provider=authority_provider,
        clock=lambda: NOW,
    )
    assert stale_scene.transition_history(
        workspace_id="workspace-ne03",
        subject_id="person-ne03",
    ) == ()
    first_scene.resolve(
        snapshot=_snapshot(1),
        context=_context(),
    )
    refreshed = stale_scene.resolve(
        snapshot=_snapshot(2),
        context=_context(task_kind="RELEASE"),
    )
    assert refreshed.revision == 2
    assert refreshed.scene_id == "scene.release-maintenance"

    first_guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-ne03",
        runtime_authority_provider=(
            authority_provider
        ),
        clock=lambda: NOW,
    )
    stale_guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-ne03",
        runtime_authority_provider=authority_provider,
        clock=lambda: NOW,
    )
    first_guardian.enter_maintenance(reason_ref="maintenance://ne03/first")
    with pytest.raises(RuntimeGuardianError, match="won by another event"):
        stale_guardian.enter_sleep(reason_ref="sleep://ne03/stale")
    assert stale_guardian.snapshot().revision == 1
    assert stale_guardian.snapshot().status == "MAINTENANCE"
    with pytest.raises(RuntimeGuardianError, match="not writable"):
        stale_guardian.enter_sleep(reason_ref="sleep://ne03/fenced")


def test_guardian_runtime_lease_takeover_fences_old_reads_and_writes(
    tmp_path: Path,
) -> None:
    current = [NOW]
    store = _store(
        tmp_path,
        "guardian-takeover.sqlite3",
        clock=lambda: current[0],
    )
    repository = EmbodimentOutboxRepository(
        store.db_path,
        runtime_scope_id="runtime-scope-takeover",
    )
    first_lease = repository.acquire_runtime_lease(
        worker_id="owner-a",
        lease_ms=1_000,
        now_ms=current[0],
    )
    assert first_lease is not None

    def provider(lease, owner):
        row = repository.validate_runtime_lease(
            worker_id=owner,
            lease_id=str(lease["lease_id"]),
            fencing_token=int(lease["fencing_token"]),
            now_ms=current[0],
        )
        return (
            {**row, "runtime_scope_id": "runtime-scope-takeover"}
            if row is not None
            else None
        )

    old_guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-takeover",
        runtime_authority_provider=lambda: provider(first_lease, "owner-a"),
        clock=lambda: current[0],
    )
    old_scene = _scene_resolver(
        event_store=store,
        authority_provider=lambda: provider(first_lease, "owner-a"),
        clock=lambda: current[0],
    )
    old_guardian.enter_maintenance(reason_ref="maintenance://takeover/one")
    old_scene.resolve(snapshot=_snapshot(1), context=_context())

    current[0] += 1_001
    second_lease = repository.acquire_runtime_lease(
        worker_id="owner-b",
        lease_ms=1_000,
        now_ms=current[0],
    )
    assert second_lease is not None
    with pytest.raises(RuntimeGuardianError, match="unavailable"):
        old_guardian.snapshot()
    with pytest.raises(RuntimeGuardianError):
        old_guardian.enter_sleep(reason_ref="sleep://takeover/old")
    with pytest.raises(SceneResolverError, match="authority unavailable"):
        old_scene.resolve(
            snapshot=_snapshot(2),
            context=_context(task_kind="RELEASE"),
        )

    new_guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id="runtime-scope-takeover",
        runtime_authority_provider=lambda: provider(second_lease, "owner-b"),
        clock=lambda: current[0],
    )
    assert new_guardian.snapshot().mode == "MAINTENANCE"
    assert new_guardian.enter_sleep(
        reason_ref="sleep://takeover/new"
    ).fencing_token == 2
    new_scene = _scene_resolver(
        event_store=store,
        authority_provider=lambda: provider(second_lease, "owner-b"),
        clock=lambda: current[0],
    )
    promoted = new_scene.resolve(
        snapshot=_snapshot(2),
        context=_context(task_kind="RELEASE"),
    )
    assert promoted.revision == 2
    assert promoted.scene_id == "scene.release-maintenance"


def test_scene_sqlite_aggregate_head_allows_only_one_concurrent_revision(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, "scene-cas.sqlite3")
    authority_provider = _runtime_authority_provider(
        store,
        runtime_scope_id="runtime-scope-scene-cas",
        owner_id="owner-scene-cas",
    )
    barrier = Barrier(2)

    class RacingStore:
        def append_event(self, event):
            barrier.wait(timeout=10)
            return store.append_event(event)

        def list_events_for_aggregates(self, **kwargs):
            return store.list_events_for_aggregates(**kwargs)

    def resolve(task_kind: str):
        resolver = _scene_resolver(
            event_store=RacingStore(),
            authority_provider=authority_provider,
            clock=lambda: NOW,
        )
        try:
            return resolver.resolve(
                snapshot=_snapshot(0),
                context=_context(task_kind=task_kind),
            )
        except SceneResolverError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(resolve, ("RESEARCH", "RELEASE")))

    decisions = [item for item in results if not isinstance(item, Exception)]
    failures = [item for item in results if isinstance(item, Exception)]
    assert len(decisions) == 1
    assert len(failures) == 1
    assert "durably appended" in str(failures[0])
    history = _scene_resolver(
        event_store=store,
        authority_provider=authority_provider,
        clock=lambda: NOW,
    ).transition_history(
        workspace_id="workspace-ne03",
        subject_id="person-ne03",
    )
    assert len(history) == 1
