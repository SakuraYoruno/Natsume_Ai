#!/usr/bin/env python
"""Backfill LifeMemory collection indexes from historical episode metadata."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.audit.sqlite_ledger import SQLiteLedger  # noqa: E402
from host_brain.memory.life_memory import LifeMemoryStore  # noqa: E402


DEFAULT_DB_PATH = "host_brain/data/audit.sqlite3"
DEFAULT_ENV_FILE = "runtime/host/host.runtime.env"
MIGRATION_ID = "life_memory_collection_backfill_20260529"
COLLECTION_KEYS = (
    "selected_recommendations",
    "recommendations",
    "selected_items",
    "items",
    "cards",
    "delivered_items",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _load_env_file(_resolve_path(str(args.env_file or DEFAULT_ENV_FILE)))
    db_path = _resolve_db_path(str(args.db_path or ""))
    result = run_collection_backfill(
        db_path=db_path,
        apply=bool(args.apply),
        include_synthetic=bool(args.include_synthetic),
        limit=max(0, int(args.limit or 0)),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "OK" else 2


def run_collection_backfill(
    *,
    db_path: Path,
    apply: bool = False,
    include_synthetic: bool = False,
    limit: int = 0,
) -> dict[str, Any]:
    if not db_path.exists():
        return {"status": "FAILED", "reason_code": "DB_NOT_FOUND", "db_path": str(db_path)}
    ledger = SQLiteLedger(db_path=db_path)
    try:
        store = LifeMemoryStore(ledger=ledger)
        candidates = _candidate_rows(ledger._conn, include_synthetic=include_synthetic, limit=limit)
        planned = [
            row
            for row in candidates
            if not _episode_has_collection_items(ledger._conn, str(row["episode_id"]))
        ]
        backup_path = None
        indexed: list[dict[str, Any]] = []
        if apply and planned:
            backup_path = _backup_db(db_path)
            for row in planned:
                result = store.index_episode_collections(
                    episode_id=str(row["episode_id"]),
                    ts_ms=int(row["created_ts_ms"] or int(time.time() * 1000)),
                )
                indexed.append(
                    {
                        "episode_id": row["episode_id"],
                        "status": result.get("status"),
                        "reason_code": result.get("reason_code"),
                        "collection_count": result.get("collection_count", 0),
                    }
                )
        collection_count = _table_count(ledger._conn, "life_memory_collection")
        collection_item_count = _table_count(ledger._conn, "life_memory_collection_item")
        return {
            "status": "OK",
            "apply": bool(apply),
            "db_path": str(db_path),
            "candidate_count": len(candidates),
            "planned_count": len(planned),
            "indexed_count": len([item for item in indexed if item.get("status") == "OK"]),
            "collection_count": collection_count,
            "collection_item_count": collection_item_count,
            "backup_path": str(backup_path) if backup_path else None,
            "samples": [
                {
                    "episode_id": row["episode_id"],
                    "episode_kind": row["episode_kind"],
                    "action": row["action"],
                    "person_id": row["person_id"],
                    "session_id": row["session_id"],
                    "created_ts_ms": row["created_ts_ms"],
                }
                for row in planned[:10]
            ],
            "indexed_samples": indexed[:10],
        }
    finally:
        ledger.close()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill life_memory_collection from historical LifeMemory episodes.")
    parser.add_argument("--db-path", default="")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--include-synthetic", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args(argv)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _resolve_db_path(explicit: str) -> Path:
    raw = str(explicit or os.environ.get("Natsume_AUDIT_DB") or DEFAULT_DB_PATH).strip() or DEFAULT_DB_PATH
    return _resolve_path(raw)


def _candidate_rows(conn: sqlite3.Connection, *, include_synthetic: bool, limit: int) -> list[sqlite3.Row]:
    sql = """
        SELECT *
        FROM life_episode
        WHERE COALESCE(retention_state, 'WARM') != 'GONE'
          AND COALESCE(metadata, '') != ''
          AND (
              metadata LIKE '%selected_recommendations%'
              OR metadata LIKE '%recommendations%'
              OR metadata LIKE '%selected_items%'
              OR metadata LIKE '%delivered_items%'
              OR metadata LIKE '%cards%'
          )
        ORDER BY created_ts_ms ASC
    """
    rows = list(conn.execute(sql).fetchall())
    candidates: list[sqlite3.Row] = []
    for row in rows:
        metadata = _json_loads(row["metadata"])
        if not include_synthetic and _is_synthetic(row, metadata):
            continue
        if not _has_collection_items(metadata):
            continue
        candidates.append(row)
        if limit and len(candidates) >= limit:
            break
    return candidates


def _has_collection_items(metadata: Mapping[str, Any]) -> bool:
    containers: list[Mapping[str, Any]] = [metadata]
    for nested_key in ("metadata", "result", "payload"):
        nested = metadata.get(nested_key)
        if isinstance(nested, Mapping):
            containers.append(nested)
    for container in containers:
        for key in COLLECTION_KEYS:
            value = container.get(key)
            if isinstance(value, list) and any(isinstance(item, Mapping) for item in value):
                return True
    return False


def _episode_has_collection_items(conn: sqlite3.Connection, episode_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM life_memory_collection_item WHERE episode_id = ? LIMIT 1",
        (episode_id,),
    ).fetchone()
    return row is not None


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(1) AS c FROM {table}").fetchone()
    return int(row["c"] if row is not None else 0)


def _backup_db(db_path: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = ROOT / "runtime" / "host" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{db_path.name}.{MIGRATION_ID}.{stamp}.sqlite3"
    source = sqlite3.connect(str(db_path))
    try:
        dest = sqlite3.connect(str(backup_path))
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    return backup_path


def _json_loads(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _is_synthetic(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> bool:
    person_id = str(row["person_id"] or "").strip().lower()
    session_id = str(row["session_id"] or "").strip().lower()
    if bool(metadata.get("e2e_probe") or metadata.get("synthetic_probe") or metadata.get("test_probe")):
        return True
    markers = ("surface-memory-recall-e2e", "pytest", "test-", "probe")
    return any(marker in person_id or marker in session_id for marker in markers)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
