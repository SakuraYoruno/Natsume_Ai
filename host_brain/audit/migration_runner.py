"""SQLite migration runner with version guards and auditable outcomes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from time import perf_counter
import re
import sqlite3
from typing import Any

from host_brain.audit.migration_compat import (
    PLATFORM_REGISTRY_DRAFT_CHECKSUM,
    evaluate_catalog_compatibility,
    is_exact_legacy_inline_schema_failure,
    is_exact_legacy_fts_failure,
    is_exact_platform_registry_draft_record,
    is_exact_platform_registry_repair_asset,
    is_legacy_duplicate_primary,
    legacy_inline_schema_postcondition_holds,
    legacy_fts_postcondition_holds,
    platform_registry_schema_postcondition_holds,
    physical_asset_key,
    transaction_policy_issues,
    uses_checksum_bound_atomic_envelope,
)
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

ASSET_DISPOSITION_APPLIED = "APPLIED"
ASSET_DISPOSITION_APPLIED_WITH_COMPAT = "APPLIED_WITH_COMPAT"
ASSET_DISPOSITION_LEGACY_BACKFILL = "LEGACY_BACKFILL"

_SQL_FILE_PATTERN = re.compile(r"^(\d{4})_(.+)\.sql$")


@dataclass(frozen=True)
class MigrationAsset:
    migration_id: int
    name: str
    path: Path
    checksum: str

    @property
    def asset_key(self) -> str:
        return physical_asset_key(self)


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

            compatibility = evaluate_catalog_compatibility(assets)
            transaction_issues = transaction_policy_issues(assets)
            duplicate_versions = _find_duplicate_versions(
                [asset.migration_id for asset in assets]
            )
            unexpected_duplicates = sorted(
                set(duplicate_versions).difference(
                    compatibility.accepted_duplicate_versions
                )
            )
            if compatibility.issues or unexpected_duplicates or transaction_issues:
                details = [*compatibility.issues, *transaction_issues]
                if unexpected_duplicates:
                    details.append(
                        f"duplicate migration versions are not compatible: {unexpected_duplicates}"
                    )
                return self._fail(
                    conn=conn,
                    request_id=request_id,
                    migration_id="catalog",
                    reason_code=MIGRATION_SCHEMA_DRIFT,
                    detail="; ".join(details),
                    records=[],
                    ts_ms=now_value,
                    current_version=self._current_version(conn),
                    latest_version=assets[-1].migration_id,
                )

            gaps = _find_missing_versions(
                [asset.migration_id for asset in assets],
                ignored_versions=compatibility.accepted_gap_versions,
            )
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

            applied_records = self._load_applied_records(conn)
            applied_ids = set(applied_records)
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

            applied_drift = self._validate_applied_records(
                conn=conn,
                assets=assets,
                applied_records=applied_records,
            )
            if applied_drift:
                return self._fail(
                    conn=conn,
                    request_id=request_id,
                    migration_id="catalog",
                    reason_code=MIGRATION_SCHEMA_DRIFT,
                    detail=applied_drift,
                    records=[],
                    ts_ms=now_value,
                    current_version=current_version,
                    latest_version=latest_version,
                )

            physical_records = self._load_applied_asset_records(conn)
            physical_drift = self._validate_applied_asset_records(
                assets=assets,
                applied_records=applied_records,
                physical_records=physical_records,
                accepted_duplicate_versions=compatibility.accepted_duplicate_versions,
            )
            if physical_drift:
                return self._fail(
                    conn=conn,
                    request_id=request_id,
                    migration_id="catalog",
                    reason_code=MIGRATION_SCHEMA_DRIFT,
                    detail=physical_drift,
                    records=[],
                    ts_ms=now_value,
                    current_version=current_version,
                    latest_version=latest_version,
                )

            applied_gaps = _find_missing_versions(
                sorted(applied_ids),
                ignored_versions=compatibility.accepted_gap_versions,
            )
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
                physical = physical_records.get(asset.asset_key)
                if physical is not None:
                    skip = self._record(
                        request_id=request_id,
                        migration_id=f"{asset.migration_id:04d}_{asset.name}",
                        status=STATUS_SKIPPED,
                        reason_code=None,
                        detail="physical migration asset already applied with matching checksum",
                        latency_ms=0,
                        ts_ms=now_value,
                    )
                    self._log_record(conn, skip)
                    conn.commit()
                    records.append(skip)
                    continue

                legacy = applied_records.get(asset.migration_id)
                if legacy is not None and _record_matches_asset(legacy, asset):
                    self._insert_applied_asset_record(
                        conn,
                        asset=asset,
                        disposition=ASSET_DISPOSITION_LEGACY_BACKFILL,
                        applied_ts_ms=int(legacy["applied_ts_ms"]),
                    )
                    skip = self._record(
                        request_id=request_id,
                        migration_id=f"{asset.migration_id:04d}_{asset.name}",
                        status=STATUS_SKIPPED,
                        reason_code=None,
                        detail="legacy migration record checksum verified and physical asset backfilled",
                        latency_ms=0,
                        ts_ms=now_value,
                    )
                    self._log_record(conn, skip)
                    conn.commit()
                    physical_records[asset.asset_key] = {
                        "asset_key": asset.asset_key,
                        "migration_id": asset.migration_id,
                        "name": asset.name,
                        "checksum": asset.checksum,
                        "disposition": ASSET_DISPOSITION_LEGACY_BACKFILL,
                        "applied_ts_ms": int(legacy["applied_ts_ms"]),
                    }
                    records.append(skip)
                    continue

                started = perf_counter()
                disposition = ASSET_DISPOSITION_APPLIED
                applied_detail = "migration applied"
                try:
                    script = asset.path.read_text(encoding="utf-8-sig")
                    platform_registry_repair = (
                        is_exact_platform_registry_repair_asset(asset)
                    )
                    if platform_registry_repair:
                        if not platform_registry_schema_postcondition_holds(
                            conn,
                            allow_missing_repair_trigger=True,
                        ):
                            raise sqlite3.IntegrityError(
                                "0072 platform registry precondition failed"
                            )
                        script = _without_final_commit(script)
                    elif uses_checksum_bound_atomic_envelope(asset):
                        script = f"BEGIN IMMEDIATE;\n{script}\nCOMMIT;\n"
                    conn.executescript(script)
                    if platform_registry_repair:
                        if not conn.in_transaction:
                            raise sqlite3.IntegrityError(
                                "0075 repair transaction closed before postcondition"
                            )
                        if not platform_registry_schema_postcondition_holds(
                            conn,
                            allow_missing_repair_trigger=False,
                        ):
                            raise sqlite3.IntegrityError(
                                "0072 platform registry strict postcondition failed"
                            )
                except Exception as exc:
                    if is_exact_legacy_fts_failure(
                        asset, exc
                    ) and legacy_fts_postcondition_holds(conn):
                        disposition = ASSET_DISPOSITION_APPLIED_WITH_COMPAT
                        applied_detail = (
                            "migration applied with checksum-bound legacy FTS compatibility"
                        )
                    elif is_exact_legacy_inline_schema_failure(
                        asset, exc
                    ) and legacy_inline_schema_postcondition_holds(conn, asset):
                        disposition = ASSET_DISPOSITION_APPLIED_WITH_COMPAT
                        applied_detail = (
                            "migration backfilled with checksum-bound legacy inline-schema compatibility"
                        )
                    else:
                        # Scripts such as 0052+ use an explicit transaction.
                        # Never let the failure audit commit their partial DDL.
                        conn.rollback()
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
                                current_version=(
                                    max(self._load_applied_ids(conn))
                                    if self._load_applied_ids(conn)
                                    else 0
                                ),
                                latest_version=latest_version,
                            ),
                            records=tuple(records),
                        )

                try:
                    if asset.migration_id not in applied_ids:
                        conn.execute(
                            """
                            INSERT INTO schema_migrations(migration_id, name, checksum, applied_ts_ms)
                            VALUES (?, ?, ?, ?)
                            """,
                            (asset.migration_id, asset.name, asset.checksum, now_value),
                        )
                        applied_ids.add(asset.migration_id)
                        applied_records[asset.migration_id] = {
                            "migration_id": asset.migration_id,
                            "name": asset.name,
                            "checksum": asset.checksum,
                            "applied_ts_ms": now_value,
                        }
                    self._insert_applied_asset_record(
                        conn,
                        asset=asset,
                        disposition=disposition,
                        applied_ts_ms=now_value,
                    )
                except Exception as exc:
                    conn.rollback()
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
                physical_records[asset.asset_key] = {
                    "asset_key": asset.asset_key,
                    "migration_id": asset.migration_id,
                    "name": asset.name,
                    "checksum": asset.checksum,
                    "disposition": disposition,
                    "applied_ts_ms": now_value,
                }
                applied = self._record(
                    request_id=request_id,
                    migration_id=f"{asset.migration_id:04d}_{asset.name}",
                    status=STATUS_APPLIED,
                    reason_code=None,
                    detail=applied_detail,
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
            text = path.read_text(encoding="utf-8-sig")
            assets.append(
                MigrationAsset(
                    migration_id=migration_id,
                    name=name,
                    path=path,
                    checksum=_sha256(text),
                )
            )
        assets.sort(
            key=lambda item: (
                item.migration_id,
                0 if is_legacy_duplicate_primary(item) else 1,
                item.path.name,
            )
        )
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

            CREATE TABLE IF NOT EXISTS schema_migration_assets (
                asset_key TEXT PRIMARY KEY,
                migration_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                disposition TEXT NOT NULL,
                applied_ts_ms INTEGER NOT NULL,
                UNIQUE(migration_id, name)
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
            CREATE INDEX IF NOT EXISTS idx_schema_migration_assets_version
                ON schema_migration_assets(migration_id);
            CREATE INDEX IF NOT EXISTS idx_migration_log_request ON migration_log(request_id);
            CREATE INDEX IF NOT EXISTS idx_migration_log_scope ON migration_log(scope);
            CREATE INDEX IF NOT EXISTS idx_migration_log_migration_id ON migration_log(migration_id);
            """
        )

    def _load_applied_ids(self, conn: sqlite3.Connection) -> set[int]:
        rows = conn.execute("SELECT migration_id FROM schema_migrations").fetchall()
        return {int(row[0]) for row in rows}

    def _load_applied_records(
        self, conn: sqlite3.Connection
    ) -> dict[int, sqlite3.Row]:
        rows = conn.execute(
            """
            SELECT migration_id, name, checksum, applied_ts_ms
            FROM schema_migrations
            """
        ).fetchall()
        return {int(row["migration_id"]): row for row in rows}

    def _load_applied_asset_records(
        self, conn: sqlite3.Connection
    ) -> dict[str, sqlite3.Row]:
        rows = conn.execute(
            """
            SELECT asset_key, migration_id, name, checksum, disposition, applied_ts_ms
            FROM schema_migration_assets
            """
        ).fetchall()
        return {str(row["asset_key"]): row for row in rows}

    def _validate_applied_records(
        self,
        *,
        conn: sqlite3.Connection,
        assets: list[MigrationAsset],
        applied_records: dict[int, sqlite3.Row],
    ) -> str | None:
        available: dict[int, list[MigrationAsset]] = {}
        for asset in assets:
            available.setdefault(asset.migration_id, []).append(asset)
        for migration_id, record in sorted(applied_records.items()):
            candidates = available.get(migration_id, [])
            if not candidates:
                return f"applied migration {migration_id:04d} is missing from catalog assets"
            if not any(
                _record_matches_asset(record, asset)
                or is_exact_platform_registry_draft_record(
                    record,
                    asset,
                    catalog_assets=assets,
                    repair_applied=75 in applied_records,
                )
                for asset in candidates
            ):
                return (
                    f"applied migration {migration_id:04d} name/checksum does not match "
                    "any physical catalog asset"
                )
        platform_record = applied_records.get(72)
        if platform_record is not None:
            checksum = str(platform_record["checksum"])
            repair_applied = 75 in applied_records
            allow_missing_repair_trigger = (
                checksum == PLATFORM_REGISTRY_DRAFT_CHECKSUM
                and not repair_applied
            )
            if not platform_registry_schema_postcondition_holds(
                conn,
                allow_missing_repair_trigger=allow_missing_repair_trigger,
            ):
                return (
                    "applied migration 0072 platform registry physical schema "
                    "does not satisfy its checksum-bound postcondition"
                )
        return None

    def _validate_applied_asset_records(
        self,
        *,
        assets: list[MigrationAsset],
        applied_records: dict[int, sqlite3.Row],
        physical_records: dict[str, sqlite3.Row],
        accepted_duplicate_versions: frozenset[int],
    ) -> str | None:
        available = {asset.asset_key: asset for asset in assets}
        allowed_dispositions = {
            ASSET_DISPOSITION_APPLIED,
            ASSET_DISPOSITION_APPLIED_WITH_COMPAT,
            ASSET_DISPOSITION_LEGACY_BACKFILL,
        }
        for asset_key, record in sorted(physical_records.items()):
            asset = available.get(asset_key)
            if asset is None:
                return f"applied physical migration asset is missing from catalog: {asset_key}"
            record_mismatch = (
                int(record["migration_id"]) != asset.migration_id
                or str(record["name"]) != asset.name
                or str(record["checksum"]) != asset.checksum
            )
            draft_compatible = is_exact_platform_registry_draft_record(
                record,
                asset,
                catalog_assets=assets,
                repair_applied=75 in applied_records,
            )
            if record_mismatch and not draft_compatible:
                return f"physical migration asset checksum drift: {asset_key}"
            if str(record["disposition"]) not in allowed_dispositions:
                return f"physical migration asset has invalid disposition: {asset_key}"
            if asset.migration_id not in applied_records:
                return (
                    f"physical migration asset lacks legacy version record: {asset_key}"
                )
            if (
                asset.migration_id not in accepted_duplicate_versions
                and not _record_matches_asset(applied_records[asset.migration_id], asset)
                and not is_exact_platform_registry_draft_record(
                    applied_records[asset.migration_id],
                    asset,
                    catalog_assets=assets,
                    repair_applied=75 in applied_records,
                )
            ):
                return f"physical and legacy migration records disagree: {asset_key}"
        return None

    def _insert_applied_asset_record(
        self,
        conn: sqlite3.Connection,
        *,
        asset: MigrationAsset,
        disposition: str,
        applied_ts_ms: int,
    ) -> None:
        conn.execute(
            """
            INSERT INTO schema_migration_assets(
                asset_key, migration_id, name, checksum, disposition, applied_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                asset.asset_key,
                asset.migration_id,
                asset.name,
                asset.checksum,
                disposition,
                int(applied_ts_ms),
            ),
        )

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


def _without_final_commit(script: str) -> str:
    match = re.search(r"\bCOMMIT\s*;\s*\Z", str(script), flags=re.IGNORECASE)
    if match is None:
        raise ValueError("checksum-bound repair asset has no final COMMIT")
    return str(script)[: match.start()]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _find_duplicate_versions(versions: list[int]) -> list[int]:
    seen: set[int] = set()
    duplicates: set[int] = set()
    for value in versions:
        normalized = int(value)
        if normalized in seen:
            duplicates.add(normalized)
        seen.add(normalized)
    return sorted(duplicates)


def _find_missing_versions(
    versions: list[int],
    *,
    ignored_versions: frozenset[int] = frozenset(),
) -> list[int]:
    if not versions:
        return []
    expected = set(range(1, max(versions) + 1))
    actual = set(int(value) for value in versions)
    return sorted(expected.difference(actual).difference(ignored_versions))


def _record_matches_asset(record: Any, asset: MigrationAsset) -> bool:
    return (
        str(record["name"]) == asset.name
        and str(record["checksum"]) == asset.checksum
    )
