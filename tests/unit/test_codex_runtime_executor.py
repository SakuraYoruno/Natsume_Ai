from __future__ import annotations

from pathlib import Path

from host_brain.codex_runtime import CodexRuntimeExecutor
from host_brain.codex_runtime.artifact_store import CodexArtifactStore


def test_codex_runtime_executor_blocks_write_payload_without_work_order() -> None:
    from host_brain.codex_runtime import execute_codex_runtime_payload

    result = execute_codex_runtime_payload({"run_mode": "worker", "task": "edit files"})

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "CODEX_WORKORDER_REQUIRED"


def test_codex_runtime_executor_degrades_when_cli_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("host_brain.codex_runtime.executor.shutil.which", lambda _name: None)
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
