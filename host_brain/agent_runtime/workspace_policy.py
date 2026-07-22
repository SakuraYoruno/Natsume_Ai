"""Workspace resolution and write-boundary checks for Agent runtime v2."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import TYPE_CHECKING, Any

from host_brain.agent_runtime.artifact_store import DEFAULT_CODEX_RUNS_ROOT, REPO_ROOT, sanitize_segment
from host_brain.contracts.agent_runtime.work_order import (
    AgentWorkOrder,
    WorkOrderV3,
    canonicalize_work_order,
)

if TYPE_CHECKING:
    from host_brain.agent_runtime.workspace_allocator import (
        DurableWorkspaceLease,
        WorkspaceAllocator,
    )


DEFAULT_CLONE_ROOT = REPO_ROOT / "artifacts" / "codex_clones"

WORKSPACE_WRITE_GRANT_REQUIRED = "WORKSPACE_WRITE_GRANT_REQUIRED"

_WRITE_FORBIDDEN_WORKSPACE_SELECTORS = frozenset(
    {
        "path",
        "git_url",
        "clone_root",
        "runs_root",
        "root",
        "workspace_root",
        "repo_root",
    }
)
_WORKSPACE_WRITE_ACTIONS = frozenset({"write_files", "agent_write_file"})


class WorkspacePolicyViolation(ValueError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


@dataclass(frozen=True, slots=True)
class WorkspaceResolution:
    path: Path
    source: str
    created_workspace: bool = False
    cloned_workspace: bool = False
    git_url: str | None = None
    workspace_id: str | None = None
    lease_owner: str | None = None
    lease_fencing_token: int | None = None


def _find_repo_root(start: Path) -> Path | None:
    current = start.parent if start.is_file() else start
    current = current.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _normalize_existing_path(path: Path) -> Path:
    return (_find_repo_root(path) or path).resolve()


def _clone_repo(git_url: str, clone_root: Path, clone_name: str | None) -> Path:
    target_name = sanitize_segment(clone_name or Path(git_url.rstrip("/")).stem or "repo", "repo")
    target_dir = clone_root / target_name
    clone_root.mkdir(parents=True, exist_ok=True)
    if target_dir.exists():
        return _normalize_existing_path(target_dir)
    completed = subprocess.run(
        ["git", "clone", "--depth", "1", git_url, str(target_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "git clone failed").strip()
        raise ValueError(f"git clone failed: {detail[:400]}")
    return _normalize_existing_path(target_dir)


class WorkspacePolicy:
    def __init__(
        self,
        *,
        workspace_allocator: "WorkspaceAllocator | None" = None,
        workspace_lease_provider: Callable[
            [AgentWorkOrder | WorkOrderV3, str], "DurableWorkspaceLease"
        ]
        | None = None,
    ) -> None:
        # W2 injects this authority only on the authenticated Internal Agent
        # path.  Every other composition—and Internal startup without its
        # explicit secret—must still fail closed rather than trust a payload
        # that merely names the durable strategy.
        self._workspace_allocator = workspace_allocator
        self._workspace_lease_provider = workspace_lease_provider

    def resolve(self, work_order: AgentWorkOrder | WorkOrderV3) -> WorkspaceResolution:
        work_order = self._prepare_work_order(work_order)
        workspace = dict(work_order.workspace or {})
        strategy = str(workspace.get("strategy") or "").strip().lower()
        if strategy == "durable_workspace":
            return self._resolve_durable(work_order, workspace=workspace)
        self._enforce_w0_workspace_source(work_order, workspace=workspace, strategy=strategy)
        if strategy in {"runtime_repo", "current_runtime_repo"}:
            return WorkspaceResolution(path=REPO_ROOT.resolve(), source="runtime_repo")

        raw_path = str(workspace.get("path") or "").strip()
        if raw_path:
            path = Path(raw_path).expanduser()
            if not path.exists():
                if bool(workspace.get("create")):
                    path.mkdir(parents=True, exist_ok=True)
                    return WorkspaceResolution(path=_normalize_existing_path(path), source="path", created_workspace=True)
                raise ValueError(f"workspace path does not exist: {path}")
            return WorkspaceResolution(path=_normalize_existing_path(path), source="path")

        git_url = str(workspace.get("git_url") or "").strip()
        if strategy == "git_url" or git_url:
            clone_root = Path(str(workspace.get("clone_root") or DEFAULT_CLONE_ROOT)).expanduser()
            path = _clone_repo(git_url, clone_root, str(workspace.get("clone_name") or "").strip() or None)
            return WorkspaceResolution(path=path, source="git_url", cloned_workspace=True, git_url=git_url)

        if strategy == "temp_workspace":
            runs_root = Path(str(workspace.get("runs_root") or DEFAULT_CODEX_RUNS_ROOT)).expanduser()
            path = runs_root / sanitize_segment(work_order.work_order_id, "work-order") / "workspace"
            path.mkdir(parents=True, exist_ok=True)
            return WorkspaceResolution(path=path.resolve(), source="temp_workspace", created_workspace=True)

        raise ValueError("workspace could not be resolved from WorkOrder")

    def _resolve_durable(
        self,
        work_order: AgentWorkOrder | WorkOrderV3,
        *,
        workspace: dict[str, Any],
    ) -> WorkspaceResolution:
        if (
            self._workspace_allocator is None
            or self._workspace_lease_provider is None
        ):
            raise WorkspacePolicyViolation(
                WORKSPACE_WRITE_GRANT_REQUIRED,
                "durable_workspace requires an injected WorkspaceAllocator and "
                "server-side lease provider",
            )
        extras = sorted(set(workspace).difference({"strategy", "workspace_id"}))
        if extras:
            raise WorkspacePolicyViolation(
                WORKSPACE_WRITE_GRANT_REQUIRED,
                "durable workspace payload may contain only strategy and "
                "workspace_id; server-side lease data cannot be serialized: "
                + ",".join(extras),
            )
        workspace_id = str(workspace.get("workspace_id") or "").strip()
        if not workspace_id:
            raise WorkspacePolicyViolation(
                WORKSPACE_WRITE_GRANT_REQUIRED,
                "durable workspace_id is required",
            )
        (
            expected_root_work_order_id,
            expected_graph_id,
            expected_graph_revision,
        ) = self._durable_graph_identity(work_order)
        if not expected_graph_id or expected_graph_revision < 1:
            raise WorkspacePolicyViolation(
                WORKSPACE_WRITE_GRANT_REQUIRED,
                "durable workspace requires canonical WorkOrder graph fields",
            )
        try:
            lease = self._workspace_lease_provider(work_order, workspace_id)
            if lease.workspace_id != workspace_id:
                raise WorkspacePolicyViolation(
                    WORKSPACE_WRITE_GRANT_REQUIRED,
                    "server-side lease is bound to another workspace",
                )
            authority = self._workspace_allocator.authorize_workspace(lease)
            if (
                authority.root_work_order_id != expected_root_work_order_id
                or authority.graph_id != expected_graph_id
                or authority.graph_revision != expected_graph_revision
            ):
                raise WorkspacePolicyViolation(
                    WORKSPACE_WRITE_GRANT_REQUIRED,
                    "workspace authority is bound to another graph epoch",
                )
            prepared = self._workspace_allocator.prepare_workspace(lease)
        except Exception as exc:
            from host_brain.persistence.workspace_repository import WorkspaceRepositoryError

            if not isinstance(exc, WorkspaceRepositoryError):
                raise
            raise WorkspacePolicyViolation(exc.reason_code, exc.detail) from exc
        return WorkspaceResolution(
            path=prepared.path,
            source="durable_workspace",
            created_workspace=True,
            workspace_id=prepared.authority.workspace_id,
            lease_owner=prepared.authority.lease_owner,
            lease_fencing_token=prepared.authority.lease_fencing_token,
        )

    @staticmethod
    def _enforce_w0_workspace_source(
        work_order: AgentWorkOrder | WorkOrderV3,
        *,
        workspace: dict[str, Any],
        strategy: str,
    ) -> None:
        """Fail closed until a durable, task-bound WorkspaceGrant exists.

        W0 intentionally permits write-capable orders only in the runtime-owned
        per-WorkOrder temporary root.  It is a stopgap boundary, not durable
        workspace ownership or OS-level isolation.
        """

        if not WorkspacePolicy._requires_workspace_write(work_order):
            return
        forbidden_selectors = sorted(
            key
            for key in _WRITE_FORBIDDEN_WORKSPACE_SELECTORS
            if key in workspace and str(workspace.get(key) or "").strip()
        )
        if strategy != "temp_workspace" or forbidden_selectors:
            detail = (
                f"; forbidden selectors={','.join(forbidden_selectors)}"
                if forbidden_selectors
                else ""
            )
            raise WorkspacePolicyViolation(
                WORKSPACE_WRITE_GRANT_REQUIRED,
                "write-capable WorkOrders may only use the runtime-owned temp_workspace "
                f"before durable WorkspaceGrant{detail}",
            )

    @staticmethod
    def _requires_workspace_write(work_order: AgentWorkOrder | WorkOrderV3) -> bool:
        actions = {
            str(action).strip().lower()
            for action in work_order.allowed_actions
            if str(action).strip()
        }
        return work_order.run_mode in {"worker", "self_heal", "self_evolve"} or bool(
            actions & _WORKSPACE_WRITE_ACTIONS
        )

    def enforce_write_policy(
        self,
        work_order: AgentWorkOrder | WorkOrderV3,
        resolution: WorkspaceResolution,
    ) -> tuple[bool, str | None]:
        work_order = self._prepare_work_order(work_order)
        if not self._requires_workspace_write(work_order):
            return True, None
        allowed_actions = {
            str(action).strip().lower()
            for action in work_order.allowed_actions
            if str(action).strip()
        }
        if not (allowed_actions & _WORKSPACE_WRITE_ACTIONS):
            return False, "write WorkOrders must include allowed_actions=['write_files', ...]"
        if "modify_secret_files" not in set(work_order.forbidden_actions):
            return False, "write WorkOrders must forbid secret-file mutation"
        if os.environ.get("HOST_CODEX_FORBID_MAIN_BRANCH_DIRECT_WRITE", "true").strip().lower() in {"1", "true", "yes", "on"}:
            branch = self._git_branch(resolution.path)
            if branch in {"main", "master"} and work_order.run_mode == "self_evolve":
                return False, "self_evolve cannot run on main/master"
        durable_review_required = (
            bool(work_order.review_policy.get("durable_review_required"))
            or bool(work_order.review_policy.get("require_durable_review"))
            or bool(work_order.approval_policy.get("require_review_token"))
            or bool(work_order.approval_policy.get("review_id"))
        )
        if durable_review_required and not work_order.approval_policy.get("review_resume_token_ref"):
            return (
                False,
                "approval_policy.review_resume_token_ref is required; "
                "approval_policy.confirmed=true is not sufficient",
            )
        if work_order.approval_policy.get("require_confirm") and not work_order.approval_policy.get("confirmed"):
            return False, "approval_policy.confirmed=true is required"
        return True, None

    @staticmethod
    def _prepare_work_order(
        work_order: AgentWorkOrder | WorkOrderV3,
    ) -> AgentWorkOrder | WorkOrderV3:
        """Keep v2 only at this public compatibility boundary.

        Host-admitted workers already arrive as v3.  The standalone policy
        still accepts v2 so existing external callers can resolve legacy W1
        workspaces while they migrate; v3 always validates its top-level graph
        identity and rejects conflicting metadata.
        """

        if isinstance(work_order, WorkOrderV3):
            return canonicalize_work_order(work_order)
        if isinstance(work_order, AgentWorkOrder):
            work_order.validate()
            return work_order
        raise TypeError("workspace policy requires AgentWorkOrder or WorkOrderV3")

    @staticmethod
    def _durable_graph_identity(
        work_order: AgentWorkOrder | WorkOrderV3,
    ) -> tuple[str, str, int]:
        if isinstance(work_order, WorkOrderV3):
            return (
                work_order.root_work_order_id,
                str(work_order.task_graph_id or "").strip(),
                work_order.graph_revision,
            )
        metadata = dict(work_order.metadata or {})
        root_work_order_id = str(
            metadata.get("root_work_order_id") or work_order.work_order_id
        ).strip()
        graph_id = str(metadata.get("task_graph_id") or "").strip()
        raw_graph_revision = metadata.get("graph_revision")
        if isinstance(raw_graph_revision, bool):
            raise WorkspacePolicyViolation(
                WORKSPACE_WRITE_GRANT_REQUIRED,
                "durable workspace requires canonical graph metadata",
            )
        try:
            graph_revision = int(raw_graph_revision)
        except (TypeError, ValueError) as exc:
            raise WorkspacePolicyViolation(
                WORKSPACE_WRITE_GRANT_REQUIRED,
                "durable workspace requires canonical graph metadata",
            ) from exc
        return root_work_order_id, graph_id, graph_revision

    @staticmethod
    def _git_branch(workspace: Path) -> str | None:
        if not (workspace / ".git").exists():
            return None
        completed = subprocess.run(
            ["git", "-C", str(workspace), "branch", "--show-current"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
        )
        return str(completed.stdout or "").strip() or None


__all__ = [
    "WORKSPACE_WRITE_GRANT_REQUIRED",
    "WorkspacePolicy",
    "WorkspacePolicyViolation",
    "WorkspaceResolution",
]
