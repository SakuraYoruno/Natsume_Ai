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

from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.app.observability_runtime.projection_ledger_view import (
    refresh_ledger_stats_projection,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.observability.projection_store import QueryProjectionStore


@dataclass(frozen=True)
class DurableObsProjectionRuntime:
    ledger: SQLiteLedger
    store: QueryProjectionStore

    def new_route_ports(self) -> ObsRoutePorts:
        """Create a route-port bundle owned by one test application."""

        store = self.store
        return ObsRoutePorts(
            query_projection_store_provider=lambda: store,
        )

    def refresh_ledger_stats(self) -> dict[str, int]:
        """Run the background-owned ledger statistics materializer."""

        return refresh_ledger_stats_projection(
            ledger=self.ledger,
            store=self.store,
        )


@contextmanager
def compose_durable_obs_projection_runtime(
    tmp_path: Path,
    *,
    request_id: str,
) -> Iterator[DurableObsProjectionRuntime]:
    """Create one isolated migrated source/projection database."""

    db_path = Path(tmp_path) / "obs-projection.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
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
    try:
        yield DurableObsProjectionRuntime(ledger=ledger, store=store)
    finally:
        ledger.close()


class LedgerStatsRefreshingTestClient(TestClient):
    """Materialize source rows before each operator query in integration tests."""

    def __init__(
        self,
        app,
        *args,
        projection_runtime: DurableObsProjectionRuntime,
        obs_route_ports: ObsRoutePorts,
        **kwargs,
    ):  # type: ignore[no-untyped-def]
        if not isinstance(obs_route_ports, ObsRoutePorts):
            raise TypeError("obs_route_ports must be ObsRoutePorts")
        if obs_route_ports.query_projection_store_provider() is not projection_runtime.store:
            raise ValueError("obs_route_ports must reference projection_runtime.store")
        self._projection_runtime = projection_runtime
        app.state.obs_route_ports = obs_route_ports
        super().__init__(app, *args, **kwargs)

    def request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self._projection_runtime.refresh_ledger_stats()
        return super().request(*args, **kwargs)
