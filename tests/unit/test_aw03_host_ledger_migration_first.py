from __future__ import annotations

import sqlite3

import host_brain.__main__ as host_main
from host_brain.composition import process_services as dependencies
from host_brain.app.settings import get_persistence_settings
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_durable_host_ledger_runs_catalog_and_skips_inline_schema(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "host-ledger.sqlite3"
    monkeypatch.setenv("NATSUME_AUDIT_DB", str(db_path))
    get_persistence_settings.cache_clear()
    previous = dependencies.get_process_service_container().ledger
    dependencies.get_process_service_container().ledger = None

    def reject_inline_schema(_self) -> None:
        raise AssertionError("durable Host ledger must not execute inline DDL")

    monkeypatch.setattr(SQLiteLedger, "_ensure_schema", reject_inline_schema)
    ledger = None
    try:
        ledger = dependencies.get_ledger()
        assert ledger.canonical_db_path == str(db_path.resolve())
        with sqlite3.connect(db_path) as conn:
            latest = conn.execute(
                "SELECT MAX(migration_id) FROM schema_migrations"
            ).fetchone()[0]
            assert int(latest) >= 69
            assert conn.execute(
                "SELECT COUNT(*) FROM schema_migration_assets WHERE migration_id = 69"
            ).fetchone() == (1,)
    finally:
        if ledger is not None:
            ledger.close()
        dependencies.get_process_service_container().ledger = previous
        get_persistence_settings.cache_clear()


def test_startup_check_runs_catalog_before_opening_durable_ledger(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "startup-check.sqlite3"

    def reject_inline_schema(_self) -> None:
        raise AssertionError("startup check must not execute inline DDL")

    monkeypatch.setattr(SQLiteLedger, "_ensure_schema", reject_inline_schema)
    monkeypatch.setattr(host_main, "collect_dependency_probes_sync", lambda: {})
    monkeypatch.setattr(
        host_main,
        "build_startup_report",
        lambda **_kwargs: {"status": "OK"},
    )
    monkeypatch.setattr(host_main, "emit_runtime_bootstrap_logs", lambda **_kwargs: None)

    assert host_main.run_startup_check(str(db_path)) is True
    with sqlite3.connect(db_path) as conn:
        latest = conn.execute(
            "SELECT MAX(migration_id) FROM schema_migrations"
        ).fetchone()[0]
        assert int(latest) >= 69
