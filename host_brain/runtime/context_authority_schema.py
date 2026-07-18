"""Checksum-bound readiness gate for NE-03 Runtime Context authority."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
from typing import Any


class RuntimeContextSchemaError(RuntimeError):
    """RuntimeEventStore lacks the exact CAS and lease fencing schema."""


def _normalized(value: str) -> str:
    compact = "".join(str(value or "").lower().split())
    return compact.translate(str.maketrans("", "", "'\"`[]"))


def assert_runtime_context_authority_schema(event_store: Any) -> None:
    db_path = Path(str(getattr(event_store, "db_path", "") or "")).resolve()
    if not db_path.is_file():
        raise RuntimeContextSchemaError("runtime context SQLite authority is unavailable")
    migration = (
        Path(__file__).resolve().parents[1]
        / "audit"
        / "migrations"
        / "0112_runtime_context_authority.sql"
    )
    expected_checksum = hashlib.sha256(
        migration.read_text(encoding="utf-8-sig").encode("utf-8")
    ).hexdigest()
    try:
        with sqlite3.connect(str(db_path)) as conn:
            asset = conn.execute(
                "SELECT checksum, disposition FROM schema_migration_assets "
                "WHERE asset_key = '0112#runtime_context_authority'"
            ).fetchone()
            rows = conn.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE name IN (?, ?, ?)",
                (
                    "ux_runtime_context_aggregate_revision",
                    "runtime_guardian_requires_active_host_lease",
                    "scene_state_requires_active_host_lease",
                ),
            ).fetchall()
    except sqlite3.Error as exc:
        raise RuntimeContextSchemaError(
            "runtime context authority schema cannot be inspected"
        ) from exc
    if (
        asset is None
        or str(asset[0]) != expected_checksum
        or str(asset[1]) not in {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
    ):
        raise RuntimeContextSchemaError("runtime context migration checksum drift")
    objects = {str(row[1]): (str(row[0]), _normalized(str(row[2] or ""))) for row in rows}
    expected = {
        "ux_runtime_context_aggregate_revision": (
            "index",
            (
                "uniqueindex",
                "runtime_event_timeline(aggregate_type,aggregate_id,aggregate_version)",
                "aggregate_typein('scene_state','runtime_guardian')",
            ),
        ),
        "runtime_guardian_requires_active_host_lease": (
            "trigger",
            (
                "host_embodiment_runtime_lease",
                "$.details.runtime_scope_id",
                "$.details.runtime_owner_id",
                "$.details.runtime_lease_id",
                "$.details.fencing_token",
                "lease.expires_at_ms>new.created_ts_ms",
            ),
        ),
        "scene_state_requires_active_host_lease": (
            "trigger",
            (
                "host_embodiment_runtime_lease",
                "$.runtime_scope_id",
                "$.runtime_owner_id",
                "$.runtime_lease_id",
                "$.fencing_token",
                "lease.expires_at_ms>new.created_ts_ms",
            ),
        ),
    }
    for name, (kind, fragments) in expected.items():
        actual = objects.get(name)
        if actual is None or actual[0] != kind:
            raise RuntimeContextSchemaError(f"runtime context schema object missing: {name}")
        if any(_normalized(fragment) not in actual[1] for fragment in fragments):
            raise RuntimeContextSchemaError(f"runtime context schema object drift: {name}")


__all__ = [
    "RuntimeContextSchemaError",
    "assert_runtime_context_authority_schema",
]
