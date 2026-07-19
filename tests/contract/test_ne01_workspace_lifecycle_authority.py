from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.workspace_lifecycle import (
    WorkspaceLifecycleState,
)
from host_brain.persistence.workspace_repository import (
    WORKSPACE_LIFECYCLE_CONFLICT,
    WORKSPACE_LIFECYCLE_MIGRATION_ASSET_KEY,
    WORKSPACE_LIFECYCLE_MIGRATION_SHA256,
    WORKSPACE_LIFECYCLE_REPLAY_DRIFT,
    WORKSPACE_LIFECYCLE_SCHEMA_NOT_READY,
    WORKSPACE_STALE_FENCE,
    WorkspaceRepository,
    WorkspaceRepositoryError,
)


TOKEN = "workspace-lifecycle-token-" + ("x" * 32)
BASE_COMMIT = "a" * 40
ACCEPTED_HEAD = "b" * 40
TREE_DIGEST = "sha256:" + ("c" * 64)
DIFF_DIGEST = "sha256:" + ("d" * 64)


class MutableClock:
    def __init__(self, value: int = 2_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


class SequenceClock:
    def __init__(self, values: list[int]) -> None:
        self.values = list(values)
        self.last = self.values[-1]

    def __call__(self) -> int:
        if self.values:
            self.last = self.values.pop(0)
        return self.last


def _migrated_runtime(
    tmp_path: Path,
    *,
    name: str = "workspace-lifecycle.sqlite3",
    clock: MutableClock | None = None,
    fault_injector=None,  # noqa: ANN001
) -> tuple[WorkspaceRepository, Path, int]:
    db_path = tmp_path / name
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"workspace-lifecycle:{name}",
        ts_ms=1,
    )
    assert result.success is True, result.as_dict()
    # The fixture always migrates through the current catalog head; 0116 is
    # verified below as an immutable historical asset, not as the latest head.
    assert result.summary["current_version"] == 118
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO task_runtime_graph(
                graph_id, revision, root_work_order_id, planner_ref,
                status, status_version, graph_json, source_kind,
                created_ts_ms, updated_ts_ms
            ) VALUES (
                'graph-workspace', 1, 'work-order-workspace',
                'planner://workspace/1', 'ACTIVE', 0, '{}', 'CANONICAL', 1, 1
            )
            """
        )
        conn.execute(
            """
            INSERT INTO task_runtime_node(
                graph_id, graph_revision, node_id, status, status_version,
                node_json, created_ts_ms, updated_ts_ms
            ) VALUES ('graph-workspace', 1, 'node-write', 'RUNNING', 0, '{}', 1, 1)
            """
        )
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, owner_id, created_ts_ms, updated_ts_ms
            ) VALUES (
                'attempt-write', 'graph-workspace', 1, 'node-write', 1,
                'RUNNING', 0, 'task-owner', 1, 1
            )
            """
        )
        conn.execute(
            """
            INSERT INTO task_runtime_lease(
                lease_id, attempt_id, owner_id, fencing_token,
                acquired_ts_ms, heartbeat_ts_ms, expires_ts_ms,
                status, status_version
            ) VALUES (
                'task-lease-write', 'attempt-write', 'task-owner', 7,
                1, 1, 10000, 'ACTIVE', 0
            )
            """
        )
    trusted_clock = clock or MutableClock()
    repository = WorkspaceRepository(
        db_path,
        clock=trusted_clock,
        lifecycle_fault_injector=fault_injector,
    )
    repository.allocate(
        workspace_id="workspace-write",
        root_work_order_id="work-order-workspace",
        graph_id="graph-workspace",
        graph_revision=1,
        owner_principal_id="principal://workspace/owner",
        managed_root_ref="agent-runtime-worktrees",
        source_ref="git://workspace/base",
        isolation_mode="DIRECTORY_FENCE",
        allowed_paths=("src", "tests"),
    )
    lease = repository.acquire_lease(
        "workspace-write",
        lease_owner="workspace-owner",
        lease_token=TOKEN,
        lease_ms=5_000,
    )
    return repository, db_path, lease.authority.lease_fencing_token


