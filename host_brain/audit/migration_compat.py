"""Checksum-bound compatibility policy for the frozen SQLite catalog debt.

The SQL directory remains the only migration catalog.  This module does not
invent replacement versions or rewrite historical assets; it only describes
the two anomalies that already exist in that directory and the one historical
SQLite execution quirk that must be handled to replay the catalog.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


LEGACY_DUPLICATE_VERSION = 9
LEGACY_DUPLICATE_ASSETS: dict[str, str] = {
    "0009_agent_loop_log.sql": "244dc97e2ca1e4060dd65dcdf3244a4ae03b000527534ca28cdffd1f0d02924e",
    "0009_service_health_schema.sql": "6d2409ed28016cf260e56d137dd7912cc12b0395f7eae8b7d838837678706bdf",
}
LEGACY_DUPLICATE_PRIMARY = "0009_agent_loop_log.sql"

LEGACY_GAP_VERSIONS = frozenset(range(23, 39))
LEGACY_GAP_ANCHORS: dict[str, str] = {
    "0022_llm_route_log.sql": "f1f0a653f584ec889ab54d7a3af381b51c50a7050a6ecdd382c94323497c0128",
    "0039_llm_route_log.sql": "839d7077ca3ea3532d1b17bd857c8e42b63fbb9c10a48162e2e2912e29aed3c9",
}

LEGACY_FTS_ASSET = "0021_episodic_fts5.sql"
LEGACY_FTS_CHECKSUM = "25c938ba7eef54c52642ecb39e17be0bd4caf04b39925212cfcb59c620f778fa"
LEGACY_FTS_ERROR = "virtual tables may not be indexed"


@dataclass(frozen=True)
class CatalogCompatibility:
    """Validated exceptions that may be used for one catalog discovery."""

    accepted_duplicate_versions: frozenset[int] = frozenset()
    accepted_gap_versions: frozenset[int] = frozenset()
    issues: tuple[str, ...] = ()


def asset_filename(asset: Any) -> str:
    path = getattr(asset, "path", None)
    if path is not None:
        return str(getattr(path, "name", path))
    migration_id = int(getattr(asset, "migration_id"))
    name = str(getattr(asset, "name"))
    return f"{migration_id:04d}_{name}.sql"


def physical_asset_key(asset: Any) -> str:
    """Return the stable physical key used by the application ledger."""

    return f"{int(getattr(asset, 'migration_id')):04d}#{str(getattr(asset, 'name'))}"


def is_legacy_duplicate_primary(asset: Any) -> bool:
    return asset_filename(asset) == LEGACY_DUPLICATE_PRIMARY


def evaluate_catalog_compatibility(assets: Iterable[Any]) -> CatalogCompatibility:
    """Validate the frozen duplicate/gap against exact names and checksums.

    Compatibility is deliberately catalog-shaped.  Small fixture catalogs keep
    the ordinary strict behavior; a catalog that crosses versions 22 -> 39 is
    considered the historical central catalog and must match both boundary
    anchors as well as both physical version-0009 assets.
    """

    rows = list(assets)
    filenames = {asset_filename(asset): str(getattr(asset, "checksum")) for asset in rows}
    by_version: dict[int, list[Any]] = {}
    for asset in rows:
        by_version.setdefault(int(getattr(asset, "migration_id")), []).append(asset)

    issues: list[str] = []
    accepted_duplicates: set[int] = set()
    accepted_gaps: set[int] = set()

    version_nine = by_version.get(LEGACY_DUPLICATE_VERSION, [])
    if len(version_nine) > 1:
        actual_names = {asset_filename(asset) for asset in version_nine}
        expected_names = set(LEGACY_DUPLICATE_ASSETS)
        if actual_names != expected_names:
            issues.append(
                "version 0009 physical assets do not match the frozen compatibility set"
            )
        else:
            drifted = [
                name
                for name, expected in LEGACY_DUPLICATE_ASSETS.items()
                if filenames.get(name) != expected
            ]
            if drifted:
                issues.append(
                    f"version 0009 compatibility checksum drift: {sorted(drifted)}"
                )
            else:
                accepted_duplicates.add(LEGACY_DUPLICATE_VERSION)

    versions = set(by_version)
    crosses_legacy_gap = 22 in versions and any(version >= 39 for version in versions)
    if crosses_legacy_gap:
        populated = sorted(versions.intersection(LEGACY_GAP_VERSIONS))
        if populated:
            issues.append(
                f"frozen compatibility gap 0023-0038 was populated: {populated}"
            )

        anchor_drift = [
            name
            for name, expected in LEGACY_GAP_ANCHORS.items()
            if filenames.get(name) != expected
        ]
        if anchor_drift:
            issues.append(
                f"legacy gap compatibility anchor drift: {sorted(anchor_drift)}"
            )

        actual_nine_names = {asset_filename(asset) for asset in version_nine}
        if actual_nine_names != set(LEGACY_DUPLICATE_ASSETS):
            issues.append(
                "central legacy catalog must retain both frozen version 0009 assets"
            )

        if not populated and not anchor_drift:
            accepted_gaps.update(LEGACY_GAP_VERSIONS)

    return CatalogCompatibility(
        accepted_duplicate_versions=frozenset(accepted_duplicates),
        accepted_gap_versions=frozenset(accepted_gaps),
        issues=tuple(dict.fromkeys(issues)),
    )


def is_exact_legacy_fts_failure(asset: Any, exc: BaseException) -> bool:
    """Return True only for the immutable 0021 FTS5 index failure."""

    return (
        asset_filename(asset) == LEGACY_FTS_ASSET
        and str(getattr(asset, "checksum")) == LEGACY_FTS_CHECKSUM
        and str(exc) == LEGACY_FTS_ERROR
    )


def legacy_fts_postcondition_holds(conn: Any) -> bool:
    """Verify that the useful FTS5 table exists despite the invalid index."""

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'episodic_fts'"
    ).fetchone()
    if row is None or row[0] is None or "using fts5" not in str(row[0]).lower():
        return False
    try:
        conn.execute("SELECT rowid, memory_id, content FROM episodic_fts LIMIT 0").fetchall()
    except Exception:
        return False
    return True
