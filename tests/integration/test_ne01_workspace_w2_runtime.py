from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.agent_runtime.result_contract import AgentResult
from host_brain.agent_runtime.workspace_allocator import (
    ManagedGitSource,
    ManagedWorkspaceRoot,
    WorkspaceAllocator,
)
from host_brain.agent_runtime.workspace_policy import WorkspacePolicy
from host_brain.agent_runtime.workspace_runtime import (
    WORKSPACE_CLEANUP_UNAVAILABLE,
    WORKSPACE_HEARTBEAT_LOST,
    WORKSPACE_RUNTIME_BINDING_INVALID,
    DurableWorkspaceRuntime,
    DurableWorkspaceRuntimeError,
)
from host_brain.composition.agent_workspace import (
    AGENT_WORKSPACE_SHUTDOWN_RETRY_REQUIRED,
    AGENT_WORKSPACE_START_FAILED,
    AGENT_WORKSPACE_START_ROLLBACK_RETRY_REQUIRED,
    AgentWorkspaceLifecycleError,
    WorkspaceCleanupContinuationWorker,
    build_process_agent_workspace_container,
    quiesce_agent_workspace_application_scope,
    start_agent_workspace_application_scope,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.app.internal_task_runtime import _build_contracts
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
from host_brain.persistence.workspace_repository import (
    WORKSPACE_LEASE_CONFLICT,
    WORKSPACE_OPERATION_TIMEOUT,
    WORKSPACE_STALE_FENCE,
    WorkspaceRepository,
    WorkspaceRepositoryError,
)


class MutableClock:
    def __init__(self, value: int = 10_000) -> None:
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


def _git_returncode(repository: Path, *args: str) -> int:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    ).returncode


