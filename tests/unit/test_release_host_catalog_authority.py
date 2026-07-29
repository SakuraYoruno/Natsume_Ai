"""Controlled recovery tests for the local Host Service Catalog lease."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.persistence.service_catalog_rollout_repository import (
    SQLiteServiceCatalogRolloutRepository,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "runtime" / "release_host_catalog_authority.py"


def _module() -> object:
    spec = importlib.util.spec_from_file_location(
        "release_host_catalog_authority_test_module",
        SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _database(tmp_path: Path) -> Path:
    db_path = tmp_path / "catalog-authority.sqlite3"
    result = SQLiteMigrationRunner(db_path=str(db_path)).apply_migrations(
        request_id="test-release-host-catalog-authority"
    )
    assert result.success, result.detail
    return db_path


def _authority(db_path: Path, *, pid: int):
    scope = EmbodimentOutboxRepository(db_path).runtime_scope_id
    repository = SQLiteServiceCatalogRolloutRepository(db_path)
    authority = repository.acquire_authority(
        runtime_scope_id=scope,
        owner_id=f"host.main.catalog::{pid}::" + ("a" * 32),
        lease_ms=10_000,
    )
    assert authority is not None
    return repository, scope, authority


def test_confirmed_stopped_host_releases_only_its_exact_active_fence(
    tmp_path: Path,
) -> None:
    module = _module()
    db_path = _database(tmp_path)
    repository, scope, authority = _authority(db_path, pid=4242)

    result = module.release_confirmed_stopped_host_authority(
        db_path=db_path,
        expected_host_pid=4242,
    )

    assert result["status"] == "RELEASED"
    assert result["expected_host_pid"] == 4242
    assert result["fencing_token"] == authority.fencing_token
    assert "runtime_scope_id" not in result
    assert repository.active_authority(runtime_scope_id=scope) is None


def test_recovery_refuses_to_release_another_host_pid_authority(tmp_path: Path) -> None:
    module = _module()
    db_path = _database(tmp_path)
    repository, scope, authority = _authority(db_path, pid=4242)

    result = module.release_confirmed_stopped_host_authority(
        db_path=db_path,
        expected_host_pid=9999,
    )

    assert result["status"] == "OWNER_MISMATCH"
    assert result["observed_owner_pid"] == 4242
    assert result["fencing_token"] == authority.fencing_token
    assert repository.active_authority(runtime_scope_id=scope) == authority


def test_recovery_reports_no_active_authority_without_creating_one(tmp_path: Path) -> None:
    module = _module()
    db_path = _database(tmp_path)

    result = module.release_confirmed_stopped_host_authority(
        db_path=db_path,
        expected_host_pid=4242,
    )

    assert result["status"] == "NOT_ACTIVE"
