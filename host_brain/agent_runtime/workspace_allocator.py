"""Configured-root adapter for durable W1 workspace authority.

No physical root is read from SQLite.  Every path is derived from a root that
process composition supplied at startup plus the validated workspace id.
Recursive deletion is intentionally absent from this adapter.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from host_brain.agent_runtime.workspace_paths import resolve_workspace_path
from host_brain.persistence.workspace_repository import (
    WORKSPACE_AUTHORITY_INVALID,
    WorkspaceAuthority,
    WorkspaceMutation,
    WorkspaceRepository,
    WorkspaceRepositoryError,
)


@dataclass(frozen=True, slots=True)
class ManagedWorkspaceRoot:
    root_ref: str
    path: Path
    isolation_modes: frozenset[str]


@dataclass(frozen=True, slots=True)
class DurableWorkspaceLease:
    workspace_id: str
    lease_owner: str
    lease_token: str
    lease_fencing_token: int


@dataclass(frozen=True, slots=True)
class DurableWorkspacePath:
    path: Path
    authority: WorkspaceAuthority


class WorkspaceAllocator:
    """Allocate logical authority and adapt it through preconfigured roots."""

    def __init__(
        self,
        *,
        repository: WorkspaceRepository,
        managed_roots: Mapping[str, str | Path | ManagedWorkspaceRoot],
    ) -> None:
        if not isinstance(repository, WorkspaceRepository):
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "repository must be a WorkspaceRepository",
            )
        if not managed_roots:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "at least one managed root must be preconfigured",
            )
        normalized: dict[str, ManagedWorkspaceRoot] = {}
        physical_paths: set[Path] = set()
        for key, value in managed_roots.items():
            root_ref = str(key or "").strip()
            if isinstance(value, ManagedWorkspaceRoot):
                if value.root_ref != root_ref:
                    raise WorkspaceRepositoryError(
                        WORKSPACE_AUTHORITY_INVALID,
                        "managed root mapping key and root_ref must match",
                    )
                raw_path = value.path
                isolation_modes = frozenset(
                    str(item).strip().upper()
                    for item in value.isolation_modes
                    if str(item).strip()
                )
            else:
                raw_path = Path(value)
                isolation_modes = frozenset({"DIRECTORY_FENCE"})
            try:
                path = Path(raw_path).expanduser().resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise WorkspaceRepositoryError(
                    WORKSPACE_AUTHORITY_INVALID,
                    f"managed root must already exist: {root_ref}",
                ) from exc
            if not path.is_dir() or path in physical_paths:
                raise WorkspaceRepositoryError(
                    WORKSPACE_AUTHORITY_INVALID,
                    "managed roots must be distinct existing directories",
                )
            for existing in physical_paths:
                if path in existing.parents or existing in path.parents:
                    raise WorkspaceRepositoryError(
                        WORKSPACE_AUTHORITY_INVALID,
                        "managed roots cannot overlap",
                    )
            if not root_ref or not isolation_modes:
                raise WorkspaceRepositoryError(
                    WORKSPACE_AUTHORITY_INVALID,
                    "managed root configuration is incomplete",
                )
            normalized[root_ref] = ManagedWorkspaceRoot(
                root_ref=root_ref,
                path=path,
                isolation_modes=isolation_modes,
            )
            physical_paths.add(path)
        self.repository = repository
        self._managed_roots = normalized

    def allocate(self, **values: Any) -> WorkspaceMutation:
        root_ref = str(values.get("managed_root_ref") or "").strip()
        isolation_mode = str(values.get("isolation_mode") or "").strip().upper()
        configured = self._managed_roots.get(root_ref)
        if configured is None:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                f"managed_root_ref is not preconfigured: {root_ref or 'missing'}",
            )
        if isolation_mode not in configured.isolation_modes:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                f"isolation_mode is not enabled for managed root {root_ref}",
            )
        return self.repository.allocate(**values)

    def prepare_workspace(self, lease: DurableWorkspaceLease) -> DurableWorkspacePath:
        """Create/resolve the workspace directory under one live durable fence."""

        return self.repository.run_fenced(
            lease.workspace_id,
            lease_owner=lease.lease_owner,
            lease_token=lease.lease_token,
            lease_fencing_token=lease.lease_fencing_token,
            operation=self._prepare_from_authority,
        )

    def authorize_workspace(self, lease: DurableWorkspaceLease) -> WorkspaceAuthority:
        """Read and decode the exact live authority without filesystem mutation."""

        return self.repository.authorize_lease(
            lease.workspace_id,
            lease_owner=lease.lease_owner,
            lease_token=lease.lease_token,
            lease_fencing_token=lease.lease_fencing_token,
        )

    def write_bytes(
        self,
        lease: DurableWorkspaceLease,
        *,
        relative_path: str,
        content: bytes,
    ) -> Path:
        """Perform one lease-fenced write; stale fences execute no callback."""

        if not isinstance(content, bytes):
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "content must be bytes",
            )

        def operation(authority: WorkspaceAuthority) -> Path:
            workspace = self._prepare_from_authority(authority).path
            target = resolve_workspace_path(workspace, relative_path)
            self._assert_allowed(authority, target.relative_to(workspace).as_posix())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            return target

        return self.repository.run_fenced(
            lease.workspace_id,
            lease_owner=lease.lease_owner,
            lease_token=lease.lease_token,
            lease_fencing_token=lease.lease_fencing_token,
            operation=operation,
        )

    def cleanup_target(self, lease: DurableWorkspaceLease) -> DurableWorkspacePath:
        """Return a fenced configured-root target; never delete it here."""

        return self.repository.run_fenced(
            lease.workspace_id,
            lease_owner=lease.lease_owner,
            lease_token=lease.lease_token,
            lease_fencing_token=lease.lease_fencing_token,
            operation=self._existing_from_authority,
        )

    def _prepare_from_authority(
        self,
        authority: WorkspaceAuthority,
    ) -> DurableWorkspacePath:
        configured = self._configured_for(authority)
        target = configured.path / authority.workspace_relpath
        if target.exists():
            resolved = target.resolve(strict=True)
            if not resolved.is_dir() or resolved.parent != configured.path:
                raise WorkspaceRepositoryError(
                    WORKSPACE_AUTHORITY_INVALID,
                    "managed workspace is not a direct child directory",
                )
        else:
            target.mkdir(parents=False, exist_ok=False)
            resolved = target.resolve(strict=True)
        if resolved.parent != configured.path:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "managed workspace escapes its configured root",
            )
        return DurableWorkspacePath(path=resolved, authority=authority)

    def _existing_from_authority(
        self,
        authority: WorkspaceAuthority,
    ) -> DurableWorkspacePath:
        configured = self._configured_for(authority)
        target = (configured.path / authority.workspace_relpath).resolve(strict=True)
        if not target.is_dir() or target.parent != configured.path:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "cleanup target is not a configured managed workspace",
            )
        return DurableWorkspacePath(path=target, authority=authority)

    def _configured_for(self, authority: WorkspaceAuthority) -> ManagedWorkspaceRoot:
        configured = self._managed_roots.get(authority.managed_root_ref)
        if configured is None:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "durable authority references an unconfigured managed root",
            )
        if authority.isolation_mode not in configured.isolation_modes:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "durable authority isolation mode is not configured locally",
            )
        return configured

    @staticmethod
    def _assert_allowed(authority: WorkspaceAuthority, candidate: str) -> None:
        if "." in authority.allowed_paths:
            return
        if not any(
            candidate == prefix or candidate.startswith(prefix + "/")
            for prefix in authority.allowed_paths
        ):
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                f"path is outside the durable allow-list: {candidate}",
            )


__all__ = [
    "DurableWorkspaceLease",
    "DurableWorkspacePath",
    "ManagedWorkspaceRoot",
    "WorkspaceAllocator",
]
