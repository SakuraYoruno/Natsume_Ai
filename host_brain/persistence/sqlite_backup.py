"""Verified, offline-safe SQLite backup and restore boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any
from uuid import uuid4


SQLITE_BACKUP_INVALID = "SQLITE_BACKUP_INVALID"
SQLITE_BACKUP_INTEGRITY_FAILED = "SQLITE_BACKUP_INTEGRITY_FAILED"
SQLITE_RESTORE_TARGET_EXISTS = "SQLITE_RESTORE_TARGET_EXISTS"


class SQLiteBackupError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or SQLITE_BACKUP_INVALID)
        self.detail = str(detail or "SQLite backup operation failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class SQLiteBackupReceipt:
    source_path: str
    backup_path: str
    manifest_path: str
    sha256: str
    page_count: int
    page_size: int
    schema_version: int
    migration_asset_count: int
    migration_latest_key: str
    migration_catalog_digest: str
    created_at_ms: int
    integrity_check: str = "ok"
    foreign_key_violation_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SQLiteRestoreReceipt:
    backup_path: str
    restored_path: str
    sha256: str
    page_count: int
    schema_version: int
    migration_asset_count: int
    migration_latest_key: str
    migration_catalog_digest: str
    restored_at_ms: int
    integrity_check: str = "ok"
    foreign_key_violation_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _canonical_file(path: str | Path, *, must_exist: bool) -> Path:
    raw = str(path or "").strip()
    if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
        raise SQLiteBackupError(
            SQLITE_BACKUP_INVALID,
            "backup paths must be canonical SQLite files",
        )
    resolved = Path(raw).expanduser().resolve()
    if must_exist and not resolved.is_file():
        raise SQLiteBackupError(SQLITE_BACKUP_INVALID, f"SQLite file does not exist: {resolved}")
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    """Flush directory metadata on POSIX and Windows."""

    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(str(path), flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(
        str(path),
        0x40000000,  # GENERIC_WRITE
        0x00000001 | 0x00000002,  # FILE_SHARE_READ | FILE_SHARE_WRITE
        None,
        3,  # OPEN_EXISTING
        0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in (None, invalid):
        raise SQLiteBackupError(
            SQLITE_BACKUP_INTEGRITY_FAILED,
            f"cannot open directory for fsync: {path}",
        )
    try:
        if not kernel32.FlushFileBuffers(handle):
            raise SQLiteBackupError(
                SQLITE_BACKUP_INTEGRITY_FAILED,
                f"cannot fsync directory: {path}",
            )
    finally:
        kernel32.CloseHandle(handle)


def _inspect(path: Path) -> dict[str, int | str]:
    uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        try:
            integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()
            integrity = "; ".join(str(row[0]) for row in integrity_rows)
            foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
            page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
            page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
            schema_version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
            catalog_rows = conn.execute(
                """
                SELECT asset_key, migration_id, name, checksum, disposition
                FROM schema_migration_assets
                ORDER BY migration_id ASC, asset_key ASC
                """
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise SQLiteBackupError(
            SQLITE_BACKUP_INTEGRITY_FAILED,
            f"cannot inspect SQLite image: {path}",
        ) from exc
    if integrity.lower() != "ok" or foreign_keys:
        raise SQLiteBackupError(
            SQLITE_BACKUP_INTEGRITY_FAILED,
            f"SQLite image failed integrity validation: {integrity}; foreign_keys={len(foreign_keys)}",
        )
    if not catalog_rows:
        raise SQLiteBackupError(
            SQLITE_BACKUP_INTEGRITY_FAILED,
            "SQLite image has no canonical migration catalog",
        )
    catalog_payload = [
        {
            "asset_key": str(row[0]),
            "migration_id": int(row[1]),
            "name": str(row[2]),
            "checksum": str(row[3]),
            "disposition": str(row[4]),
        }
        for row in catalog_rows
    ]
    catalog_digest = hashlib.sha256(
        json.dumps(
            catalog_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "integrity_check": integrity,
        "foreign_key_violation_count": len(foreign_keys),
        "page_count": page_count,
        "page_size": page_size,
        "schema_version": schema_version,
        "migration_asset_count": len(catalog_payload),
        "migration_latest_key": str(catalog_payload[-1]["asset_key"]),
        "migration_catalog_digest": catalog_digest,
    }


class SQLiteBackupManager:
    """Create verified online backups and restore them only to offline targets."""

    def create_verified_backup(
        self,
        *,
        source_path: str | Path,
        backup_path: str | Path,
        created_at_ms: int | None = None,
    ) -> SQLiteBackupReceipt:
        source = _canonical_file(source_path, must_exist=True)
        destination = _canonical_file(backup_path, must_exist=False)
        if source == destination:
            raise SQLiteBackupError(SQLITE_BACKUP_INVALID, "backup path must differ from source")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise SQLiteBackupError(SQLITE_BACKUP_INVALID, f"backup already exists: {destination}")
        temp = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        if temp.exists():
            temp.unlink()
        source_conn: sqlite3.Connection | None = None
        target_conn: sqlite3.Connection | None = None
        try:
            # The backup source is an authority owned by the running system.
            # Open it read-only so this utility cannot create journals, mutate
            # pragmas, or accidentally become a second writer.  Do not use
            # ``immutable=1`` here: an online source may have a live WAL that
            # SQLite must include in the consistent backup snapshot.
            source_conn = sqlite3.connect(
                f"file:{source.as_posix()}?mode=ro",
                uri=True,
                isolation_level=None,
            )
            target_conn = sqlite3.connect(str(temp), isolation_level=None)
            source_conn.backup(target_conn)
            target_conn.close()
            target_conn = None
            source_conn.close()
            source_conn = None
            inspection = _inspect(temp)
            _fsync_file(temp)
            os.replace(temp, destination)
            _fsync_file(destination)
            _fsync_directory(destination.parent)
        except BaseException:
            if target_conn is not None:
                target_conn.close()
            if source_conn is not None:
                source_conn.close()
            if temp.exists():
                temp.unlink()
            raise
        receipt = SQLiteBackupReceipt(
            source_path=str(source),
            backup_path=str(destination),
            manifest_path=str(destination.with_suffix(destination.suffix + ".manifest.json")),
            sha256=_sha256_file(destination),
            page_count=int(inspection["page_count"]),
            page_size=int(inspection["page_size"]),
            schema_version=int(inspection["schema_version"]),
            migration_asset_count=int(inspection["migration_asset_count"]),
            migration_latest_key=str(inspection["migration_latest_key"]),
            migration_catalog_digest=str(inspection["migration_catalog_digest"]),
            created_at_ms=int(created_at_ms or time.time() * 1000),
            integrity_check=str(inspection["integrity_check"]),
            foreign_key_violation_count=int(inspection["foreign_key_violation_count"]),
        )
        manifest = Path(receipt.manifest_path)
        manifest_temp = manifest.with_name(f".{manifest.name}.{uuid4().hex}.tmp")
        try:
            with manifest_temp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    receipt.as_dict(),
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(manifest_temp, manifest)
            _fsync_file(manifest)
            _fsync_directory(manifest.parent)
        finally:
            if manifest_temp.exists():
                manifest_temp.unlink()
        return receipt

    def verify_backup(self, *, backup_path: str | Path) -> SQLiteBackupReceipt:
        backup = _canonical_file(backup_path, must_exist=True)
        manifest_path = backup.with_suffix(backup.suffix + ".manifest.json")
        if not manifest_path.is_file():
            raise SQLiteBackupError(SQLITE_BACKUP_INVALID, "backup manifest is missing")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            receipt = SQLiteBackupReceipt(**manifest)
        except (OSError, TypeError, json.JSONDecodeError) as exc:
            raise SQLiteBackupError(SQLITE_BACKUP_INVALID, "backup manifest is invalid") from exc
        if Path(receipt.backup_path).resolve() != backup:
            raise SQLiteBackupError(SQLITE_BACKUP_INVALID, "backup manifest path mismatch")
        if _sha256_file(backup) != receipt.sha256:
            raise SQLiteBackupError(SQLITE_BACKUP_INTEGRITY_FAILED, "backup digest mismatch")
        inspection = _inspect(backup)
        if (
            int(inspection["page_count"]) != receipt.page_count
            or int(inspection["page_size"]) != receipt.page_size
            or int(inspection["schema_version"]) != receipt.schema_version
            or int(inspection["migration_asset_count"]) != receipt.migration_asset_count
            or str(inspection["migration_latest_key"]) != receipt.migration_latest_key
            or str(inspection["migration_catalog_digest"])
            != receipt.migration_catalog_digest
        ):
            raise SQLiteBackupError(SQLITE_BACKUP_INTEGRITY_FAILED, "backup metadata mismatch")
        return receipt

    def restore_verified_backup(
        self,
        *,
        backup_path: str | Path,
        target_path: str | Path,
        restored_at_ms: int | None = None,
    ) -> SQLiteRestoreReceipt:
        backup_receipt = self.verify_backup(backup_path=backup_path)
        backup = Path(backup_receipt.backup_path)
        target = _canonical_file(target_path, must_exist=False)
        if target.exists():
            raise SQLiteBackupError(
                SQLITE_RESTORE_TARGET_EXISTS,
                "offline restore target must not already exist",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{uuid4().hex}.restore.tmp")
        if temp.exists():
            temp.unlink()
        source_conn: sqlite3.Connection | None = None
        target_conn: sqlite3.Connection | None = None
        try:
            source_conn = sqlite3.connect(
                f"file:{backup.as_posix()}?mode=ro&immutable=1",
                uri=True,
                isolation_level=None,
            )
            target_conn = sqlite3.connect(str(temp), isolation_level=None)
            source_conn.backup(target_conn)
            target_conn.close()
            target_conn = None
            source_conn.close()
            source_conn = None
            inspection = _inspect(temp)
            if (
                int(inspection["migration_asset_count"])
                != backup_receipt.migration_asset_count
                or str(inspection["migration_latest_key"])
                != backup_receipt.migration_latest_key
                or str(inspection["migration_catalog_digest"])
                != backup_receipt.migration_catalog_digest
            ):
                raise SQLiteBackupError(
                    SQLITE_BACKUP_INTEGRITY_FAILED,
                    "restored migration catalog does not match backup receipt",
                )
            _fsync_file(temp)
            os.replace(temp, target)
            _fsync_file(target)
            _fsync_directory(target.parent)
        except BaseException:
            if target_conn is not None:
                target_conn.close()
            if source_conn is not None:
                source_conn.close()
            if temp.exists():
                temp.unlink()
            raise
        return SQLiteRestoreReceipt(
            backup_path=str(backup),
            restored_path=str(target),
            sha256=_sha256_file(target),
            page_count=int(inspection["page_count"]),
            schema_version=int(inspection["schema_version"]),
            migration_asset_count=int(inspection["migration_asset_count"]),
            migration_latest_key=str(inspection["migration_latest_key"]),
            migration_catalog_digest=str(inspection["migration_catalog_digest"]),
            restored_at_ms=int(restored_at_ms or time.time() * 1000),
            integrity_check=str(inspection["integrity_check"]),
            foreign_key_violation_count=int(inspection["foreign_key_violation_count"]),
        )


__all__ = [
    "SQLITE_BACKUP_INTEGRITY_FAILED",
    "SQLITE_BACKUP_INVALID",
    "SQLITE_RESTORE_TARGET_EXISTS",
    "SQLiteBackupError",
    "SQLiteBackupManager",
    "SQLiteBackupReceipt",
    "SQLiteRestoreReceipt",
]