def _accept_values(fence: int, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "workspace_id": "workspace-write",
        "event_id": "workspace-accepted-1",
        "expected_transition_revision": 0,
        "node_id": "node-write",
        "attempt_id": "attempt-write",
        "task_lease_id": "task-lease-write",
        "task_lease_fencing_token": 7,
        "lease_owner": "workspace-owner",
        "lease_token": TOKEN,
        "lease_fencing_token": fence,
        "base_commit": BASE_COMMIT,
        "accepted_head": ACCEPTED_HEAD,
        "tree_digest": TREE_DIGEST,
        "diff_digest": DIFF_DIGEST,
        "verification_ref": "verification://workspace/node-write/1",
        "acceptance_ref": "acceptance://workspace/node-write/1",
    }
    values.update(overrides)
    return values


def test_0116_is_checksum_bound_and_append_only_with_later_migrations(
    tmp_path: Path,
) -> None:
    repository, db_path, fence = _migrated_runtime(tmp_path)
    migration = (
        Path(__file__).resolve().parents[2]
        / "host_brain"
        / "audit"
        / "migrations"
        / "0116_agent_workspace_lifecycle.sql"
    )
    assert hashlib.sha256(migration.read_bytes()).hexdigest() == (
        WORKSPACE_LIFECYCLE_MIGRATION_SHA256
    )
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT checksum FROM schema_migration_assets WHERE asset_key = ?",
            (WORKSPACE_LIFECYCLE_MIGRATION_ASSET_KEY,),
        ).fetchone() == (WORKSPACE_LIFECYCLE_MIGRATION_SHA256,)
        old_table = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'agent_workspace_authority'"
        ).fetchone()
        assert old_table is not None
        old_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(agent_workspace_authority)")
        }
        assert "accepted_head" not in old_columns

    accepted = repository.accept_workspace(**_accept_values(fence))
    assert accepted.current_state is WorkspaceLifecycleState.ACCEPTED
    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE agent_workspace_lifecycle_event SET event_digest = ?",
                ("sha256:" + ("e" * 64),),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM agent_workspace_lifecycle_event")


def test_accept_receipt_binds_exact_execution_git_and_acceptance_evidence(
    tmp_path: Path,
) -> None:
    repository, db_path, fence = _migrated_runtime(tmp_path)
    mutation = repository.accept_workspace(**_accept_values(fence))
    event = mutation.event

    assert mutation.replayed is False
    assert event.workspace_id == "workspace-write"
    assert (event.graph_id, event.graph_revision, event.node_id) == (
        "graph-workspace",
        1,
        "node-write",
    )
    assert (event.attempt_id, event.task_lease_id, event.task_lease_fencing_token) == (
        "attempt-write",
        "task-lease-write",
        7,
    )
    assert event.workspace_lease_fencing_token == fence
    assert event.workspace_lease_ref.startswith("workspace-lease://")
    assert event.base_commit == BASE_COMMIT
    assert event.accepted_head == ACCEPTED_HEAD
    assert event.tree_digest == TREE_DIGEST
    assert event.diff_digest == DIFF_DIGEST
    assert event.verification_ref.startswith("verification://")
    assert event.acceptance_ref.startswith("acceptance://")
    assert event.previous_event_digest is None
    assert event.event_digest == event.compute_event_digest()

    restarted = WorkspaceRepository(db_path, clock=MutableClock())
    snapshot = restarted.get_lifecycle("workspace-write")
    assert snapshot.state is WorkspaceLifecycleState.ACCEPTED
    assert snapshot.revision == 1
    assert snapshot.accepted_event == event


