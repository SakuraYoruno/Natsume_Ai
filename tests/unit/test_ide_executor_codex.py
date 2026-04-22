from __future__ import annotations

import io
import subprocess
from pathlib import Path

from host_brain.skills.impl.ide_executor import (
    CODEX_CLI_UNAVAILABLE,
    STATUS_DEGRADED,
    STATUS_EXECUTED,
    STATUS_REJECTED,
    _build_codex_skill_prompt,
    _coerce_codex_timeout_seconds,
    _diff_git_summary,
    _resolve_direct_codegen_runtime,
    execute_codex_codegen,
)


def test_execute_codex_codegen_degrades_when_cli_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("host_brain.skills.impl.ide_executor.shutil.which", lambda _name: None)

    result = execute_codex_codegen({"task": "write a helper", "workspace": str(tmp_path)})

    assert result["status"] == STATUS_DEGRADED
    assert result["reason_code"] == CODEX_CLI_UNAVAILABLE


def test_execute_codex_codegen_uses_direct_codegen_path_for_temp_workspace_inspect(monkeypatch) -> None:
    monkeypatch.setenv("HOST_CODEX_INSPECT_DIRECT_LLM_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setattr("host_brain.skills.impl.ide_executor.shutil.which", lambda _name: None)
    monkeypatch.setattr(
        "host_brain.skills.impl.ide_executor._execute_direct_codegen_fast_path",
        lambda *, prompt, timeout_seconds: {
            "result_text": "def add(a, b):\n    return a + b",
            "backend": "direct_openai",
            "model_id": "gpt-4o-mini",
            "base_url": None,
        },
    )

    result = execute_codex_codegen(
        {
            "task": "write an add helper",
            "allow_temp_workspace": True,
            "request_id": "req-direct-fast",
        }
    )

    assert result["status"] == STATUS_EXECUTED
    assert result["prompt_profile"] == "inspect_direct_openai_codegen"
    assert result["execution_backend"] == "direct_openai"
    assert result["model_id"] == "gpt-4o-mini"
    assert "def add(a, b)" in str(result["result_text"])
    assert Path(str(result["artifacts_dir"])).exists()


def test_resolve_direct_codegen_runtime_uses_host_config_resolution(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)

    class _Config:
        llm_api_key = "resolved-openai-key"

    class _LoadResult:
        success = True
        config = _Config()

    monkeypatch.setattr(
        "host_brain.app.config.load_host_config",
        lambda local_overrides=None: _LoadResult(),
    )

    runtime = _resolve_direct_codegen_runtime()
    assert runtime is not None
    assert runtime["api_key"] == "resolved-openai-key"
    assert runtime["backend"] == "direct_openai"


def test_resolve_direct_codegen_runtime_falls_back_to_gemini_pool(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.setenv("HOST_GEMINI_MODEL_ID", "gemini-2.5-flash-lite")
    monkeypatch.setattr(
        "host_brain.app.config.load_host_config",
        lambda local_overrides=None: type("_LoadResult", (), {"success": False, "config": None})(),
    )
    monkeypatch.setattr(
        "host_brain.app.model_router.resolve_gemini_key_pool",
        lambda preferred_api_key=None: ["gemini-fast-key"],
    )

    runtime = _resolve_direct_codegen_runtime()
    assert runtime is not None
    assert runtime["provider"] == "gemini"
    assert runtime["api_key"] == "gemini-fast-key"
    assert runtime["model_id"] == "gemini-2.5-flash-lite"
    assert runtime["backend"] == "direct_gemini_openai_compat"


def test_coerce_codex_timeout_seconds_uses_run_mode_defaults() -> None:
    assert _coerce_codex_timeout_seconds({}, run_mode="inspect") == 120
    assert _coerce_codex_timeout_seconds({}, run_mode="worker") == 300
    assert _coerce_codex_timeout_seconds({}, run_mode="self_heal") == 600
    assert _coerce_codex_timeout_seconds({}, run_mode="self_evolve") == 900


def test_coerce_codex_timeout_seconds_honors_and_clamps_override() -> None:
    assert _coerce_codex_timeout_seconds({"timeout_seconds": 5}, run_mode="self_evolve") == 15
    assert _coerce_codex_timeout_seconds({"timeout_seconds": 1200}, run_mode="inspect") == 1200
    assert _coerce_codex_timeout_seconds({"timeout_seconds": 9_999}, run_mode="inspect") == 1800


def test_execute_codex_codegen_returns_captured_output(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class _FakeProc:
        def __init__(self, command, **kwargs):  # noqa: ANN001
            captured["command"] = list(command)
            captured["kwargs"] = dict(kwargs)
            self.pid = 4321
            self.returncode = 0

        def communicate(self, input=None, timeout=None):  # noqa: ANN001
            captured["input"] = input
            captured["timeout"] = timeout
            output_index = captured["command"].index("-o") + 1
            output_path = captured["command"][output_index]
            with open(output_path, "w", encoding="utf-8") as handle:
                handle.write("```python\nprint('ok')\n```")
            return ('{"type":"thread.started","thread_id":"thread-xyz"}\n', "")

        def poll(self):
            return 0

    monkeypatch.setattr("host_brain.skills.impl.ide_executor.shutil.which", lambda _name: "codex")
    monkeypatch.setattr("host_brain.skills.impl.ide_executor.subprocess.Popen", _FakeProc)
    monkeypatch.setattr(
        "host_brain.skills.impl.ide_executor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args, returncode=0),
    )

    result = execute_codex_codegen(
        {
            "task": "write a fibonacci helper",
            "mode": "codegen",
            "workspace": str(tmp_path),
            "request_id": "req-codex-exec",
        }
    )

    assert result["status"] == STATUS_EXECUTED
    assert result["mode"] == "codegen"
    assert result["run_mode"] == "inspect"
    assert result["prompt_profile"] == "inspect_light_codegen"
    assert result["prompt_chars"] > 0
    assert result["thread_id"] == "thread-xyz"
    assert "print('ok')" in str(result["result_text"])
    assert captured["command"][0] == "codex"
    assert "--sandbox" in captured["command"]
    assert "--json" in captured["command"]
    assert "--ephemeral" not in captured["command"]
    assert "write a fibonacci helper" in str(captured["input"])
    assert Path(str(result["artifacts_dir"])).exists()
    assert Path(str(result["artifacts_dir"])) .joinpath("last_message.txt").exists()


def test_execute_codex_codegen_rejects_when_workspace_cannot_be_resolved(monkeypatch) -> None:
    monkeypatch.setattr("host_brain.skills.impl.ide_executor.shutil.which", lambda _name: "codex")

    result = execute_codex_codegen({"task": "write a helper"})

    assert result["status"] == STATUS_REJECTED
    assert result["reason_code"] == "IDE_WORKSPACE_UNRESOLVED"


def test_execute_codex_codegen_uses_resume_command(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class _FakeProc:
        def __init__(self, command, **kwargs):  # noqa: ANN001
            captured["command"] = list(command)
            self.pid = 9876
            self.returncode = 0

        def communicate(self, input=None, timeout=None):  # noqa: ANN001
            output_index = captured["command"].index("-o") + 1
            output_path = captured["command"][output_index]
            with open(output_path, "w", encoding="utf-8") as handle:
                handle.write("done")
            return ("", "")

        def poll(self):
            return 0

    monkeypatch.setattr("host_brain.skills.impl.ide_executor.shutil.which", lambda _name: "codex")
    monkeypatch.setattr("host_brain.skills.impl.ide_executor.subprocess.Popen", _FakeProc)

    result = execute_codex_codegen(
        {
            "task": "continue the fix",
            "workspace": str(tmp_path),
            "resume_thread_id": "thread-prev-001",
            "run_mode": "worker",
        }
    )

    assert result["status"] == STATUS_EXECUTED
    assert captured["command"][:3] == ["codex", "exec", "resume"]
    assert "thread-prev-001" in captured["command"]
    assert "-C" not in captured["command"]
    assert result["resumed"] is True


def test_execute_codex_codegen_runs_optional_python_verification(monkeypatch, tmp_path) -> None:
    class _FakeProc:
        def __init__(self, command, **kwargs):  # noqa: ANN001
            self.command = list(command)
            self.pid = 2222
            self.returncode = 0

        def communicate(self, input=None, timeout=None):  # noqa: ANN001
            output_index = self.command.index("-o") + 1
            output_path = self.command[output_index]
            with open(output_path, "w", encoding="utf-8") as handle:
                handle.write("ok")
            return ("", "")

        def poll(self):
            return 0

    class _SandboxResult:
        status = "OK"
        reason_code = None
        exit_code = 0
        stdout = "verified"
        stderr = ""
        duration_ms = 12
        evidence_ref = "sandbox://sbx-1"
        code_digest = "abc"

    monkeypatch.setattr("host_brain.skills.impl.ide_executor.shutil.which", lambda _name: "codex")
    monkeypatch.setattr("host_brain.skills.impl.ide_executor.subprocess.Popen", _FakeProc)
    monkeypatch.setattr(
        "host_brain.skills.impl.ide_executor.SandboxExecutor.execute",
        lambda self, code, request_id=None, intent_id=None, language="python": _SandboxResult(),
    )

    result = execute_codex_codegen(
        {
            "task": "write helper",
            "workspace": str(tmp_path),
            "verification_code": "print('verify')",
            "run_mode": "worker",
        }
    )

    assert result["status"] == STATUS_EXECUTED
    assert result["verification"]["status"] == "OK"
    assert result["verification"]["stdout"] == "verified"


def test_build_codex_skill_prompt_uses_lightweight_inspect_profile(tmp_path) -> None:
    prompt = _build_codex_skill_prompt(
        task="Write an add helper",
        context="x" * 8_000,
        mode="codegen",
        run_mode="inspect",
        workspace=tmp_path,
    )

    assert "Read-only task. Do not modify files." in prompt
    assert "If code is requested, return only the code" in prompt
    assert "Do not explicitly invoke `powershell` or `cmd`" in prompt
    assert "Avoid shell/tool calls unless the provided context is insufficient." in prompt
    assert "Prefer targeted ripgrep/file reads." in prompt
    assert "You are fulfilling a coding skill request for the Natsume host runtime." not in prompt
    assert len(prompt) < 5_500


def test_build_codex_skill_prompt_self_heal_guides_targeted_search(tmp_path) -> None:
    prompt = _build_codex_skill_prompt(
        task="repair readiness degradation",
        context="reason_code=SERVICE_UNAVAILABLE_EDGE_RPC",
        mode="analysis",
        run_mode="self_heal",
        workspace=tmp_path,
    )

    assert "Prioritize diagnosis, smallest viable repair, and regression prevention." in prompt
    assert "Start from the affected components and reason code provided in context before widening your search." in prompt
    assert "If the evidence indicates an external dependency outage or unreachable endpoint" in prompt
    assert "Prefer runtime source files under `host_brain/app`, `host_brain/integrations`, `host_brain/channels`, and `scripts/runtime`" in prompt
    assert "Avoid nested shell invocations" in prompt
    assert "Prefer targeted ripgrep/file reads." in prompt


def test_build_codex_skill_prompt_inspect_analysis_forbids_shell(tmp_path) -> None:
    prompt = _build_codex_skill_prompt(
        task="diagnose readiness degradation",
        context="priority file excerpts here",
        mode="analysis",
        run_mode="inspect",
        workspace=tmp_path,
    )

    assert "This is an analysis-only inspect task. Do not use shell commands or external tools." in prompt
    assert "Base the answer exclusively on the provided context and file excerpts." in prompt


def test_diff_git_summary_only_reports_delta_from_dirty_baseline() -> None:
    result = _diff_git_summary(
        before={
            "status_map": {
                "existing.py": " M",
                "renamed_new.py": "R ",
            }
        },
        after={
            "status_map": {
                "existing.py": " M",
                "renamed_new.py": "R ",
                "fresh_fix.py": " M",
                "new_file.py": "??",
            },
            "git_diff_stat": " 2 files changed, 10 insertions(+)",
            "git_status": " M existing.py\n M fresh_fix.py\n?? new_file.py",
        },
    )

    assert result["workspace_dirty_baseline"] is True
    assert result["baseline_changed_files"] == ["existing.py", "renamed_new.py"]
    assert result["current_changed_files"] == ["existing.py", "fresh_fix.py", "new_file.py", "renamed_new.py"]
    assert result["changed_files"] == ["fresh_fix.py", "new_file.py"]
    assert result["git_diff_stat"] is None


def test_execute_codex_codegen_inspect_completes_from_last_message_capture(monkeypatch, tmp_path) -> None:
    class _FakeProc:
        def __init__(self, command, **kwargs):  # noqa: ANN001
            self.command = list(command)
            self.pid = 3456
            self.returncode = None
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")
            self._poll_count = 0
            output_index = self.command.index("-o") + 1
            self._output_path = Path(self.command[output_index])

        def poll(self):
            self._poll_count += 1
            if self._poll_count == 2:
                self._output_path.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
            return self.returncode

    def _fake_terminate_process_tree(proc):  # noqa: ANN001
        proc.returncode = 0

    monkeypatch.setattr("host_brain.skills.impl.ide_executor.shutil.which", lambda _name: "codex")
    monkeypatch.setattr("host_brain.skills.impl.ide_executor.subprocess.Popen", _FakeProc)
    monkeypatch.setattr("host_brain.skills.impl.ide_executor._terminate_process_tree", _fake_terminate_process_tree)

    result = execute_codex_codegen(
        {
            "task": "write an add helper",
            "workspace": str(tmp_path),
            "run_mode": "inspect",
            "timeout_seconds": 15,
        }
    )

    assert result["status"] == STATUS_EXECUTED
    assert result["completed_from_output"] is True
    assert result["timed_out"] is False
    assert result["detail"] == "codex exec completed from last_message capture"
    assert "def add(a, b)" in str(result["result_text"])


def test_execute_codex_codegen_collects_runtime_diagnostics(monkeypatch, tmp_path) -> None:
    class _FakeProc:
        def __init__(self, command, **kwargs):  # noqa: ANN001
            self.command = list(command)
            self.pid = 7788
            self.returncode = 0
            self.stdin = io.StringIO()
            self.stdout = io.StringIO('{"type":"thread.started","thread_id":"thread-diag"}\n')
            self.stderr = io.StringIO(
                "ERROR: Reconnecting... 2/5\n"
                "WARN codex_core::client: falling back to HTTP\n"
                "WARN plugins list failed with status 403 Forbidden\n"
            )
            output_index = self.command.index("-o") + 1
            Path(self.command[output_index]).write_text("ok", encoding="utf-8")

        def poll(self):
            return self.returncode

    monkeypatch.setattr("host_brain.skills.impl.ide_executor.shutil.which", lambda _name: "codex")
    monkeypatch.setattr("host_brain.skills.impl.ide_executor.subprocess.Popen", _FakeProc)

    result = execute_codex_codegen(
        {
            "task": "write helper",
            "workspace": str(tmp_path),
            "run_mode": "inspect",
        }
    )

    assert result["status"] == STATUS_EXECUTED
    assert result["thread_id"] == "thread-diag"
    assert result["runtime_diagnostics"]["reconnect_count"] == 1
    assert result["runtime_diagnostics"]["transport_fallback_detected"] is True
    assert result["runtime_diagnostics"]["plugin_sync_forbidden_detected"] is True


def test_execute_codex_codegen_does_not_treat_json_events_as_result_text(monkeypatch, tmp_path) -> None:
    class _FakeProc:
        def __init__(self, command, **kwargs):  # noqa: ANN001
            self.command = list(command)
            self.pid = 9988
            self.returncode = 0
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(
                '{"type":"thread.started","thread_id":"thread-json-only"}\n'
                '{"type":"turn.started"}\n'
                '{"type":"error","message":"Reconnecting... 2/5"}\n'
            )
            self.stderr = io.StringIO("")

        def poll(self):
            return self.returncode

    monkeypatch.setattr("host_brain.skills.impl.ide_executor.shutil.which", lambda _name: "codex")
    monkeypatch.setattr("host_brain.skills.impl.ide_executor.subprocess.Popen", _FakeProc)

    result = execute_codex_codegen(
        {
            "task": "write helper",
            "workspace": str(tmp_path),
            "run_mode": "inspect",
        }
    )

    assert result["status"] == STATUS_REJECTED
    assert result["reason_code"] == "IDE_EXEC_ERROR"
    assert "thread.started" not in str(result["detail"])
