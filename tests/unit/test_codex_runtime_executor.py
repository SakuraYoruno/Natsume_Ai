from __future__ import annotations

from pathlib import Path

from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.result_contract import AgentResult


def _inspect_order(tmp_path: Path, *, work_order_id: str) -> dict[str, object]:
    return {
        "work_order_id": work_order_id,
        "domain": "code",
        "run_mode": "inspect",
        "objective": "inspect something",
        "workspace": {"strategy": "temp_workspace", "runs_root": str(tmp_path / "workspaces")},
        "allowed_actions": ["read_files", "generate_report"],
        "forbidden_actions": ["write_files", "modify_secret_files"],
        "review_policy": {"review_required": False},
    }


def test_codex_runtime_executor_blocks_write_payload_without_work_order() -> None:
    from host_brain.agent_runtime.executor import execute_agent_runtime_payload

    result = execute_agent_runtime_payload({"run_mode": "worker", "task": "edit files"})

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "AGENT_WORKORDER_REQUIRED"


def test_codex_runtime_executor_blocks_unconfirmed_worker(tmp_path: Path) -> None:
    result = AgentRuntimeExecutor(artifact_store=AgentArtifactStore(tmp_path / "runs")).execute(
        {
            "work_order_id": "wo-unconfirmed",
            "domain": "code",
            "run_mode": "worker",
            "objective": "edit something",
            "workspace": {"strategy": "temp_workspace", "runs_root": str(tmp_path / "workspaces")},
            "allowed_actions": ["read_files", "write_files"],
            "forbidden_actions": ["modify_secret_files"],
            "approval_policy": {"require_confirm": True, "confirmed": False},
        }
    )

    assert result["status"] == "BLOCKED"
    assert result["review_required"] is True


def test_runtime_uses_canonical_agent_executor_with_default_environment(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, Path]] = []

    class FakeAgentExecutor:
        def execute(self, order, *, workspace: Path) -> AgentResult:
            calls.append((order.work_order_id, workspace))
            return AgentResult(
                status="EXECUTED",
                work_order_id=order.work_order_id,
                summary="done",
                result_text="canonical result",
                review_required=False,
                metadata={"agent_trace": "preserved"},
            )

    git_states = iter(
        [
            {"changed_files": ["baseline.py"], "git_diff_stat": None, "git_status": " M baseline.py", "status_map": {"baseline.py": " M"}},
            {
                "changed_files": ["baseline.py", "created.py"],
                "git_diff_stat": None,
                "git_status": " M baseline.py\n?? created.py",
                "status_map": {"baseline.py": " M", "created.py": "??"},
            },
        ]
    )
    monkeypatch.delenv("HOST_NATSUME_AGENT_ENABLED", raising=False)
    monkeypatch.setattr("host_brain.agent_runtime.executor.AgentExecutor", FakeAgentExecutor)
    monkeypatch.setattr("host_brain.agent_runtime.executor._collect_git_summary", lambda _workspace: next(git_states))

    result = AgentRuntimeExecutor(artifact_store=AgentArtifactStore(tmp_path / "runs")).execute(
        _inspect_order(tmp_path, work_order_id="wo-canonical")
    )

    assert len(calls) == 1
    assert calls[0][0] == "wo-canonical"
    assert calls[0][1].is_absolute()
    assert result["status"] == "EXECUTED"
    assert result["result_text"] == "canonical result"
    assert result["changed_files"] == ["created.py"]
    assert result["metadata"]["execution_authority"] == "canonical_agent_executor"
    assert result["metadata"]["agent_trace"] == "preserved"
    run_dir = Path(result["artifacts_dir"])
    for artifact in ("work_order.json", "prompt.txt", "changed_files.json", "verification.json", "metadata.json", "result.json"):
        assert run_dir.joinpath(artifact).exists()


def test_runtime_converts_canonical_agent_crash_to_failed_result(monkeypatch, tmp_path: Path) -> None:
    class CrashingAgentExecutor:
        def execute(self, _order, *, workspace: Path) -> AgentResult:
            raise RuntimeError(f"boom in {workspace.name}")

    monkeypatch.setattr("host_brain.agent_runtime.executor.AgentExecutor", CrashingAgentExecutor)
    monkeypatch.setattr(
        "host_brain.agent_runtime.executor._collect_git_summary",
        lambda _workspace: {"changed_files": [], "git_diff_stat": None, "git_status": None, "status_map": {}},
    )

    result = AgentRuntimeExecutor(artifact_store=AgentArtifactStore(tmp_path / "runs")).execute(
        _inspect_order(tmp_path, work_order_id="wo-crash")
    )

    assert result["status"] == "FAILED"
    assert result["reason_code"] == "AGENT_ERROR"
    assert "boom in" in result["detail"]
    assert result["metadata"]["execution_authority"] == "canonical_agent_executor"
    assert Path(result["artifacts_dir"]).joinpath("result.json").exists()


def test_runtime_source_has_no_independent_codex_cli_authority() -> None:
    import host_brain.agent_runtime.executor as executor_module

    source = Path(executor_module.__file__).read_text(encoding="utf-8")
    forbidden = (
        "agent_enabled",
        "HOST_NATSUME_AGENT_ENABLED",
        "codex_cli_provider",
        "codex_cli_command",
        "build_codex_cli_env",
        "subprocess.Popen",
        "Popen(",
        "_build_codex_command",
        "_build_codex_subprocess_env",
        "CODEX_EXEC",
    )

    assert "AgentExecutor().execute" in source
    assert all(symbol not in source for symbol in forbidden)
