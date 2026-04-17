"""Workspace resolution helpers for Codex-backed skill execution."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_ALIAS_REGISTRY = _REPO_ROOT / "runtime" / "codex" / "repo_aliases.json"
_DEFAULT_CLONE_ROOT = _REPO_ROOT / "artifacts" / "codex_clones"
_DEFAULT_RUNS_ROOT = _REPO_ROOT / "artifacts" / "codex_runs"
_RUNTIME_REPO_STRATEGIES = {"runtime_repo", "current_runtime_repo"}


@dataclass(frozen=True)
class WorkspaceResolution:
    workspace: Path
    source: str
    repo_alias: str | None = None
    git_url: str | None = None
    created_workspace: bool = False
    cloned_workspace: bool = False


def _sanitize_name(value: str, fallback: str = "workspace") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip(".-")
    return cleaned or fallback


def _find_repo_root(start: Path) -> Path | None:
    current = start
    if current.is_file():
        current = current.parent
    current = current.resolve()
    for candidate in (current, *current.parents):
        git_marker = candidate / ".git"
        if git_marker.exists():
            return candidate
    return None


def _normalize_existing_path(path: Path) -> Path:
    if path.is_file():
        base = path.parent
    else:
        base = path
    repo_root = _find_repo_root(base)
    return repo_root or base


def _load_repo_aliases(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in payload.items():
        alias = str(key or "").strip()
        target = str(value or "").strip()
        if alias and target:
            normalized[alias] = target
    return normalized


def _resolve_alias_registry(payload: dict[str, Any]) -> Path:
    raw = str(payload.get("repo_alias_file") or "").strip()
    candidate = Path(raw).expanduser() if raw else _DEFAULT_ALIAS_REGISTRY
    return candidate.resolve() if candidate.exists() else candidate


def _resolve_runs_root(payload: dict[str, Any]) -> Path:
    raw = str(payload.get("runs_root") or "").strip()
    candidate = Path(raw).expanduser() if raw else _DEFAULT_RUNS_ROOT
    return candidate.resolve() if candidate.exists() else candidate


def _resolve_clone_root(payload: dict[str, Any]) -> Path:
    raw = str(payload.get("clone_root") or "").strip()
    candidate = Path(raw).expanduser() if raw else _DEFAULT_CLONE_ROOT
    return candidate.resolve() if candidate.exists() else candidate


def _clone_repo(git_url: str, clone_root: Path, clone_name: str | None) -> Path:
    git_cmd = ["git", "clone", "--depth", "1", git_url]
    target_name = _sanitize_name(clone_name or Path(git_url.rstrip("/")).stem or "repo")
    target_dir = clone_root / target_name
    clone_root.mkdir(parents=True, exist_ok=True)
    if target_dir.exists():
        return _normalize_existing_path(target_dir)
    completed = subprocess.run(
        [*git_cmd, str(target_dir)],
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


def resolve_workspace(payload: dict[str, Any], *, request_id: str | None = None) -> WorkspaceResolution:
    workspace_strategy = str(payload.get("workspace_strategy") or "").strip().lower()
    if workspace_strategy in _RUNTIME_REPO_STRATEGIES:
        return WorkspaceResolution(workspace=_REPO_ROOT.resolve(), source="runtime_repo")

    raw_workspace = str(payload.get("workspace") or "").strip()
    if raw_workspace:
        workspace = Path(raw_workspace).expanduser()
        if not workspace.exists():
            if payload.get("create_workspace"):
                workspace.mkdir(parents=True, exist_ok=True)
                return WorkspaceResolution(
                    workspace=_normalize_existing_path(workspace),
                    source="workspace",
                    created_workspace=True,
                )
            raise ValueError(f"workspace does not exist: {workspace}")
        return WorkspaceResolution(workspace=_normalize_existing_path(workspace), source="workspace")

    for key in ("file_path", "path", "project_path"):
        raw_path = str(payload.get(key) or "").strip()
        if not raw_path:
            continue
        candidate = Path(raw_path).expanduser()
        if not candidate.exists():
            raise ValueError(f"{key} does not exist: {candidate}")
        return WorkspaceResolution(workspace=_normalize_existing_path(candidate), source=key)

    repo_alias = str(payload.get("repo_alias") or "").strip()
    if repo_alias:
        registry_path = _resolve_alias_registry(payload)
        aliases = _load_repo_aliases(registry_path)
        target = aliases.get(repo_alias)
        if not target:
            raise ValueError(f"repo alias not found: {repo_alias}")
        workspace = Path(target).expanduser()
        if not workspace.exists():
            raise ValueError(f"repo alias target does not exist: {workspace}")
        return WorkspaceResolution(
            workspace=_normalize_existing_path(workspace),
            source="repo_alias",
            repo_alias=repo_alias,
        )

    git_url = str(payload.get("git_url") or "").strip()
    if git_url:
        clone_root = _resolve_clone_root(payload)
        workspace = _clone_repo(
            git_url,
            clone_root=clone_root,
            clone_name=str(payload.get("clone_name") or "").strip() or None,
        )
        return WorkspaceResolution(
            workspace=workspace,
            source="git_url",
            git_url=git_url,
            cloned_workspace=True,
        )

    if payload.get("allow_temp_workspace"):
        runs_root = _resolve_runs_root(payload)
        workspace = runs_root / _sanitize_name(request_id or "codex-run") / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        return WorkspaceResolution(
            workspace=workspace.resolve(),
            source="temp_workspace",
            created_workspace=True,
        )

    raise ValueError(
        "workspace could not be resolved; provide workspace, file_path, repo_alias, git_url, or allow_temp_workspace"
    )


__all__ = ["WorkspaceResolution", "resolve_workspace"]
