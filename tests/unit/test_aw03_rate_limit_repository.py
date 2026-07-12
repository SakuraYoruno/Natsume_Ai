from __future__ import annotations

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.governor.rate_limit_store import SQLiteRateLimitStore


def test_durable_rate_limit_store_uses_catalog_and_survives_restart(tmp_path) -> None:
    db_path = tmp_path / "canonical.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-rate-limit-repository",
    )
    assert migrated.success, migrated.as_dict()

    first = SQLiteRateLimitStore(db_path=str(db_path), window_ms=10_000)
    assert first.incr("principal://owner", now_ms=1_000) == 1
    first.close()

    restarted = SQLiteRateLimitStore(db_path=str(db_path), window_ms=10_000)
    assert restarted.get_count("principal://owner", now_ms=1_001) == 1
    restarted.close()
