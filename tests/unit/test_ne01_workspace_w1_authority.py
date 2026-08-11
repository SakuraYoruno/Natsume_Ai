from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
import shutil
import sqlite3

import pytest

from host_brain.persistence import workspace_operation_fencing, workspace_repository

from host_brain.agent_runtime.workspace_allocator import (
    DurableWorkspaceLease,
    WorkspaceAllocator,
)
from host_brain.agent_runtime.workspace_policy import WorkspacePolicy
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder, WorkOrderV3
from host_brain.persistence.workspace_repository import (
    WORKSPACE_AUTHORITY_CONFLICT,
    WORKSPACE_AUTHORITY_INVALID,
    WORKSPACE_AUTHORITY_MIGRATION_ASSET_KEY,
    WORKSPACE_AUTHORITY_MIGRATION_SHA256,
    WORKSPACE_DURABLE_DATA_INVALID,
    WORKSPACE_OWNERSHIP_CONFLICT,
    WORKSPACE_SCHEMA_NOT_READY,
    WORKSPACE_STALE_FENCE,
    WorkspaceRepository,
    WorkspaceRepositoryError,
)
from host_brain.persistence.task_graph_admission_authority import (
    canonical_task_graph_admission_scope,
    register_task_graph_admission_authority,
)


def test_workspace_repository_and_operation_fencing_remain_focused() -> None:
    repository_source = inspect.getsource(workspace_repository)
    fencing_source = inspect.getsource(workspace_operation_fencing)

    assert len(repository_source.splitlines()) < 1_500
    assert len(fencing_source.splitlines()) < 500
    assert (
        WorkspaceRepository.run_fenced
        is workspace_operation_fencing.WorkspaceOperationFencingMixin.run_fenced
    )
    assert "sqlite3.connect" not in fencing_source


TOKEN_ONE = "lease-token-one-" + ("a" * 32)
TOKEN_TWO = "lease-token-two-" + ("b" * 32)


class MutableClock:
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


def _migrated_db(tmp_path: Path, name: str) -> Path:
    db_path = tmp_path / name
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"workspace-w1:{name}",
        ts_ms=101,
    )
    assert result.success is True, result.as_dict()
    assert "0101_agent_workspace_authority" in {
        record.migration_id for record in result.records
    }
    return db_path


def _migration_catalog_through_0101(tmp_path: Path) -> Path:
    source = (
        Path(__file__).resolve().parents[2]
        / "host_brain"
        / "audit"
        / "migrations"
    )
    target = tmp_path / "migrations-through-0101"
    target.mkdir()
    for path in source.glob("*.sql"):
        if int(path.name.split("_", 1)[0]) <= 101:
            shutil.copy2(path, target / path.name)
    return target


def _insert_graph(
    db_path: Path,
    *,
    graph_id: str = "graph-w1",
    root_work_order_id: str = "work-order-w1",
    revision: int = 1,
) -> None:
    with sqlite3.connect(db_path) as conn:
        register_task_graph_admission_authority(conn)
        with canonical_task_graph_admission_scope(graph_id, revision):
            conn.execute(
                """
                INSERT INTO task_runtime_graph(
                    graph_id, revision, root_work_order_id, planner_ref,
                    status, status_version, graph_json, source_kind,
                    created_ts_ms, updated_ts_ms
                ) VALUES (?, ?, ?, ?, 'ACTIVE', 0, '{}', 'CANONICAL', 1, 1)
                """,
                (
                    graph_id,
                    revision,
                    root_work_order_id,
                    f"planner://workspace/{graph_id}/{revision}",
                ),
            )


def _repository(
    tmp_path: Path,
    *,
    clock: MutableClock | None = None,
    name: str = "workspace-w1.sqlite3",
) -> tuple[WorkspaceRepository, Path]:
    db_path = _migrated_db(tmp_path, name)
    _insert_graph(db_path)
    return WorkspaceRepository(db_path, clock=clock or MutableClock()), db_path


def _allocation_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "workspace_id": "workspace-w1",
        "root_work_order_id": "work-order-w1",
        "graph_id": "graph-w1",
        "graph_revision": 1,
        "owner_principal_id": "principal://workspace/owner-a",
        "managed_root_ref": "codex-runs",
        "source_ref": "source://workspace/repository/1",
        "isolation_mode": "DIRECTORY_FENCE",
        "allowed_paths": ("src", "tests"),
    }
    values.update(overrides)
    return values


