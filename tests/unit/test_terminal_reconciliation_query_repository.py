from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.task_store_types import TaskStoreError
from host_brain.persistence.terminal_reconciliation_query_repository import (
    TerminalReconciliationQueryRepository,
)
from tests.contract.test_ne01_atomic_failure_bundle import (
    GRAPH_ID,
    _fail,
    _running_store,
)


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "terminal-query.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="terminal-query-migration",
        ts_ms=1_910_000_000_000,
    )
    assert result.success, result.as_dict()
    assert result.summary["current_version"] >= 102
    return db_path


def test_query_repository_opens_canonical_database_read_only(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    repository = TerminalReconciliationQueryRepository(db_path)

    conn = repository._connect()
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO task_runtime_terminal_reconciliation_case(case_id) VALUES ('x')"
            )
    finally:
        conn.close()

    assert repository.list_terminal_reconciliation_inventory_candidates() == ()
    assert repository.list_terminal_reconciliation_cases() == ()
    assert repository.list_terminal_reconciliation_candidates() == ()


@pytest.mark.parametrize("path", ("", ":memory:", "file:test.sqlite3"))
def test_query_repository_rejects_non_durable_or_caller_owned_uris(
    path: str,
) -> None:
    with pytest.raises(TaskStoreError) as error:
        TerminalReconciliationQueryRepository(path)
    assert error.value.reason_code == "TASK_STORE_INVALID"


def test_query_repository_requires_existing_0099_schema(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(TaskStoreError) as error:
        TerminalReconciliationQueryRepository(missing)
    assert error.value.reason_code == "TASK_STORE_SCHEMA_NOT_READY"

    empty = tmp_path / "empty.sqlite3"
    sqlite3.connect(empty).close()
    with pytest.raises(TaskStoreError) as error:
        TerminalReconciliationQueryRepository(empty)
    assert error.value.reason_code == "TASK_STORE_SCHEMA_NOT_READY"


def test_query_repository_public_reads_leave_database_unchanged(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    repository = TerminalReconciliationQueryRepository(db_path)
    with sqlite3.connect(db_path) as conn:
        before = conn.total_changes
        before_cases = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_terminal_reconciliation_case"
        ).fetchone()[0]

    repository.list_terminal_reconciliation_inventory_candidates(limit=10)
    repository.list_terminal_reconciliation_cases(limit=10)
    repository.list_terminal_reconciliation_candidates(limit=10)

    with sqlite3.connect(db_path) as conn:
        after_cases = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_terminal_reconciliation_case"
        ).fetchone()[0]
        after = conn.total_changes
    assert before == after == 0
    assert before_cases == after_cases == 0


def test_inventory_discovery_uses_one_sqlite_read_snapshot(tmp_path: Path) -> None:
    store, context = _running_store(tmp_path)
    # The shared running-store fixture already owns the single graph budget.
    # Reuse it so this test measures snapshot isolation rather than creating
    # an invalid two-authority graph aggregate.
    budget_id = "budget-terminal-reconciliation"
    _fail(store, context["lease"])
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")

    class ConcurrentMutationReader(TerminalReconciliationQueryRepository):
        mutated = False

        def _terminal_observation(self, conn, graph):  # noqa: ANN001
            if not self.mutated:
                self.mutated = True
                with sqlite3.connect(self.canonical_db_path) as writer:
                    writer.execute(
                        """
                        UPDATE task_runtime_budget_reservation
                        SET status = 'RELEASED', status_version = status_version + 1
                        WHERE budget_id = ?
                        """,
                        (budget_id,),
                    )
                    writer.commit()
            return super()._terminal_observation(conn, graph)

    reader = ConcurrentMutationReader(store.db_path)
    observed = reader.list_terminal_reconciliation_inventory_candidates()

    assert len(observed) == 1
    assert observed[0]["budget_status"] == "ACTIVE"
    assert (
        TerminalReconciliationQueryRepository(store.db_path)
        .list_terminal_reconciliation_inventory_candidates()
        == ()
    )
