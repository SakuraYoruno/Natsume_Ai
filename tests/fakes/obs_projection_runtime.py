"""Explicit durable composition for observability projection tests.

The operator HTTP surface is projection-only.  Tests that seed canonical
ledger rows therefore need the same two-step topology as production: a
migrated durable source database plus an explicit background materialization
before the query adapter is exercised.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient

from host_brain.app.observability_runtime.projection_ledger_view import (
    refresh_ledger_stats_projection,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.process_service_container import (
    get_process_service_container,
)
from host_brain.observability.projection_store import QueryProjectionStore


@dataclass(frozen=True)
class DurableObsProjectionRuntime:
    ledger: SQLiteLedger
    store: QueryProjectionStore

    def refresh_ledger_stats(self) -> dict[str, int]:
        """Run the background-owned ledger statistics materializer."""

        return refresh_ledger_stats_projection()


@contextmanager
def compose_durable_obs_projection_runtime(
    tmp_path: Path,
    *,
    request_id: str,
) -> Iterator[DurableObsProjectionRuntime]:
    """Bind one migrated source/projection database to the process container."""

    db_path = Path(tmp_path) / "obs-projection.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=request_id,
    )
    assert migration.success, migration

    ledger = SQLiteLedger(
        db_path,
        check_same_thread=False,
        initialize_schema=False,
    )
    store = QueryProjectionStore(db_path)
    container = get_process_service_container()
    previous_ledger = container.ledger
    previous_store = container.query_projection_store
    container.ledger = ledger
    container.query_projection_store = store
    try:
        yield DurableObsProjectionRuntime(ledger=ledger, store=store)
    finally:
        if container.ledger is ledger:
            container.ledger = previous_ledger
        if container.query_projection_store is store:
            container.query_projection_store = previous_store
        ledger.close()


class LedgerStatsRefreshingTestClient(TestClient):
    """Materialize source rows before each operator query in integration tests."""

    def request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        refresh_ledger_stats_projection()
        return super().request(*args, **kwargs)

