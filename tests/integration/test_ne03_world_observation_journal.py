from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.integrations.runtime_events import (
    RuntimeEvent,
    RuntimeEventError,
    RuntimeEventStore,
)
from host_brain.persistence.world_observation_journal import (
    SQLiteWorldObservationJournal,
    WorldObservationJournalError,
)
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.contracts.runtime_context import WorldFactExpiryV1, WorldFactV2
from host_brain.runtime.world_source_verifier import (
    RuntimeEventWorldSourceVerifier,
    world_observation_source_identity_digest,
    world_observation_source_payload,
)
from host_brain.runtime.world_resolver import WorldResolver


NOW = 1_900_000_000_000


def _journal(
    tmp_path: Path,
    *,
    clock=lambda: NOW,
) -> tuple[SQLiteWorldObservationJournal, object]:
    db_path = tmp_path / "world.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne03-world-observation",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    runtime_scope_id = "runtime-scope-world-ne03"
    repository = EmbodimentOutboxRepository(
        db_path,
        runtime_scope_id=runtime_scope_id,
    )
    lease = repository.acquire_runtime_lease(
        worker_id="world-owner-ne03",
        lease_ms=30_000,
        now_ms=NOW,
    )
    assert lease is not None

    def provider():
        return {**lease, "runtime_scope_id": runtime_scope_id}

    return (
        SQLiteWorldObservationJournal(
            db_path,
            runtime_scope_id=runtime_scope_id,
            runtime_authority_provider=provider,
            source_event_verifier=lambda _workspace, _subject, _ref, _digest, _observed, _upserts, _expiries: True,
            clock=clock,
        ),
        provider,
    )


def _fact(source: str, value: str, *, ttl: int = 1_000) -> WorldFactV2:
    return WorldFactV2(
        workspace_id="workspace-ne03",
        subject_id="person-ne03",
        key="runtime.state",
        value=value,
        source_ref=source,
        confidence=1.0,
        observed_at_ms=NOW,
        expires_at_ms=NOW + ttl,
    )


def test_sqlite_world_journal_assigns_revision_and_exact_replays(tmp_path: Path) -> None:
    journal, _provider = _journal(tmp_path)
    fact = _fact("observation://host/one", "ready")
    first = journal.append_observation_batch(
        workspace_id="workspace-ne03",
        subject_id="person-ne03",
        source_event_ref="ingress://event/one",
        source_event_digest="1" * 64,
        observed_at_ms=NOW,
        upserts=(fact,),
    )
    replay = journal.append_observation_batch(
        workspace_id="workspace-ne03",
        subject_id="person-ne03",
        source_event_ref="ingress://event/one",
        source_event_digest="1" * 64,
        observed_at_ms=NOW,
        upserts=(fact,),
    )

    assert first == replay
    assert first.revision == 1
    assert len(
        journal.list_observation_batches(
            workspace_id="workspace-ne03",
            subject_id="person-ne03",
            after_revision=0,
            through_revision=1,
            limit=1000,
        )
    ) == 1
    with pytest.raises(WorldObservationJournalError, match="REPLAY_DRIFT"):
        journal.append_observation_batch(
            workspace_id="workspace-ne03",
            subject_id="person-ne03",
            source_event_ref="ingress://event/one",
            source_event_digest="2" * 64,
            observed_at_ms=NOW,
            upserts=(fact,),
        )


def test_sqlite_world_journal_expiry_is_explicit_and_survives_restart(
    tmp_path: Path,
) -> None:
    current = [NOW]
    journal, provider = _journal(tmp_path, clock=lambda: current[0])
    fact = _fact("observation://host/ttl", "temporary", ttl=10)
    journal.append_observation_batch(
        workspace_id="workspace-ne03",
        subject_id="person-ne03",
        source_event_ref="ingress://event/ttl",
        source_event_digest="3" * 64,
        observed_at_ms=NOW,
        upserts=(fact,),
    )
    expiry = WorldFactExpiryV1(
        fact_digest=fact.fact_digest,
        authority_ref=journal.current_expiry_authority_ref(),
        effective_at_ms=NOW + 10,
    )
    current[0] = NOW + 10
    journal.append_observation_batch(
        workspace_id="workspace-ne03",
        subject_id="person-ne03",
        source_event_ref="scheduler://world-ttl/event-one",
        source_event_digest="4" * 64,
        observed_at_ms=NOW + 10,
        expiries=(expiry,),
    )

    restarted = SQLiteWorldObservationJournal(
        journal.db_path,
        runtime_scope_id="runtime-scope-world-ne03",
        runtime_authority_provider=provider,
        source_event_verifier=lambda _workspace, _subject, _ref, _digest, _observed, _upserts, _expiries: True,
        clock=lambda: NOW + 20,
    )
    snapshot = WorldResolver(observation_port=restarted).resolve(
        workspace_id="workspace-ne03",
        subject_id="person-ne03",
        now_ms=NOW + 20,
    )

    assert snapshot.revision == 2
    assert snapshot.facts == ()
    with sqlite3.connect(str(journal.db_path)) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE world_observation_batch SET observed_at_ms = observed_at_ms + 1"
            )