def test_exact_replay_is_permanent_after_promotion_and_drift_fails_closed(
    tmp_path: Path,
) -> None:
    repository, _, fence = _migrated_runtime(tmp_path)
    accepted = repository.accept_workspace(**_accept_values(fence))
    promoted = repository.promote_workspace(
        "workspace-write",
        event_id="workspace-promoted-1",
        expected_transition_revision=1,
        acceptance_event_id=accepted.event.event_id,
        promotion_ref="promotion://workspace/accepted-head/1",
        lease_owner="workspace-owner",
        lease_token=TOKEN,
        lease_fencing_token=fence,
    )

    assert promoted.current_state is WorkspaceLifecycleState.PROMOTED
    assert promoted.event.previous_event_digest == accepted.event.event_digest
    assert promoted.event.acceptance_event_id == accepted.event.event_id
    replay = repository.accept_workspace(**_accept_values(fence))
    assert replay.replayed is True
    assert replay.event == accepted.event
    assert replay.current_state is WorkspaceLifecycleState.PROMOTED
    assert replay.current_revision == 2

    with pytest.raises(WorkspaceRepositoryError) as drifted:
        repository.accept_workspace(
            **_accept_values(
                fence,
                acceptance_ref="acceptance://workspace/node-write/drift",
            )
        )
    assert drifted.value.reason_code == WORKSPACE_LIFECYCLE_REPLAY_DRIFT
    with pytest.raises(WorkspaceRepositoryError) as terminal_drift:
        repository.promote_workspace(
            "workspace-write",
            event_id="workspace-promoted-1",
            expected_transition_revision=1,
            acceptance_event_id="workspace-accepted-drift",
            promotion_ref="promotion://workspace/accepted-head/1",
            lease_owner="workspace-owner",
            lease_token=TOKEN,
            lease_fencing_token=fence,
        )
    assert terminal_drift.value.reason_code == WORKSPACE_LIFECYCLE_REPLAY_DRIFT


@pytest.mark.parametrize(
    ("override", "reason_code"),
    [
        ({"lease_fencing_token": 99}, WORKSPACE_STALE_FENCE),
        ({"task_lease_fencing_token": 99}, WORKSPACE_STALE_FENCE),
        ({"expected_transition_revision": 1}, WORKSPACE_STALE_FENCE),
    ],
)
def test_accept_rejects_stale_workspace_task_and_revision_fences(
    tmp_path: Path,
    override: dict[str, object],
    reason_code: str,
) -> None:
    repository, _, fence = _migrated_runtime(tmp_path)
    with pytest.raises(WorkspaceRepositoryError) as raised:
        repository.accept_workspace(**_accept_values(fence, **override))
    assert raised.value.reason_code == reason_code
    assert repository.get_lifecycle("workspace-write").revision == 0


def test_workspace_lease_is_resampled_at_the_final_commit_boundary(
    tmp_path: Path,
) -> None:
    clock = SequenceClock([2_000, 2_000, 6_999, 7_000])
    repository, _, fence = _migrated_runtime(tmp_path, clock=clock)
    with pytest.raises(WorkspaceRepositoryError) as raised:
        repository.accept_workspace(**_accept_values(fence))
    assert raised.value.reason_code == WORKSPACE_STALE_FENCE
    assert repository.get_lifecycle("workspace-write").revision == 0