def _source_repository(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.email", "workspace-w2@example.invalid")
    _git(source, "config", "user.name", "Workspace W2 Test")
    (source / "baseline.txt").write_text("main baseline\n", encoding="utf-8")
    _git(source, "add", "baseline.txt")
    _git(source, "commit", "-m", "initial")
    return source.resolve()


def _database(tmp_path: Path, *, graph_id: str, root_work_order_id: str) -> Path:
    db_path = tmp_path / "workspace-w2.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="workspace-w2-migrate",
        ts_ms=100,
    )
    assert migrated.success is True, migrated.as_dict()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO task_runtime_graph(
                graph_id, revision, root_work_order_id, planner_ref,
                status, status_version, graph_json, source_kind,
                created_ts_ms, updated_ts_ms
            ) VALUES (?, 1, ?, ?, 'ACTIVE', 0, '{}', 'CANONICAL', 1, 1)
            """,
            (graph_id, root_work_order_id, f"planner://workspace-w2/{graph_id}"),
        )
    return db_path


def _runtime(
    *,
    db_path: Path,
    source: Path,
    managed: Path,
    clock: MutableClock,
    runtime_owner: str,
    secret_byte: bytes,
) -> DurableWorkspaceRuntime:
    managed.mkdir(exist_ok=True)
    repository = WorkspaceRepository(db_path, clock=clock)
    allocator = WorkspaceAllocator(
        repository=repository,
        managed_roots={
            "w2-worktrees": ManagedWorkspaceRoot(
                root_ref="w2-worktrees",
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
        runtime_owner_id=runtime_owner,
        managed_root_ref="w2-worktrees",
        git_source_key="runtime-repository",
        lease_ms=3_000,
        heartbeat_interval_ms=500,
        token_secret=secret_byte * 48,
    )


def _order(
    *,
    graph_id: str,
    root_work_order_id: str,
    node_id: str = "node-w2",
) -> AgentWorkOrder:
    workspace_id = DurableWorkspaceRuntime.workspace_id_for(
        graph_id=graph_id,
        graph_revision=1,
    )
    return AgentWorkOrder.from_mapping(
        {
            "work_order_id": root_work_order_id,
            "domain": "code",
            "run_mode": "worker",
            "objective": "write only in the isolated worktree",
            "workspace": {
                "strategy": "durable_workspace",
                "workspace_id": workspace_id,
            },
            "allowed_actions": ["read_files", "write_files"],
            "forbidden_actions": ["modify_secret_files"],
            "metadata": {
                "root_work_order_id": root_work_order_id,
                "task_graph_id": graph_id,
                "task_node_id": node_id,
                "graph_revision": 1,
            },
        }
    )


def _authority(
    *,
    graph_id: str,
    root_work_order_id: str,
    runtime_owner: str,
    attempt: int = 1,
    node_id: str = "node-w2",
) -> dict[str, object]:
    return {
        "command_id": f"command-w2-{node_id}-{attempt}",
        "principal_id": "principal-w2",
        "root_work_order_id": root_work_order_id,
        "graph_id": graph_id,
        "graph_revision": 1,
        "node_id": node_id,
        "attempt_id": f"attempt-w2-{attempt}",
        "lease_id": f"task-lease-w2-{attempt}",
        "lease_owner_id": runtime_owner,
        "fencing_token": attempt,
    }


def _resolve(
    runtime: DurableWorkspaceRuntime,
    order: AgentWorkOrder,
    authority: dict[str, object],
):
    policy = WorkspacePolicy(
        workspace_allocator=runtime.allocator,
        workspace_lease_provider=lambda candidate, workspace_id: runtime.lease_for_order(
            candidate,
            workspace_id,
            execution_authority=dict(authority),
        ),
    )
    return policy.resolve(order)


def test_internal_admission_replaces_caller_workspace_with_server_graph_id() -> None:
    order = AgentWorkOrder.from_mapping(
        {
            "work_order_id": "work-order-w2-admission",
            "domain": "code",
            "run_mode": "worker",
            "objective": "exercise server workspace admission",
            "workspace": {
                "strategy": "durable_workspace",
                "workspace_id": "caller-selected-workspace",
                "lease_token": "caller-secret-must-not-survive",
                "path": "C:/caller/path",
            },
            "allowed_actions": ["write_files"],
            "forbidden_actions": ["modify_secret_files"],
        }
    )
    _command, canonical, graph = _build_contracts(
        work_order=order,
        request_id="request-w2-admission",
        session_id="session-w2-admission",
        ts_ms=1_000,
        governor_grant_ref="governor-grant://workspace-w2/admission",
        command_authority={
            "principal_id": "principal-w2-admission",
            "ingress_claim_ref": "ingress-claim://workspace-w2/admission",
            "ingress_audit_ref": "audit://workspace-w2/admission",
            "input_sha256": "sha256:" + ("a" * 64),
        },
    )
    assert canonical.workspace == {
        "strategy": "durable_workspace",
        "workspace_id": DurableWorkspaceRuntime.workspace_id_for(
            graph_id=graph.graph_id,
            graph_revision=graph.revision,
        ),
    }
    assert "token" not in str(canonical.workspace).lower()
    assert "caller" not in str(canonical.workspace).lower()


def test_production_executor_uses_server_authority_and_isolated_git_worktree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    graph_id = "graph-workspace-w2-executor"
    root_order = "work-order-workspace-w2-executor"
    runtime_owner = "runtime-owner-w2-executor"
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=MutableClock(),
        runtime_owner=runtime_owner,
        secret_byte=b"a",
    )
    order = _order(graph_id=graph_id, root_work_order_id=root_order)
    authority = _authority(
        graph_id=graph_id,
        root_work_order_id=root_order,
        runtime_owner=runtime_owner,
    )

    class FakeAgentExecutor:
        def __init__(self, **_: object) -> None:
            pass

        def execute(
            self,
            candidate: AgentWorkOrder,
            *,
            workspace: Path,
            cancelled=None,
        ) -> AgentResult:
            assert candidate is order
            assert callable(cancelled)
            assert cancelled() is False
            (workspace / "generated.txt").write_text("isolated\n", encoding="utf-8")
            return AgentResult(
                status="EXECUTED",
                work_order_id=candidate.work_order_id,
                summary="isolated write complete",
                result_text="done",
            )

    monkeypatch.setattr("host_brain.agent_runtime.executor.AgentExecutor", FakeAgentExecutor)
    result = AgentRuntimeExecutor(
        artifact_store=AgentArtifactStore(tmp_path / "runs"),
        workspace_runtime=runtime,
        model_attempt_service=object(),
        prompt_registry=object(),
    ).execute(order, workspace_execution_authority=dict(authority))

    workspace = Path(result["metadata"]["workspace"])
    assert result["status"] == "EXECUTED"
    assert workspace.parent == managed.resolve()
    assert (workspace / "generated.txt").read_text(encoding="utf-8") == "isolated\n"
    assert not (source / "generated.txt").exists()
    assert (source / "baseline.txt").read_text(encoding="utf-8") == "main baseline\n"
    assert _git(source, "branch", "--show-current") == "main"
    assert _git(workspace, "branch", "--show-current").startswith("natsume-agent/workspace-")
    durable_workspace = runtime.repository.get(
        str(result["metadata"]["workspace_id"])
    )
    assert _git(workspace, "rev-parse", "HEAD") == durable_workspace.source_ref.rsplit(
        "/", 1
    )[-1]
    # Successful write workspaces remain ACTIVE for review/promotion.  Only an
    # explicit cleanup transition may remove them; startup resumes PENDING only.
    assert durable_workspace.status == "ACTIVE"
    serialized_order = (tmp_path / "runs" / root_order / "work_order.json").read_text(encoding="utf-8")
    assert "workspace-lease-" not in serialized_order
    assert "lease_token" not in serialized_order
    with sqlite3.connect(db_path) as conn:
        token_digest = conn.execute(
            "SELECT lease_token_digest FROM agent_workspace_authority"
        ).fetchone()[0]
    assert str(token_digest).startswith("sha256:")
    assert "workspace-lease-" not in str(token_digest)


def test_known_heartbeat_loss_blocks_post_summary_and_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    graph_id = "graph-workspace-w2-known-heartbeat-loss"
    root_order = "work-order-workspace-w2-known-heartbeat-loss"
    runtime_owner = "runtime-owner-w2-known-heartbeat-loss"
    source = _source_repository(tmp_path)
    db_path = _database(
        tmp_path,
        graph_id=graph_id,
        root_work_order_id=root_order,
    )
    runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=tmp_path / "managed",
        clock=MutableClock(),
        runtime_owner=runtime_owner,
        secret_byte=b"z",
    )
    runtime.heartbeat_interval_s = 0.001

    def lose_heartbeat(*_args, **_kwargs):  # noqa: ANN202
        raise WorkspaceRepositoryError(
            WORKSPACE_STALE_FENCE,
            "simulated heartbeat authority loss",
        )

    runtime.heartbeat_once = lose_heartbeat  # type: ignore[method-assign]

    class WaitingAgentExecutor:
        def __init__(self, **_: object) -> None:
            pass

        def execute(
            self,
            candidate: AgentWorkOrder,
            *,
            workspace: Path,
            cancelled=None,
        ) -> AgentResult:
            assert workspace.is_dir()
            assert callable(cancelled)
            deadline = time.monotonic() + 5.0
            while not cancelled() and time.monotonic() < deadline:
                time.sleep(0.001)
            assert cancelled()
            return AgentResult(
                status="DEGRADED",
                work_order_id=candidate.work_order_id,
                summary="stopped after heartbeat loss",
            )

    summary_calls = 0

    def collect_summary(_workspace: Path) -> dict[str, object]:
        nonlocal summary_calls
        summary_calls += 1
        return {
            "changed_files": [],
            "git_diff_stat": None,
            "git_status": None,
            "status_map": {},
        }

    class VerificationMustNotStart:
        calls = 0

        def run(self, *_args, **_kwargs):  # noqa: ANN202
            self.calls += 1
            raise AssertionError("verification started after known lease loss")

    verification = VerificationMustNotStart()
    monkeypatch.setattr(
        "host_brain.agent_runtime.executor.AgentExecutor",
        WaitingAgentExecutor,
    )
    monkeypatch.setattr(
        "host_brain.agent_runtime.executor._collect_git_summary",
        collect_summary,
    )
    result = AgentRuntimeExecutor(
        artifact_store=AgentArtifactStore(tmp_path / "runs"),
        workspace_runtime=runtime,
        verification_policy=verification,  # type: ignore[arg-type]
        model_attempt_service=object(),
        prompt_registry=object(),
    ).execute(
        _order(graph_id=graph_id, root_work_order_id=root_order),
        workspace_execution_authority=_authority(
            graph_id=graph_id,
            root_work_order_id=root_order,
            runtime_owner=runtime_owner,
        ),
    )

    assert result["status"] == "FAILED"
    assert result["reason_code"] == WORKSPACE_HEARTBEAT_LOST
    assert summary_calls == 1
    assert verification.calls == 0


def test_heartbeat_and_expired_takeover_fence_the_previous_runtime(tmp_path: Path) -> None:
    graph_id = "graph-workspace-w2-takeover"
    root_order = "work-order-workspace-w2-takeover"
    clock = MutableClock()
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    first = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=clock,
        runtime_owner="runtime-owner-first",
        secret_byte=b"b",
    )
    order = _order(graph_id=graph_id, root_work_order_id=root_order)
    first_resolution = _resolve(
        first,
        order,
        _authority(
            graph_id=graph_id,
            root_work_order_id=root_order,
            runtime_owner="runtime-owner-first",
        ),
    )
    first_authority = first.repository.get(str(first_resolution.workspace_id))
    clock.value += 1_000
    heartbeated = first.heartbeat_once(first_resolution)
    assert heartbeated.status_version == first_authority.status_version + 1
    assert heartbeated.lease_expires_ts_ms == clock.value + first.lease_ms

    restarted_same_credential = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=clock,
        runtime_owner="runtime-owner-first",
        secret_byte=b"b",
    )
    with pytest.raises(Exception) as restarted_live_conflict:
        _resolve(
            restarted_same_credential,
            order,
            _authority(
                graph_id=graph_id,
                root_work_order_id=root_order,
                runtime_owner="runtime-owner-first",
            ),
        )
    assert WORKSPACE_LEASE_CONFLICT in str(restarted_live_conflict.value)

    second = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=clock,
        runtime_owner="runtime-owner-second",
        secret_byte=b"c",
    )
    with pytest.raises(Exception) as live_conflict:
        _resolve(
            second,
            order,
            _authority(
                graph_id=graph_id,
                root_work_order_id=root_order,
                runtime_owner="runtime-owner-second",
                attempt=2,
            ),
        )
    assert WORKSPACE_LEASE_CONFLICT in str(live_conflict.value)

    clock.value = int(heartbeated.lease_expires_ts_ms or 0) + 1
    second_resolution = _resolve(
        second,
        order,
        _authority(
            graph_id=graph_id,
            root_work_order_id=root_order,
            runtime_owner="runtime-owner-second",
            attempt=2,
        ),
    )
    taken_over = second.repository.get(str(second_resolution.workspace_id))
    assert taken_over.lease_fencing_token == heartbeated.lease_fencing_token + 1
    with pytest.raises(WorkspaceRepositoryError) as stale:
        first.assert_live(first_resolution)
    assert stale.value.reason_code == WORKSPACE_STALE_FENCE


def test_long_fenced_callback_does_not_hold_sqlite_writer_and_allows_heartbeat(
    tmp_path: Path,
) -> None:
    graph_id = "graph-workspace-w2-short-sqlite-transaction"
    root_order = "work-order-workspace-w2-short-sqlite-transaction"
    runtime_owner = "runtime-owner-w2-short-sqlite-transaction"
    clock = MutableClock()
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=clock,
        runtime_owner=runtime_owner,
        secret_byte=b"r",
    )
    resolution = _resolve(
        runtime,
        _order(graph_id=graph_id, root_work_order_id=root_order),
        _authority(
            graph_id=graph_id,
            root_work_order_id=root_order,
            runtime_owner=runtime_owner,
        ),
    )
    lease = runtime._leases[str(resolution.workspace_id)]
    entered = threading.Event()
    release = threading.Event()
    results: list[str] = []
    failures: list[BaseException] = []

    def long_operation(_context) -> str:  # noqa: ANN001
        entered.set()
        assert release.wait(timeout=5.0)
        return "completed"

    def execute_long_operation() -> None:
        try:
            results.append(
                runtime.repository.run_fenced(
                    lease.workspace_id,
                    lease_owner=lease.lease_owner,
                    lease_token=lease.lease_token,
                    lease_fencing_token=lease.lease_fencing_token,
                    expected_status="ACTIVE",
                    max_operation_ms=2_000,
                    operation=long_operation,
                )
            )
        except BaseException as exc:  # noqa: BLE001 - transferred to test thread
            failures.append(exc)

    worker = threading.Thread(target=execute_long_operation)
    worker.start()
    assert entered.wait(timeout=5.0)
    before = runtime.repository.get(str(resolution.workspace_id))
    heartbeated = runtime.heartbeat_once(resolution)
    assert heartbeated.status_version == before.status_version + 1
    release.set()
    worker.join(timeout=5.0)
    assert not worker.is_alive()
    assert failures == []
    assert results == ["completed"]


def test_fenced_callback_rejects_result_when_physical_fence_changes_after_precheck(
    tmp_path: Path,
) -> None:
    graph_id = "graph-workspace-w2-post-fence"
    root_order = "work-order-workspace-w2-post-fence"
    runtime_owner = "runtime-owner-w2-post-fence"
    clock = MutableClock()
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=clock,
        runtime_owner=runtime_owner,
        secret_byte=b"s",
    )
    resolution = _resolve(
        runtime,
        _order(graph_id=graph_id, root_work_order_id=root_order),
        _authority(
            graph_id=graph_id,
            root_work_order_id=root_order,
            runtime_owner=runtime_owner,
        ),
    )
    lease = runtime._leases[str(resolution.workspace_id)]
    marker = resolution.path / "post-fence-side-effect.txt"

    def bypass_advisory_lock_after_side_effect(_context) -> str:  # noqa: ANN001
        marker.write_text("must not be reported successful\n", encoding="utf-8")
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                UPDATE agent_workspace_authority
                SET lease_fencing_token = lease_fencing_token + 1,
                    status_version = status_version + 1,
                    updated_ts_ms = updated_ts_ms + 1
                WHERE workspace_id = ?
                """,
                (lease.workspace_id,),
            )
        return "must-not-escape"

    with pytest.raises(WorkspaceRepositoryError) as stale:
        runtime.repository.run_fenced(
            lease.workspace_id,
            lease_owner=lease.lease_owner,
            lease_token=lease.lease_token,
            lease_fencing_token=lease.lease_fencing_token,
            expected_status="ACTIVE",
            max_operation_ms=2_000,
            operation=bypass_advisory_lock_after_side_effect,
        )
    assert stale.value.reason_code == WORKSPACE_STALE_FENCE
    assert marker.is_file()


