from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest

import host_brain.agent_runtime.executor as executor_module
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.agent_runtime.result_contract import AgentResult
from host_brain.agent_runtime.workspace_allocator import (
    ManagedGitSource,
    ManagedWorkspaceRoot,
    WorkspaceAllocator,
)
from host_brain.agent_runtime.workspace_runtime import DurableWorkspaceRuntime
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.persistence.task_graph_admission_authority import (
    canonical_task_graph_admission_scope,
    register_task_graph_admission_authority,
)
from host_brain.persistence.workspace_repository import WorkspaceRepository


NOW = 2_000
GRAPH_ID = "graph-workspace-write-promotion"
NODE_ID = "node-workspace-write-promotion"
ATTEMPT_ID = "attempt-workspace-write-promotion"
TASK_LEASE_ID = "task-lease-workspace-write-promotion"
ROOT_WORK_ORDER_ID = "work-order-workspace-write-promotion"
RUNTIME_OWNER = "runtime-owner-workspace-write-promotion"


class MutableClock:
    def __init__(self, value: int = NOW) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return str(completed.stdout or "").strip()


def _source_repository(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.email", "workspace-write@example.invalid")
    _git(source, "config", "user.name", "Workspace Write Test")
    (source / "src").mkdir()
    (source / "src" / "baseline.txt").write_text(
        "baseline\n",
        encoding="utf-8",
    )
    _git(source, "add", "src/baseline.txt")
    _git(source, "commit", "-m", "initial")
    return source.resolve(), _git(source, "rev-parse", "HEAD")


def _database(tmp_path: Path) -> Path:
    db_path = tmp_path / "workspace-write.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="workspace-write-migrate",
        ts_ms=1,
    )
    assert migrated.success is True, migrated.as_dict()
    with sqlite3.connect(db_path) as conn:
        register_task_graph_admission_authority(conn)
        with canonical_task_graph_admission_scope(GRAPH_ID, 1):
            conn.execute(
                """
                INSERT INTO task_runtime_graph(
                    graph_id, revision, root_work_order_id, planner_ref,
                    status, status_version, graph_json, source_kind,
                    created_ts_ms, updated_ts_ms
                ) VALUES (?, 1, ?, ?, 'ACTIVE', 0, '{}', 'CANONICAL', 1, 1)
                """,
                (
                    GRAPH_ID,
                    ROOT_WORK_ORDER_ID,
                    "planner://workspace-write/1",
                ),
            )
            conn.execute(
                """
                INSERT INTO task_runtime_node(
                    graph_id, graph_revision, node_id, status, status_version,
                    node_json, created_ts_ms, updated_ts_ms
                ) VALUES (?, 1, ?, 'RUNNING', 0, '{}', 1, 1)
                """,
                (GRAPH_ID, NODE_ID),
            )
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, owner_id, created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, 1, ?, 1, 'RUNNING', 0, ?, 1, 1)
            """,
            (ATTEMPT_ID, GRAPH_ID, NODE_ID, RUNTIME_OWNER),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_lease(
                lease_id, attempt_id, owner_id, fencing_token,
                acquired_ts_ms, heartbeat_ts_ms, expires_ts_ms,
                status, status_version
            ) VALUES (?, ?, ?, 7, 1, 1, 50000, 'ACTIVE', 0)
            """,
            (TASK_LEASE_ID, ATTEMPT_ID, RUNTIME_OWNER),
        )
    return db_path


def _runtime(
    tmp_path: Path,
    *,
    db_path: Path,
    source: Path,
    clock: MutableClock | None = None,
    lifecycle_fault_injector=None,  # noqa: ANN001
) -> DurableWorkspaceRuntime:
    managed = tmp_path / "managed"
    managed.mkdir(exist_ok=True)
    repository = WorkspaceRepository(
        db_path,
        clock=clock or (lambda: NOW),
        lifecycle_fault_injector=lifecycle_fault_injector,
    )
    allocator = WorkspaceAllocator(
        repository=repository,
        managed_roots={
            "worktrees": ManagedWorkspaceRoot(
                root_ref="worktrees",
                path=managed,
                isolation_modes=frozenset({"DIRECTORY_FENCE"}),
            )
        },
        git_sources={
            "runtime-repository": ManagedGitSource(
                source_key="runtime-repository",
                source_ref_prefix="git-worktree://runtime-repository",
                repository=source,
            )
        },
    )
    return DurableWorkspaceRuntime(
        repository=repository,
        allocator=allocator,
        runtime_owner_id=RUNTIME_OWNER,
        managed_root_ref="worktrees",
        git_source_key="runtime-repository",
        lease_ms=5_000,
        heartbeat_interval_ms=1_000,
        token_secret=b"w" * 48,
    )


