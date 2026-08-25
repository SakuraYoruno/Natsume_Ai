from __future__ import annotations

from io import BytesIO
from pathlib import Path
import subprocess
import tarfile

import pytest

import host_brain.agent_runtime.workspace_policy as workspace_policy_module
from host_brain.agent_runtime.agent_tools import (
    build_agent_tool_specs,
    is_tool_allowed_for_run_mode,
)
from host_brain.agent_runtime.workspace_policy import (
    WORKSPACE_WRITE_GRANT_REQUIRED,
    WorkspacePolicy,
    WorkspacePolicyViolation,
)
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder


def _order(*, run_mode: str, workspace: dict[str, object]) -> AgentWorkOrder:
    can_write = run_mode in {"worker", "self_heal", "self_evolve"}
    return AgentWorkOrder.from_mapping(
        {
            "work_order_id": f"wo-w0-{run_mode}",
            "domain": "code",
            "run_mode": run_mode,
            "objective": "exercise the W0 workspace boundary",
            "workspace": workspace,
            "allowed_actions": ["read_files", "generate_report"]
            + (["write_files", "run_tests"] if can_write else []),
            "forbidden_actions": ["modify_secret_files"],
            "approval_policy": {"require_confirm": False},
        }
    )


def _tool_ids(*, run_mode: str, allowed: list[str], forbidden: list[str]) -> set[str]:
    return {
        spec.tool_id
        for spec in build_agent_tool_specs(
            run_mode=run_mode,
            allowed_actions=allowed,
            forbidden_actions=forbidden,
        )
    }


def test_read_only_inspect_uses_exact_ephemeral_runtime_repo_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "snapshot@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Snapshot Fixture"],
        check=True,
    )
    tracked = repository / "tracked.txt"
    tracked.write_text("accepted\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", "fixture"],
        check=True,
        capture_output=True,
    )
    tracked.write_text("dirty checkout\n", encoding="utf-8")
    (repository / "untracked.txt").write_text("private\n", encoding="utf-8")
    snapshot_root = tmp_path / "runs"
    monkeypatch.setattr(workspace_policy_module, "REPO_ROOT", repository)
    monkeypatch.setattr(
        workspace_policy_module,
        "DEFAULT_CODEX_RUNS_ROOT",
        snapshot_root,
    )

    resolution = WorkspacePolicy().resolve(
        _order(run_mode="inspect", workspace={"strategy": "runtime_repo"})
    )
    snapshot_path = resolution.path

    assert resolution.source == "runtime_repo_snapshot"
    assert resolution.source_ref and resolution.source_ref.startswith("git-commit://")
    assert snapshot_path != repository.resolve()
    assert snapshot_path.is_relative_to(snapshot_root.resolve())
    assert (snapshot_path / "tracked.txt").read_text(encoding="utf-8") == "accepted\n"
    assert not (snapshot_path / "untracked.txt").exists()
    assert not (snapshot_path / ".git").exists()

    resolution.cleanup()

    assert not snapshot_path.exists()


@pytest.mark.parametrize(
    ("member_name", "member_type"),
    [
        ("../escape.txt", tarfile.REGTYPE),
        ("/absolute.txt", tarfile.REGTYPE),
        ("unsafe-symlink", tarfile.SYMTYPE),
        ("unsafe-hardlink", tarfile.LNKTYPE),
    ],
)
def test_runtime_repo_snapshot_rejects_unsafe_archive_members(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    member_name: str,
    member_type: bytes,
) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    archive_buffer = BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w:") as bundle:
        member = tarfile.TarInfo(member_name)
        member.type = member_type
        if member_type == tarfile.REGTYPE:
            payload = b"escape"
            member.size = len(payload)
            bundle.addfile(member, BytesIO(payload))
        else:
            member.linkname = "../outside"
            bundle.addfile(member)
    archive_bytes = archive_buffer.getvalue()
    commit = "a" * 40

    def fake_git(
        command: list[str],
        **options: object,
    ) -> subprocess.CompletedProcess:
        if "rev-parse" in command:
            return subprocess.CompletedProcess(command, 0, f"{commit}\n", "")
        if "archive" in command:
            output = options.get("stdout")
            assert output is not None and hasattr(output, "write")
            output.write(archive_bytes)
            return subprocess.CompletedProcess(command, 0, None, b"")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(workspace_policy_module, "REPO_ROOT", repository)
    monkeypatch.setattr(
        workspace_policy_module,
        "DEFAULT_CODEX_RUNS_ROOT",
        tmp_path / "runs",
    )
    monkeypatch.setattr(workspace_policy_module.subprocess, "run", fake_git)

    with pytest.raises(
        WorkspacePolicyViolation,
        match="archive contains an unsafe member",
    ):
        WorkspacePolicy().resolve(
            _order(run_mode="inspect", workspace={"strategy": "runtime_repo"})
        )

    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "absolute.txt").exists()


