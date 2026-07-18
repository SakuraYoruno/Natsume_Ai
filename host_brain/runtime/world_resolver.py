"""Workspace-scoped, read-only World fact resolver.

The resolver consumes observation journal projections and never writes Life or
Memory.  Facts with the same key are retained when their source refs compete;
confidence is only metadata and cannot silently erase a disagreement.  TTL is
evaluated against the supplied clock and is never extended by a read.
"""

from __future__ import annotations

from collections.abc import Iterable

from .context_contracts import WorldFactV2, WorldSnapshotV2


class WorldResolverError(ValueError):
    pass


class WorldResolver:
    """Resolve one workspace from immutable journal observations."""

    def resolve(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        observations: Iterable[WorldFactV2],
        now_ms: int,
    ) -> WorldSnapshotV2:
        workspace = str(workspace_id or "").strip()
        if not workspace:
            raise WorldResolverError("workspace_id is required")
        subject = str(subject_id or "").strip()
        if not subject:
            raise WorldResolverError("subject_id is required")
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise WorldResolverError("now_ms must be a non-negative integer")
        active: list[WorldFactV2] = []
        for fact in observations:
            if not isinstance(fact, WorldFactV2):
                raise WorldResolverError("observations must contain WorldFactV2")
            if fact.workspace_id != workspace:
                raise WorldResolverError("cross-workspace observation rejected")
            if fact.subject_id != subject:
                raise WorldResolverError("cross-subject observation rejected")
            if now_ms >= fact.expires_at_ms:
                continue
            active.append(fact)
        active.sort(key=lambda item: (item.key, item.source_ref, item.revision, item.fact_digest))
        return WorldSnapshotV2(
            workspace_id=workspace,
            subject_id=subject,
            revision=max((item.revision for item in active), default=1),
            facts=tuple(active),
        )


__all__ = ["WorldResolver", "WorldResolverError"]