def test_two_sqlite_world_writers_receive_distinct_monotonic_revisions(
    tmp_path: Path,
) -> None:
    first_journal, provider = _journal(tmp_path)
    second_journal = SQLiteWorldObservationJournal(
        first_journal.db_path,
        runtime_scope_id="runtime-scope-world-ne03",
        runtime_authority_provider=provider,
        source_event_verifier=lambda _workspace, _subject, _ref, _digest, _observed, _upserts, _expiries: True,
        clock=lambda: NOW,
    )

    def append(index: int) -> int:
        owner = first_journal if index == 1 else second_journal
        result = owner.append_observation_batch(
            workspace_id="workspace-ne03",
            subject_id="person-ne03",
            source_event_ref=f"ingress://event/concurrent-{index}",
            source_event_digest=f"{index:064x}",
            observed_at_ms=NOW,
            upserts=(
                _fact(f"observation://host/{index}", f"value-{index}"),
            ),
        )
        return result.revision

    with ThreadPoolExecutor(max_workers=2) as pool:
        revisions = set(pool.map(append, (1, 2)))

    assert revisions == {1, 2}


def test_world_writer_rejects_future_time_forged_expiry_and_reactivation(
    tmp_path: Path,
) -> None:
    current = [NOW]
    journal, _provider = _journal(tmp_path, clock=lambda: current[0])
    fact = _fact("observation://host/strict", "temporary", ttl=10)
    journal.append_observation_batch(
        workspace_id="workspace-ne03",
        subject_id="person-ne03",
        source_event_ref="ingress://event/strict-one",
        source_event_digest="a" * 64,
        observed_at_ms=NOW,
        upserts=(fact,),
    )
    with pytest.raises(WorldObservationJournalError, match="TIME_INVALID"):
        journal.append_observation_batch(
            workspace_id="workspace-ne03",
            subject_id="person-ne03",
            source_event_ref="ingress://event/future",
            source_event_digest="b" * 64,
            observed_at_ms=NOW + 1,
            upserts=(_fact("observation://host/future", "future"),),
        )

    current[0] = NOW + 10
    forged = WorldFactExpiryV1(
        fact_digest=fact.fact_digest,
        authority_ref="runtime-authority://sha256/" + "0" * 64,
        effective_at_ms=NOW + 10,
    )
    with pytest.raises(WorldObservationJournalError, match="EXPIRY_AUTHORITY_INVALID"):
        journal.append_observation_batch(
            workspace_id="workspace-ne03",
            subject_id="person-ne03",
            source_event_ref="scheduler://world-ttl/forged",
            source_event_digest="c" * 64,
            observed_at_ms=NOW + 10,
            expiries=(forged,),
        )

    valid = WorldFactExpiryV1(
        fact_digest=fact.fact_digest,
        authority_ref=journal.current_expiry_authority_ref(),
        effective_at_ms=NOW + 10,
    )
    journal.append_observation_batch(
        workspace_id="workspace-ne03",
        subject_id="person-ne03",
        source_event_ref="scheduler://world-ttl/valid",
        source_event_digest="d" * 64,
        observed_at_ms=NOW + 10,
        expiries=(valid,),
    )
    with pytest.raises(
        WorldObservationJournalError,
        match="EXPIRED_FACT_REACTIVATION",
    ):
        journal.append_observation_batch(
            workspace_id="workspace-ne03",
            subject_id="person-ne03",
            source_event_ref="ingress://event/reactivate",
            source_event_digest="e" * 64,
            observed_at_ms=NOW + 10,
            upserts=(fact,),
        )


