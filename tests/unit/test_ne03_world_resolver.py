from __future__ import annotations

from typing import Sequence

import pytest

from host_brain.runtime.context_contracts import (
    WorldFactExpiryV1,
    WorldFactV2,
    WorldObservationBatchV1,
    WorldObservationHeadV1,
)
from host_brain.runtime.world_resolver import WorldResolver, WorldResolverError


class MemoryObservationPort:
    def __init__(self, batches: Sequence[WorldObservationBatchV1]) -> None:
        self.batches = tuple(batches)

    def read_observation_head(
        self, *, workspace_id: str, subject_id: str
    ) -> WorldObservationHeadV1:
        if not self.batches:
            return WorldObservationHeadV1(
                workspace_id=workspace_id,
                subject_id=subject_id,
                revision=0,
                batch_digest="",
            )
        latest = self.batches[-1]
        return WorldObservationHeadV1(
            workspace_id=workspace_id,
            subject_id=subject_id,
            revision=latest.revision,
            batch_digest=latest.batch_digest,
        )

    def list_observation_batches(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        after_revision: int,
        through_revision: int,
        limit: int,
    ) -> tuple[WorldObservationBatchV1, ...]:
        return tuple(
            item
            for item in self.batches
            if after_revision < item.revision <= through_revision
        )[:limit]


def _fact(
    source: str,
    value: str,
    *,
    observed: int = 100,
    ttl: int = 10_000,
    revision: int = 99,
) -> WorldFactV2:
    return WorldFactV2(
        workspace_id="ws-a",
        subject_id="person:1",
        key="mood",
        value=value,
        source_ref=source,
        confidence=0.7,
        observed_at_ms=observed,
        expires_at_ms=observed + ttl,
        revision=revision,
    )


def _batch(
    revision: int,
    *,
    previous: str,
    upserts: tuple[WorldFactV2, ...] = (),
    expiries: tuple[WorldFactExpiryV1, ...] = (),
    observed_at_ms: int = 100,
) -> WorldObservationBatchV1:
    return WorldObservationBatchV1(
        workspace_id="ws-a",
        subject_id="person:1",
        runtime_scope_id="runtime-scope-a",
        runtime_owner_id="owner-a",
        runtime_lease_id="lease-a",
        fencing_token=1,
        revision=revision,
        previous_batch_digest=previous,
        source_event_ref=f"observation://journal/event-{revision}",
        source_event_digest=f"{revision:064x}",
        observed_at_ms=observed_at_ms,
        upserts=upserts,
        expiries=expiries,
    )


def test_world_revision_comes_from_journal_and_preserves_competing_refs() -> None:
    first = _batch(
        1,
        previous="",
        upserts=(_fact("life://a", "calm"), _fact("journal://b", "tired")),
    )
    resolver = WorldResolver(observation_port=MemoryObservationPort((first,)))

    snapshot = resolver.resolve(
        workspace_id="ws-a",
        subject_id="person:1",
        now_ms=150,
    )

    assert snapshot.revision == 1
    assert {fact.value for fact in snapshot.competing_facts("mood")} == {
        "calm",
        "tired",
    }


def test_world_ttl_requires_explicit_expiry_and_read_never_writes() -> None:
    fact = _fact("life://a", "calm", ttl=50)
    first = _batch(1, previous="", upserts=(fact,))
    port = MemoryObservationPort((first,))
    resolver = WorldResolver(observation_port=port)

    with pytest.raises(WorldResolverError, match="WORLD_EXPIRY_TRANSITION_MISSING"):
        resolver.resolve(workspace_id="ws-a", subject_id="person:1", now_ms=150)
    assert port.batches == (first,)

    expiry = WorldFactExpiryV1(
        fact_digest=fact.fact_digest,
        authority_ref="scheduler://world-ttl/ws-a",
        effective_at_ms=150,
    )
    second = _batch(
        2,
        previous=first.batch_digest,
        expiries=(expiry,),
        observed_at_ms=150,
    )
    expired = WorldResolver(
        observation_port=MemoryObservationPort((first, second))
    ).resolve(workspace_id="ws-a", subject_id="person:1", now_ms=150)

    assert expired.revision == 2
    assert expired.facts == ()


def test_world_resolver_rejects_non_contiguous_or_cross_scope_journal() -> None:
    first = _batch(1, previous="", upserts=(_fact("life://a", "calm"),))
    gap = WorldObservationBatchV1(
        workspace_id="ws-b",
        subject_id="person:1",
        runtime_scope_id="runtime-scope-a",
        runtime_owner_id="owner-a",
        runtime_lease_id="lease-a",
        fencing_token=1,
        revision=2,
        previous_batch_digest=first.batch_digest,
        source_event_ref="observation://journal/event-2",
        source_event_digest="2" * 64,
        observed_at_ms=101,
        upserts=(),
        expiries=(
            WorldFactExpiryV1(
                fact_digest=first.upserts[0].fact_digest,
                authority_ref="scheduler://world-ttl/ws-b",
                effective_at_ms=10_100,
            ),
        ),
    )
    with pytest.raises(WorldResolverError, match="cross-scope"):
        WorldResolver(observation_port=MemoryObservationPort((first, gap))).resolve(
            workspace_id="ws-a",
            subject_id="person:1",
            now_ms=100,
        )


def test_world_resolver_fails_closed_before_captured_head() -> None:
    first = _batch(1, previous="", upserts=(_fact("life://a", "calm"),))

    class TruncatedPort(MemoryObservationPort):
        def read_observation_head(
            self, *, workspace_id: str, subject_id: str
        ) -> WorldObservationHeadV1:
            return WorldObservationHeadV1(
                workspace_id=workspace_id,
                subject_id=subject_id,
                revision=2,
                batch_digest="f" * 64,
            )

    with pytest.raises(WorldResolverError, match="truncated before head"):
        WorldResolver(observation_port=TruncatedPort((first,))).resolve(
            workspace_id="ws-a",
            subject_id="person:1",
            now_ms=150,
        )


def test_world_resolver_rejects_future_dated_expiry_batch() -> None:
    fact = _fact("life://a", "calm", ttl=50)
    first = _batch(1, previous="", upserts=(fact,), observed_at_ms=100)
    expiry = WorldFactExpiryV1(
        fact_digest=fact.fact_digest,
        authority_ref="scheduler://world-ttl/ws-a",
        effective_at_ms=150,
    )
    future = _batch(
        2,
        previous=first.batch_digest,
        expiries=(expiry,),
        observed_at_ms=150,
    )
    with pytest.raises(WorldResolverError, match="future-dated batch"):
        WorldResolver(observation_port=MemoryObservationPort((first, future))).resolve(
            workspace_id="ws-a",
            subject_id="person:1",
            now_ms=140,
        )
