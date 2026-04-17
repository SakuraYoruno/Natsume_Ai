from __future__ import annotations

import json
import subprocess

from host_brain.skills.project_locator import resolve_workspace


def test_resolve_workspace_uses_explicit_workspace(tmp_path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()

    resolved = resolve_workspace({"workspace": str(workspace)}, request_id="req-1")

    assert resolved.workspace == workspace.resolve()
    assert resolved.source == "workspace"


def test_resolve_workspace_uses_file_path_and_climbs_to_repo_root(tmp_path) -> None:
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    (repo / ".git").mkdir()
    file_path = src / "app.py"
    file_path.write_text("print('ok')", encoding="utf-8")

    resolved = resolve_workspace({"file_path": str(file_path)}, request_id="req-2")

    assert resolved.workspace == repo.resolve()
    assert resolved.source == "file_path"


def test_resolve_workspace_uses_repo_alias_registry(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    alias_file = tmp_path / "repo_aliases.json"
    alias_file.write_text(json.dumps({"edge": str(repo)}), encoding="utf-8")

    resolved = resolve_workspace(
        {"repo_alias": "edge", "repo_alias_file": str(alias_file)},
        request_id="req-3",
    )

    assert resolved.workspace == repo.resolve()
    assert resolved.source == "repo_alias"
    assert resolved.repo_alias == "edge"


def test_resolve_workspace_can_create_temp_workspace(tmp_path) -> None:
    resolved = resolve_workspace(
        {"allow_temp_workspace": True, "runs_root": str(tmp_path)},
        request_id="req-4",
    )

    assert resolved.source == "temp_workspace"
    assert resolved.created_workspace is True
    assert resolved.workspace.exists()


def test_resolve_workspace_supports_runtime_repo_strategy() -> None:
    resolved = resolve_workspace({"workspace_strategy": "runtime_repo"}, request_id="req-runtime")

    assert resolved.source == "runtime_repo"
    assert resolved.workspace.exists()


def test_resolve_workspace_can_clone_git_url(monkeypatch, tmp_path) -> None:
    cloned_target = tmp_path / "clones" / "demo"
    captured: dict[str, object] = {}

    def _fake_run(command, **kwargs):  # noqa: ANN001
        captured["command"] = list(command)
        cloned_target.mkdir(parents=True, exist_ok=True)
        (cloned_target / ".git").mkdir()
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("host_brain.skills.project_locator.subprocess.run", _fake_run)

    resolved = resolve_workspace(
        {
            "git_url": "https://github.com/example/demo.git",
            "clone_root": str(tmp_path / "clones"),
        },
        request_id="req-5",
    )

    assert captured["command"][:3] == ["git", "clone", "--depth"]
    assert resolved.source == "git_url"
    assert resolved.cloned_workspace is True
    assert resolved.workspace == cloned_target.resolve()