def test_cross_runtime_fenced_callbacks_are_mutually_exclusive(tmp_path: Path) -> None:
    graph_id = "graph-workspace-w2-cross-runtime-lock"
    root_order = "work-order-workspace-w2-cross-runtime-lock"
    runtime_owner = "runtime-owner-w2-cross-runtime-lock"
    clock = MutableClock()
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    first = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=clock,
        runtime_owner=runtime_owner,
        secret_byte=b"t",
    )
    resolution = _resolve(
        first,
        _order(graph_id=graph_id, root_work_order_id=root_order),
        _authority(
            graph_id=graph_id,
            root_work_order_id=root_order,
            runtime_owner=runtime_owner,
        ),
    )
    lease = first._leases[str(resolution.workspace_id)]
    second_repository = WorkspaceRepository(db_path, clock=clock)
    entered = threading.Event()
    release = threading.Event()
    first_failures: list[BaseException] = []

    def first_callback(_context) -> None:  # noqa: ANN001
        entered.set()
        assert release.wait(timeout=5.0)

    def first_worker() -> None:
        try:
            first.repository.run_fenced(
                lease.workspace_id,
                lease_owner=lease.lease_owner,
                lease_token=lease.lease_token,
                lease_fencing_token=lease.lease_fencing_token,
                expected_status="ACTIVE",
                max_operation_ms=2_000,
                operation=first_callback,
            )
        except BaseException as exc:  # noqa: BLE001 - transferred to test thread
            first_failures.append(exc)

    worker = threading.Thread(target=first_worker)
    worker.start()
    assert entered.wait(timeout=5.0)
    second_callback_ran = False

    def second_callback(_context) -> None:  # noqa: ANN001
        nonlocal second_callback_ran
        second_callback_ran = True

    with pytest.raises(WorkspaceRepositoryError) as timed_out:
        second_repository.run_fenced(
            lease.workspace_id,
            lease_owner=lease.lease_owner,
            lease_token=lease.lease_token,
            lease_fencing_token=lease.lease_fencing_token,
            expected_status="ACTIVE",
            max_operation_ms=150,
            operation=second_callback,
        )
    assert timed_out.value.reason_code == WORKSPACE_OPERATION_TIMEOUT
    assert second_callback_ran is False
    release.set()
    worker.join(timeout=5.0)
    assert not worker.is_alive()
    assert first_failures == []


def test_fenced_callback_with_expired_post_lease_never_returns_success(
    tmp_path: Path,
) -> None:
    graph_id = "graph-workspace-w2-post-expiry"
    root_order = "work-order-workspace-w2-post-expiry"
    runtime_owner = "runtime-owner-w2-post-expiry"
    clock = MutableClock()
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=clock,
        runtime_owner=runtime_owner,
        secret_byte=b"u",
    )
    resolution = _resolve(
        runtime,
        _order(graph_id=graph_id, root_work_order_id=root_order),
        _authority(
            graph_id=graph_id,
            root_work_order_id=root_order,
            runtime_owner=runtime_owner,
        ),
    )
    lease = runtime._leases[str(resolution.workspace_id)]
    expires = int(runtime.repository.get(lease.workspace_id).lease_expires_ts_ms or 0)
    marker = resolution.path / "expired-side-effect.txt"

    def expire_after_write(_context) -> str:  # noqa: ANN001
        marker.write_text("uncertain\n", encoding="utf-8")
        clock.value = expires + 1
        return "must-not-escape"

    with pytest.raises(WorkspaceRepositoryError) as stale:
        runtime.repository.run_fenced(
            lease.workspace_id,
            lease_owner=lease.lease_owner,
            lease_token=lease.lease_token,
            lease_fencing_token=lease.lease_fencing_token,
            expected_status="ACTIVE",
            max_operation_ms=2_000,
            operation=expire_after_write,
        )
    assert stale.value.reason_code == WORKSPACE_STALE_FENCE
    assert marker.is_file()


