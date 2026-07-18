"""Workspace-scoped, read-only World observation-journal resolver.

World revision belongs to the append-only observation journal, never to an
individual fact.  TTL removal is represented by an explicit expiry
transition.  A read never renews TTL and never writes Life or Memory.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from .context_contracts import (
    WorldFactV2,
    WorldObservationBatchV1,
    WorldObservationHeadV1,
    WorldSnapshotV2,
)


class WorldResolverError(ValueError):
    """Observation evidence cannot produce a trustworthy World snapshot."""


class WorldObservationPort(Protocol):
    """Read-only, cursor-paged projection of a workspace observation journal."""

    def read_observation_head(
        self,
        *,
        workspace_id: str,
        subject_id: str,
    ) -> WorldObservationHeadV1: ...

    def list_observation_batches(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        after_revision: int,
        through_revision: int,
        limit: int,
    ) -> Sequence[WorldObservationBatchV1]: ...


class WorldResolver:
    """Rebuild World from a monotonic, digest-chained observation journal."""

    _PAGE_SIZE = 1000

    def __init__(self, *, observation_port: WorldObservationPort) -> None:
        if observation_port is None or not all(
            callable(getattr(observation_port, name, None))
            for name in ("read_observation_head", "list_observation_batches")
        ):
            raise WorldResolverError("workspace observation port is required")
        self._observation_port = observation_port

    def resolve(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        now_ms: int,
    ) -> WorldSnapshotV2:
        return self._replay(
            workspace_id=workspace_id,
            subject_id=subject_id,
            now_ms=now_ms,
            allow_overdue=False,
            through_revision=None,
        )

    def require_snapshot(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        revision: int,
        snapshot_digest: str,
        observed_at_ms: int,
    ) -> WorldSnapshotV2:
        """Reopen one historical snapshot and reverify every source event."""

        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise WorldResolverError("snapshot revision must be non-negative")
        expected_digest = str(snapshot_digest or "").strip().lower()
        snapshot = self._replay(
            workspace_id=workspace_id,
            subject_id=subject_id,
            now_ms=observed_at_ms,
            allow_overdue=False,
            through_revision=revision,
        )
        if snapshot.snapshot_digest != expected_digest:
            raise WorldResolverError(
                "World snapshot is absent or digest-drifted"
            )
        return snapshot

    def due_expiry_facts(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        now_ms: int,
    ) -> tuple[WorldFactV2, ...]:
        """Return overdue active facts without mutating or renewing their TTL.

        Only the World observation application may turn these candidates into
        explicit, authority-bound expiry transitions.  Normal resolve remains
        fail-closed while even one overdue fact is still active.
        """

        snapshot = self._replay(
            workspace_id=workspace_id,
            subject_id=subject_id,
            now_ms=now_ms,
            allow_overdue=True,
            through_revision=None,
        )
        return tuple(
            item for item in snapshot.facts if now_ms >= item.expires_at_ms
        )

    def _replay(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        now_ms: int,
        allow_overdue: bool,
        through_revision: int | None,
    ) -> WorldSnapshotV2:
        workspace = str(workspace_id or "").strip()
        if not workspace:
            raise WorldResolverError("workspace_id is required")
        subject = str(subject_id or "").strip()
        if not subject:
            raise WorldResolverError("subject_id is required")
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise WorldResolverError("now_ms must be a non-negative integer")

        active: dict[str, WorldFactV2] = {}
        expired: set[str] = set()
        source_events: dict[str, str] = {}
        expected_revision = 1
        previous_digest = ""
        previous_observed_at = 0
        previous_owner: str | None = None
        previous_lease: str | None = None
        previous_fence = 0
        cursor = 0
        try:
            head = self._observation_port.read_observation_head(
                workspace_id=workspace,
                subject_id=subject,
            )
        except Exception as exc:
            raise WorldResolverError("observation journal head is unavailable") from exc
        if (
            not isinstance(head, WorldObservationHeadV1)
            or head.workspace_id != workspace
            or head.subject_id != subject
        ):
            raise WorldResolverError("observation journal head scope is invalid")
        terminal_revision = head.revision
        if through_revision is not None:
            if through_revision > head.revision:
                raise WorldResolverError(
                    "observation journal does not contain requested revision"
                )
            terminal_revision = through_revision
        while cursor < terminal_revision:
            try:
                page = tuple(
                    self._observation_port.list_observation_batches(
                        workspace_id=workspace,
                        subject_id=subject,
                        after_revision=cursor,
                        through_revision=terminal_revision,
                        limit=min(self._PAGE_SIZE, terminal_revision - cursor),
                    )
                )
            except Exception as exc:
                raise WorldResolverError("observation journal is unavailable") from exc
            if len(page) > self._PAGE_SIZE:
                raise WorldResolverError("observation journal page exceeds limit")
            if not page:
                raise WorldResolverError("observation journal is truncated before head")
            for batch in page:
                if not isinstance(batch, WorldObservationBatchV1):
                    raise WorldResolverError(
                        "observation port returned an untyped batch"
                    )
                if (
                    batch.workspace_id != workspace
                    or batch.subject_id != subject
                ):
                    raise WorldResolverError("cross-scope observation batch rejected")
                if batch.revision != expected_revision:
                    raise WorldResolverError(
                        "observation journal revision is not contiguous"
                    )
                if batch.previous_batch_digest != previous_digest:
                    raise WorldResolverError(
                        "observation journal digest chain is invalid"
                    )
                if batch.observed_at_ms < previous_observed_at:
                    raise WorldResolverError(
                        "observation journal time is not monotonic"
                    )
                if batch.observed_at_ms > now_ms:
                    raise WorldResolverError(
                        "observation journal contains a future-dated batch"
                    )
                if batch.fencing_token < previous_fence or (
                    batch.fencing_token == previous_fence
                    and previous_owner is not None
                    and (
                        batch.runtime_owner_id != previous_owner
                        or batch.runtime_lease_id != previous_lease
                    )
                ) or (
                    batch.fencing_token > previous_fence
                    and batch.runtime_lease_id == previous_lease
                ):
                    raise WorldResolverError(
                        "observation runtime authority lineage is invalid"
                    )
                prior_source_digest = source_events.get(batch.source_event_ref)
                if prior_source_digest is not None:
                    if prior_source_digest != batch.source_event_digest:
                        raise WorldResolverError(
                            "observation source event replay drift"
                        )
                    raise WorldResolverError(
                        "observation source event was appended more than once"
                    )
                source_events[batch.source_event_ref] = batch.source_event_digest
                if not batch.upserts and not batch.expiries:
                    raise WorldResolverError(
                        "observation journal cannot contain an empty transition"
                    )
                for expiry in batch.expiries:
                    fact = active.get(expiry.fact_digest)
                    if fact is None:
                        raise WorldResolverError(
                            "expiry transition does not reference an active fact"
                        )
                    if (
                        expiry.effective_at_ms < fact.expires_at_ms
                        or batch.observed_at_ms < expiry.effective_at_ms
                        or expiry.effective_at_ms > now_ms
                    ):
                        raise WorldResolverError(
                            "TTL expiry transition occurred before expires_at_ms"
                        )
                    active.pop(expiry.fact_digest)
                    expired.add(expiry.fact_digest)
                for fact in batch.upserts:
                    if fact.fact_digest in expired:
                        raise WorldResolverError(
                            "an expired fact digest cannot be reactivated"
                        )
                    active[fact.fact_digest] = fact
                previous_digest = batch.batch_digest
                previous_observed_at = batch.observed_at_ms
                previous_owner = batch.runtime_owner_id
                previous_lease = batch.runtime_lease_id
                previous_fence = batch.fencing_token
                cursor = batch.revision
                expected_revision += 1
        if cursor != terminal_revision or (
            terminal_revision == head.revision
            and previous_digest != head.batch_digest
        ):
            raise WorldResolverError("observation journal did not reach captured head")

        overdue = tuple(
            sorted(
                fact.fact_digest
                for fact in active.values()
                if now_ms >= fact.expires_at_ms
            )
        )
        if overdue and not allow_overdue:
            raise WorldResolverError(
                "WORLD_EXPIRY_TRANSITION_MISSING: active facts exceeded expires_at_ms"
            )
        facts = tuple(
            sorted(
                active.values(),
                key=lambda item: (
                    item.key,
                    item.source_ref,
                    item.fact_digest,
                ),
            )
        )
        return WorldSnapshotV2(
            workspace_id=workspace,
            subject_id=subject,
            revision=cursor,
            facts=facts,
        )


__all__ = ["WorldObservationPort", "WorldResolver", "WorldResolverError"]
