from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import host_brain.agent_runtime.executor as executor_module
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder


def test_agent_executor_failure_never_persists_or_logs_raw_detail(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:  # noqa: ANN001
    secret = "provider-password=must-not-enter-attempt-artifacts"

    class FailingAgentExecutor:
        def __init__(self, **_dependencies: Any) -> None:
            pass

        def execute(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError(secret)

    monkeypatch.setattr(executor_module, "AgentExecutor", FailingAgentExecutor)
    monkeypatch.setattr(
        executor_module,
        "_collect_git_summary",
        lambda _workspace: {
            "changed_files": [],
            "git_diff_stat": None,
            "git_status": None,
            "status_map": {},
        },
    )
    runs_root = tmp_path / "runs"
    caplog.set_level(logging.WARNING, logger=executor_module.__name__)
    result = AgentRuntimeExecutor(
        artifact_store=AgentArtifactStore(runs_root),
        model_attempt_service=object(),
        prompt_registry=object(),
    ).execute(
        AgentWorkOrder(
            work_order_id="work-order-agent-redaction",
            domain="code",
            run_mode="inspect",
            objective="verify opaque failure persistence",
            workspace={
                "strategy": "temp_workspace",
                "runs_root": str(tmp_path / "workspaces"),
            },
            allowed_actions=["read_files", "generate_report"],
            review_policy={"review_required": False},
        )
    )

    assert result["status"] == "FAILED"
    assert secret not in json.dumps(result, ensure_ascii=False)
    assert secret not in caplog.text
    assert "error_type=builtins.RuntimeError" in result["detail"]
    persisted = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in runs_root.rglob("*")
        if path.is_file()
    )
    assert secret not in persisted
    assert "error_type=builtins.RuntimeError" in persisted