def test_sequential_nodes_and_retry_handoff_the_live_workspace_fence(tmp_path: Path) -> None:
    graph_id = "graph-workspace-w2-sequential"
    root_order = "work-order-workspace-w2-sequential"
    runtime_owner = "runtime-owner-w2-sequential"
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=MutableClock(),
        runtime_owner=runtime_owner,
        secret_byte=b"g",
    )
    first_order = _order(
        graph_id=graph_id,
        root_work_order_id=root_order,
        node_id="node-w2-first",
    )
    first_resolution = _resolve(
        runtime,
        first_order,
        _authority(
            graph_id=graph_id,
            root_work_order_id=root_order,
            runtime_owner=runtime_owner,
            node_id="node-w2-first",
        ),
    )
    first_lease = runtime._leases[str(first_resolution.workspace_id)]
    second_order = _order(
        graph_id=graph_id,
        root_work_order_id=root_order,
        node_id="node-w2-second",
    )
    second_authority = _authority(
        graph_id=graph_id,
        root_work_order_id=root_order,
        runtime_owner=runtime_owner,
        node_id="node-w2-second",
    )

    # An actually running worker cannot be pre-empted merely because the next
    # node belongs to the same graph workspace.
    with runtime.heartbeat_scope(first_resolution):
        with pytest.raises(Exception) as live_conflict:
            _resolve(runtime, second_order, second_authority)
        assert WORKSPACE_LEASE_CONFLICT in str(live_conflict.value)

    # Scope exit proves the old worker and heartbeat are stopped.  The next
    # node can now atomically advance the durable fence without waiting for
    # lease expiry.
    second_resolution = _resolve(runtime, second_order, second_authority)
    assert second_resolution.path == first_resolution.path
    assert second_resolution.lease_fencing_token == first_resolution.lease_fencing_token + 1
    with pytest.raises(WorkspaceRepositoryError) as stale_first:
        runtime.allocator.write_bytes(
            first_lease,
            relative_path="stale-first.txt",
            content=b"must not be written",
        )
    assert stale_first.value.reason_code == WORKSPACE_STALE_FENCE
    assert not (second_resolution.path / "stale-first.txt").exists()

    second_lease = runtime._leases[str(second_resolution.workspace_id)]
    with runtime.heartbeat_scope(second_resolution):
        pass
    retry_resolution = _resolve(
        runtime,
        second_order,
        _authority(
            graph_id=graph_id,
            root_work_order_id=root_order,
            runtime_owner=runtime_owner,
            node_id="node-w2-second",
            attempt=2,
        ),
    )
    assert retry_resolution.lease_fencing_token == second_resolution.lease_fencing_token + 1
    with pytest.raises(WorkspaceRepositoryError) as stale_retry:
        runtime.allocator.write_bytes(
            second_lease,
            relative_path="stale-retry.txt",
            content=b"must not be written",
        )
    assert stale_retry.value.reason_code == WORKSPACE_STALE_FENCE


def test_expired_same_runtime_handoff_marker_falls_back_to_takeover(
    tmp_path: Path,
) -> None:
    graph_id = "graph-workspace-w2-expired-local-handoff"
    root_order = "work-order-workspace-w2-expired-local-handoff"
    runtime_owner = "runtime-owner-w2-expired-local-handoff"
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(
        tmp_path,
        graph_id=graph_id,
        root_work_order_id=root_order,
    )
    clock = MutableClock()
    runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=clock,
        runtime_owner=runtime_owner,
        secret_byte=b"x",
    )
    first_order = _order(
        graph_id=graph_id,
        root_work_order_id=root_order,
        node_id="node-w2-expired-first",
    )
    first_resolution = _resolve(
        runtime,
        first_order,
        _authority(
            graph_id=graph_id,
            root_work_order_id=root_order,
            runtime_owner=runtime_owner,
            node_id="node-w2-expired-first",
        ),
    )
    with runtime.heartbeat_scope(first_resolution):
        pass
    expired = runtime.repository.get(str(first_resolution.workspace_id))
    clock.value = int(expired.lease_expires_ts_ms or 0) + 1

    second_resolution = _resolve(
        runtime,
        _order(
            graph_id=graph_id,
            root_work_order_id=root_order,
            node_id="node-w2-expired-second",
        ),
        _authority(
            graph_id=graph_id,
            root_work_order_id=root_order,
            runtime_owner=runtime_owner,
            node_id="node-w2-expired-second",
        ),
    )

    assert second_resolution.path == first_resolution.path
    assert (
        second_resolution.lease_fencing_token
        == first_resolution.lease_fencing_token + 1
    )
    current = runtime.repository.get(str(second_resolution.workspace_id))
    assert current.lease_owner == second_resolution.lease_owner
    assert current.lease_fencing_token == second_resolution.lease_fencing_token


def test_duplicate_same_attempt_scope_is_process_local_exclusive(tmp_path: Path) -> None:
    graph_id = "graph-workspace-w2-exclusive-scope"
    root_order = "work-order-workspace-w2-exclusive-scope"
    runtime_owner = "runtime-owner-w2-exclusive-scope"
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=MutableClock(),
        runtime_owner=runtime_owner,
        secret_byte=b"p",
    )
    order = _order(graph_id=graph_id, root_work_order_id=root_order)
    authority = _authority(
        graph_id=graph_id,
        root_work_order_id=root_order,
        runtime_owner=runtime_owner,
    )
    first = _resolve(runtime, order, authority)
    replay = _resolve(runtime, order, authority)
    assert replay.lease_owner == first.lease_owner
    assert replay.lease_fencing_token == first.lease_fencing_token

    with runtime.heartbeat_scope(first):
        with pytest.raises(DurableWorkspaceRuntimeError) as duplicate:
            with runtime.heartbeat_scope(replay):
                pass
        assert duplicate.value.reason_code == WORKSPACE_LEASE_CONFLICT

    with runtime.heartbeat_scope(replay):
        pass


def test_scope_stop_cancellation_race_is_not_reported_as_lease_loss(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    graph_id = "graph-workspace-w2-heartbeat-stop"
    root_order = "work-order-workspace-w2-heartbeat-stop"
    runtime_owner = "runtime-owner-w2-heartbeat-stop"
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=MutableClock(),
        runtime_owner=runtime_owner,
        secret_byte=b"q",
    )
    resolution = _resolve(
        runtime,
        _order(graph_id=graph_id, root_work_order_id=root_order),
        _authority(
            graph_id=graph_id,
            root_work_order_id=root_order,
            runtime_owner=runtime_owner,
        ),
    )
    entered = threading.Event()

    def cancelled_heartbeat(_resolution, *, cancelled=None):  # noqa: ANN001, ANN202
        assert callable(cancelled)
        entered.set()
        deadline = time.monotonic() + 5.0
        while not cancelled() and time.monotonic() < deadline:
            time.sleep(0.001)
        assert cancelled()
        raise WorkspaceRepositoryError(
            WORKSPACE_STALE_FENCE,
            "workspace heartbeat was cancelled before its durable write",
        )

    monkeypatch.setattr(runtime, "heartbeat_once", cancelled_heartbeat)
    runtime.heartbeat_interval_s = 0.001
    with runtime.heartbeat_scope(resolution):
        assert entered.wait(timeout=5.0)
    runtime.assert_live(resolution)


