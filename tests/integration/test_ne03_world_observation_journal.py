from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.world_observation_journal import (
    SQLiteWorldObservationJournal,
    WorldObservationJournalError,
)
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.runtime.context_contracts import WorldFactExpiryV1, WorldFactV2
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
            source_event_verifier=lambda _workspace, _subject, _ref, _digest: True,
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
        source_event_verifier=lambda _workspace, _subject, _ref, _digest: True,
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
        source_event_verifier=lambda _workspace, _subject, _ref, _digest: True,
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
        source_event_verifier=lambda _workspace, _subject, _ref, _digest: False,
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
