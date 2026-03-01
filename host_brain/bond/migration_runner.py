"""Bond migration runner with version guard and auditable records."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from time import perf_counter
import re
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    MIGRATION_ASSET_MISSING,
    MIGRATION_EXEC_FAILED,
    MIGRATION_SCHEMA_DRIFT,
    MIGRATION_VERSION_ROLLBACK,
)
from host_brain.core.ids import now_ms


S_MIGRATION_BOND = "S_MIGRATION_BOND"
SCOPE_BOND = "bond"

STATUS_APPLIED = "APPLIED"
STATUS_SKIPPED = "SKIPPED"
STATUS_FAILED = "FAILED"

_CYPHER_FILE_PATTERN = re.compile(r"^(\d{4})_(.+)\.cypher$")


@dataclass(frozen=True)
class BondMigrationAsset:
    migration_id: int
    name: str
    path: Path
    checksum: str


@dataclass(frozen=True)
class BondMigrationRecord:
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
class BondMigrationBatchResult:
    success: bool
    stage: str
    reason_code: str | None
    detail: str
    summary: dict[str, Any]
    records: tuple[BondMigrationRecord, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "summary": dict(self.summary),
            "records": [record.as_dict() for record in self.records],
        }


class BondMigrationRunner:
    """Apply cypher migration assets with idempotent guards."""

    def __init__(
        self,
        *,
        migrations_dir: str | Path | None = None,
        ledger: SQLiteLedger | None = None,
        applied_migration_ids: set[int] | None = None,
    ) -> None:
        self.migrations_dir = Path(migrations_dir or Path(__file__).parent / "migrations")
        self.ledger = ledger
        self._applied_ids: set[int] = set(int(value) for value in (applied_migration_ids or set()))
        self._records: list[BondMigrationRecord] = []

    def apply_migrations(
        self,
        *,
        request_id: str | None = None,
        ts_ms: int | None = None,
    ) -> BondMigrationBatchResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        assets = self._discover_assets()
        if not assets:
            return self._fail(
                request_id=request_id,
                migration_id="catalog",
                reason_code=MIGRATION_ASSET_MISSING,
                detail=f"no bond migration assets found in {self.migrations_dir}",
                ts_ms=now_value,
                latest_version=0,
            )

        gaps = _find_missing_versions([asset.migration_id for asset in assets])
        if gaps:
            return self._fail(
                request_id=request_id,
                migration_id="catalog",
                reason_code=MIGRATION_ASSET_MISSING,
                detail=f"bond migration catalog has gaps: missing {gaps}",
                ts_ms=now_value,
                latest_version=assets[-1].migration_id,
            )

        current_version = max(self._applied_ids) if self._applied_ids else 0
        latest_version = assets[-1].migration_id
        if current_version > latest_version:
            return self._fail(
                request_id=request_id,
                migration_id="catalog",
                reason_code=MIGRATION_VERSION_ROLLBACK,
                detail=(
                    f"bond schema version {current_version} is newer than "
                    f"available migrations {latest_version}"
                ),
                ts_ms=now_value,
                latest_version=latest_version,
            )

        available_ids = {asset.migration_id for asset in assets}
        missing_assets = sorted(self._applied_ids.difference(available_ids))
        if missing_assets:
            return self._fail(
                request_id=request_id,
                migration_id="catalog",
                reason_code=MIGRATION_SCHEMA_DRIFT,
                detail=f"applied bond migrations missing in assets: {missing_assets}",
                ts_ms=now_value,
                latest_version=latest_version,
            )

        applied_gaps = _find_missing_versions(sorted(self._applied_ids))
        if applied_gaps:
            return self._fail(
                request_id=request_id,
                migration_id="catalog",
                reason_code=MIGRATION_SCHEMA_DRIFT,
                detail=f"applied bond migration history has gaps: missing {applied_gaps}",
                ts_ms=now_value,
                latest_version=latest_version,
            )

        run_records: list[BondMigrationRecord] = []
        for asset in assets:
            migration_name = f"{asset.migration_id:04d}_{asset.name}"
            if asset.migration_id in self._applied_ids:
                skipped = self._record(
                    request_id=request_id,
                    migration_id=migration_name,
                    status=STATUS_SKIPPED,
                    reason_code=None,
                    detail="bond migration already applied",
                    latency_ms=0,
                    ts_ms=now_value,
                )
                self._append_record(skipped)
                run_records.append(skipped)
                continue

            started = perf_counter()
            try:
                text = asset.path.read_text(encoding="utf-8")
                statements = _extract_cypher_statements(text)
                if not statements:
                    raise ValueError("migration has no executable cypher statements")
                self._applied_ids.add(asset.migration_id)
            except Exception as exc:
                latency_ms = int(round((perf_counter() - started) * 1000))
                failed = self._record(
                    request_id=request_id,
                    migration_id=migration_name,
                    status=STATUS_FAILED,
                    reason_code=MIGRATION_EXEC_FAILED,
                    detail=f"bond migration execution failed: {exc}",
                    latency_ms=latency_ms,
                    ts_ms=now_value,
                )
                self._append_record(failed)
                run_records.append(failed)
                return BondMigrationBatchResult(
                    success=False,
                    stage=S_MIGRATION_BOND,
                    reason_code=MIGRATION_EXEC_FAILED,
                    detail=failed.detail,
                    summary=self._build_summary(
                        records=run_records,
                        latest_version=latest_version,
                    ),
                    records=tuple(run_records),
                )

            latency_ms = int(round((perf_counter() - started) * 1000))
            applied = self._record(
                request_id=request_id,
                migration_id=migration_name,
                status=STATUS_APPLIED,
                reason_code=None,
                detail="bond migration applied",
                latency_ms=latency_ms,
                ts_ms=now_value,
            )
            self._append_record(applied)
            run_records.append(applied)

        return BondMigrationBatchResult(
            success=True,
            stage=S_MIGRATION_BOND,
            reason_code=None,
            detail="bond migrations completed",
            summary=self._build_summary(
                records=run_records,
                latest_version=latest_version,
            ),
            records=tuple(run_records),
        )

    def read_migration_log(self) -> list[dict[str, Any]]:
        return [record.as_dict() for record in self._records]

    def _discover_assets(self) -> list[BondMigrationAsset]:
        if not self.migrations_dir.exists():
            return []
        assets: list[BondMigrationAsset] = []
        for path in sorted(self.migrations_dir.glob("*.cypher")):
            match = _CYPHER_FILE_PATTERN.match(path.name)
            if not match:
                continue
            migration_id = int(match.group(1))
            name = match.group(2)
            text = path.read_text(encoding="utf-8")
            assets.append(
                BondMigrationAsset(
                    migration_id=migration_id,
                    name=name,
                    path=path,
                    checksum=_sha256(text),
                )
            )
        assets.sort(key=lambda item: item.migration_id)
        return assets

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
    ) -> BondMigrationRecord:
        return BondMigrationRecord(
            request_id=request_id,
            scope=SCOPE_BOND,
            migration_id=migration_id,
            status=status,
            reason_code=reason_code,
            stage=S_MIGRATION_BOND,
            detail=detail,
            latency_ms=int(latency_ms),
            ts_ms=int(ts_ms),
        )

    def _append_record(self, record: BondMigrationRecord) -> None:
        self._records.append(record)
        if self.ledger is not None and hasattr(self.ledger, "log_migration"):
            self.ledger.log_migration(record.as_dict())

    def _fail(
        self,
        *,
        request_id: str | None,
        migration_id: str,
        reason_code: str,
        detail: str,
        ts_ms: int,
        latest_version: int,
    ) -> BondMigrationBatchResult:
        failed = self._record(
            request_id=request_id,
            migration_id=migration_id,
            status=STATUS_FAILED,
            reason_code=reason_code,
            detail=detail,
            latency_ms=0,
            ts_ms=ts_ms,
        )
        self._append_record(failed)
        return BondMigrationBatchResult(
            success=False,
            stage=S_MIGRATION_BOND,
            reason_code=reason_code,
            detail=detail,
            summary=self._build_summary(
                records=[failed],
                latest_version=latest_version,
            ),
            records=(failed,),
        )

    def _build_summary(self, *, records: list[BondMigrationRecord], latest_version: int) -> dict[str, Any]:
        applied_count = sum(1 for row in records if row.status == STATUS_APPLIED)
        skipped_count = sum(1 for row in records if row.status == STATUS_SKIPPED)
        failed_count = sum(1 for row in records if row.status == STATUS_FAILED)
        current_version = max(self._applied_ids) if self._applied_ids else 0
        return {
            "scope": SCOPE_BOND,
            "current_version": int(current_version),
            "latest_version": int(latest_version),
            "applied_count": int(applied_count),
            "skipped_count": int(skipped_count),
            "failed_count": int(failed_count),
        }


def _extract_cypher_statements(text: str) -> list[str]:
    statements: list[str] = []
    for raw in text.split(";"):
        line = raw.strip()
        if not line:
            continue
        statements.append(line + ";")
    return statements


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _find_missing_versions(versions: list[int]) -> list[int]:
    if not versions:
        return []
    expected = set(range(1, max(versions) + 1))
    actual = set(int(value) for value in versions)
    return sorted(expected.difference(actual))