def test_active_and_accepted_abandonment_are_distinct_exact_transitions(
    tmp_path: Path,
) -> None:
    active_repo, _, active_fence = _migrated_runtime(
        tmp_path,
        name="workspace-active-abandon.sqlite3",
    )
    direct = active_repo.abandon_workspace(
        "workspace-write",
        event_id="workspace-abandoned-direct",
        expected_transition_revision=0,
        node_id="node-write",
        attempt_id="attempt-write",
        task_lease_id="task-lease-write",
        task_lease_fencing_token=7,
        abandonment_ref="abandonment://verification-failed/1",
        reason_code="VERIFICATION_FAILED",
        lease_owner="workspace-owner",
        lease_token=TOKEN,
        lease_fencing_token=active_fence,
    )
    assert direct.current_state is WorkspaceLifecycleState.ABANDONED
    assert direct.event.carries_acceptance is False

    accepted_repo, _, accepted_fence = _migrated_runtime(
        tmp_path,
        name="workspace-accepted-abandon.sqlite3",
    )
    accepted = accepted_repo.accept_workspace(**_accept_values(accepted_fence))
    abandoned = accepted_repo.abandon_workspace(
        "workspace-write",
        event_id="workspace-abandoned-after-accept",
        expected_transition_revision=1,
        node_id="node-write",
        attempt_id="attempt-write",
        task_lease_id="task-lease-write",
        task_lease_fencing_token=7,
        acceptance_event_id=accepted.event.event_id,
        abandonment_ref="abandonment://operator/1",
        reason_code="OPERATOR_ABANDONED",
        lease_owner="workspace-owner",
        lease_token=TOKEN,
        lease_fencing_token=accepted_fence,
    )
    assert abandoned.current_state is WorkspaceLifecycleState.ABANDONED
    assert abandoned.event.acceptance_event_id == accepted.event.event_id
    assert abandoned.event.accepted_head == accepted.event.accepted_head


def test_competing_terminal_cas_has_one_winner(tmp_path: Path) -> None:
    repository, db_path, fence = _migrated_runtime(tmp_path)
    accepted = repository.accept_workspace(**_accept_values(fence))
    second = WorkspaceRepository(db_path, clock=MutableClock())

    def promote() -> str:
        repository.promote_workspace(
            "workspace-write",
            event_id="workspace-terminal-promote",
            expected_transition_revision=1,
            acceptance_event_id=accepted.event.event_id,
            promotion_ref="promotion://workspace/race",
            lease_owner="workspace-owner",
            lease_token=TOKEN,
            lease_fencing_token=fence,
        )
        return "PROMOTED"

    def abandon() -> str:
        second.abandon_workspace(
            "workspace-write",
            event_id="workspace-terminal-abandon",
            expected_transition_revision=1,
            node_id="node-write",
            attempt_id="attempt-write",
            task_lease_id="task-lease-write",
            task_lease_fencing_token=7,
            acceptance_event_id=accepted.event.event_id,
            abandonment_ref="abandonment://workspace/race",
            reason_code="RACE_ABANDONED",
            lease_owner="workspace-owner",
            lease_token=TOKEN,
            lease_fencing_token=fence,
        )
        return "ABANDONED"

    outcomes: list[str] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(promote), pool.submit(abandon)]
        for future in futures:
            try:
                outcomes.append(future.result())
            except WorkspaceRepositoryError as exc:
                errors.append(exc.reason_code)
    assert len(outcomes) == 1
    assert len(errors) == 1
    assert errors[0] in {WORKSPACE_STALE_FENCE, WORKSPACE_LIFECYCLE_CONFLICT}
    assert repository.get_lifecycle("workspace-write").state.value == outcomes[0]


def test_append_failure_rolls_back_and_schema_drift_fails_closed(
    tmp_path: Path,
) -> None:
    def fail(point: str) -> None:
        if point == "after_event_insert":
            raise RuntimeError("injected-after-event-insert")

    repository, db_path, fence = _migrated_runtime(
        tmp_path,
        fault_injector=fail,
    )
    with pytest.raises(RuntimeError, match="injected-after-event-insert"):
        repository.accept_workspace(**_accept_values(fence))
    assert repository.get_lifecycle("workspace-write").revision == 0

    recovered = WorkspaceRepository(db_path, clock=MutableClock())
    assert recovered.accept_workspace(**_accept_values(fence)).current_revision == 1
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER trg_agent_workspace_lifecycle_sequence_guard")
    drifted = WorkspaceRepository(db_path, clock=MutableClock())
    with pytest.raises(WorkspaceRepositoryError) as raised:
        drifted.get_lifecycle("workspace-write")
    assert raised.value.reason_code == WORKSPACE_LIFECYCLE_SCHEMA_NOT_READY
