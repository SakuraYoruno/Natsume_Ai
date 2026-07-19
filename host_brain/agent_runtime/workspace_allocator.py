"""Configured-root adapter for durable W1 workspace authority.

No physical root is read from SQLite.  Every path is derived from a root that
process composition supplied at startup plus the validated workspace id.
Recursive deletion is intentionally absent from this adapter.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable, Iterator, TypeVar

from host_brain.agent_runtime.workspace_paths import resolve_workspace_path
from host_brain.persistence.workspace_repository import (
    WORKSPACE_AUTHORITY_INVALID,
    WorkspaceAuthority,
    WorkspaceMutation,
    WorkspaceOperationContext,
    WorkspaceRepository,
    WorkspaceRepositoryError,
    WORKSPACE_OPERATION_TIMEOUT,
)


@dataclass(frozen=True, slots=True)
class ManagedWorkspaceRoot:
    root_ref: str
    path: Path
    isolation_modes: frozenset[str]


@dataclass(frozen=True, slots=True)
class ManagedGitSource:
    source_key: str
    source_ref_prefix: str
    repository: Path


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


@dataclass(frozen=True, slots=True)
class WorkspaceCleanupResult:
    workspace_id: str
    source_ref: str
    already_absent: bool


@dataclass(frozen=True, slots=True)
class WorkspaceNoChangeSnapshot:
    workspace_id: str
    base_commit: str
    accepted_head: str
    tree_digest: str
    diff_digest: str
    receipt_ref: str


_SOURCE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$")
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_GIT_COMMAND_TIMEOUT_S = 60.0
_GIT_DEADLINE: ContextVar[float | None] = ContextVar(
    "natsume_workspace_git_deadline",
    default=None,
)
_T = TypeVar("_T")


class WorkspaceAllocator:
    """Allocate logical authority and adapt it through preconfigured roots."""

    def __init__(
        self,
        *,
        repository: WorkspaceRepository,
        managed_roots: Mapping[str, str | Path | ManagedWorkspaceRoot],
        git_sources: Mapping[str, str | Path | ManagedGitSource] | None = None,
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
        self._git_sources = self._normalize_git_sources(
            git_sources or {},
            managed_paths=physical_paths,
        )

    @property
    def operation_timeout_s(self) -> float:
        """Conservative bound for one fenced Git adapter operation.

        The Git/workspace lock sequence shares the 60-second external deadline.
        A pre-callback authority read and the final authority read can each
        enter SQLite immediately before that deadline and consume one complete
        repository busy timeout, so both reserves belong in the close bound.
        """

        return (
            _GIT_COMMAND_TIMEOUT_S
            + (2 * self.repository.operation_timeout_s)
            + 1.0
        )

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
            expected_status="ACTIVE",
            max_operation_ms=int(_GIT_COMMAND_TIMEOUT_S * 1000),
            operation=lambda context: self._run_authority_operation(
                context,
                operation="prepare",
                callback=self._prepare_from_authority,
            ),
        )

    def snapshot_git_source(self, source_key: str) -> str:
        """Resolve a configured source to an immutable commit reference."""

        normalized = str(source_key or "").strip()
        source = self._git_sources.get(normalized)
        if source is None:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                f"git source is not preconfigured: {normalized or 'missing'}",
            )
        deadline = time.monotonic() + _GIT_COMMAND_TIMEOUT_S
        with self._git_sequence(source, deadline_monotonic=deadline):
            commit = self._git_output(
                source.repository,
                "rev-parse",
                "--verify",
                "HEAD^{commit}",
            ).lower()
        if not _GIT_OBJECT.fullmatch(commit):
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "configured git source returned a non-canonical commit id",
            )
        return f"{source.source_ref_prefix}/{commit}"

    def validate_git_source_ref(self, source_key: str, source_ref: str) -> str:
        """Validate one durable ref against the exact configured Git source."""

        normalized = str(source_key or "").strip()
        source = self._git_sources.get(normalized)
        if source is None:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                f"git source is not preconfigured: {normalized or 'missing'}",
            )
        durable_ref = str(source_ref or "").strip()
        expected_prefix = source.source_ref_prefix + "/"
        if not durable_ref.startswith(expected_prefix):
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "durable git source reference is bound to another configured source",
            )
        commit = durable_ref.removeprefix(expected_prefix)
        if not _GIT_OBJECT.fullmatch(commit):
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "durable git source reference has an invalid commit id",
            )
        deadline = time.monotonic() + _GIT_COMMAND_TIMEOUT_S
        with self._git_sequence(source, deadline_monotonic=deadline):
            if self._git_returncode(
                source.repository,
                "cat-file",
                "-e",
                f"{commit}^{{commit}}",
            ) != 0:
                raise WorkspaceRepositoryError(
                    WORKSPACE_AUTHORITY_INVALID,
                    "durable git source commit is unavailable",
                )
        return durable_ref

    def authorize_workspace(self, lease: DurableWorkspaceLease) -> WorkspaceAuthority:
        """Read and decode the exact live authority without filesystem mutation."""

        authority = self.repository.authorize_lease(
            lease.workspace_id,
            lease_owner=lease.lease_owner,
            lease_token=lease.lease_token,
            lease_fencing_token=lease.lease_fencing_token,
        )
        self._require_status(authority, expected="ACTIVE", operation="execution")
        return authority

    def capture_no_change_head(
        self,
        lease: DurableWorkspaceLease,
    ) -> WorkspaceNoChangeSnapshot:
        """Capture a fenced, explicit read-only/no-change Git receipt.

        This deliberately refuses dirty worktrees and advanced branches.  It
        never invents an ``accepted_head`` for a non-Git workspace and cannot
        silently promote uncommitted Agent writes.
        """

        def capture(authority: WorkspaceAuthority) -> WorkspaceNoChangeSnapshot:
            resolved_source = self._git_source_for(authority)
            if resolved_source is None:
                raise WorkspaceRepositoryError(
                    WORKSPACE_AUTHORITY_INVALID,
                    "accepted HEAD requires a configured Git workspace",
                )
            source, base_commit = resolved_source
            del source
            workspace = self._prepare_from_authority(authority).path
            status = self._git_output(
                workspace,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            )
            accepted_head = self._git_output(
                workspace,
                "rev-parse",
                "--verify",
                "HEAD^{commit}",
            ).lower()
            tree_object = self._git_output(
                workspace,
                "rev-parse",
                "--verify",
                "HEAD^{tree}",
            ).lower()
            if status or accepted_head != base_commit:
                raise WorkspaceRepositoryError(
                    WORKSPACE_AUTHORITY_INVALID,
                    "read-only Workspace changed and has no governed write promotion",
                )
            if not _GIT_OBJECT.fullmatch(accepted_head) or not _GIT_OBJECT.fullmatch(
                tree_object
            ):
                raise WorkspaceRepositoryError(
                    WORKSPACE_AUTHORITY_INVALID,
                    "Git no-change snapshot returned a non-canonical object id",
                )
            tree_digest = "sha256:" + hashlib.sha256(
                f"git-tree:{tree_object}".encode("ascii")
            ).hexdigest()
            diff_digest = "sha256:" + hashlib.sha256(b"").hexdigest()
            receipt_digest = hashlib.sha256(
                (
                    f"{authority.workspace_id}\n{base_commit}\n{accepted_head}\n"
                    f"{tree_digest}\n{diff_digest}"
                ).encode("utf-8")
            ).hexdigest()
            return WorkspaceNoChangeSnapshot(
                workspace_id=authority.workspace_id,
                base_commit=base_commit,
                accepted_head=accepted_head,
                tree_digest=tree_digest,
                diff_digest=diff_digest,
                receipt_ref=f"workspace-no-change://sha256/{receipt_digest}",
            )

        return self.repository.run_fenced(
            lease.workspace_id,
            lease_owner=lease.lease_owner,
            lease_token=lease.lease_token,
            lease_fencing_token=lease.lease_fencing_token,
            expected_status="ACTIVE",
            max_operation_ms=int(_GIT_COMMAND_TIMEOUT_S * 1000),
            operation=lambda context: self._run_authority_operation(
                context,
                operation="accepted-head",
                callback=capture,
            ),
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

        def operation(context: WorkspaceOperationContext) -> Path:
            def write(authority: WorkspaceAuthority) -> Path:
                workspace = self._prepare_from_authority(authority).path
                target = resolve_workspace_path(workspace, relative_path)
                self._assert_allowed(authority, target.relative_to(workspace).as_posix())
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                return target

            return self._run_authority_operation(
                context,
                operation="write",
                callback=write,
            )

        return self.repository.run_fenced(
            lease.workspace_id,
            lease_owner=lease.lease_owner,
            lease_token=lease.lease_token,
            lease_fencing_token=lease.lease_fencing_token,
            expected_status="ACTIVE",
            max_operation_ms=int(_GIT_COMMAND_TIMEOUT_S * 1000),
            operation=operation,
        )

    def cleanup_target(self, lease: DurableWorkspaceLease) -> DurableWorkspacePath:
        """Return a fenced configured-root target; never delete it here."""

        return self.repository.run_fenced(
            lease.workspace_id,
            lease_owner=lease.lease_owner,
            lease_token=lease.lease_token,
            lease_fencing_token=lease.lease_fencing_token,
            expected_status="CLEANUP_PENDING",
            max_operation_ms=5_000,
            operation=lambda context: self._existing_from_authority(context.authority),
        )

    def remove_git_worktree(
        self,
        lease: DurableWorkspaceLease,
    ) -> WorkspaceCleanupResult:
        """Remove one configured git worktree under a live durable fence.

        This deliberately delegates removal to ``git worktree remove``.  It
        never recursively deletes a path supplied by SQLite or a WorkOrder.
        """

        return self.repository.run_fenced(
            lease.workspace_id,
            lease_owner=lease.lease_owner,
            lease_token=lease.lease_token,
            lease_fencing_token=lease.lease_fencing_token,
            expected_status="CLEANUP_PENDING",
            max_operation_ms=int(_GIT_COMMAND_TIMEOUT_S * 1000),
            operation=lambda context: self._run_authority_operation(
                context,
                operation="cleanup",
                callback=self._remove_git_worktree_from_authority,
            ),
        )

    def _run_authority_operation(
        self,
        context: WorkspaceOperationContext,
        *,
        operation: str,
        callback: Callable[[WorkspaceAuthority], _T],
    ) -> _T:
        authority = context.authority
        self._require_status(
            authority,
            expected="CLEANUP_PENDING" if operation == "cleanup" else "ACTIVE",
            operation=operation,
        )
        source = self._configured_git_source(authority)
        if source is None:
            context.remaining_s()
            return callback(authority)
        with self._git_sequence(
            source,
            deadline_monotonic=context.deadline_monotonic,
        ):
            context.remaining_s()
            return callback(authority)

    @contextmanager
    def _git_sequence(
        self,
        source: ManagedGitSource,
        *,
        deadline_monotonic: float,
    ) -> Iterator[None]:
        """Serialize one configured source and share one total Git deadline."""

        source_key = os.path.normcase(str(source.repository.resolve(strict=True)))
        with self.repository.operation_lock(
            namespace="git-source",
            key=source_key,
            deadline_monotonic=deadline_monotonic,
        ):
            token = _GIT_DEADLINE.set(deadline_monotonic)
            try:
                yield
            finally:
                _GIT_DEADLINE.reset(token)

    def _configured_git_source(
        self,
        authority: WorkspaceAuthority,
    ) -> ManagedGitSource | None:
        matches = [
            source
            for source in self._git_sources.values()
            if authority.source_ref.startswith(source.source_ref_prefix + "/")
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "durable source reference is ambiguous",
            )
        return matches[0]

    def _prepare_from_authority(
        self,
        authority: WorkspaceAuthority,
    ) -> DurableWorkspacePath:
        configured = self._configured_for(authority)
        target = configured.path / authority.workspace_relpath
        git_source = self._git_source_for(authority)
        if git_source is not None:
            return self._prepare_git_worktree(
                authority=authority,
                configured=configured,
                source=git_source[0],
                commit=git_source[1],
                target=target,
            )
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

    def _prepare_active_from_authority(
        self,
        authority: WorkspaceAuthority,
    ) -> DurableWorkspacePath:
        self._require_status(authority, expected="ACTIVE", operation="prepare")
        return self._prepare_from_authority(authority)

    def _prepare_git_worktree(
        self,
        *,
        authority: WorkspaceAuthority,
        configured: ManagedWorkspaceRoot,
        source: ManagedGitSource,
        commit: str,
        target: Path,
    ) -> DurableWorkspacePath:
        branch = self._workspace_branch(authority.workspace_id)
        if target.exists():
            resolved = target.resolve(strict=True)
            self._verify_git_worktree(
                source=source,
                commit=commit,
                branch=branch,
                target=resolved,
                configured_root=configured.path,
            )
            return DurableWorkspacePath(path=resolved, authority=authority)
        if target.parent != configured.path:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "managed git worktree is not a direct configured-root child",
            )
        branch_exists = self._git_returncode(
            source.repository,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
        ) == 0
        if branch_exists:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "new workspace authority cannot adopt a pre-existing branch",
            )
        self._git_output(
            source.repository,
            "worktree",
            "add",
            "-b",
            branch,
            str(target),
            commit,
        )
        resolved = target.resolve(strict=True)
        self._verify_git_worktree(
            source=source,
            commit=commit,
            branch=branch,
            target=resolved,
            configured_root=configured.path,
        )
        current_head = self._git_output(
            resolved,
            "rev-parse",
            "--verify",
            "HEAD^{commit}",
        ).lower()
        if current_head != commit:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "new workspace HEAD differs from its frozen source commit",
            )
        return DurableWorkspacePath(path=resolved, authority=authority)

    def _remove_git_worktree_from_authority(
        self,
        authority: WorkspaceAuthority,
    ) -> WorkspaceCleanupResult:
        self._require_status(
            authority,
            expected="CLEANUP_PENDING",
            operation="cleanup",
        )
        configured = self._configured_for(authority)
        resolved_source = self._git_source_for(authority)
        if resolved_source is None:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "only configured git worktrees have an executable cleanup adapter",
            )
        source, commit = resolved_source
        target = configured.path / authority.workspace_relpath
        branch = self._workspace_branch(authority.workspace_id)
        if target.parent != configured.path:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "cleanup target is not a configured-root child",
            )
        branch_exists = self._git_returncode(
            source.repository,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
        ) == 0
        already_absent = not target.exists() and not branch_exists
        registered = self._registered_worktrees(source)
        normalized_target = target.resolve(strict=False)
        if target.exists():
            resolved = target.resolve(strict=True)
            self._verify_git_worktree(
                source=source,
                commit=commit,
                branch=branch,
                target=resolved,
                configured_root=configured.path,
            )
            self._git_output(
                source.repository,
                "worktree",
                "remove",
                "--force",
                str(resolved),
            )
        elif normalized_target in registered:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "missing worktree still has registered git metadata",
            )
        if target.exists() or normalized_target in self._registered_worktrees(source):
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "git worktree cleanup did not remove the configured target",
            )
        if branch_exists:
            if self._git_returncode(
                source.repository,
                "merge-base",
                "--is-ancestor",
                commit,
                branch,
            ) != 0:
                raise WorkspaceRepositoryError(
                    WORKSPACE_AUTHORITY_INVALID,
                    "workspace branch no longer descends from its frozen source commit",
                )
            self._git_output(source.repository, "branch", "-D", branch)
        if self._git_returncode(
            source.repository,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
        ) == 0:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "git worktree cleanup left its exact namespace branch behind",
            )
        return WorkspaceCleanupResult(
            workspace_id=authority.workspace_id,
            source_ref=authority.source_ref,
            already_absent=already_absent,
        )

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

    def _git_source_for(
        self,
        authority: WorkspaceAuthority,
    ) -> tuple[ManagedGitSource, str] | None:
        source = self._configured_git_source(authority)
        if source is None:
            return None
        commit = authority.source_ref.removeprefix(source.source_ref_prefix + "/")
        if not _GIT_OBJECT.fullmatch(commit):
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "durable git source reference has an invalid commit id",
            )
        if self._git_returncode(
            source.repository,
            "cat-file",
            "-e",
            f"{commit}^{{commit}}",
        ) != 0:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "durable git source commit is unavailable",
            )
        return source, commit

    @classmethod
    def _normalize_git_sources(
        cls,
        values: Mapping[str, str | Path | ManagedGitSource],
        *,
        managed_paths: set[Path],
    ) -> dict[str, ManagedGitSource]:
        normalized: dict[str, ManagedGitSource] = {}
        repositories: set[Path] = set()
        for key, value in values.items():
            source_key = str(key or "").strip()
            if not _SOURCE_KEY.fullmatch(source_key):
                raise WorkspaceRepositoryError(
                    WORKSPACE_AUTHORITY_INVALID,
                    "git source key is invalid",
                )
            if isinstance(value, ManagedGitSource):
                if value.source_key != source_key:
                    raise WorkspaceRepositoryError(
                        WORKSPACE_AUTHORITY_INVALID,
                        "git source mapping key and source_key must match",
                    )
                raw_repository = value.repository
                prefix = str(value.source_ref_prefix or "").strip()
            else:
                raw_repository = Path(value)
                prefix = f"git-worktree://{source_key}"
            if prefix != f"git-worktree://{source_key}":
                raise WorkspaceRepositoryError(
                    WORKSPACE_AUTHORITY_INVALID,
                    "git source reference prefix is not canonical",
                )
            try:
                repository = Path(raw_repository).expanduser().resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise WorkspaceRepositoryError(
                    WORKSPACE_AUTHORITY_INVALID,
                    f"git source repository does not exist: {source_key}",
                ) from exc
            if not repository.is_dir() or repository in repositories:
                raise WorkspaceRepositoryError(
                    WORKSPACE_AUTHORITY_INVALID,
                    "git source repositories must be distinct directories",
                )
            top = cls._git_output(repository, "rev-parse", "--show-toplevel")
            if Path(top).resolve(strict=True) != repository:
                raise WorkspaceRepositoryError(
                    WORKSPACE_AUTHORITY_INVALID,
                    "git source must be the configured repository root",
                )
            for managed in managed_paths:
                if repository == managed or repository in managed.parents or managed in repository.parents:
                    raise WorkspaceRepositoryError(
                        WORKSPACE_AUTHORITY_INVALID,
                        "managed roots and git source repositories cannot overlap",
                    )
            normalized[source_key] = ManagedGitSource(
                source_key=source_key,
                source_ref_prefix=prefix,
                repository=repository,
            )
            repositories.add(repository)
        return normalized

    @classmethod
    def _verify_git_worktree(
        cls,
        *,
        source: ManagedGitSource,
        commit: str,
        branch: str,
        target: Path,
        configured_root: Path,
    ) -> None:
        if not target.is_dir() or target.parent != configured_root:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "git worktree escapes its configured managed root",
            )
        top = Path(cls._git_output(target, "rev-parse", "--show-toplevel")).resolve(strict=True)
        if top != target:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "configured workspace is not its git worktree root",
            )
        common_raw = cls._git_output(target, "rev-parse", "--git-common-dir")
        common = Path(common_raw)
        if not common.is_absolute():
            common = target / common
        source_common_raw = cls._git_output(source.repository, "rev-parse", "--git-common-dir")
        source_common = Path(source_common_raw)
        if not source_common.is_absolute():
            source_common = source.repository / source_common
        if common.resolve(strict=True) != source_common.resolve(strict=True):
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "configured workspace belongs to another git repository",
            )
        current_branch = cls._git_output(target, "symbolic-ref", "--short", "HEAD")
        if current_branch != branch:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "configured workspace is on an unexpected branch",
            )
        if cls._git_returncode(target, "merge-base", "--is-ancestor", commit, "HEAD") != 0:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "configured workspace does not descend from its frozen source commit",
            )
        if target not in cls._registered_worktrees(source):
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                "configured workspace is not registered as a git worktree",
            )

    @classmethod
    def _registered_worktrees(cls, source: ManagedGitSource) -> set[Path]:
        output = cls._git_output(source.repository, "worktree", "list", "--porcelain")
        registered: set[Path] = set()
        for line in output.splitlines():
            if line.startswith("worktree "):
                registered.add(Path(line.removeprefix("worktree ")).resolve(strict=False))
        return registered

    @staticmethod
    def _workspace_branch(workspace_id: str) -> str:
        return f"natsume-agent/{workspace_id}"

    @staticmethod
    def _require_status(
        authority: WorkspaceAuthority,
        *,
        expected: str,
        operation: str,
    ) -> None:
        if authority.status != expected:
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                f"workspace {operation} requires {expected} authority",
            )

    @staticmethod
    def _git_returncode(repository: Path, *args: str) -> int:
        timeout_s = WorkspaceAllocator._git_remaining_s()
        try:
            completed = subprocess.run(
                ["git", "-C", str(repository), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceRepositoryError(
                WORKSPACE_OPERATION_TIMEOUT,
                "configured Git sequence exhausted its operation deadline",
            ) from exc
        return int(completed.returncode)

    @staticmethod
    def _git_output(repository: Path, *args: str) -> str:
        timeout_s = WorkspaceAllocator._git_remaining_s()
        try:
            completed = subprocess.run(
                ["git", "-C", str(repository), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceRepositoryError(
                WORKSPACE_OPERATION_TIMEOUT,
                "configured Git sequence exhausted its operation deadline",
            ) from exc
        if completed.returncode != 0:
            detail = str(completed.stderr or completed.stdout or "git command failed").strip()
            raise WorkspaceRepositoryError(
                WORKSPACE_AUTHORITY_INVALID,
                f"configured git operation failed: {detail[:400]}",
            )
        return str(completed.stdout or "").strip()

    @staticmethod
    def _git_remaining_s() -> float:
        deadline = _GIT_DEADLINE.get()
        if deadline is None:
            return _GIT_COMMAND_TIMEOUT_S
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WorkspaceRepositoryError(
                WORKSPACE_OPERATION_TIMEOUT,
                "configured Git sequence exhausted its operation deadline",
            )
        return remaining

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
    "ManagedGitSource",
    "ManagedWorkspaceRoot",
    "WorkspaceCleanupResult",
    "WorkspaceNoChangeSnapshot",
    "WorkspaceAllocator",
]
