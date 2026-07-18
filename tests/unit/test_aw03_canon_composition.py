from __future__ import annotations

import sqlite3

from host_brain.app import canon_routes
from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.canon import build_process_canon_container


def test_canon_is_composed_once_after_central_migration_and_injected(tmp_path) -> None:
    db_path = tmp_path / "canonical.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-canon-composition",
    )
    assert migrated.success, migrated.as_dict()

    ledger = SQLiteLedger(db_path=db_path, initialize_schema=False)
    container = build_process_canon_container(
        ledger=ledger,
        canonical_db_path=db_path,
        world_root=tmp_path / "world",
    )
    route_ports = HostRoutePorts(
        canon_service_provider=lambda: container.application,
    )
    try:
        assert canon_routes._get_canon_service(route_ports) is container.application
        assert container.application.preview_sync().records == ()
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM canon_journal").fetchone() == (0,)
    finally:
        ledger.close()