def test_pid_change_invalidates_inherited_runtime_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    graph_id = "graph-workspace-w2-fork-fence"
    root_order = "work-order-workspace-w2-fork-fence"
    runtime_owner = "runtime-owner-w2-fork-fence"
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=MutableClock(),
        runtime_owner=runtime_owner,
        secret_byte=b"s",
    )
    order = _order(graph_id=graph_id, root_work_order_id=root_order)
    authority = _authority(
        graph_id=graph_id,
        root_work_order_id=root_order,
        runtime_owner=runtime_owner,
    )
    parent_resolution = _resolve(runtime, order, authority)
    parent_pid = runtime._runtime_pid
    parent_instance = runtime._runtime_instance_id
    monkeypatch.setattr(
        "host_brain.agent_runtime.workspace_runtime.os.getpid",
        lambda: parent_pid + 1,
    )

    with pytest.raises(Exception) as inherited_conflict:
        _resolve(runtime, order, authority)
    assert WORKSPACE_LEASE_CONFLICT in str(inherited_conflict.value)
    assert runtime._runtime_pid == parent_pid + 1
    assert runtime._runtime_instance_id != parent_instance
    with pytest.raises(DurableWorkspaceRuntimeError):
        runtime.assert_live(parent_resolution)


def test_workspace_replay_reuses_frozen_source_ref_after_head_advances(tmp_path: Path) -> None:
    graph_id = "graph-workspace-w2-frozen-source"
    root_order = "work-order-workspace-w2-frozen-source"
    runtime_owner = "runtime-owner-w2-frozen-source"
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=MutableClock(),
        runtime_owner=runtime_owner,
        secret_byte=b"h",
    )
    order = _order(graph_id=graph_id, root_work_order_id=root_order)
    authority = _authority(
        graph_id=graph_id,
        root_work_order_id=root_order,
        runtime_owner=runtime_owner,
    )
    first = _resolve(runtime, order, authority)
    durable_before = runtime.repository.get(str(first.workspace_id))
    first_lease_owner = first.lease_owner
    (first.path / "accepted-local.txt").write_text(
        "accepted local descendant\n",
        encoding="utf-8",
    )
    _git(first.path, "add", "accepted-local.txt")
    _git(first.path, "commit", "-m", "accepted workspace descendant")
    accepted_descendant = _git(first.path, "rev-parse", "HEAD")

    (source / "later.txt").write_text("new main commit\n", encoding="utf-8")
    _git(source, "add", "later.txt")
    _git(source, "commit", "-m", "advance source after workspace allocation")
    assert runtime.allocator.snapshot_git_source(runtime.git_source_key) != durable_before.source_ref

    replay = _resolve(runtime, order, authority)
    durable_after = runtime.repository.get(str(replay.workspace_id))
    assert replay.path == first.path
    assert replay.lease_fencing_token == first.lease_fencing_token
    assert replay.lease_owner == first_lease_owner
    assert durable_after.source_ref == durable_before.source_ref
    assert _git(replay.path, "rev-parse", "HEAD") == accepted_descendant
    assert not (replay.path / "later.txt").exists()


def test_new_workspace_rejects_a_preexisting_namespace_branch(tmp_path: Path) -> None:
    graph_id = "graph-workspace-w2-preexisting-branch"
    root_order = "work-order-workspace-w2-preexisting-branch"
    runtime_owner = "runtime-owner-w2-preexisting-branch"
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=MutableClock(),
        runtime_owner=runtime_owner,
        secret_byte=b"r",
    )
    workspace_id = DurableWorkspaceRuntime.workspace_id_for(
        graph_id=graph_id,
        graph_revision=1,
    )
    branch = f"natsume-agent/{workspace_id}"
    _git(source, "branch", branch, "HEAD")

    with pytest.raises(Exception) as rejected:
        _resolve(
            runtime,
            _order(graph_id=graph_id, root_work_order_id=root_order),
            _authority(
                graph_id=graph_id,
                root_work_order_id=root_order,
                runtime_owner=runtime_owner,
            ),
        )
    assert "cannot adopt a pre-existing branch" in str(rejected.value)
    assert not (managed / workspace_id).exists()
    assert _git_returncode(
        source,
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/heads/{branch}",
    ) == 0


def test_concurrent_first_allocation_reuses_winning_durable_head(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    graph_id = "graph-workspace-w2-concurrent-head"
    root_order = "work-order-workspace-w2-concurrent-head"
    runtime_owner = "runtime-owner-w2-concurrent-head"
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    clock = MutableClock()
    first_runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=clock,
        runtime_owner=runtime_owner,
        secret_byte=b"n",
    )
    second_runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=clock,
        runtime_owner=runtime_owner,
        secret_byte=b"n",
    )
    order = _order(graph_id=graph_id, root_work_order_id=root_order)
    authority = _authority(
        graph_id=graph_id,
        root_work_order_id=root_order,
        runtime_owner=runtime_owner,
    )
    first_sampled = threading.Event()
    release_first = threading.Event()
    sampled_refs: list[str] = []
    original_snapshot = first_runtime.allocator.snapshot_git_source

    def paused_snapshot(source_key: str) -> str:
        sampled = original_snapshot(source_key)
        sampled_refs.append(sampled)
        first_sampled.set()
        assert release_first.wait(timeout=10.0)
        return sampled

    monkeypatch.setattr(first_runtime.allocator, "snapshot_git_source", paused_snapshot)
    first_resolutions = []
    first_failures: list[BaseException] = []

    def resolve_first_runtime() -> None:
        try:
            first_resolutions.append(_resolve(first_runtime, order, authority))
        except BaseException as exc:  # noqa: BLE001 - transferred to test thread
            first_failures.append(exc)

    first_thread = threading.Thread(target=resolve_first_runtime)
    first_thread.start()
    assert first_sampled.wait(timeout=10.0)
    (source / "concurrent-head.txt").write_text("winning HEAD\n", encoding="utf-8")
    _git(source, "add", "concurrent-head.txt")
    _git(source, "commit", "-m", "advance HEAD during first allocation")
    winning_source_ref = second_runtime.allocator.snapshot_git_source(
        second_runtime.git_source_key
    )
    try:
        second_resolution = _resolve(second_runtime, order, authority)
    finally:
        release_first.set()
    first_thread.join(timeout=15.0)

    assert not first_thread.is_alive()
    assert len(first_failures) == 1
    assert WORKSPACE_LEASE_CONFLICT in str(first_failures[0])
    assert first_resolutions == []
    durable = second_runtime.repository.get(str(second_resolution.workspace_id))
    assert durable.source_ref == winning_source_ref
    assert durable.source_ref != sampled_refs[0]
    assert (second_resolution.path / "concurrent-head.txt").read_text(
        encoding="utf-8"
    ) == "winning HEAD\n"