def _authority() -> dict[str, Any]:
    return {
        "root_work_order_id": ROOT_WORK_ORDER_ID,
        "graph_id": GRAPH_ID,
        "graph_revision": 1,
        "node_id": NODE_ID,
        "attempt_id": ATTEMPT_ID,
        "lease_id": TASK_LEASE_ID,
        "command_id": "host-command://internal/workspace-write",
        "principal_id": "principal-workspace-write",
        "lease_owner_id": RUNTIME_OWNER,
        "fencing_token": 7,
    }


def _order() -> WorkOrderV3:
    return WorkOrderV3(
        work_order_id=ROOT_WORK_ORDER_ID,
        root_work_order_id=ROOT_WORK_ORDER_ID,
        domain="code",
        run_mode="worker",
        objective="create one governed source file",
        workspace={
            "strategy": "durable_workspace",
            "workspace_id": DurableWorkspaceRuntime.workspace_id_for(
                graph_id=GRAPH_ID,
                graph_revision=1,
            ),
        },
        allowed_actions=("read_files", "write_files"),
        forbidden_actions=("modify_secret_files", "git_push"),
        review_policy={"review_required": False},
        task_graph_id=GRAPH_ID,
        task_node_id=NODE_ID,
        graph_revision=1,
        operation_fingerprint="sha256:" + ("a" * 64),
    )


class _Verification:
    def run(self, *_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"status": "PASS", "receipt_ref": "verification://workspace/write"}


