from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.composition.task_runtime import _INFRASTRUCTURE_ORDER
from host_brain.persistence.internal_agent_checkpoint_uow import (
    DurableInternalAgentCheckpointUnitOfWork,
)
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.task_store_types import TaskStoreError
from host_brain.persistence.worker_projection_repository import (
    WORKER_PROJECTION_NOT_FOUND,
    WORKER_PROJECTION_SCHEMA_VERSION,
    WorkerProjectionStore,
    WorkerProjectionStoreError,
)
from host_brain.task_runtime import internal_agent_sealed_checkpoint_recovery
from host_brain.task_runtime import internal_checkpoint_budget_rejection
from host_brain.task_runtime.ports import (
    InternalAgentCheckpointUnitOfWorkPort,
    TaskStorePort,
)


NOW = 1_800_000_000_000


def _migrated(path: Path) -> Path:
    result = SQLiteMigrationRunner(db_path=path).apply_migrations(
        request_id=f"checkpoint-uow::{path.stem}",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return path


def _projection() -> tuple[str, dict[str, object]]:
    body: dict[str, object] = {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "reason_code": None,
        "command_id": "command-checkpoint-uow",
        "request_id": "request-checkpoint-uow",
        "narrative": "canonical checkpoint",
        "route_type": "delegate",
        "target_id": "agent_runtime",
        "graph_id": "graph-checkpoint-uow",
        "graph_revision": 1,
        "node_id": "root",
        "attempt_id": "attempt-checkpoint-uow",
        "node_grant_ref": "governor-grant://checkpoint-uow/root",
        "audit_refs": {"checkpoint": "audit://checkpoint-uow/root"},
    }
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    projection_id = "worker-projection://checkpoint-uow/" + digest.split(":", 1)[1]
    return projection_id, {
        **body,
        "projection_id": projection_id,
        "payload_digest": digest,
        "schema_version": WORKER_PROJECTION_SCHEMA_VERSION,
    }


def test_checkpoint_mutation_authority_is_not_exposed_by_generic_task_store() -> None:
    mutation_names = {
        "commit_internal_agent_recovered_checkpoint",
        "reject_internal_agent_checkpoint_budget_bundle",
    }
    assert not mutation_names.intersection(vars(TaskStorePort))
    assert not any(hasattr(DurableTaskStore, name) for name in mutation_names)
    assert mutation_names.issubset(vars(InternalAgentCheckpointUnitOfWorkPort))
    assert hasattr(DurableTaskStore, "_commit_internal_agent_recovered_checkpoint_uow")
    assert hasattr(
        DurableTaskStore,
        "_reject_internal_agent_checkpoint_budget_bundle_uow",
    )


def test_worker_projection_insert_has_one_private_connection_scoped_owner() -> None:
    root = Path(__file__).resolve().parents[2]
    production = root / "host_brain"
    owners: list[Path] = []
    for path in production.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "INSERT INTO task_runtime_worker_projection" in source:
            owners.append(path)
        elif path.name == "worker_projection_repository.py" and source.count(
            "INSERT INTO {_TABLE}"
        ):
            owners.append(path)
    assert [path.relative_to(root).as_posix() for path in owners] == [
        "host_brain/persistence/worker_projection_repository.py"
    ]
    source = owners[0].read_text(encoding="utf-8")
    assert source.count("INSERT INTO {_TABLE}") == 1
    hook = inspect.getsource(WorkerProjectionStore._stage_in_transaction)
    assert "conn.in_transaction" in hook
    assert "BEGIN" not in hook and ".commit(" not in hook and ".rollback(" not in hook
    hook_callers = {
        path.name
        for path in production.rglob("*.py")
        if "._stage_in_transaction(" in path.read_text(encoding="utf-8")
    }
    assert hook_callers == {
        "task_internal_agent_checkpoint_commit_store.py",
        "worker_projection_repository.py",
    }


def test_private_task_store_checkpoint_hooks_only_have_the_canonical_adapter_caller() -> None:
    root = Path(__file__).resolve().parents[2]
    production = root / "host_brain"
    private_hooks = {
        "_commit_internal_agent_recovered_checkpoint_uow",
        "_reject_internal_agent_checkpoint_budget_bundle_uow",
        "_reopen_internal_agent_budget_rejection_mutation",
    }
    for hook_name in private_hooks:
        callers = {
            path.relative_to(root).as_posix()
            for path in production.rglob("*.py")
            if f".{hook_name}(" in path.read_text(encoding="utf-8")
        }
        assert callers == {"host_brain/persistence/internal_agent_checkpoint_uow.py"}


def test_checkpoint_uow_rejects_different_physical_databases(tmp_path: Path) -> None:
    task_path = _migrated(tmp_path / "task.sqlite3")
    projection_path = _migrated(tmp_path / "projection.sqlite3")
    with pytest.raises(TaskStoreError, match="different databases"):
        DurableInternalAgentCheckpointUnitOfWork(
            task_store=DurableTaskStore(task_path),
            worker_projection_store=WorkerProjectionStore(projection_path),
        )


def test_checkpoint_uow_rejects_repository_substitution(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path / "exact.sqlite3")

    class SubstitutedTaskStore(DurableTaskStore):
        pass

    with pytest.raises(TaskStoreError, match="exact DurableTaskStore"):
        DurableInternalAgentCheckpointUnitOfWork(
            task_store=SubstitutedTaskStore(db_path),
            worker_projection_store=WorkerProjectionStore(db_path),
        )


def test_projection_hook_requires_transaction_and_rolls_back_with_caller(
    tmp_path: Path,
) -> None:
    db_path = _migrated(tmp_path / "rollback.sqlite3")
    store = WorkerProjectionStore(db_path, clock=lambda: NOW)
    projection_id, projection = _projection()
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(WorkerProjectionStoreError, match="active transaction"):
            store._stage_in_transaction(  # noqa: SLF001
                conn,
                projection_id=projection_id,
                projection=projection,
                created_ts_ms=NOW,
            )
        conn.execute("BEGIN IMMEDIATE")
        store._stage_in_transaction(  # noqa: SLF001
            conn,
            projection_id=projection_id,
            projection=projection,
            created_ts_ms=NOW,
        )
        conn.rollback()
    finally:
        conn.close()
    with pytest.raises(WorkerProjectionStoreError) as raised:
        store.get(projection_id)
    assert raised.value.reason_code == WORKER_PROJECTION_NOT_FOUND


def test_runtime_uses_explicit_uow_once_and_keeps_abc_budget_boundary() -> None:
    sealed_tree = ast.parse(inspect.getsource(internal_agent_sealed_checkpoint_recovery))
    sealed_calls = [
        node
        for node in ast.walk(sealed_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "commit_internal_agent_recovered_checkpoint"
    ]
    assert len(sealed_calls) == 1
    assert isinstance(sealed_calls[0].func.value, ast.Name)
    assert sealed_calls[0].func.value.id == "checkpoint_uow"

    rejection_tree = ast.parse(inspect.getsource(internal_checkpoint_budget_rejection))
    calls = [
        node.func.attr
        for node in ast.walk(rejection_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert calls.count("reject_internal_agent_checkpoint_budget_bundle") == 1
    assert calls.count("reopen_internal_agent_budget_rejection_terminal") == 1
    assert not {"consume_budget_once", "settle_worker_checkpoint"}.intersection(calls)
    assert "internal_agent_checkpoint_uow" in _INFRASTRUCTURE_ORDER