def test_head_conflict_recovery_does_not_swallow_other_immutable_drift(
    tmp_path: Path,
) -> None:
    graph_id = "graph-workspace-w2-head-nonsource-conflict"
    root_order = "work-order-workspace-w2-head-nonsource-conflict"
    runtime_owner = "runtime-owner-w2-head-nonsource-conflict"
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=MutableClock(),
        runtime_owner=runtime_owner,
        secret_byte=b"o",
    )
    order = _order(graph_id=graph_id, root_work_order_id=root_order)
    authority = _authority(
        graph_id=graph_id,
        root_work_order_id=root_order,
        runtime_owner=runtime_owner,
    )
    workspace_id = DurableWorkspaceRuntime.workspace_id_for(
        graph_id=graph_id,
        graph_revision=1,
    )
    runtime.allocator.allocate(
        workspace_id=workspace_id,
        root_work_order_id=root_order,
        graph_id=graph_id,
        graph_revision=1,
        owner_principal_id="principal-w2",
        managed_root_ref=runtime.managed_root_ref,
        source_ref=runtime.allocator.snapshot_git_source(runtime.git_source_key),
        isolation_mode="DIRECTORY_FENCE",
        allowed_paths=("src",),
    )

    with pytest.raises(Exception) as conflict:
        _resolve(runtime, order, authority)
    assert "WORKSPACE_AUTHORITY_CONFLICT" in str(conflict.value)
    durable = runtime.repository.get(workspace_id)
    assert durable.allowed_paths == ("src",)


def test_cleanup_response_loss_replays_and_deletes_exact_worktree_branch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    graph_id = "graph-workspace-w2-cleanup-response"
    root_order = "work-order-workspace-w2-cleanup-response"
    runtime_owner = "runtime-owner-cleanup-response"
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=MutableClock(),
        runtime_owner=runtime_owner,
        secret_byte=b"d",
    )
    order = _order(graph_id=graph_id, root_work_order_id=root_order)
    resolution = _resolve(
        runtime,
        order,
        _authority(
            graph_id=graph_id,
            root_work_order_id=root_order,
            runtime_owner=runtime_owner,
        ),
    )
    workspace = resolution.path
    branch = _git(workspace, "branch", "--show-current")
    original_record = runtime.repository.record_cleanup

    def committed_then_lost(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        original_record(*args, **kwargs)
        raise ConnectionError("cleanup response lost")

    monkeypatch.setattr(runtime.repository, "record_cleanup", committed_then_lost)
    with pytest.raises(ConnectionError, match="response lost"):
        runtime.cleanup(str(resolution.workspace_id))
    assert not workspace.exists()
    assert _git_returncode(
        source,
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/heads/{branch}",
    ) != 0
    monkeypatch.setattr(runtime.repository, "record_cleanup", original_record)
    replay = runtime.cleanup(str(resolution.workspace_id))
    assert replay.replayed is True
    assert replay.receipt_ref.startswith("workspace-cleanup://")


def test_cleanup_pending_after_remove_converges_after_expired_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    graph_id = "graph-workspace-w2-cleanup-restart"
    root_order = "work-order-workspace-w2-cleanup-restart"
    clock = MutableClock()
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    first = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=clock,
        runtime_owner="runtime-owner-cleanup-first",
        secret_byte=b"e",
    )
    order = _order(graph_id=graph_id, root_work_order_id=root_order)
    resolution = _resolve(
        first,
        order,
        _authority(
            graph_id=graph_id,
            root_work_order_id=root_order,
            runtime_owner="runtime-owner-cleanup-first",
        ),
    )

    def lose_before_receipt(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise ConnectionError("crash before cleanup receipt")

    monkeypatch.setattr(first.repository, "record_cleanup", lose_before_receipt)
    with pytest.raises(ConnectionError, match="before cleanup receipt"):
        first.cleanup(str(resolution.workspace_id))
    pending = first.repository.get(str(resolution.workspace_id))
    assert pending.status == "CLEANUP_PENDING"
    assert not resolution.path.exists()

    second = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=clock,
        runtime_owner="runtime-owner-cleanup-second",
        secret_byte=b"f",
    )
    report = second.continue_pending_cleanups()
    assert report[0]["status"] == "DEFERRED"
    assert report[0]["reason_code"] == WORKSPACE_LEASE_CONFLICT
    clock.value = int(pending.lease_expires_ts_ms or 0) + 1
    report = second.continue_pending_cleanups()
    assert report[0]["status"] == "CLEANED"
    cleaned = second.repository.get(str(resolution.workspace_id))
    assert cleaned.status == "CLEANED"
    assert cleaned.lease_fencing_token == pending.lease_fencing_token + 1
    assert cleaned.cleanup_receipt_ref == report[0]["receipt_ref"]


def test_cleanup_fails_closed_while_execution_scope_is_active(tmp_path: Path) -> None:
    graph_id = "graph-workspace-w2-active-cleanup"
    root_order = "work-order-workspace-w2-active-cleanup"
    runtime_owner = "runtime-owner-active-cleanup"
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=MutableClock(),
        runtime_owner=runtime_owner,
        secret_byte=b"i",
    )
    resolution = _resolve(
        runtime,
        _order(graph_id=graph_id, root_work_order_id=root_order),
        _authority(
            graph_id=graph_id,
            root_work_order_id=root_order,
            runtime_owner=runtime_owner,
        ),
    )

    with runtime.heartbeat_scope(resolution):
        with pytest.raises(DurableWorkspaceRuntimeError) as blocked:
            runtime.cleanup(str(resolution.workspace_id))
        assert blocked.value.reason_code == WORKSPACE_CLEANUP_UNAVAILABLE
        assert resolution.path.exists()
        assert runtime.repository.get(str(resolution.workspace_id)).status == "ACTIVE"

    receipt = runtime.cleanup(str(resolution.workspace_id))
    assert receipt.replayed is False
    assert not resolution.path.exists()
    assert runtime.repository.get(str(resolution.workspace_id)).status == "CLEANED"


def test_cleanup_pending_rejects_heartbeat_without_receipt_cas_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    graph_id = "graph-workspace-w2-cleanup-heartbeat"
    root_order = "work-order-workspace-w2-cleanup-heartbeat"
    runtime_owner = "runtime-owner-cleanup-heartbeat"
    source = _source_repository(tmp_path)
    managed = tmp_path / "managed"
    db_path = _database(tmp_path, graph_id=graph_id, root_work_order_id=root_order)
    runtime = _runtime(
        db_path=db_path,
        source=source,
        managed=managed,
        clock=MutableClock(),
        runtime_owner=runtime_owner,
        secret_byte=b"j",
    )
    resolution = _resolve(
        runtime,
        _order(graph_id=graph_id, root_work_order_id=root_order),
        _authority(
            graph_id=graph_id,
            root_work_order_id=root_order,
            runtime_owner=runtime_owner,
        ),
    )
    cleanup_entered = threading.Event()
    allow_remove = threading.Event()
    original_remove = runtime.allocator.remove_git_worktree

    def blocked_remove(lease):  # noqa: ANN001, ANN202
        cleanup_entered.set()
        assert allow_remove.wait(timeout=5.0)
        return original_remove(lease)

    monkeypatch.setattr(runtime.allocator, "remove_git_worktree", blocked_remove)
    receipts = []
    failures: list[BaseException] = []

    def cleanup_in_background() -> None:
        try:
            receipts.append(runtime.cleanup(str(resolution.workspace_id)))
        except BaseException as exc:  # noqa: BLE001 - transferred to test thread
            failures.append(exc)

    cleanup_thread = threading.Thread(target=cleanup_in_background)
    cleanup_thread.start()
    assert cleanup_entered.wait(timeout=5.0)
    pending = runtime.repository.get(str(resolution.workspace_id))
    assert pending.status == "CLEANUP_PENDING"

    with pytest.raises(WorkspaceRepositoryError) as stale_heartbeat:
        runtime.heartbeat_once(resolution)
    assert stale_heartbeat.value.reason_code == WORKSPACE_STALE_FENCE
    after_rejected_heartbeat = runtime.repository.get(str(resolution.workspace_id))
    assert after_rejected_heartbeat.status == "CLEANUP_PENDING"
    assert after_rejected_heartbeat.status_version == pending.status_version

    allow_remove.set()
    cleanup_thread.join(timeout=10.0)
    assert not cleanup_thread.is_alive()
    assert failures == []
    assert len(receipts) == 1
    assert runtime.repository.get(str(resolution.workspace_id)).status == "CLEANED"


