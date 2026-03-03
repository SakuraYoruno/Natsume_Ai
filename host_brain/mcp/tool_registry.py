"""MCP ToolMeta registry: sync, grade, and change-log for provider tools.

Tables:
  mcp_tool_registry  - current tool metadata (upsert by provider_id+tool_id)
  mcp_sync_log       - change log (before/after diff, append-only)
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from host_brain.core.ids import now_ms


# ---------------------------------------------------------------------------
# ToolMeta dataclass
# ---------------------------------------------------------------------------

VALID_ACCESS_LEVELS = frozenset({"PUBLIC", "INTERNAL", "RESTRICTED"})
VALID_RISK_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})


@dataclass
class ToolMeta:
    """Metadata for a single MCP provider tool."""

    provider_id: str
    tool_id: str
    name: str
    description: str
    access_level: str          # PUBLIC | INTERNAL | RESTRICTED
    risk_level: str            # LOW | MEDIUM | HIGH
    reversible: bool
    version: str = "1.0.0"
    schema_hash: str = ""
    domain_tags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "tool_id": self.tool_id,
            "name": self.name,
            "description": self.description,
            "access_level": self.access_level,
            "risk_level": self.risk_level,
            "reversible": self.reversible,
            "version": self.version,
            "schema_hash": self.schema_hash,
            "domain_tags": list(self.domain_tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolMeta":
        tags = data.get("domain_tags") or []
        if isinstance(tags, str):
            tags = json.loads(tags)
        return cls(
            provider_id=str(data["provider_id"]),
            tool_id=str(data["tool_id"]),
            name=str(data.get("name") or data["tool_id"]),
            description=str(data.get("description") or ""),
            access_level=str(data.get("access_level") or "PUBLIC").upper(),
            risk_level=str(data.get("risk_level") or "LOW").upper(),
            reversible=bool(data.get("reversible", False)),
            version=str(data.get("version") or "1.0.0"),
            schema_hash=str(data.get("schema_hash") or ""),
            domain_tags=list(tags),
        )


# ---------------------------------------------------------------------------
# MCPToolRegistry
# ---------------------------------------------------------------------------

class MCPToolRegistry:
    """SQLite-backed registry for MCP provider tool metadata.

    Supports:
    - register / upsert ToolMeta
    - query by provider_id or tool_id
    - change log (mcp_sync_log) with before/after diff
    """

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        *,
        check_same_thread: bool = True,
    ) -> None:
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=check_same_thread)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, meta: ToolMeta) -> dict[str, Any]:
        """Register or update a ToolMeta. Returns sync_log entry."""
        self._validate(meta)
        before = self.get(provider_id=meta.provider_id, tool_id=meta.tool_id)
        before_dict = before.as_dict() if before else None

        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO mcp_tool_registry
                        (provider_id, tool_id, name, description,
                         access_level, risk_level, reversible,
                         version, schema_hash, domain_tags, updated_ts_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider_id, tool_id) DO UPDATE SET
                        name = excluded.name,
                        description = excluded.description,
                        access_level = excluded.access_level,
                        risk_level = excluded.risk_level,
                        reversible = excluded.reversible,
                        version = excluded.version,
                        schema_hash = excluded.schema_hash,
                        domain_tags = excluded.domain_tags,
                        updated_ts_ms = excluded.updated_ts_ms
                    """,
                    (
                        meta.provider_id,
                        meta.tool_id,
                        meta.name,
                        meta.description,
                        meta.access_level,
                        meta.risk_level,
                        int(meta.reversible),
                        meta.version,
                        meta.schema_hash,
                        json.dumps(meta.domain_tags, ensure_ascii=False),
                        now_ms(),
                    ),
                )

        after_dict = meta.as_dict()
        diff = _compute_diff(before_dict, after_dict)
        sync_entry = self._log_sync(
            provider_id=meta.provider_id,
            tool_id=meta.tool_id,
            change_kind="UPDATE" if before else "INSERT",
            before=before_dict,
            after=after_dict,
            diff=diff,
        )
        return sync_entry

    def get(self, *, provider_id: str, tool_id: str) -> ToolMeta | None:
        """Fetch a single ToolMeta by provider_id + tool_id."""
        row = self._conn.execute(
            "SELECT * FROM mcp_tool_registry WHERE provider_id = ? AND tool_id = ?",
            (provider_id, tool_id),
        ).fetchone()
        if row is None:
            return None
        return _row_to_meta(row)

    def list_by_provider(self, provider_id: str) -> list[ToolMeta]:
        """List all tools for a given provider."""
        rows = self._conn.execute(
            "SELECT * FROM mcp_tool_registry WHERE provider_id = ? ORDER BY tool_id ASC",
            (provider_id,),
        ).fetchall()
        return [_row_to_meta(r) for r in rows]

    def list_all(self) -> list[ToolMeta]:
        """List all registered tools."""
        rows = self._conn.execute(
            "SELECT * FROM mcp_tool_registry ORDER BY provider_id ASC, tool_id ASC"
        ).fetchall()
        return [_row_to_meta(r) for r in rows]

    def sync_log(self) -> list[dict[str, Any]]:
        """Return all sync log entries (change history)."""
        rows = self._conn.execute(
            "SELECT * FROM mcp_sync_log ORDER BY id ASC"
        ).fetchall()
        return [_decode_sync_row(r) for r in rows]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate(self, meta: ToolMeta) -> None:
        if not meta.provider_id or not meta.tool_id:
            raise ValueError("ToolMeta requires non-empty provider_id and tool_id")
        if meta.access_level not in VALID_ACCESS_LEVELS:
            raise ValueError(f"Invalid access_level: {meta.access_level!r}; must be one of {sorted(VALID_ACCESS_LEVELS)}")
        if meta.risk_level not in VALID_RISK_LEVELS:
            raise ValueError(f"Invalid risk_level: {meta.risk_level!r}; must be one of {sorted(VALID_RISK_LEVELS)}")

    def _log_sync(
        self,
        *,
        provider_id: str,
        tool_id: str,
        change_kind: str,
        before: dict[str, Any] | None,
        after: dict[str, Any],
        diff: dict[str, Any],
    ) -> dict[str, Any]:
        ts = now_ms()
        with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO mcp_sync_log
                        (provider_id, tool_id, change_kind, before_json, after_json, diff_json, ts_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        provider_id,
                        tool_id,
                        change_kind,
                        json.dumps(before, ensure_ascii=False) if before else None,
                        json.dumps(after, ensure_ascii=False),
                        json.dumps(diff, ensure_ascii=False),
                        ts,
                    ),
                )
        return {
            "provider_id": provider_id,
            "tool_id": tool_id,
            "change_kind": change_kind,
            "before": before,
            "after": after,
            "diff": diff,
            "ts_ms": ts,
        }

    def _ensure_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS mcp_tool_registry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id TEXT NOT NULL,
                    tool_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    access_level TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    reversible INTEGER NOT NULL DEFAULT 0,
                    version TEXT NOT NULL DEFAULT '1.0.0',
                    schema_hash TEXT,
                    domain_tags TEXT,
                    updated_ts_ms INTEGER NOT NULL,
                    UNIQUE(provider_id, tool_id)
                );

                CREATE TABLE IF NOT EXISTS mcp_sync_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id TEXT NOT NULL,
                    tool_id TEXT NOT NULL,
                    change_kind TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT NOT NULL,
                    diff_json TEXT,
                    ts_ms INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_mcp_registry_provider ON mcp_tool_registry(provider_id);
                CREATE INDEX IF NOT EXISTS idx_mcp_registry_tool ON mcp_tool_registry(tool_id);
                CREATE INDEX IF NOT EXISTS idx_mcp_sync_log_provider ON mcp_sync_log(provider_id, tool_id);
                CREATE INDEX IF NOT EXISTS idx_mcp_sync_log_ts ON mcp_sync_log(ts_ms);
                """
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_meta(row: sqlite3.Row) -> ToolMeta:
    data = dict(row)
    tags_raw = data.get("domain_tags") or "[]"
    if isinstance(tags_raw, str):
        try:
            tags = json.loads(tags_raw)
        except Exception:
            tags = []
    else:
        tags = list(tags_raw)
    return ToolMeta(
        provider_id=str(data["provider_id"]),
        tool_id=str(data["tool_id"]),
        name=str(data["name"]),
        description=str(data.get("description") or ""),
        access_level=str(data["access_level"]),
        risk_level=str(data["risk_level"]),
        reversible=bool(data.get("reversible", 0)),
        version=str(data.get("version") or "1.0.0"),
        schema_hash=str(data.get("schema_hash") or ""),
        domain_tags=tags,
    )


def _decode_sync_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in ("before_json", "after_json", "diff_json"):
        raw = data.pop(key, None)
        field_name = key.replace("_json", "")
        if raw and isinstance(raw, str):
            try:
                data[field_name] = json.loads(raw)
            except Exception:
                data[field_name] = raw
        else:
            data[field_name] = None
    data.pop("id", None)
    return data


def _compute_diff(
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> dict[str, Any]:
    """Compute field-level diff between before and after."""
    if before is None:
        return {"added": list(after.keys()), "changed": {}, "removed": []}
    changed: dict[str, Any] = {}
    for key, new_val in after.items():
        old_val = before.get(key)
        if old_val != new_val:
            changed[key] = {"before": old_val, "after": new_val}
    removed = [k for k in before if k not in after]
    return {"added": [], "changed": changed, "removed": removed}
