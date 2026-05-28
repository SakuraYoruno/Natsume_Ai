from __future__ import annotations

from pathlib import Path
import subprocess

from host_brain.codex_runtime import CodexRuntimeExecutor
from host_brain.codex_runtime.artifact_store import CodexArtifactStore
from host_brain.codex_runtime.executor import _build_codex_command, _build_codex_subprocess_env
from host_brain.codex_runtime.work_order import CodexWorkOrder


def test_codex_runtime_executor_blocks_write_payload_without_work_order() -> None:
    from host_brain.codex_runtime import execute_codex_runtime_payload

    result = execute_codex_runtime_payload({"run_mode": "worker", "task": "edit files"})

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "CODEX_WORKORDER_REQUIRED"


def test_codex_runtime_executor_degrades_when_cli_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("host_brain.codex_runtime.executor.codex_cli_command", lambda: None)
    result = CodexRuntimeExecutor(artifact_store=CodexArtifactStore(tmp_path)).execute(
        {
            "work_order_id": "wo-cli-missing",
            "domain": "code",
            "run_mode": "inspect",
            "objective": "inspect something",
            "workspace": {"strategy": "temp_workspace", "runs_root": str(tmp_path)},
            "allowed_actions": ["read_files", "generate_report"],
            "forbidden_actions": ["write_files", "modify_secret_files"],
            "review_policy": {"review_required": False},
        }
    )

    assert result["status"] == "DEGRADED"
    assert result["reason_code"] == "CODEX_CLI_UNAVAILABLE"
    assert Path(result["artifacts_dir"]).joinpath("work_order.json").exists()
    assert Path(result["artifacts_dir"]).joinpath("result.json").exists()


def test_codex_runtime_executor_blocks_unconfirmed_worker(tmp_path) -> None:
    result = CodexRuntimeExecutor(artifact_store=CodexArtifactStore(tmp_path)).execute(
        {
            "work_order_id": "wo-unconfirmed",
            "domain": "code",
            "run_mode": "worker",
            "objective": "edit something",
            "workspace": {"strategy": "temp_workspace", "runs_root": str(tmp_path)},
            "allowed_actions": ["read_files", "write_files"],
            "forbidden_actions": ["modify_secret_files"],
            "approval_policy": {"require_confirm": True, "confirmed": False},
        }
    )

    assert result["status"] == "BLOCKED"
    assert result["review_required"] is True


def test_codex_runtime_command_honors_model_override(tmp_path) -> None:
    order = CodexWorkOrder.from_mapping(
        {
            "work_order_id": "wo-model",
            "domain": "code",
            "run_mode": "inspect",
            "objective": "inspect",
            "workspace": {"strategy": "temp_workspace", "runs_root": str(tmp_path)},
            "metadata": {"codex_model": "gpt-5.4-mini", "codex_reasoning_effort": "low"},
        }
    )

    command = _build_codex_command(
        codex_cmd="codex",
        order=order,
        workspace=tmp_path,
        last_message_path=tmp_path / "last.txt",
        sandbox_mode="read-only",
    )

    assert command[:4] == ["codex", "exec", "-m", "gpt-5.4-mini"]
    assert 'model_reasoning_effort="low"' in command