def test_cleanup_worker_survives_failure_retains_health_and_waits_for_inflight_close() -> None:
    class FlakyCleanupRuntime:
        operation_timeout_s = 0.25

        def __init__(self) -> None:
            self.calls = 0
            self.recovered = threading.Event()
            self.inflight = threading.Event()
            self.release = threading.Event()

        def continue_pending_cleanups(  # noqa: ANN202
            self,
            *,
            limit: int,
            cancelled=None,  # noqa: ANN001
        ):
            assert limit == 7
            assert callable(cancelled)
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("simulated cleanup timeout")
            if self.calls == 2:
                self.recovered.set()
                return ({"workspace_id": "workspace-recovered", "status": "CLEANED"},)
            self.inflight.set()
            assert self.release.wait(timeout=5.0)
            return ()

    runtime = FlakyCleanupRuntime()
    worker = WorkspaceCleanupContinuationWorker(
        runtime,  # type: ignore[arg-type]
        interval_ms=100,
        batch_limit=7,
    )
    worker.start()
    assert runtime.recovered.wait(timeout=3.0)
    deadline = time.monotonic() + 3.0
    health = worker.health_snapshot()
    while health["last_success_ts_ms"] is None and time.monotonic() < deadline:
        time.sleep(0.01)
        health = worker.health_snapshot()
    assert worker.running is True
    assert health["status"] == "HEALTHY"
    assert health["consecutive_failures"] == 0
    assert health["last_failure"]["error_type"] == "TimeoutError"
    assert worker.last_failure == health["last_failure"]
    assert worker.close_timeout_s >= runtime.operation_timeout_s

    assert runtime.inflight.wait(timeout=3.0)
    release_timer = threading.Timer(0.2, runtime.release.set)
    release_timer.start()
    close_started = time.monotonic()
    worker.close()
    close_elapsed = time.monotonic() - close_started
    release_timer.join(timeout=1.0)
    assert close_elapsed >= 0.15
    assert close_elapsed < worker.close_timeout_s
    assert worker.running is False
    stopped = worker.health_snapshot()
    assert stopped["status"] == "STOPPED"
    assert stopped["last_failure"]["error_type"] == "TimeoutError"


def test_cleanup_continuation_stop_probe_does_not_start_another_row() -> None:
    stopped = False
    cleaned: list[str] = []

    class CleanupHarness:
        repository = SimpleNamespace(
            list_cleanup_pending=lambda *, limit: (
                SimpleNamespace(workspace_id="workspace-first"),
                SimpleNamespace(workspace_id="workspace-second"),
            )[:limit]
        )

        def cleanup(self, workspace_id: str):  # noqa: ANN202
            nonlocal stopped
            cleaned.append(workspace_id)
            stopped = True
            return SimpleNamespace(
                receipt_ref=f"workspace-cleanup://{workspace_id}/receipt",
                digest="sha256:" + ("a" * 64),
            )

    report = DurableWorkspaceRuntime.continue_pending_cleanups(
        CleanupHarness(),  # type: ignore[arg-type]
        limit=2,
        cancelled=lambda: stopped,
    )

    assert cleaned == ["workspace-first"]
    assert [item["workspace_id"] for item in report] == ["workspace-first"]


def test_builder_recovers_one_pending_workspace_then_worker_continues_rest(
    tmp_path: Path,
) -> None:
    first_graph = "graph-workspace-w2-startup-bound-first"
    first_root = "work-order-workspace-w2-startup-bound-first"
    second_graph = "graph-workspace-w2-startup-bound-second"
    second_root = "work-order-workspace-w2-startup-bound-second"
    source = _source_repository(tmp_path)
    db_path = _database(
        tmp_path,
        graph_id=first_graph,
        root_work_order_id=first_root,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO task_runtime_graph(
                graph_id, revision, root_work_order_id, planner_ref,
                status, status_version, graph_json, source_kind,
                created_ts_ms, updated_ts_ms
            ) VALUES (?, 1, ?, ?, 'ACTIVE', 0, '{}', 'CANONICAL', 2, 2)
            """,
            (
                second_graph,
                second_root,
                f"planner://workspace-w2/{second_graph}",
            ),
        )

    managed_root = tmp_path / "managed"
    initial = build_process_agent_workspace_container(
        canonical_db_path=db_path,
        managed_root=managed_root,
        source_repository=source,
        runtime_owner_id="runtime-owner-w2-startup-bound-initial",
        lease_ms=3_000,
        heartbeat_interval_ms=500,
        cleanup_interval_ms=100,
        token_secret=b"startup-bound-workspace-secret-0001",
    )
    try:
        for graph_id, root_order in (
            (first_graph, first_root),
            (second_graph, second_root),
        ):
            resolution = _resolve(
                initial.runtime,
                _order(graph_id=graph_id, root_work_order_id=root_order),
                _authority(
                    graph_id=graph_id,
                    root_work_order_id=root_order,
                    runtime_owner=initial.runtime.runtime_owner_id,
                ),
            )
            workspace_id = str(resolution.workspace_id)
            lease = initial.runtime._leases[workspace_id]
            authority = initial.repository.get(workspace_id)
            initial.repository.begin_cleanup(
                workspace_id,
                lease_owner=lease.lease_owner,
                lease_token=lease.lease_token,
                lease_fencing_token=lease.lease_fencing_token,
                expected_status_version=authority.status_version,
            )
    finally:
        initial.close()

    # Simulate process loss after durable CLEANUP_PENDING but before either
    # physical cleanup.  The next process may take over both expired leases.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE agent_workspace_authority
            SET lease_expires_ts_ms = 1,
                status_version = status_version + 1,
                updated_ts_ms = updated_ts_ms + 1
            WHERE status = 'CLEANUP_PENDING'
            """
        )

    resumed = build_process_agent_workspace_container(
        canonical_db_path=db_path,
        managed_root=managed_root,
        source_repository=source,
        runtime_owner_id="runtime-owner-w2-startup-bound-resumed",
        lease_ms=3_000,
        heartbeat_interval_ms=500,
        cleanup_interval_ms=100,
        token_secret=b"startup-bound-workspace-secret-0001",
    )
    try:
        assert len(resumed.cleanup_continuation_report) == 1
        assert resumed.cleanup_continuation_report[0]["status"] == "CLEANED"
        statuses = {
            row.status
            for row in (
                resumed.repository.get(
                    DurableWorkspaceRuntime.workspace_id_for(
                        graph_id=first_graph,
                        graph_revision=1,
                    )
                ),
                resumed.repository.get(
                    DurableWorkspaceRuntime.workspace_id_for(
                        graph_id=second_graph,
                        graph_revision=1,
                    )
                ),
            )
        }
        assert statuses == {"CLEANED", "CLEANUP_PENDING"}

        resumed.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            statuses = {
                resumed.repository.get(
                    DurableWorkspaceRuntime.workspace_id_for(
                        graph_id=graph_id,
                        graph_revision=1,
                    )
                ).status
                for graph_id in (first_graph, second_graph)
            }
            if statuses == {"CLEANED"}:
                break
            time.sleep(0.02)
        assert statuses == {"CLEANED"}
    finally:
        resumed.close()