def _allocator(
    repository: WorkspaceRepository,
    managed_root: Path,
) -> WorkspaceAllocator:
    managed_root.mkdir()
    return WorkspaceAllocator(
        repository=repository,
        managed_roots={"codex-runs": managed_root},
    )


def test_0101_migration_is_checksum_bound_and_physically_guarded(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path, "workspace-w1-shape.sqlite3")
    asset_path = (
        Path(__file__).resolve().parents[2]
        / "host_brain"
        / "audit"
        / "migrations"
        / "0101_agent_workspace_authority.sql"
    )
    assert hashlib.sha256(asset_path.read_bytes()).hexdigest() == (
        WORKSPACE_AUTHORITY_MIGRATION_SHA256
    )

    with sqlite3.connect(db_path) as conn:
        asset = conn.execute(
            "SELECT checksum FROM schema_migration_assets WHERE asset_key = ?",
            (WORKSPACE_AUTHORITY_MIGRATION_ASSET_KEY,),
        ).fetchone()
        assert asset == (WORKSPACE_AUTHORITY_MIGRATION_SHA256,)
        triggers = {
            row[0]
            for row in conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'trigger' AND tbl_name = 'agent_workspace_authority'
                """
            )
        }
        assert triggers == {
            "trg_agent_workspace_authority_immutable_update",
            "trg_agent_workspace_authority_delete_guard",
            "trg_agent_workspace_authority_version_guard",
            "trg_agent_workspace_authority_fence_guard",
            "trg_agent_workspace_authority_status_guard",
            "trg_agent_workspace_authority_cleanup_immutable",
        }
    WorkspaceRepository(db_path)


def test_0101_receipt_loss_replay_keeps_one_exact_workspace_shape(
    tmp_path: Path,
) -> None:
    catalog = _migration_catalog_through_0101(tmp_path)
    db_path = tmp_path / "workspace-w1-receipt-loss.sqlite3"
    runner = SQLiteMigrationRunner(db_path=db_path, migrations_dir=catalog)
    seeded = runner.apply_migrations(request_id="workspace-w1-seed", ts_ms=101)
    assert seeded.success is True, seeded.as_dict()
    assert seeded.summary["current_version"] == 101

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id = 101")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id = 101")
        conn.commit()

    replayed = runner.apply_migrations(
        request_id="workspace-w1-receipt-loss-replay",
        ts_ms=102,
    )
    assert replayed.success is True, replayed.as_dict()
    assert replayed.summary["current_version"] == 101
    assert "0101_agent_workspace_authority" in {
        record.migration_id for record in replayed.records
    }
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table' AND name = 'agent_workspace_authority'
            """
        ).fetchone() == (1,)
        assert conn.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'trigger'
              AND tbl_name = 'agent_workspace_authority'
            """
        ).fetchone() == (6,)
    WorkspaceRepository(db_path)


@pytest.mark.parametrize("drift_kind", ["trigger", "index"])
def test_schema_readiness_rejects_same_name_weakened_objects(
    tmp_path: Path,
    drift_kind: str,
) -> None:
    db_path = _migrated_db(tmp_path, f"workspace-w1-{drift_kind}-drift.sqlite3")
    with sqlite3.connect(db_path) as conn:
        if drift_kind == "trigger":
            conn.executescript(
                """
                DROP TRIGGER trg_agent_workspace_authority_version_guard;
                CREATE TRIGGER trg_agent_workspace_authority_version_guard
                BEFORE UPDATE ON agent_workspace_authority
                BEGIN
                    SELECT 1;
                END;
                """
            )
        else:
            conn.executescript(
                """
                DROP INDEX idx_agent_workspace_authority_owner_status;
                CREATE INDEX idx_agent_workspace_authority_owner_status
                ON agent_workspace_authority(status, workspace_id);
                """
            )

    with pytest.raises(WorkspaceRepositoryError) as raised:
        WorkspaceRepository(db_path)
    assert raised.value.reason_code == WORKSPACE_SCHEMA_NOT_READY


def test_allocation_replay_is_exact_and_cross_owner_root_is_rejected(
    tmp_path: Path,
) -> None:
    repository, _ = _repository(tmp_path)
    first = repository.allocate(**_allocation_values())
    replay = repository.allocate(**_allocation_values())

    assert first.created is True
    assert replay.replayed is True
    assert replay.authority == first.authority

    with pytest.raises(WorkspaceRepositoryError) as raised:
        repository.allocate(
            **_allocation_values(
                owner_principal_id="principal://workspace/owner-b",
            )
        )
    assert raised.value.reason_code == WORKSPACE_OWNERSHIP_CONFLICT

    with pytest.raises(WorkspaceRepositoryError) as drifted:
        repository.allocate(**_allocation_values(allowed_paths=("other",)))
    assert drifted.value.reason_code == WORKSPACE_AUTHORITY_CONFLICT


def test_allocation_must_bind_the_exact_durable_graph_epoch(tmp_path: Path) -> None:
    repository, _ = _repository(tmp_path)

    with pytest.raises(WorkspaceRepositoryError) as raised:
        repository.allocate(
            **_allocation_values(root_work_order_id="another-root")
        )
    assert raised.value.reason_code == WORKSPACE_AUTHORITY_CONFLICT


def test_allocator_rejects_unconfigured_or_disabled_managed_root(
    tmp_path: Path,
) -> None:
    repository, _ = _repository(tmp_path)
    allocator = _allocator(repository, tmp_path / "managed")

    with pytest.raises(WorkspaceRepositoryError) as unconfigured:
        allocator.allocate(**_allocation_values(managed_root_ref="arbitrary-db-path"))
    assert unconfigured.value.reason_code == WORKSPACE_AUTHORITY_INVALID

    with pytest.raises(WorkspaceRepositoryError) as fake_isolation:
        allocator.allocate(**_allocation_values(isolation_mode="CONTAINER"))
    assert fake_isolation.value.reason_code == WORKSPACE_AUTHORITY_INVALID


def test_lease_response_loss_replay_does_not_advance_fence_or_version(
    tmp_path: Path,
) -> None:
    repository, _ = _repository(tmp_path)
    repository.allocate(**_allocation_values())

    first = repository.acquire_lease(
        "workspace-w1",
        lease_owner="worker-a",
        lease_token=TOKEN_ONE,
        lease_ms=100,
        expected_status_version=0,
    )
    replay = repository.acquire_lease(
        "workspace-w1",
        lease_owner="worker-a",
        lease_token=TOKEN_ONE,
        lease_ms=100,
        expected_status_version=0,
    )

    assert first.authority.status == "ACTIVE"
    assert first.authority.status_version == 1
    assert first.authority.lease_fencing_token == 1
    assert replay.replayed is True
    assert replay.authority == first.authority


def test_expired_takeover_makes_stale_fence_zero_write_and_zero_state_advance(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    repository, _ = _repository(tmp_path, clock=clock)
    managed = tmp_path / "managed"
    allocator = _allocator(repository, managed)
    allocator.allocate(**_allocation_values(allowed_paths=("src",)))
    first = repository.acquire_lease(
        "workspace-w1",
        lease_owner="worker-a",
        lease_token=TOKEN_ONE,
        lease_ms=100,
        expected_status_version=0,
    ).authority
    lease_one = DurableWorkspaceLease(
        workspace_id="workspace-w1",
        lease_owner="worker-a",
        lease_token=TOKEN_ONE,
        lease_fencing_token=first.lease_fencing_token,
    )
    allocator.write_bytes(lease_one, relative_path="src/first.txt", content=b"one")

    clock.value = 1_101
    second = repository.acquire_lease(
        "workspace-w1",
        lease_owner="worker-b",
        lease_token=TOKEN_TWO,
        lease_ms=100,
        expected_status_version=first.status_version,
    ).authority
    before_stale = repository.get("workspace-w1")

    with pytest.raises(WorkspaceRepositoryError) as raised:
        allocator.write_bytes(
            lease_one,
            relative_path="src/stale.txt",
            content=b"must-not-write",
        )
    assert raised.value.reason_code == WORKSPACE_STALE_FENCE
    assert not (managed / "workspace-w1" / "src" / "stale.txt").exists()
    assert repository.get("workspace-w1") == before_stale
    assert second.lease_fencing_token == 2
    assert second.status_version == 2


def test_allow_list_blocks_write_before_filesystem_mutation(tmp_path: Path) -> None:
    repository, _ = _repository(tmp_path)
    allocator = _allocator(repository, tmp_path / "managed")
    allocator.allocate(**_allocation_values(allowed_paths=("src",)))
    authority = repository.acquire_lease(
        "workspace-w1",
        lease_owner="worker-a",
        lease_token=TOKEN_ONE,
        lease_ms=100,
    ).authority
    lease = DurableWorkspaceLease(
        workspace_id="workspace-w1",
        lease_owner="worker-a",
        lease_token=TOKEN_ONE,
        lease_fencing_token=authority.lease_fencing_token,
    )

    with pytest.raises(WorkspaceRepositoryError) as raised:
        allocator.write_bytes(
            lease,
            relative_path="outside.txt",
            content=b"blocked",
        )
    assert raised.value.reason_code == WORKSPACE_AUTHORITY_INVALID
    assert not (tmp_path / "managed" / "workspace-w1" / "outside.txt").exists()


def test_tampered_allowed_paths_fail_before_any_filesystem_callback(
    tmp_path: Path,
) -> None:
    repository, db_path = _repository(tmp_path)
    managed = tmp_path / "managed"
    allocator = _allocator(repository, managed)
    allocator.allocate(**_allocation_values(allowed_paths=("src",)))
    active = repository.acquire_lease(
        "workspace-w1",
        lease_owner="worker-a",
        lease_token=TOKEN_ONE,
        lease_ms=100,
    ).authority
    lease = DurableWorkspaceLease(
        workspace_id="workspace-w1",
        lease_owner="worker-a",
        lease_token=TOKEN_ONE,
        lease_fencing_token=active.lease_fencing_token,
    )
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            DROP TRIGGER trg_agent_workspace_authority_immutable_update;
            UPDATE agent_workspace_authority
            SET allowed_paths_json = '["evil"]',
                status_version = status_version + 1
            WHERE workspace_id = 'workspace-w1';
            """
        )

    with pytest.raises(WorkspaceRepositoryError) as raised:
        allocator.write_bytes(
            lease,
            relative_path="evil/payload.txt",
            content=b"must-not-write",
        )
    assert raised.value.reason_code == WORKSPACE_DURABLE_DATA_INVALID
    assert not (managed / "workspace-w1").exists()


