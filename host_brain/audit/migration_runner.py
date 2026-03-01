"""SQLite migration runner with version guards and auditable outcomes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from time import perf_counter
import re
import sqlite3
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    MIGRATION_ASSET_MISSING,
    MIGRATION_EXEC_FAILED,
    MIGRATION_SCHEMA_DRIFT,
    MIGRATION_VERSION_ROLLBACK,
)
from host_brain.core.ids import now_ms


S_MIGRATION_SQLITE = "S_MIGRATION_SQLITE"
SCOPE_SQLITE = "sqlite"

STATUS_APPLIED = "APPLIED"
STATUS_SKIPPED = "SKIPPED"
STATUS_FAILED = "FAILED"

_SQL_FILE_PATTERN = re.compile(r"^(\d{4})_(.+)\.sql$")


@dataclass(frozen=True)
class MigrationAsset:
    migration_id: int
    name: str
    path: Path
    checksum: str


@dataclass(frozen=True)
class MigrationRecord:
    request_id: str | None
    scope: str
    migration_id: str
    status: str
    reason_code: str | None
    stage: str
    detail: str
    latency_ms: int
    ts_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "scope": self.scope,
            "migration_id": self.migration_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "stage": self.stage,
            "detail": self.detail,
            "latency_ms": self.latency_ms,
            "ts_ms": self.ts_ms,
        }


@dataclass(frozen=True)
class MigrationBatchResult:
    success: bool
    stage: str
    reason_code: str | None
    detail: str
    summary: dict[str, Any]
    records: tuple[MigrationRecord, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "summary": dict(self.summary),
            "records": [record.as_dict() for record in self.records],
        }


class SQLiteMigrationRunner:
    """Apply SQL migration assets with idempotent and rollback-safe guards."""

    def __init__(
        self,
        *,
        db_path: str | Path,
        migrations_dir: str | Path | None = None,
        ledger: SQLiteLedger | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.migrations_dir = Path(migrations_dir or Path(__file__).parent / "migrations")
        self.ledger = ledger

    def apply_migrations(
        self,
        *,
        request_id: str | None = None,
        ts_ms: int | None = None,
    ) -> MigrationBatchResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            self._ensure_meta_tables(conn)

            assets = self._discover_assets()
            if not assets:
                return self._fail(
                    conn=conn,
                    request_id=request_id,
                    migration_id="catalog",
                    reason_code=MIGRATION_ASSET_MISSING,
                    detail=f"no migration assets found in {self.migrations_dir}",
                    records=[],
                    ts_ms=now_value,
                    current_version=self._current_version(conn),
                    latest_version=0,
                )

            gaps = _find_missing_versions([asset.migration_id for asset in assets])
            if gaps:
                return self._fail(
                    conn=conn,
                    request_id=request_id,
                    migration_id="catalog",
                    reason_code=MIGRATION_ASSET_MISSING,
                    detail=f"migration catalog has gaps: missing {gaps}",
                    records=[],
                    ts_ms=now_value,
                    current_version=self._current_version(conn),
                    latest_version=assets[-1].migration_id,
                )

            applied_ids = self._load_applied_ids(conn)
            current_version = max(applied_ids) if applied_ids else 0
            latest_version = assets[-1].migration_id
            if current_version > latest_version:
                return self._fail(
                    conn=conn,
                    request_id=request_id,
                    migration_id="catalog",
                    reason_code=MIGRATION_VERSION_ROLLBACK,
                    detail=(
                        f"db schema version {current_version} is newer than "
                        f"available migrations {latest_version}"
                    ),
                    records=[],
                    ts_ms=now_value,
                    current_version=current_version,
                    latest_version=latest_version,
                )

            available_ids = {asset.migration_id for asset in assets}
            missing_assets = sorted(applied_ids.difference(available_ids))
            if missing_assets:
                return self._fail(
                    conn=conn,
                    request_id=request_id,
                    migration_id="catalog",
                    reason_code=MIGRATION_SCHEMA_DRIFT,
                    detail=f"applied migrations missing in assets: {missing_assets}",
                    records=[],
                    ts_ms=now_value,
                    current_version=current_version,
                    latest_version=latest_version,
                )

            applied_gaps = _find_missing_versions(sorted(applied_ids))
            if applied_gaps:
                return self._fail(
                    conn=conn,
                    request_id=request_id,
                    migration_id="catalog",
                    reason_code=MIGRATION_SCHEMA_DRIFT,
                    detail=f"applied migration history has gaps: missing {applied_gaps}",
                    records=[],
                    ts_ms=now_value,
                    current_version=current_version,
                    latest_version=latest_version,
                )

            records: list[MigrationRecord] = []
            for asset in assets:
                if asset.migration_id in applied_ids:
                    skip = self._record(
                        request_id=request_id,
                        migration_id=f"{asset.migration_id:04d}_{asset.name}",
                        status=STATUS_SKIPPED,
                        reason_code=None,
                        detail="migration already applied",
                        latency_ms=0,
                        ts_ms=now_value,
                    )
                    self._log_record(conn, skip)
                    conn.commit()
                    records.append(skip)
                    continue

                started = perf_counter()
                try:
                    script = asset.path.read_text(encoding="utf-8")
                    conn.executescript(script)
                    conn.execute(
                        """
                        INSERT INTO schema_migrations(migration_id, name, checksum, applied_ts_ms)
                        VALUES (?, ?, ?, ?)
                        """,
                        (asset.migration_id, asset.name, asset.checksum, now_value),
                    )
                except Exception as exc:
                    latency_ms = int(round((perf_counter() - started) * 1000))
                    failed = self._record(
                        request_id=request_id,
                        migration_id=f"{asset.migration_id:04d}_{asset.name}",
                        status=STATUS_FAILED,
                        reason_code=MIGRATION_EXEC_FAILED,
                        detail=f"migration execution failed: {exc}",
                        latency_ms=latency_ms,
                        ts_ms=now_value,
                    )
                    self._log_record(conn, failed)
                    conn.commit()
                    records.append(failed)
                    return MigrationBatchResult(
                        success=False,
                        stage=S_MIGRATION_SQLITE,
                        reason_code=MIGRATION_EXEC_FAILED,
                        detail=failed.detail,
                        summary=self._build_summary(
                            records=records,
                            current_version=(max(self._load_applied_ids(conn)) if self._load_applied_ids(conn) else 0),
                            latest_version=latest_version,
                        ),
                        records=tuple(records),
                    )

                latency_ms = int(round((perf_counter() - started) * 1000))
                applied = self._record(
                    request_id=request_id,
                    migration_id=f"{asset.migration_id:04d}_{asset.name}",
                    status=STATUS_APPLIED,
                    reason_code=None,
                    detail="migration applied",
                    latency_ms=latency_ms,
                    ts_ms=now_value,
                )
                self._log_record(conn, applied)
                conn.commit()
                records.append(applied)

            new_version = max(self._load_applied_ids(conn)) if self._load_applied_ids(conn) else 0
            return MigrationBatchResult(
                success=True,
                stage=S_MIGRATION_SQLITE,
                reason_code=None,
                detail="sqlite migrations completed",
                summary=self._build_summary(
                    records=records,
                    current_version=new_version,
                    latest_version=latest_version,
                ),
                records=tuple(records),
            )
        finally:
            conn.close()

    def read_migration_log(self) -> list[dict[str, Any]]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            self._ensure_meta_tables(conn)
            rows = conn.execute(
                """
                SELECT request_id, scope, migration_id, status, reason_code, stage, detail, latency_ms, ts_ms
                FROM migration_log
                ORDER BY id ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def _discover_assets(self) -> list[MigrationAsset]:
        if not self.migrations_dir.exists():
            return []
        assets: list[MigrationAsset] = []
        for path in sorted(self.migrations_dir.glob("*.sql")):
            match = _SQL_FILE_PATTERN.match(path.name)
            if not match:
                continue
            migration_id = int(match.group(1))
            name = match.group(2)
            text = path.read_text(encoding="utf-8")
            assets.append(
                MigrationAsset(
                    migration_id=migration_id,
                    name=name,
                    path=path,
                    checksum=_sha256(text),
                )
            )
        assets.sort(key=lambda item: item.migration_id)
        return assets

    def _ensure_meta_tables(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_ts_ms INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS migration_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT,
                scope TEXT NOT NULL,
                migration_id TEXT NOT NULL,
                status TEXT NOT NULL,
                reason_code TEXT,
                stage TEXT NOT NULL,
                detail TEXT NOT NULL,
                latency_ms INTEGER NOT NULL,
                ts_ms INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_schema_migrations_name ON schema_migrations(name);
            CREATE INDEX IF NOT EXISTS idx_migration_log_request ON migration_log(request_id);
            CREATE INDEX IF NOT EXISTS idx_migration_log_scope ON migration_log(scope);
            CREATE INDEX IF NOT EXISTS idx_migration_log_migration_id ON migration_log(migration_id);
            """
        )

    def _load_applied_ids(self, conn: sqlite3.Connection) -> set[int]:
        rows = conn.execute("SELECT migration_id FROM schema_migrations").fetchall()
        return {int(row[0]) for row in rows}

    def _current_version(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT MAX(migration_id) FROM schema_migrations").fetchone()
        if row is None or row[0] is None:
            return 0
        return int(row[0])

    def _record(
        self,
        *,
        request_id: str | None,
        migration_id: str,
        status: str,
        reason_code: str | None,
        detail: str,
        latency_ms: int,
        ts_ms: int,
    ) -> MigrationRecord:
        return MigrationRecord(
            request_id=request_id,
            scope=SCOPE_SQLITE,
            migration_id=migration_id,
            status=status,
            reason_code=reason_code,
            stage=S_MIGRATION_SQLITE,
            detail=detail,
            latency_ms=int(latency_ms),
            ts_ms=int(ts_ms),
        )

    def _log_record(self, conn: sqlite3.Connection, record: MigrationRecord) -> None:
        conn.execute(
            """
            INSERT INTO migration_log(request_id, scope, migration_id, status, reason_code, stage, detail, latency_ms, ts_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.request_id,
                record.scope,
                record.migration_id,
                record.status,
                record.reason_code,
                record.stage,
                record.detail,
                record.latency_ms,
                record.ts_ms,
            ),
        )
        if self.ledger is not None and hasattr(self.ledger, "log_migration"):
            self.ledger.log_migration(record.as_dict())

    def _fail(
        self,
        *,
        conn: sqlite3.Connection,
        request_id: str | None,
        migration_id: str,
        reason_code: str,
        detail: str,
        records: list[MigrationRecord],
        ts_ms: int,
        current_version: int,
        latest_version: int,
    ) -> MigrationBatchResult:
        failed = self._record(
            request_id=request_id,
            migration_id=migration_id,
            status=STATUS_FAILED,
            reason_code=reason_code,
            detail=detail,
            latency_ms=0,
            ts_ms=ts_ms,
        )
        self._log_record(conn, failed)
        conn.commit()
        merged = list(records) + [failed]
        return MigrationBatchResult(
            success=False,
            stage=S_MIGRATION_SQLITE,
            reason_code=reason_code,
            detail=detail,
            summary=self._build_summary(
                records=merged,
                current_version=current_version,
                latest_version=latest_version,
            ),
            records=tuple(merged),
        )

    def _build_summary(
        self,
        *,
        records: list[MigrationRecord],
        current_version: int,
        latest_version: int,
    ) -> dict[str, Any]:
        applied_count = sum(1 for row in records if row.status == STATUS_APPLIED)
        skipped_count = sum(1 for row in records if row.status == STATUS_SKIPPED)
        failed_count = sum(1 for row in records if row.status == STATUS_FAILED)
        return {
            "scope": SCOPE_SQLITE,
            "current_version": int(current_version),
            "latest_version": int(latest_version),
            "applied_count": int(applied_count),
            "skipped_count": int(skipped_count),
            "failed_count": int(failed_count),
        }


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _find_missing_versions(versions: list[int]) -> list[int]:
    if not versions:
        return []
    expected = set(range(1, max(versions) + 1))
    actual = set(int(value) for value in versions)
    return sorted(expected.difference(actual))
