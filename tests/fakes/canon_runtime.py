"""Explicit migrated Canon composition for route and sync tests."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.canon import ProcessCanonContainer, build_process_canon_container


@dataclass(frozen=True)
class CanonTestRuntime:
    db_path: Path
    ledger: SQLiteLedger
    container: ProcessCanonContainer


@contextmanager
def compose_canon_test_runtime(
    tmp_path: Path,
    *,
    request_id: str,
    world_root: Path | None = None,
) -> Iterator[CanonTestRuntime]:
    db_path = Path(tmp_path) / "canon.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=request_id,
    )
    assert migrated.success, migrated.as_dict()
    ledger = SQLiteLedger(
        db_path=db_path,
        check_same_thread=False,
        initialize_schema=False,
    )
    container = build_process_canon_container(
        ledger=ledger,
        canonical_db_path=db_path,
        world_root=world_root or (Path(tmp_path) / "world"),
    )
    try:
        yield CanonTestRuntime(db_path=db_path, ledger=ledger, container=container)
    finally:
        ledger.close()


__all__ = ["CanonTestRuntime", "compose_canon_test_runtime"]