def test_asgi_workspace_composition_missing_secret_keeps_writes_fail_closed(
    tmp_path: Path,
) -> None:
    graph_id = "graph-workspace-w2-asgi-missing-secret"
    root_order = "work-order-workspace-w2-asgi-missing-secret"
    source = _source_repository(tmp_path)
    db_path = _database(
        tmp_path,
        graph_id=graph_id,
        root_work_order_id=root_order,
    )

    managed_root = tmp_path / "managed"
    with pytest.raises(DurableWorkspaceRuntimeError) as missing_secret:
        build_process_agent_workspace_container(
            canonical_db_path=db_path,
            managed_root=managed_root,
            source_repository=source,
            runtime_owner_id="runtime-owner-w2-asgi-missing-secret",
            cleanup_interval_ms=100,
            token_secret=b"",
        )
    assert missing_secret.value.reason_code == WORKSPACE_RUNTIME_BINDING_INVALID
    assert not managed_root.exists()

    # ASGI deliberately omits workspace_runtime when composition fails.  A
    # caller naming durable_workspace still cannot grant itself write access.
    result = AgentRuntimeExecutor(
        artifact_store=AgentArtifactStore(tmp_path / "runs"),
        model_attempt_service=object(),
        prompt_registry=object(),
    ).execute(_order(graph_id=graph_id, root_work_order_id=root_order))
    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "WORKSPACE_WRITE_GRANT_REQUIRED"
    assert not (source / "generated.txt").exists()


def test_asgi_workspace_composition_starts_and_injects_valid_secret_runtime(
    tmp_path: Path,
) -> None:
    graph_id = "graph-workspace-w2-asgi-start"
    root_order = "work-order-workspace-w2-asgi-start"
    source = _source_repository(tmp_path)
    db_path = _database(
        tmp_path,
        graph_id=graph_id,
        root_work_order_id=root_order,
    )
    application = SimpleNamespace(state=SimpleNamespace())
    task_runtime_handle = object()
    container = build_process_agent_workspace_container(
        canonical_db_path=db_path,
        managed_root=tmp_path / "managed",
        source_repository=source,
        runtime_owner_id="runtime-owner-w2-asgi-start",
        cleanup_interval_ms=100,
        token_secret=b"valid-w2-asgi-workspace-secret-0001",
    )
    expected_allocator_timeout = (
        60.0 + (2 * container.repository.operation_timeout_s) + 1.0
    )
    assert container.allocator.operation_timeout_s == pytest.approx(
        expected_allocator_timeout
    )
    assert container.runtime.operation_timeout_s == pytest.approx(
        expected_allocator_timeout
        + (10 * container.repository.operation_timeout_s)
        + 1.0
    )
    assert (
        container.cleanup_worker.close_timeout_s
        > container.runtime.operation_timeout_s
    )

    try:
        start_agent_workspace_application_scope(application, container)
        assert application.state.agent_workspace_container is container
        assert application.state.agent_workspace_shutdown_error is None
        assert container.cleanup_worker.running is True
        canonical_executor = AgentRuntimeExecutor(
            artifact_store=AgentArtifactStore(tmp_path / "runs"),
            workspace_runtime=application.state.agent_workspace_container.runtime,
            model_attempt_service=object(),
            prompt_registry=object(),
        )
        assert canonical_executor.workspace_runtime is container.runtime
    finally:
        quiesce_agent_workspace_application_scope(
            application,
            container,
            task_runtime_container=task_runtime_handle,
        )
    assert container.cleanup_worker.running is False
    assert application.state.agent_workspace_container is None


def test_asgi_workspace_start_failure_closes_resource_and_clears_state() -> None:
    events: list[str] = []

    class FailingStartContainer:
        def start(self) -> None:
            events.append("workspace-start")
            raise RuntimeError("worker start failed")

        def close(self) -> None:
            events.append("workspace-close")

    application = SimpleNamespace(state=SimpleNamespace())
    container = FailingStartContainer()
    with pytest.raises(AgentWorkspaceLifecycleError) as failed:
        start_agent_workspace_application_scope(
            application,
            container,  # type: ignore[arg-type]
        )

    assert failed.value.reason_code == AGENT_WORKSPACE_START_FAILED
    assert events == ["workspace-start", "workspace-close"]
    assert application.state.agent_workspace_container is None
    assert application.state.agent_workspace_shutdown_error is None


def test_asgi_workspace_start_rollback_failure_retains_retry_handle() -> None:
    events: list[str] = []

    class PartiallyStartedContainer:
        def start(self) -> None:
            events.append("workspace-start")
            raise RuntimeError("worker published before start failure")

        def close(self) -> None:
            events.append("workspace-close")
            raise TimeoutError("worker did not stop")

    application = SimpleNamespace(state=SimpleNamespace())
    container = PartiallyStartedContainer()
    with pytest.raises(AgentWorkspaceLifecycleError) as retry_required:
        start_agent_workspace_application_scope(
            application,
            container,  # type: ignore[arg-type]
        )

    assert (
        retry_required.value.reason_code
        == AGENT_WORKSPACE_START_ROLLBACK_RETRY_REQUIRED
    )
    assert events == ["workspace-start", "workspace-close"]
    assert application.state.agent_workspace_container is container
    assert application.state.agent_workspace_shutdown_error["retry_required"] is True
    assert (
        application.state.agent_workspace_shutdown_error["reason_code"]
        == AGENT_WORKSPACE_START_ROLLBACK_RETRY_REQUIRED
    )


def test_asgi_workspace_close_failure_precedes_task_store_and_retains_retry_handles() -> None:
    events: list[str] = []

    class FailingCloseContainer:
        def close(self) -> None:
            events.append("workspace-close")
            raise RuntimeError("cleanup worker still running")

    class TaskRuntimeHandle:
        async def aclose(self) -> None:
            events.append("task-runtime-close")

    application = SimpleNamespace(state=SimpleNamespace())
    workspace = FailingCloseContainer()
    task_runtime = TaskRuntimeHandle()

    async def asgi_shutdown_order() -> None:
        quiesce_agent_workspace_application_scope(
            application,
            workspace,  # type: ignore[arg-type]
            task_runtime_container=task_runtime,
        )
        await task_runtime.aclose()

    with pytest.raises(AgentWorkspaceLifecycleError) as retry_required:
        asyncio.run(asgi_shutdown_order())

    assert retry_required.value.reason_code == AGENT_WORKSPACE_SHUTDOWN_RETRY_REQUIRED
    assert events == ["workspace-close"]
    assert application.state.agent_workspace_container is workspace
    assert application.state.task_runtime_container is task_runtime
    assert application.state.agent_workspace_shutdown_error == {
        "reason_code": AGENT_WORKSPACE_SHUTDOWN_RETRY_REQUIRED,
        "retry_required": True,
        "error_type": "RuntimeError",
        "detail": "cleanup worker still running",
    }
