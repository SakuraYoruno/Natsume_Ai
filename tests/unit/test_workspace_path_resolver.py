from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from host_brain.agent_runtime.workspace_paths import (
    WORKSPACE_PATH_ESCAPE,
    WORKSPACE_PATH_INVALID,
    WorkspacePathViolation,
    resolve_workspace_path,
)


def test_resolver_returns_canonical_workspace_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    nested = workspace / "nested"
    nested.mkdir(parents=True)

    assert resolve_workspace_path(workspace, ".", allow_root=True) == workspace.resolve()
    assert resolve_workspace_path(workspace, "nested", must_exist=True) == nested.resolve()
    assert resolve_workspace_path(workspace, "new/output.txt") == (workspace / "new/output.txt").resolve()
    assert resolve_workspace_path(
        workspace,
        "output.txt",
        base=nested,
    ) == (nested / "output.txt").resolve()


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside.txt",
        r"..\outside.txt",
        r"C:\outside.txt",
        r"C:outside.txt",
        r"\\server\share\outside.txt",
        r"\\?\C:\outside.txt",
        r"\\.\PhysicalDrive0",
        "NUL.txt",
        "CONIN$",
        "COM¹.log",
        "dir/COM1.log",
    ],
)
def test_resolver_rejects_traversal_absolute_unc_and_device_paths(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()

    with pytest.raises(WorkspacePathViolation) as caught:
        resolve_workspace_path(workspace, unsafe_path)

    assert caught.value.reason_code in {WORKSPACE_PATH_ESCAPE, WORKSPACE_PATH_INVALID}


def test_resolver_rejects_absolute_path_even_when_it_is_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()

    with pytest.raises(WorkspacePathViolation) as caught:
        resolve_workspace_path(workspace, workspace / "inside.txt")

    assert caught.value.reason_code == WORKSPACE_PATH_INVALID


def test_resolver_closes_same_prefix_sibling_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    sibling = tmp_path / "work-escape"
    workspace.mkdir()
    sibling.mkdir()

    with pytest.raises(WorkspacePathViolation):
        resolve_workspace_path(workspace, f"../{sibling.name}/secret.txt")


def test_resolver_rejects_link_that_resolves_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = workspace / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        if os.name != "nt":
            pytest.skip(f"directory symlinks are unavailable on this platform: {exc}")
        junction = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            check=False,
        )
        if junction.returncode != 0:
            pytest.skip(f"directory links are unavailable on this platform: {exc}")

    with pytest.raises(WorkspacePathViolation) as caught:
        resolve_workspace_path(workspace, "escape/secret.txt", must_exist=True)

    assert caught.value.reason_code == WORKSPACE_PATH_ESCAPE