def test_cleanup_receipt_is_fenced_and_cleanup_adapter_never_uses_db_path(
    tmp_path: Path,
) -> None:
    repository, db_path = _repository(tmp_path)
    managed = tmp_path / "managed"
    allocator = _allocator(repository, managed)
    allocator.allocate(**_allocation_values(allowed_paths=(".",)))
    active = repository.acquire_lease(
        "workspace-w1",
        lease_owner="cleanup-worker",
        lease_token=TOKEN_ONE,
        lease_ms=100,
    ).authority
    lease = DurableWorkspaceLease(
        workspace_id="workspace-w1",
        lease_owner="cleanup-worker",
        lease_token=TOKEN_ONE,
        lease_fencing_token=active.lease_fencing_token,
    )
    allocator.prepare_workspace(lease)
    pending = repository.begin_cleanup(
        "workspace-w1",
        lease_owner="cleanup-worker",
        lease_token=TOKEN_ONE,
        lease_fencing_token=active.lease_fencing_token,
        expected_status_version=active.status_version,
    )
    target = allocator.cleanup_target(lease)
    assert target.path == (managed / "workspace-w1").resolve()
    assert target.path.is_dir()

    digest = "sha256:" + hashlib.sha256(b"removed-by-trusted-cleaner").hexdigest()
    cleaned = repository.record_cleanup(
        "workspace-w1",
        lease_owner="cleanup-worker",
        lease_token=TOKEN_ONE,
        lease_fencing_token=active.lease_fencing_token,
        expected_status_version=pending.status_version,
        cleanup_receipt_ref="cleanup://workspace/workspace-w1/1",
        cleanup_digest=digest,
    )
    assert cleaned.status == "CLEANED"
    assert cleaned.cleanup_digest == digest
    assert cleaned.lease_owner is None
    # Authority records cleanup evidence; the repository/allocator does not
    # recursively delete a path selected from SQLite.
    assert target.path.is_dir()

    with sqlite3.connect(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            UPDATE agent_workspace_authority
            SET managed_root_ref = 'C:/arbitrary', status_version = status_version + 1
            WHERE workspace_id = 'workspace-w1'
            """
        )


def test_workspace_policy_needs_injected_allocator_and_only_logical_lease_payload(
    tmp_path: Path,
) -> None:
    repository, _ = _repository(tmp_path)
    allocator = _allocator(repository, tmp_path / "managed")
    allocator.allocate(**_allocation_values(allowed_paths=(".",)))
    active = repository.acquire_lease(
        "workspace-w1",
        lease_owner="worker-a",
        lease_token=TOKEN_ONE,
        lease_ms=100,
    ).authority
    payload = {
        "strategy": "durable_workspace",
        "workspace_id": "workspace-w1",
    }
    order = AgentWorkOrder.from_mapping(
        {
            "work_order_id": "work-order-w1",
            "domain": "code",
            "run_mode": "worker",
            "objective": "exercise W1",
            "workspace": payload,
            "allowed_actions": ["write_files"],
            "forbidden_actions": ["modify_secret_files"],
            "metadata": {
                "root_work_order_id": "work-order-w1",
                "task_graph_id": "graph-w1",
                "graph_revision": 1,
            },
        }
    )

    assert TOKEN_ONE not in str(order.to_dict())
    assert "token" not in str(order.workspace).lower()

    with pytest.raises(ValueError, match="server-side lease provider"):
        WorkspacePolicy().resolve(order)

    with pytest.raises(ValueError, match="server-side lease provider"):
        WorkspacePolicy(workspace_allocator=allocator).resolve(order)

    server_lease = DurableWorkspaceLease(
        workspace_id="workspace-w1",
        lease_owner="worker-a",
        lease_token=TOKEN_ONE,
        lease_fencing_token=active.lease_fencing_token,
    )
    policy = WorkspacePolicy(
        workspace_allocator=allocator,
        workspace_lease_provider=lambda _order, _workspace_id: server_lease,
    )
    order.metadata["task_graph_id"] = "graph-other"
    with pytest.raises(ValueError, match="another graph epoch"):
        policy.resolve(order)
    assert not (tmp_path / "managed" / "workspace-w1").exists()

    order.metadata["task_graph_id"] = "graph-w1"
    resolution = policy.resolve(order)
    assert resolution.source == "durable_workspace"
    assert resolution.workspace_id == "workspace-w1"
    assert resolution.path == (tmp_path / "managed" / "workspace-w1").resolve()

    order.workspace = {**payload, "lease": {"token": TOKEN_ONE}}
    with pytest.raises(ValueError, match="cannot be serialized"):
        policy.resolve(order)


def test_workspace_policy_uses_v3_graph_fields_and_rejects_metadata_drift(
    tmp_path: Path,
) -> None:
    repository, _ = _repository(tmp_path)
    allocator = _allocator(repository, tmp_path / "managed-v3")
    allocator.allocate(**_allocation_values())
    active = repository.acquire_lease(
        "workspace-w1",
        lease_owner="worker-v3",
        lease_token=TOKEN_ONE,
        lease_ms=100,
    ).authority
    order = WorkOrderV3(
        work_order_id="work-order-w1",
        root_work_order_id="work-order-w1",
        domain="code",
        run_mode="worker",
        objective="exercise canonical workspace graph binding",
        workspace={
            "strategy": "durable_workspace",
            "workspace_id": "workspace-w1",
        },
        allowed_actions=("write_files",),
        forbidden_actions=("modify_secret_files",),
        task_graph_id="graph-w1",
        task_node_id="node-w1",
        graph_revision=1,
    )
    server_lease = DurableWorkspaceLease(
        workspace_id="workspace-w1",
        lease_owner="worker-v3",
        lease_token=TOKEN_ONE,
        lease_fencing_token=active.lease_fencing_token,
    )
    policy = WorkspacePolicy(
        workspace_allocator=allocator,
        workspace_lease_provider=lambda _order, _workspace_id: server_lease,
    )

    resolution = policy.resolve(order)

    assert resolution.workspace_id == "workspace-w1"
    order.metadata["task_graph_id"] = "graph-drift"
    with pytest.raises(ValueError, match="conflicts with canonical WorkOrder v3 field"):
        policy.resolve(order)


def test_direct_sql_cannot_mutate_or_delete_immutable_authority(tmp_path: Path) -> None:
    repository, db_path = _repository(tmp_path)
    repository.allocate(**_allocation_values())

    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE agent_workspace_authority
                SET owner_principal_id = 'principal://other',
                    status_version = status_version + 1
                WHERE workspace_id = 'workspace-w1'
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "DELETE FROM agent_workspace_authority WHERE workspace_id = 'workspace-w1'"
            )