def test_world_writer_requires_canonical_source_authority(tmp_path: Path) -> None:
    journal, provider = _journal(tmp_path)
    rejected = SQLiteWorldObservationJournal(
        journal.db_path,
        runtime_scope_id="runtime-scope-world-ne03",
        runtime_authority_provider=provider,
        source_event_verifier=lambda _workspace, _subject, _ref, _digest, _observed, _upserts, _expiries: False,
        clock=lambda: NOW,
    )
    with pytest.raises(WorldObservationJournalError, match="SOURCE_AUTHORITY_REJECTED"):
        rejected.append_observation_batch(
            workspace_id="workspace-ne03",
            subject_id="person-ne03",
            source_event_ref="ingress://event/unverified",
            source_event_digest="f" * 64,
            observed_at_ms=NOW,
            upserts=(_fact("observation://host/unverified", "bad"),),
        )


def test_world_source_authority_reopens_exact_runtime_event_from_sqlite(
    tmp_path: Path,
) -> None:
    journal, provider = _journal(tmp_path)
    event_store = RuntimeEventStore(journal.db_path, clock=lambda: NOW)
    pending = _fact("world-observation://pending/source", "ready")
    identity = world_observation_source_identity_digest(
        workspace_id="workspace-ne03",
        subject_id="person-ne03",
        observed_at_ms=NOW,
        upserts=(pending,),
    )
    draft = RuntimeEvent.create(
        event_type="WORLD_OBSERVATION_CAPTURED",
        source_kind="SYSTEM",
        source_ref=f"world-observation://source/{identity}",
        aggregate_type="WORLD_OBSERVATION_SOURCE",
        aggregate_id="world-scope-"
        + hashlib.sha256(
            b"workspace-ne03\x00person-ne03"
        ).hexdigest(),
        aggregate_version=NOW,
        payload_ref="world-observation://transition/" + "0" * 64,
        payload={"workspace_id": "workspace-ne03", "subject_id": "person-ne03"},
        redaction_level="INTERNAL_REDACTED",
        ts_ms=NOW,
    )
    fact = replace(pending, source_ref=str(draft.event_id))
    payload = world_observation_source_payload(
        workspace_id="workspace-ne03",
        subject_id="person-ne03",
        observed_at_ms=NOW,
        upserts=(fact,),
    )
    source = event_store.append_event(
        RuntimeEvent.create(
            event_type=draft.event_type,
            source_kind=draft.source_kind,
            source_ref=draft.source_ref,
            aggregate_type=draft.aggregate_type,
            aggregate_id=draft.aggregate_id,
            aggregate_version=draft.aggregate_version,
            payload_ref="world-observation://transition/"
            + payload["transition_digest"],
            payload=payload,
            dedupe_key=draft.dedupe_key,
            event_id=draft.event_id,
            redaction_level="INTERNAL_REDACTED",
            ts_ms=NOW,
        )
    )

    restarted_store = RuntimeEventStore(journal.db_path, clock=lambda: NOW)
    verifier = RuntimeEventWorldSourceVerifier(restarted_store)
    authoritative = SQLiteWorldObservationJournal(
        journal.db_path,
        runtime_scope_id="runtime-scope-world-ne03",
        runtime_authority_provider=provider,
        source_event_verifier=verifier,
        clock=lambda: NOW,
    )
    digest = str(source["payload_digest"]).removeprefix("sha256:")
    batch = authoritative.append_observation_batch(
        workspace_id="workspace-ne03",
        subject_id="person-ne03",
        source_event_ref=str(source["event_id"]),
        source_event_digest=digest,
        observed_at_ms=NOW,
        upserts=(fact,),
    )

    assert batch.source_event_ref == source["event_id"]
    assert batch.source_event_digest == digest
    assert restarted_store.get_event_by_id(event_id=str(source["event_id"])) == source
    assert restarted_store.get_event_by_id(
        event_id="runtimeevent://sha256/" + "0" * 64
    ) is None
    with pytest.raises(RuntimeEventError, match="exact canonical form"):
        restarted_store.get_event_by_id(event_id=" " + str(source["event_id"]))
    assert verifier(
        "other-workspace",
        "person-ne03",
        str(source["event_id"]),
        digest,
        NOW,
        (fact,),
        (),
    ) is False
    assert verifier(
        "workspace-ne03",
        "other-person",
        str(source["event_id"]),
        digest,
        NOW,
        (fact,),
        (),
    ) is False
    assert verifier(
        "workspace-ne03",
        "person-ne03",
        str(source["event_id"]),
        "0" * 64,
        NOW,
        (fact,),
        (),
    ) is False
