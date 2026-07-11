"""SQLite 迁移版本守卫（M43-B）。

在系统启动时检查 host_brain/audit/migrations/ 目录的版本连续性，
防止 Schema 漂移（重复版本号、版本跳跃、迁移文件缺失）。

与 BondMigrationRunner 的区别：
  - BondMigrationRunner：执行 Cypher 迁移（Neo4j Bond Schema）
  - SQLiteMigrationVersionGuard：只做静态检查，不执行 SQL（SQLite 迁移由 SQLiteLedger 自身管理）
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from host_brain.audit.migration_compat import evaluate_catalog_compatibility
from host_brain.core.errors import MIGRATION_ASSET_MISSING, MIGRATION_SCHEMA_DRIFT
from host_brain.core.ids import now_ms


S_MIGRATION_SQLITE = "S_MIGRATION_SQLITE"
STATUS_READY = "READY"
STATUS_DEGRADED = "DEGRADED"
STATUS_FAILED = "FAILED"

_SQL_FILE_PATTERN = re.compile(r"^(\d{4})_(.+)\.sql$")

# 允许的版本跳跃（历史遗留，已知的跳跃区间）
# 格式：(from_version, to_version) 表示 from_version+1 ~ to_version-1 允许缺失
_KNOWN_GAPS: frozenset[int] = frozenset()


@dataclass(frozen=True)
class SQLiteMigrationAsset:
    """SQLite 迁移文件资产。"""

    migration_id: int
    name: str
    path: Path
    checksum: str


@dataclass(frozen=True)
class SQLiteMigrationGuardReport:
    """SQLite 迁移版本守卫报告。"""

    request_id: str | None
    status: str
    reason_code: str | None
    detail: str
    stage: str
    asset_count: int
    duplicate_versions: list[int]
    missing_versions: list[int]
    latest_version: int
    ts_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "stage": self.stage,
            "asset_count": self.asset_count,
            "duplicate_versions": list(self.duplicate_versions),
            "missing_versions": list(self.missing_versions),
            "latest_version": self.latest_version,
            "ts_ms": self.ts_ms,
        }


class SQLiteMigrationVersionGuard:
    """SQLite Schema 版本守卫。

    静态检查 migrations/ 目录的版本连续性，不执行任何 SQL。
    在系统启动时调用 build_guard_report() 获取版本守卫报告。
    """

    def __init__(
        self,
        *,
        migrations_dir: str | Path | None = None,
        known_gaps: frozenset[int] | None = None,
    ) -> None:
        self.migrations_dir = Path(
            migrations_dir or Path(__file__).parent / "migrations"
        )
        # 允许跳过的版本号（历史遗留跳跃）
        self._known_gaps: frozenset[int] = known_gaps if known_gaps is not None else _KNOWN_GAPS

    def discover_assets(self) -> tuple[list[SQLiteMigrationAsset], list[int]]:
        """发现并排序迁移文件，检测重复版本号。

        Returns:
            (assets, duplicate_versions)
            - assets: 按版本号排序的资产列表（重复版本只保留第一个）
            - duplicate_versions: 重复的版本号列表
        """
        physical_assets = self._discover_physical_assets()
        raw: dict[int, list[SQLiteMigrationAsset]] = {}
        for asset in physical_assets:
            raw.setdefault(asset.migration_id, []).append(asset)

        # 检测重复版本号
        duplicate_versions = sorted(
            mid for mid, assets in raw.items() if len(assets) > 1
        )

        # 构建资产列表（重复版本只保留第一个，按文件名排序）
        assets: list[SQLiteMigrationAsset] = []
        for mid in sorted(raw.keys()):
            assets.append(raw[mid][0])

        return assets, duplicate_versions

    def _discover_physical_assets(self) -> list[SQLiteMigrationAsset]:
        if not self.migrations_dir.exists():
            return []
        assets: list[SQLiteMigrationAsset] = []
        for path in sorted(self.migrations_dir.glob("*.sql")):
            match = _SQL_FILE_PATTERN.match(path.name)
            if not match:
                continue
            text = path.read_text(encoding="utf-8-sig")
            assets.append(
                SQLiteMigrationAsset(
                    migration_id=int(match.group(1)),
                    name=match.group(2),
                    path=path,
                    checksum=_sha256(text),
                )
            )
        assets.sort(key=lambda item: (item.migration_id, item.path.name))
        return assets

    def check_version_continuity(
        self,
        assets: list[SQLiteMigrationAsset],
        *,
        accepted_gaps: frozenset[int] = frozenset(),
    ) -> list[int]:
        """检查版本连续性，返回缺失的版本号列表（排除已知跳跃）。

        Args:
            assets: 已排序的迁移资产列表

        Returns:
            缺失的版本号列表（不含已知跳跃）
        """
        if not assets:
            return []
        versions = [asset.migration_id for asset in assets]
        expected = set(range(1, max(versions) + 1))
        actual = set(versions)
        missing = sorted(expected - actual - self._known_gaps - accepted_gaps)
        return missing

    def build_guard_report(
        self,
        *,
        request_id: str | None = None,
        ts_ms: int | None = None,
    ) -> SQLiteMigrationGuardReport:
        """生成版本守卫报告。

        Args:
            request_id: 请求 ID（可选）
            ts_ms: 时间戳（可选，默认当前时间）

        Returns:
            SQLiteMigrationGuardReport
        """
        now_value = int(ts_ms if ts_ms is not None else now_ms())

        # 目录不存在
        if not self.migrations_dir.exists():
            return SQLiteMigrationGuardReport(
                request_id=request_id,
                status=STATUS_FAILED,
                reason_code=MIGRATION_ASSET_MISSING,
                detail=f"migrations directory not found: {self.migrations_dir}",
                stage=S_MIGRATION_SQLITE,
                asset_count=0,
                duplicate_versions=[],
                missing_versions=[],
                latest_version=0,
                ts_ms=now_value,
            )

        physical_assets = self._discover_physical_assets()
        assets, duplicate_versions = self.discover_assets()
        compatibility = evaluate_catalog_compatibility(physical_assets)
        unexpected_duplicates = sorted(
            set(duplicate_versions).difference(
                compatibility.accepted_duplicate_versions
            )
        )

        # 只有 checksum-bound 的历史 0009 alias 可被接受。
        if compatibility.issues or unexpected_duplicates:
            details = list(compatibility.issues)
            if unexpected_duplicates:
                details.append(
                    f"duplicate migration version(s) detected: {unexpected_duplicates}"
                )
            return SQLiteMigrationGuardReport(
                request_id=request_id,
                status=STATUS_FAILED,
                reason_code=MIGRATION_SCHEMA_DRIFT,
                detail="; ".join(details),
                stage=S_MIGRATION_SQLITE,
                asset_count=len(physical_assets),
                duplicate_versions=unexpected_duplicates,
                missing_versions=[],
                latest_version=assets[-1].migration_id if assets else 0,
                ts_ms=now_value,
            )

        # 无迁移文件
        if not assets:
            return SQLiteMigrationGuardReport(
                request_id=request_id,
                status=STATUS_FAILED,
                reason_code=MIGRATION_ASSET_MISSING,
                detail="no SQL migration assets found",
                stage=S_MIGRATION_SQLITE,
                asset_count=0,
                duplicate_versions=[],
                missing_versions=[],
                latest_version=0,
                ts_ms=now_value,
            )

        missing_versions = self.check_version_continuity(
            assets,
            accepted_gaps=compatibility.accepted_gap_versions,
        )
        latest_version = assets[-1].migration_id

        # 存在版本跳跃 → DEGRADED（不阻断启动，但需要关注）
        if missing_versions:
            return SQLiteMigrationGuardReport(
                request_id=request_id,
                status=STATUS_DEGRADED,
                reason_code=MIGRATION_ASSET_MISSING,
                detail=f"migration version gap(s) detected: missing {missing_versions}",
                stage=S_MIGRATION_SQLITE,
                asset_count=len(physical_assets),
                duplicate_versions=[],
                missing_versions=missing_versions,
                latest_version=latest_version,
                ts_ms=now_value,
            )

        # 一切正常 → READY
        return SQLiteMigrationGuardReport(
            request_id=request_id,
            status=STATUS_READY,
            reason_code=None,
            detail=(
                f"all {len(physical_assets)} physical migration assets verified, "
                f"latest version: {latest_version}"
            ),
            stage=S_MIGRATION_SQLITE,
            asset_count=len(physical_assets),
            duplicate_versions=[],
            missing_versions=[],
            latest_version=latest_version,
            ts_ms=now_value,
        )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
