from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import host_brain.__main__ as host_main
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_durable_host_ledger_runs_catalog_and_skips_inline_schema(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "host-ledger.sqlite3"

    def reject_inline_schema(_self) -> None:
        raise AssertionError("durable Host ledger must not execute inline DDL")

    monkeypatch.setattr(SQLiteLedger, "_ensure_schema", reject_inline_schema)
    ledger = None
    try:
        migrated = SQLiteMigrationRunner(db_path=str(db_path)).apply_migrations(
            request_id="test-aw03-durable-ledger",
        )
        assert migrated.success is True
        ledger = SQLiteLedger(db_path=str(db_path), initialize_schema=False)
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


def test_startup_check_runs_catalog_before_opening_durable_ledger(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "startup-check.sqlite3"
    startup_calls: list[str] = []

    def reject_inline_schema(_self) -> None:
        raise AssertionError("startup check must not execute inline DDL")

    def build_ports(*, persistence_db_path=None):
        assert persistence_db_path == str(db_path)
        startup_calls.append("build_ports")
        return SimpleNamespace(
            collect_dependency_probes_sync=(
                lambda: startup_calls.append("collect_probes") or {}
            )
        )

    monkeypatch.setattr(SQLiteLedger, "_ensure_schema", reject_inline_schema)
    monkeypatch.setattr(
        host_main,
        "build_process_application_ports",
        build_ports,
    )
    monkeypatch.setattr(
        host_main,
        "build_startup_report",
        lambda **_kwargs: {"status": "OK"},
    )
    monkeypatch.setattr(host_main, "emit_runtime_bootstrap_logs", lambda **_kwargs: None)

    assert host_main.run_startup_check(str(db_path)) is True
    assert startup_calls == ["build_ports", "collect_probes"]
    with sqlite3.connect(db_path) as conn:
        latest = conn.execute(
            "SELECT MAX(migration_id) FROM schema_migrations"
        ).fetchone()[0]
        assert int(latest) >= 69


def test_cli_db_path_remains_authoritative_when_asgi_rebuilds_ports(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "cli-authoritative.sqlite3"
    monkeypatch.setenv("NATSUME_AUDIT_DB", "stale.sqlite3")
    get_persistence_settings.cache_clear()

    try:
        host_main._bind_cli_persistence_path(str(db_path))

        assert get_persistence_settings().canonical_db_path == str(db_path)
    finally:
        get_persistence_settings.cache_clear()