def test_codex_runtime_env_uses_run_local_temp_dir(monkeypatch, tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    isolated_home = tmp_path / "codex-home"
    monkeypatch.setenv("HOST_CODEX_CLI_HOME", str(isolated_home))
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")

    env = _build_codex_subprocess_env(run_dir=run_dir)

    temp_dir = run_dir / ".codex_tmp"
    assert temp_dir.exists()
    assert env["TEMP"] == str(temp_dir.resolve())
    assert env["TMP"] == str(temp_dir.resolve())
    assert env["TMPDIR"] == str(temp_dir.resolve())
    assert env["CODEX_HOME"] == str(isolated_home.resolve())
    assert "OPENAI_API_KEY" not in env
    assert env.get("HOME") or env.get("USERPROFILE")


def test_codex_runtime_passes_absolute_last_message_path(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    captured: dict[str, list[str]] = {}

    class FakePopen:
        pid = 12345
        returncode = 0

        def __init__(self, command, *_args, **_kwargs) -> None:
            captured["command"] = list(command)

        def communicate(self, *args, **kwargs):
            command = captured["command"]
            output_path = Path(command[command.index("-o") + 1])
            assert output_path.is_absolute()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("done", encoding="utf-8")
            return '{"type":"thread.started","thread_id":"thread-ok"}\n', ""

    monkeypatch.setattr("host_brain.codex_runtime.executor.codex_cli_command", lambda: "codex")
    monkeypatch.setattr("host_brain.codex_runtime.executor.subprocess.Popen", FakePopen)
    monkeypatch.setattr("host_brain.codex_runtime.executor._collect_git_summary", lambda _workspace: {"changed_files": [], "status_map": {}})
    monkeypatch.setattr("host_brain.codex_runtime.executor._diff_git_summary", lambda _before, _after: {"changed_files": []})

    result = CodexRuntimeExecutor(artifact_store=CodexArtifactStore(Path("relative_runs"))).execute(
        {
            "work_order_id": "wo-absolute-output",
            "domain": "code",
            "run_mode": "inspect",
            "objective": "inspect something",
            "workspace": {"strategy": "temp_workspace", "runs_root": str(tmp_path / "workspaces")},
            "allowed_actions": ["read_files", "generate_report"],
            "forbidden_actions": ["write_files", "modify_secret_files"],
        }
    )

    assert result["status"] == "EXECUTED"
    assert result["result_text"] == "done"
    assert result["thread_id"] == "thread-ok"


def test_codex_runtime_timeout_persists_partial_logs(monkeypatch, tmp_path) -> None:
    class FakePopen:
        pid = 12345
        returncode = None

        def __init__(self, *_args, **_kwargs) -> None:
            self.calls = 0

        def communicate(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(
                    cmd="codex",
                    timeout=15,
                    output='{"type":"thread.started","thread_id":"thread-timeout"}\n',
                    stderr="partial stderr",
                )
            self.returncode = -9
            return "", "after kill stderr"

    monkeypatch.setattr("host_brain.codex_runtime.executor.codex_cli_command", lambda: "codex")
    monkeypatch.setattr("host_brain.codex_runtime.executor.subprocess.Popen", FakePopen)
    monkeypatch.setattr("host_brain.codex_runtime.executor._terminate_process_tree", lambda _proc: None)
    monkeypatch.setattr("host_brain.codex_runtime.executor._collect_git_summary", lambda _workspace: {"changed_files": [], "status_map": {}})
    monkeypatch.setattr("host_brain.codex_runtime.executor._diff_git_summary", lambda _before, _after: {"changed_files": []})

    result = CodexRuntimeExecutor(artifact_store=CodexArtifactStore(tmp_path)).execute(
        {
            "work_order_id": "wo-timeout",
            "domain": "code",
            "run_mode": "inspect",
            "objective": "inspect something",
            "workspace": {"strategy": "temp_workspace", "runs_root": str(tmp_path)},
            "allowed_actions": ["read_files", "generate_report"],
            "forbidden_actions": ["write_files", "modify_secret_files"],
            "metadata": {"timeout_seconds": 15, "codex_model": "gpt-5.4-mini"},
        }
    )

    run_dir = Path(result["artifacts_dir"])
    assert result["status"] == "DEGRADED"
    assert result["reason_code"] == "CODEX_EXEC_TIMEOUT"
    assert result["thread_id"] == "thread-timeout"
    assert "partial stderr" in run_dir.joinpath("stderr.log").read_text(encoding="utf-8")
    assert run_dir.joinpath("events.jsonl").read_text(encoding="utf-8").strip().startswith('{"type":"thread.started"')
