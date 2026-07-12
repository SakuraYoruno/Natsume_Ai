from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.context_memory_repository import MemoryEventRepository
from host_brain.persistence.sqlite_backup import (
    SQLITE_BACKUP_INTEGRITY_FAILED,
    SQLiteBackupError,
    SQLiteBackupManager,
)


NOW = 1_900_000_000_000


def _database(tmp_path: Path) -> Path:
    db_path = tmp_path / "canonical.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-backup",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    MemoryEventRepository(db_path).append_event(
        {
            "event_id": "memory-event-before-backup",
            "person_id": "person-1",
            "session_id": "session-1",
            "conversation_id": "session-1",
            "source_kind": "contract",
            "event_kind": "user_message",
            "visibility": "visible",
            "role": "user",
            "content": "before backup",
            "content_summary": "before backup",
            "created_at_ms": NOW,
        }
    )
    return db_path


def test_aw03_verified_backup_binds_migration_catalog_and_restores_offline(
    tmp_path: Path,
) -> None:
    source = _database(tmp_path)
    backup = tmp_path / "backup" / "canonical.sqlite3"
    manager = SQLiteBackupManager()

    receipt = manager.create_verified_backup(
        source_path=source,
        backup_path=backup,
        created_at_ms=NOW + 1,
    )
    verified = manager.verify_backup(backup_path=backup)

    assert verified == receipt
    assert receipt.migration_asset_count > 0
    assert int(receipt.migration_latest_key.split("#", 1)[0]) >= 70
    assert len(receipt.migration_catalog_digest) == 64
    assert Path(receipt.manifest_path).is_file()

    restored = tmp_path / "offline-restore" / "canonical.sqlite3"
    restore = manager.restore_verified_backup(
        backup_path=backup,
        target_path=restored,
        restored_at_ms=NOW + 2,
    )
    assert restore.migration_catalog_digest == receipt.migration_catalog_digest
    assert restore.migration_asset_count == receipt.migration_asset_count
    with sqlite3.connect(str(restored)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute(
            "SELECT content FROM memory_event WHERE event_id = ?",
            ("memory-event-before-backup",),
        ).fetchone() == ("before backup",)


def test_aw03_backup_manifest_catalog_tamper_fails_closed(tmp_path: Path) -> None:
    source = _database(tmp_path)
    backup = tmp_path / "backup.sqlite3"
    manager = SQLiteBackupManager()
    receipt = manager.create_verified_backup(source_path=source, backup_path=backup)
    manifest = Path(receipt.manifest_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["migration_catalog_digest"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SQLiteBackupError) as error:
        manager.verify_backup(backup_path=backup)
    assert error.value.reason_code == SQLITE_BACKUP_INTEGRITY_FAILED