@pytest.mark.parametrize(
    "workspace",
    [
        {"strategy": "runtime_repo"},
        {"strategy": "current_runtime_repo"},
        {"path": "C:/arbitrary/repo"},
        {"strategy": "git_url", "git_url": "https://example.invalid/repo.git"},
        {"strategy": "temp_workspace", "runs_root": "C:/custom/root"},
        {"strategy": "temp_workspace", "workspace_root": "C:/custom/root"},
    ],
)
def test_write_capable_order_rejects_ungranted_workspace_sources(
    workspace: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match=WORKSPACE_WRITE_GRANT_REQUIRED):
        WorkspacePolicy().resolve(_order(run_mode="worker", workspace=workspace))


def test_write_capable_order_uses_only_runtime_owned_temp_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_owned_root = tmp_path / "runtime-owned"
    monkeypatch.setattr(
        workspace_policy_module,
        "DEFAULT_CODEX_RUNS_ROOT",
        runtime_owned_root,
    )

    resolution = WorkspacePolicy().resolve(
        _order(run_mode="worker", workspace={"strategy": "temp_workspace"})
    )

    assert resolution.source == "temp_workspace"
    assert resolution.path == (runtime_owned_root / "wo-w0-worker" / "workspace").resolve()
    assert resolution.path.is_dir()


def test_direct_write_tool_alias_cannot_bypass_workspace_source_fuse() -> None:
    order = AgentWorkOrder.from_mapping(
        {
            "work_order_id": "wo-w0-translate-alias",
            "domain": "translation",
            "run_mode": "translate",
            "objective": "write translated output",
            "workspace": {"strategy": "runtime_repo"},
            "allowed_actions": ["AGENT_WRITE_FILE"],
            "forbidden_actions": ["modify_secret_files"],
        }
    )

    with pytest.raises(ValueError, match=WORKSPACE_WRITE_GRANT_REQUIRED):
        WorkspacePolicy().resolve(order)


def test_tool_policy_intersects_run_mode_allowed_and_forbidden_actions() -> None:
    tool_ids = _tool_ids(
        run_mode="worker",
        allowed=["read_files", "write_files", "run_tests", "generate_report"],
        forbidden=["write_files", "browser_click"],
    )

    assert {"agent_read_file", "agent_list_files", "agent_finish"} <= tool_ids
    assert "agent_run_shell" not in tool_ids
    assert "agent_write_file" not in tool_ids
    assert "browser_click" not in tool_ids
    assert "browser_navigate" not in tool_ids
    assert "agent_run_python" not in tool_ids


def test_tool_policy_does_not_let_actions_expand_run_mode() -> None:
    tool_ids = _tool_ids(
        run_mode="inspect",
        allowed=["read_files", "write_files", "use_playwright_browser", "generate_report"],
        forbidden=[],
    )

    assert tool_ids == {"agent_read_file", "agent_list_files", "agent_finish"}


def test_forbidden_write_action_removes_translate_write_tool() -> None:
    assert not is_tool_allowed_for_run_mode(
        "agent_write_file",
        "translate",
        allowed_actions=["read_files", "write_files", "generate_report"],
        forbidden_actions=["write_files"],
    )


def test_default_subprocess_python_is_not_a_controlled_workspace_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SANDBOX_ISOLATION_BACKEND", raising=False)
    monkeypatch.delenv("SANDBOX_REQUIRE_CONTAINER", raising=False)
    monkeypatch.delenv("Natsume_ENV", raising=False)

    assert not is_tool_allowed_for_run_mode(
        "agent_run_python",
        "audit",
        allowed_actions=["run_python"],
        forbidden_actions=[],
    )


def test_shell_is_not_advertised_before_container_executor() -> None:
    assert not is_tool_allowed_for_run_mode(
        "agent_run_shell",
        "worker",
        allowed_actions=["run_shell", "run_tests"],
        forbidden_actions=[],
    )
