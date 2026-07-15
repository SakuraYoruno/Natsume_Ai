"""Canonical workspace path boundary checks for agent-runtime I/O.

The resolver deliberately accepts only workspace-relative, portable paths.  It
also resolves existing links before enforcing the boundary, so a symlink or
junction inside the workspace cannot redirect an operation outside it.
"""

from __future__ import annotations

from os import PathLike, fspath
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import TypeAlias


WORKSPACE_ROOT_INVALID = "WORKSPACE_ROOT_INVALID"
WORKSPACE_PATH_INVALID = "WORKSPACE_PATH_INVALID"
WORKSPACE_PATH_ESCAPE = "WORKSPACE_PATH_ESCAPE"
WORKSPACE_PATH_NOT_FOUND = "WORKSPACE_PATH_NOT_FOUND"

WorkspacePath: TypeAlias = str | PathLike[str]

_WINDOWS_DEVICE_NAME = re.compile(
    r"^(?:(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|CLOCK\$)|"
    r"(?:COM|LPT)[1-9¹²³])(?:\..*)?$",
    flags=re.IGNORECASE,
)


class WorkspacePathViolation(PermissionError):
    """A stable, fail-closed workspace path rejection."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def normalize_workspace_root(workspace: WorkspacePath) -> Path:
    """Return an existing canonical workspace directory."""

    try:
        raw_workspace = fspath(workspace)
    except TypeError as exc:
        raise WorkspacePathViolation(
            WORKSPACE_ROOT_INVALID,
            "workspace must be a path",
        ) from exc
    if not isinstance(raw_workspace, str) or not raw_workspace or "\x00" in raw_workspace:
        raise WorkspacePathViolation(
            WORKSPACE_ROOT_INVALID,
            "workspace must be a non-empty text path",
        )
    try:
        resolved = Path(raw_workspace).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkspacePathViolation(
            WORKSPACE_ROOT_INVALID,
            "workspace must exist",
        ) from exc
    if not resolved.is_dir():
        raise WorkspacePathViolation(
            WORKSPACE_ROOT_INVALID,
            "workspace must be a directory",
        )
    return resolved


def resolve_workspace_path(
    workspace: WorkspacePath,
    candidate: WorkspacePath,
    *,
    base: WorkspacePath | None = None,
    must_exist: bool = False,
    allow_root: bool = False,
) -> Path:
    """Resolve a safe workspace-relative path.

    ``workspace`` is the authority boundary. ``base`` may select an existing
    directory inside that boundary for relative resolution, but is never a new
    boundary. Absolute, drive-relative, UNC/device, traversal, and Windows
    reserved-device paths are rejected before filesystem resolution.
    """

    root = normalize_workspace_root(workspace)
    base_path = _resolve_base(root, base)
    raw_candidate = _candidate_text(candidate)
    _validate_relative_path(raw_candidate, allow_root=allow_root)

    try:
        resolved = (base_path / raw_candidate).resolve(strict=must_exist)
    except FileNotFoundError as exc:
        raise WorkspacePathViolation(
            WORKSPACE_PATH_NOT_FOUND,
            f"path does not exist: {raw_candidate}",
        ) from exc
    except (OSError, RuntimeError) as exc:
        raise WorkspacePathViolation(
            WORKSPACE_PATH_INVALID,
            f"path cannot be resolved safely: {raw_candidate}",
        ) from exc

    _require_within_root(resolved, root, detail=f"path escapes workspace: {raw_candidate}")
    if resolved == root and not allow_root:
        raise WorkspacePathViolation(
            WORKSPACE_PATH_INVALID,
            "workspace root is not a valid target for this operation",
        )
    return resolved


def _resolve_base(root: Path, base: WorkspacePath | None) -> Path:
    if base is None:
        return root
    raw_base = _candidate_text(base)
    try:
        candidate = Path(raw_base)
        resolved = (
            candidate.resolve(strict=True)
            if candidate.is_absolute()
            else (root / candidate).resolve(strict=True)
        )
    except (OSError, RuntimeError) as exc:
        raise WorkspacePathViolation(
            WORKSPACE_PATH_INVALID,
            "path base must be an existing workspace directory",
        ) from exc
    _require_within_root(resolved, root, detail="path base escapes workspace")
    if not resolved.is_dir():
        raise WorkspacePathViolation(
            WORKSPACE_PATH_INVALID,
            "path base must be a directory",
        )
    return resolved


def _candidate_text(candidate: WorkspacePath) -> str:
    try:
        raw_candidate = fspath(candidate)
    except TypeError as exc:
        raise WorkspacePathViolation(
            WORKSPACE_PATH_INVALID,
            "candidate must be a path",
        ) from exc
    if not isinstance(raw_candidate, str):
        raise WorkspacePathViolation(
            WORKSPACE_PATH_INVALID,
            "candidate must be a text path",
        )
    if "\x00" in raw_candidate:
        raise WorkspacePathViolation(
            WORKSPACE_PATH_INVALID,
            "path cannot contain NUL bytes",
        )
    return raw_candidate


def _validate_relative_path(raw_path: str, *, allow_root: bool) -> None:
    if raw_path == "":
        if allow_root:
            return
        raise WorkspacePathViolation(WORKSPACE_PATH_INVALID, "path cannot be empty")

    posix = PurePosixPath(raw_path)
    windows = PureWindowsPath(raw_path)
    if (
        Path(raw_path).is_absolute()
        or posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or bool(windows.root)
    ):
        raise WorkspacePathViolation(
            WORKSPACE_PATH_INVALID,
            f"absolute, UNC, and device paths are forbidden: {raw_path}",
        )

    portable_parts = tuple(part for part in raw_path.replace("\\", "/").split("/") if part)
    if ".." in portable_parts or ".." in posix.parts or ".." in windows.parts:
        raise WorkspacePathViolation(
            WORKSPACE_PATH_ESCAPE,
            f"parent traversal is forbidden: {raw_path}",
        )

    for part in portable_parts:
        if part == ".":
            continue
        normalized = part.rstrip(" .")
        if not normalized or normalized != part:
            raise WorkspacePathViolation(
                WORKSPACE_PATH_INVALID,
                f"empty or ambiguous path component is forbidden: {raw_path}",
            )
        if ":" in normalized or _WINDOWS_DEVICE_NAME.fullmatch(normalized):
            raise WorkspacePathViolation(
                WORKSPACE_PATH_INVALID,
                f"Windows device or stream path is forbidden: {raw_path}",
            )


def _require_within_root(path: Path, root: Path, *, detail: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise WorkspacePathViolation(WORKSPACE_PATH_ESCAPE, detail) from exc


__all__ = [
    "WORKSPACE_PATH_ESCAPE",
    "WORKSPACE_PATH_INVALID",
    "WORKSPACE_PATH_NOT_FOUND",
    "WORKSPACE_ROOT_INVALID",
    "WorkspacePathViolation",
    "normalize_workspace_root",
    "resolve_workspace_path",
]
