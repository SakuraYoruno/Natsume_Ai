from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Callable

import pytest

import host_brain.audit.migration_runner as migration_runner_module
from host_brain.audit.migration_compat import (
    checksum_bound_schema_replay_postcondition_holds,
    is_exact_checksum_bound_schema_replay_failure,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.core.errors import MIGRATION_EXEC_FAILED


_ASSET_FILENAME = "0104_host_embodiment_runtime_authority.sql"
_MIGRATIONS = Path(migration_runner_module.__file__).with_name("migrations")


def _asset() -> SimpleNamespace:
    path = _MIGRATIONS / _ASSET_FILENAME
    return SimpleNamespace(
        migration_id=104,
        name="host_embodiment_runtime_authority",
        path=path,
        checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _seed(tmp_path: Path, name: str) -> tuple[Path, SQLiteMigrationRunner]:
    db_path = tmp_path / f"{name}.sqlite3"
    runner = SQLiteMigrationRunner(db_path=db_path)
    seeded = runner.apply_migrations(request_id=f"d8-0104-{name}-seed", ts_ms=104)
    assert seeded.success, seeded.as_dict()
    assert seeded.summary["current_version"] == 104
    return db_path, runner


def _forget_0104_receipt(connection: sqlite3.Connection) -> None:
    # The physical DDL is intentionally retained; this models a process crash
    # after the migration COMMIT and before either authoritative receipt write.
    connection.execute("DELETE FROM schema_migration_assets WHERE migration_id = 104")
    connection.execute("DELETE FROM schema_migrations WHERE migration_id = 104")
    connection.commit()


def test_0104_receipt_loss_replay_repairs_only_complete_authority_schema(tmp_path) -> None:
    db_path, runner = _seed(tmp_path, "receipt-loss-complete")
    asset = _asset()
    duplicate = sqlite3.OperationalError("duplicate column name: runtime_scope_id")

    assert is_exact_checksum_bound_schema_replay_failure(asset, duplicate) is True
    assert is_exact_checksum_bound_schema_replay_failure(
        asset,
        sqlite3.OperationalError("duplicate column name: lease_id"),
    ) is False
    assert is_exact_checksum_bound_schema_replay_failure(
        SimpleNamespace(
            migration_id=104,
            name="host_embodiment_runtime_authority",
            path=asset.path,
            checksum="0" * 64,
        ),
        duplicate,
    ) is False

    with sqlite3.connect(db_path) as connection:
        assert checksum_bound_schema_replay_postcondition_holds(connection, asset) is True
        _forget_0104_receipt(connection)

    replayed = runner.apply_migrations(
        request_id="d8-0104-receipt-loss-replay",
        ts_ms=105,
    )
    assert replayed.success, replayed.as_dict()
    assert "0104_host_embodiment_runtime_authority" in {
        record.migration_id for record in replayed.records
    }
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT disposition FROM schema_migration_assets WHERE migration_id = 104"
        ).fetchone() == ("APPLIED_WITH_COMPAT",)
        assert connection.execute(
            "SELECT checksum FROM schema_migrations WHERE migration_id = 104"
        ).fetchone() == (asset.checksum,)


def test_0104_receipt_loss_replay_fails_closed_when_one_authority_object_is_missing(
    tmp_path,
) -> None:
    db_path, runner = _seed(tmp_path, "receipt-loss-partial")
    asset = _asset()
    with sqlite3.connect(db_path) as connection:
        _forget_0104_receipt(connection)
        connection.execute("DROP TRIGGER host_embodiment_edge_session_lease_no_delete")
        connection.commit()
        assert checksum_bound_schema_replay_postcondition_holds(connection, asset) is False

    replayed = runner.apply_migrations(
        request_id="d8-0104-receipt-loss-partial-replay",
        ts_ms=106,
    )
    assert replayed.success is False
    assert replayed.reason_code == MIGRATION_EXEC_FAILED
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = 104"
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM schema_migration_assets WHERE migration_id = 104"
        ).fetchone() is None


def _rename_runtime_scope_column(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE host_embodiment_outbox "
        "RENAME COLUMN runtime_scope_id TO wrong_runtime_scope_id"
    )


def _drop_outbox_scope_index(connection: sqlite3.Connection) -> None:
    connection.execute("DROP INDEX idx_host_embodiment_outbox_scope_ready")


def _drop_runtime_lease_table(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TABLE host_embodiment_runtime_lease")


def _drop_edge_session_table(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TABLE host_embodiment_edge_session_lease")


def _corrupt_runtime_lease_constraint(connection: sqlite3.Connection) -> None:
    original = connection.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'host_embodiment_runtime_lease'"
    ).fetchone()[0]
    corrupted = str(original).replace(
        "fencing_token >= 1)",
        "fencing_token >= 0)",
        1,
    )
    assert corrupted != original
    connection.execute("PRAGMA writable_schema = ON")
    connection.execute(
        "UPDATE sqlite_master SET sql = ? "
        "WHERE type = 'table' AND name = 'host_embodiment_runtime_lease'",
        (corrupted,),
    )
    connection.execute("PRAGMA writable_schema = OFF")


@pytest.mark.parametrize(
    "mutate",
    (
        _rename_runtime_scope_column,
        _drop_outbox_scope_index,
        _drop_runtime_lease_table,
        _drop_edge_session_table,
        _corrupt_runtime_lease_constraint,
    ),
    ids=(
        "runtime-scope-column",
        "outbox-scope-index",
        "runtime-lease-table",
        "edge-session-lease-table",
        "runtime-lease-check-constraint",
    ),
)
def test_0104_replay_postcondition_rejects_partial_authority_shapes(
    tmp_path,
    mutate: Callable[[sqlite3.Connection], None],
) -> None:
    db_path, _runner = _seed(tmp_path, f"shape-{mutate.__name__}")
    with sqlite3.connect(db_path) as connection:
        mutate(connection)
        connection.commit()
    with sqlite3.connect(db_path) as connection:
        assert checksum_bound_schema_replay_postcondition_holds(connection, _asset()) is False