def test_governed_internal_write_seals_and_promotes_exact_git_delta(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source, source_head = _source_repository(tmp_path)
    db_path = _database(tmp_path)
    runtime = _runtime(tmp_path, db_path=db_path, source=source)
    artifacts = AgentArtifactStore(tmp_path / "runs")

    class _WritingAgent:
        def __init__(self, **_dependencies: Any) -> None:
            pass

        def execute(
            self,
            order: WorkOrderV3,
            *,
            workspace: Path,
            cancelled=None,  # noqa: ANN001
        ) -> AgentResult:
            assert callable(cancelled)
            assert not cancelled()
            generated = workspace / "src" / "generated.txt"
            generated.write_text("accepted write\n", encoding="utf-8")
            return AgentResult(
                status="EXECUTED",
                work_order_id=order.work_order_id,
                summary="generated one governed file",
                verification={"status": "PASS"},
                acceptance={"decision": "ACCEPT"},
                review_required=False,
            )

    monkeypatch.setattr(executor_module, "AgentExecutor", _WritingAgent)
    raw = AgentRuntimeExecutor(
        artifact_store=artifacts,
        workspace_runtime=runtime,
        verification_policy=_Verification(),  # type: ignore[arg-type]
        model_attempt_service=object(),
        prompt_registry=object(),
    ).execute(
        _order(),
        workspace_execution_authority=_authority(),
    )

    assert raw["status"] == "EXECUTED"
    assert raw["changed_files"] == ["src/generated.txt"]
    descriptor = raw["_protected_stage"]
    seal = artifacts.inspect_governed_stage(
        stage_ref=descriptor["ref"],
        seal_digest=descriptor["digest"],
    )
    candidate = artifacts.read_workspace_write_candidate(seal)
    assert candidate is not None
    manifest, patch = candidate
    assert manifest["changed_paths"] == ["src/generated.txt"]
    assert manifest["base_commit"] == source_head
    assert b"accepted write" in patch

    promoted = runtime.promote_write_artifact(
        workspace_id=seal.workspace_id,
        execution_authority=_authority(),
        verification_ref="verification://workspace/write",
        acceptance_ref="acceptance://workspace/write",
        effect_ref="acceptance-effect://workspace/write",
        write_manifest=manifest,
        write_patch=patch,
        artifact_promoter=lambda: artifacts.promote_governed_stage(
            stage_ref=seal.stage_ref,
            seal_digest=seal.seal_digest,
            acceptance_ref="acceptance://workspace/write",
        ),
    )
    lifecycle = runtime.repository.get_lifecycle(seal.workspace_id)
    assert lifecycle.state.value == "PROMOTED"
    assert lifecycle.accepted_event is not None
    assert lifecycle.accepted_event.base_commit == source_head
    assert lifecycle.accepted_event.accepted_head != source_head
    assert promoted.no_change_receipt_ref.startswith("workspace-write://")
    assert _git(source, "rev-parse", "main") == source_head
    assert not (source / "src" / "generated.txt").exists()

    replayed = runtime.promote_write_artifact(
        workspace_id=seal.workspace_id,
        execution_authority=_authority(),
        verification_ref="verification://workspace/write",
        acceptance_ref="acceptance://workspace/write",
        effect_ref="acceptance-effect://workspace/write",
        write_manifest=manifest,
        write_patch=patch,
        artifact_promoter=lambda: artifacts.promote_governed_stage(
            stage_ref=seal.stage_ref,
            seal_digest=seal.seal_digest,
            acceptance_ref="acceptance://workspace/write",
        ),
    )
    assert replayed.replayed is True
    assert replayed.artifact_ref == promoted.artifact_ref
    assert replayed.no_change_receipt_ref == promoted.no_change_receipt_ref

    cleanup = runtime.cleanup_terminal_workspace(
        workspace_id=seal.workspace_id,
        expected_state=lifecycle.state,
        expected_terminal_ref=promoted.artifact_ref,
    )
    assert cleanup.receipt_ref.startswith("workspace-cleanup://")
    assert _git(source, "rev-parse", "main") == source_head
    assert _git(source, "branch", "--list", "natsume-agent/*") == ""


def test_write_promotion_rejects_post_seal_worktree_tamper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source, source_head = _source_repository(tmp_path)
    db_path = _database(tmp_path)
    runtime = _runtime(tmp_path, db_path=db_path, source=source)
    artifacts = AgentArtifactStore(tmp_path / "runs")

    class _WritingAgent:
        def __init__(self, **_dependencies: Any) -> None:
            pass

        def execute(
            self,
            order: WorkOrderV3,
            *,
            workspace: Path,
            cancelled=None,  # noqa: ANN001
        ) -> AgentResult:
            (workspace / "src" / "generated.txt").write_text(
                "sealed\n",
                encoding="utf-8",
            )
            return AgentResult(
                status="EXECUTED",
                work_order_id=order.work_order_id,
                summary="generated",
            )

    monkeypatch.setattr(executor_module, "AgentExecutor", _WritingAgent)
    raw = AgentRuntimeExecutor(
        artifact_store=artifacts,
        workspace_runtime=runtime,
        verification_policy=_Verification(),  # type: ignore[arg-type]
        model_attempt_service=object(),
        prompt_registry=object(),
    ).execute(
        _order(),
        workspace_execution_authority=_authority(),
    )
    descriptor = raw["_protected_stage"]
    seal = artifacts.inspect_governed_stage(
        stage_ref=descriptor["ref"],
        seal_digest=descriptor["digest"],
    )
    candidate = artifacts.read_workspace_write_candidate(seal)
    assert candidate is not None
    manifest, patch = candidate
    authority = runtime.repository.get(seal.workspace_id)
    worktree = tmp_path / "managed" / authority.workspace_relpath
    (worktree / "src" / "generated.txt").write_text(
        "tampered after seal\n",
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="differs from the live worktree"):
        runtime.promote_write_artifact(
            workspace_id=seal.workspace_id,
            execution_authority=_authority(),
            verification_ref="verification://workspace/write",
            acceptance_ref="acceptance://workspace/write",
            effect_ref="acceptance-effect://workspace/write",
            write_manifest=manifest,
            write_patch=patch,
            artifact_promoter=lambda: "agent-artifact://should-not-run",
        )
    assert runtime.repository.get_lifecycle(seal.workspace_id).state.value == "ACTIVE"
    assert _git(source, "rev-parse", "main") == source_head
    assert not (source / "src" / "generated.txt").exists()


def test_write_promotion_restart_reuses_commit_after_lifecycle_rollback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source, source_head = _source_repository(tmp_path)
    db_path = _database(tmp_path)
    clock = MutableClock()
    injected = False

    def fail_once(point: str) -> None:
        nonlocal injected
        if point == "after_event_insert" and not injected:
            injected = True
            raise RuntimeError("simulated lifecycle transaction rollback")

    first_runtime = _runtime(
        tmp_path,
        db_path=db_path,
        source=source,
        clock=clock,
        lifecycle_fault_injector=fail_once,
    )
    artifacts = AgentArtifactStore(tmp_path / "runs")

    class _WritingAgent:
        def __init__(self, **_dependencies: Any) -> None:
            pass

        def execute(
            self,
            order: WorkOrderV3,
            *,
            workspace: Path,
            cancelled=None,  # noqa: ANN001
        ) -> AgentResult:
            (workspace / "src" / "generated.txt").write_text(
                "restart-stable\n",
                encoding="utf-8",
            )
            return AgentResult(
                status="EXECUTED",
                work_order_id=order.work_order_id,
                summary="generated",
            )

    monkeypatch.setattr(executor_module, "AgentExecutor", _WritingAgent)
    raw = AgentRuntimeExecutor(
        artifact_store=artifacts,
        workspace_runtime=first_runtime,
        verification_policy=_Verification(),  # type: ignore[arg-type]
        model_attempt_service=object(),
        prompt_registry=object(),
    ).execute(
        _order(),
        workspace_execution_authority=_authority(),
    )
    descriptor = raw["_protected_stage"]
    seal = artifacts.inspect_governed_stage(
        stage_ref=descriptor["ref"],
        seal_digest=descriptor["digest"],
    )
    candidate = artifacts.read_workspace_write_candidate(seal)
    assert candidate is not None
    manifest, patch = candidate

    with pytest.raises(Exception, match="simulated lifecycle transaction rollback"):
        first_runtime.promote_write_artifact(
            workspace_id=seal.workspace_id,
            execution_authority=_authority(),
            verification_ref="verification://workspace/write",
            acceptance_ref="acceptance://workspace/write",
            effect_ref="acceptance-effect://workspace/write",
            write_manifest=manifest,
            write_patch=patch,
            artifact_promoter=lambda: "agent-artifact://must-not-run",
        )
    assert first_runtime.repository.get_lifecycle(seal.workspace_id).state.value == "ACTIVE"
    authority = first_runtime.repository.get(seal.workspace_id)
    worktree = tmp_path / "managed" / authority.workspace_relpath
    commit_before_restart = _git(worktree, "rev-parse", "HEAD")
    assert commit_before_restart != source_head
    assert _git(source, "rev-parse", "main") == source_head

    clock.value += 6_000
    restarted = _runtime(
        tmp_path,
        db_path=db_path,
        source=source,
        clock=clock,
    )
    promoted = restarted.promote_write_artifact(
        workspace_id=seal.workspace_id,
        execution_authority=_authority(),
        verification_ref="verification://workspace/write",
        acceptance_ref="acceptance://workspace/write",
        effect_ref="acceptance-effect://workspace/write",
        write_manifest=manifest,
        write_patch=patch,
        artifact_promoter=lambda: artifacts.promote_governed_stage(
            stage_ref=seal.stage_ref,
            seal_digest=seal.seal_digest,
            acceptance_ref="acceptance://workspace/write",
        ),
    )
    lifecycle = restarted.repository.get_lifecycle(seal.workspace_id)
    assert lifecycle.accepted_event is not None
    assert lifecycle.accepted_event.accepted_head == commit_before_restart
    assert promoted.no_change_receipt_ref.startswith("workspace-write://")
    assert _git(source, "rev-parse", "main") == source_head
