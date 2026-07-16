from __future__ import annotations

from pathlib import Path

import pytest

import host_brain.agent_runtime.workspace_policy as workspace_policy_module
from host_brain.agent_runtime.agent_tools import (
    build_agent_tool_specs,
    execute_agent_tool,
    is_tool_allowed_for_run_mode,
)
from host_brain.agent_runtime.workspace_policy import (
    WORKSPACE_WRITE_GRANT_REQUIRED,
    WorkspacePolicy,
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


def test_read_only_inspect_can_read_runtime_repo() -> None:
    resolution = WorkspacePolicy().resolve(
        _order(run_mode="inspect", workspace={"strategy": "runtime_repo"})
    )

    assert resolution.source == "runtime_repo"
    assert resolution.path == workspace_policy_module.REPO_ROOT.resolve()


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
    tmp_path: Path,
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
    result = execute_agent_tool(
        tool_id="agent_run_python",
        args={"code": "from pathlib import Path; Path('escape').write_text('x')"},
        workspace=tmp_path,
        sandbox_enabled=True,
    )

    assert "fail-closed container isolation" in str(result.get("error"))
    assert not (tmp_path / "escape").exists()


def test_shell_is_not_advertised_or_executable_before_container_executor(
    tmp_path: Path,
) -> None:
    assert not is_tool_allowed_for_run_mode(
        "agent_run_shell",
        "worker",
        allowed_actions=["run_shell", "run_tests"],
        forbidden_actions=[],
    )

    result = execute_agent_tool(
        tool_id="agent_run_shell",
        args={"command": "python -c \"print('must not run')\""},
        workspace=tmp_path,
        sandbox_enabled=True,
    )

    assert "controlled shell execution is unavailable" in str(result.get("error"))


def test_browser_screenshot_cannot_persist_into_workspace_without_grant(
    tmp_path: Path,
) -> None:
    result = execute_agent_tool(
        tool_id="browser_screenshot",
        args={"save_path": "capture.png"},
        workspace=tmp_path,
    )

    assert "requires durable WorkspaceGrant" in str(result.get("error"))
    assert not (tmp_path / "capture.png").exists()
