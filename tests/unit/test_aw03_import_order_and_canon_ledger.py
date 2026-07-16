from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

import pytest

from host_brain.app import http_hooks
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger


ROOT = Path(__file__).resolve().parents[2]


def test_agent_runtime_and_persistence_use_precise_imports() -> None:
    program = "\n".join(
        (
            "import host_brain.persistence.task_repository as task",
            "import host_brain.persistence.review_repository as review",
            "import host_brain.agent_runtime.executor as runtime_executor",
            "assert task.DurableTaskStore.__module__ == 'host_brain.persistence.task_repository'",
            "assert review.ReviewStore.__module__ == 'host_brain.persistence.review_repository'",
            "assert runtime_executor.AgentRuntimeExecutor.__module__ == 'host_brain.agent_runtime.executor'",
        )
    )
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["NATSUME_AUDIT_DB"] = ":memory:"
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_task_repository_reexports_canonical_task_store_value_types() -> None:
    from host_brain.persistence import task_repository
    from host_brain.persistence import task_store_types

    exported_names = (
        "AcceptanceEffectCommitScope",
        "AcceptanceEffectIntentSpec",
        "AcceptanceEffectStatus",
        "AttemptStatus",
        "BudgetConsumptionResult",
        "IdempotencyRecoveryAction",
        "IdempotencyStatus",
        "LeaseStatus",
        "OperationReservationResult",
        "OutboxMessage",
        "RecoveryReport",
        "TASK_STORE_BUDGET_EXCEEDED",
        "TASK_STORE_CAS_CONFLICT",
        "TASK_STORE_CANCELLATION_CONFLICT",
        "TASK_STORE_DURABLE_DATA_INVALID",
        "TASK_STORE_INVALID",
        "TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED",
        "TASK_STORE_LEASE_CONFLICT",
        "TASK_STORE_NOT_FOUND",
        "TASK_STORE_OUTBOX_CONFLICT",
        "TASK_STORE_OWNERSHIP_CONFLICT",
        "TASK_STORE_SCHEMA_NOT_READY",
        "TASK_STORE_SCHEDULER_CAPACITY",
        "TaskStoreError",
        "build_outbox_payload_ref",
    )
    for name in exported_names:
        assert getattr(task_repository, name) is getattr(task_store_types, name)


def test_agent_runtime_package_import_does_not_eager_load_persistence() -> None:
    program = "\n".join(
        (
            "import sys",
            "import host_brain.agent_runtime as runtime",
            "assert 'host_brain.persistence.task_repository' not in sys.modules",
            "assert 'host_brain.persistence.review_repository' not in sys.modules",
            "assert runtime.__all__ == ()",
        )
    )
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_startup_world_canon_reader_reuses_migrated_canonical_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "canonical.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-world-canon-reader",
        ts_ms=1_900_000_000_000,
    )
    assert migrated.success is True, migrated.as_dict()
    ledger = SQLiteLedger(
        db_path=db_path,
        check_same_thread=False,
        initialize_schema=False,
    )
    world_root = tmp_path / "world-canon-read-only"
    monkeypatch.setattr(
        http_hooks,
        "get_persistence_settings",
        lambda: SimpleNamespace(world_canon_root=world_root),
    )

    from host_brain.memory import canon_world_store

    def reject_second_ledger(*_args, **_kwargs):
        raise AssertionError("startup reader attempted to open a second SQLiteLedger")

    monkeypatch.setattr(canon_world_store, "SQLiteLedger", reject_second_ledger)
    try:
        reader = http_hooks._build_world_canon_reader(ledger=ledger)
        assert reader.ledger is ledger
        assert reader.world_root == world_root
        assert ledger.canonical_db_path == str(db_path.resolve())
        assert world_root.exists() is False
    finally:
        ledger.close()


def test_http_hooks_contains_no_default_world_canon_constructor() -> None:
    source = (ROOT / "host_brain" / "app" / "http_hooks.py").read_text(
        encoding="utf-8"
    )
    assert "WorldCanonStore()" not in source
