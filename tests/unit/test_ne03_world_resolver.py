from __future__ import annotations

import pytest

from host_brain.runtime.context_contracts import WorldFactV2
from host_brain.runtime.world_resolver import WorldResolver, WorldResolverError


def _fact(source: str, value: str, *, observed: int = 100, ttl: int | None = None) -> WorldFactV2:
    return WorldFactV2(
        workspace_id="ws-a",
        subject_id="person:1",
        key="mood",
        value=value,
        source_ref=source,
        confidence=0.7,
        observed_at_ms=observed,
        expires_at_ms=observed + (ttl if ttl is not None else 10_000),
        revision=3,
    )


def test_world_resolver_preserves_competing_refs_without_read_renewal() -> None:
    snapshot = WorldResolver().resolve(
        workspace_id="ws-a",
        subject_id="person:1",
        observations=(_fact("life://a", "calm"), _fact("journal://b", "tired")),
        now_ms=150,
    )
    assert {fact.value for fact in snapshot.competing_facts("mood")} == {"calm", "tired"}
    expired = WorldResolver().resolve(
        workspace_id="ws-a",
        subject_id="person:1",
        observations=(_fact("life://a", "calm", ttl=50),),
        now_ms=150,
    )
    assert expired.facts == ()


def test_world_resolver_rejects_cross_workspace_observation() -> None:
    other = WorldFactV2(
        workspace_id="ws-b",
        subject_id="person:1",
        key="mood",
        value="calm",
        source_ref="life://b",
        confidence=0.5,
        observed_at_ms=100,
        expires_at_ms=1_000,
        revision=1,
    )
    with pytest.raises(WorldResolverError):
        WorldResolver().resolve(workspace_id="ws-a", subject_id="person:1", observations=(other,), now_ms=100)
