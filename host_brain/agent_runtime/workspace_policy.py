"""Workspace resolution and write-boundary checks for Agent runtime v2."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Any

from host_brain.agent_runtime.artifact_store import DEFAULT_CODEX_RUNS_ROOT, REPO_ROOT, sanitize_segment
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder


DEFAULT_CLONE_ROOT = REPO_ROOT / "artifacts" / "codex_clones"


@dataclass(frozen=True, slots=True)
class WorkspaceResolution:
    path: Path
    source: str
    created_workspace: bool = False
    cloned_workspace: bool = False
    git_url: str | None = None


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
    def resolve(self, work_order: AgentWorkOrder) -> WorkspaceResolution:
        workspace = dict(work_order.workspace or {})
        strategy = str(workspace.get("strategy") or "").strip().lower()
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

    def enforce_write_policy(self, work_order: AgentWorkOrder, resolution: WorkspaceResolution) -> tuple[bool, str | None]:
        if not work_order.requires_write:
            return True, None
        if not work_order.allowed_actions or "write_files" not in set(work_order.allowed_actions):
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


__all__ = ["WorkspacePolicy", "WorkspaceResolution"]
