from __future__ import annotations

from pathlib import Path
import subprocess

import host_brain.agent_runtime.workspace_policy as workspace_policy_module
from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.result_contract import AgentResult
from host_brain.skills.agent_runtime_registry import build_agent_runtime_skill_registry


def test_codex_workplane_runs_from_skill_work_order_and_writes_artifacts(monkeypatch, tmp_path) -> None:
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
    (repository / "tracked.txt").write_text("accepted\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", "fixture"],
        check=True,
        capture_output=True,
    )
    snapshot_root = tmp_path / "snapshots"
    monkeypatch.setattr(workspace_policy_module, "REPO_ROOT", repository)
    monkeypatch.setattr(
        workspace_policy_module,
        "DEFAULT_CODEX_RUNS_ROOT",
        snapshot_root,
    )
    observed_workspace: Path | None = None

    class CanonicalFixtureExecutor:
        def __init__(self, **dependencies: object) -> None:
            assert dependencies["model_attempt_service"] is model_attempt_service
            assert dependencies["prompt_registry"] is prompt_registry

        def execute(
            self,
            order,  # noqa: ANN001
            *,
            workspace: Path,
            **_: object,
        ) -> AgentResult:
            nonlocal observed_workspace
            observed_workspace = workspace
            assert workspace != repository.resolve()
            assert (workspace / "tracked.txt").read_text(encoding="utf-8") == "accepted\n"
            (workspace / "tracked.txt").write_text(
                "attempted mutation\n",
                encoding="utf-8",
            )
            assert (repository / "tracked.txt").read_text(encoding="utf-8") == "accepted\n"
            return AgentResult(
                status="EXECUTED",
                work_order_id=order.work_order_id,
                summary="Canonical inspect fixture completed.",
                result_text=f"inspected {workspace.name}",
                review_required=False,
            )

    model_attempt_service = object()
    prompt_registry = object()
    monkeypatch.setattr(
        "host_brain.agent_runtime.executor.AgentExecutor",
        CanonicalFixtureExecutor,
    )
    registry = build_agent_runtime_skill_registry()
    payload = registry.require("agent_inspect").handler(
        {
            "task": "inspect a lightweight snippet",
            "workspace": {"strategy": "runtime_repo"},
        },
        {"request_id": "req-workplane"},
    )

    result = AgentRuntimeExecutor(
        artifact_store=AgentArtifactStore(tmp_path),
        model_attempt_service=model_attempt_service,
        prompt_registry=prompt_registry,
    ).execute(payload["work_order"])

    assert result["status"] == "EXECUTED"
    assert result["work_order_id"] == payload["work_order"]["work_order_id"]
    assert result["changed_files"] == []
    assert result["verification"]["status"] == "NOT_RUN"
    assert result["review_required"] is False
    assert result["metadata"]["execution_authority"] == "canonical_agent_executor"
    assert result["metadata"]["workspace_source"] == "runtime_repo_snapshot"
    assert result["metadata"]["workspace_source_ref"].startswith("git-commit://")
    assert observed_workspace is not None
    assert not observed_workspace.exists()
    assert (repository / "tracked.txt").read_text(encoding="utf-8") == "accepted\n"
    assert Path(result["artifacts_dir"], "work_order.json").exists()
    assert Path(result["artifacts_dir"], "result.json").exists()
