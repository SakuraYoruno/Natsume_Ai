#!/usr/bin/env python
"""Backfill canonical collection item metadata and entity mentions."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.audit.sqlite_ledger import SQLiteLedger  # noqa: E402
from host_brain.memory.life_memory import LifeMemoryStore  # noqa: E402


DEFAULT_DB_PATH = "host_brain/data/audit.sqlite3"
DEFAULT_ENV_FILE = "runtime/host/host.runtime.env"
MIGRATION_ID = "life_collection_canonical_entity_backfill_20260530"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _load_env_file(_resolve_path(str(args.env_file or DEFAULT_ENV_FILE)))
    result = run_canonical_backfill(
        db_path=_resolve_db_path(str(args.db_path or "")),
        apply=bool(args.apply),
        include_non_lightnovel=bool(args.include_non_lightnovel),
        limit=max(1, int(args.limit or 1000)),
    )
    if args.out:
        out_path = _resolve_path(str(args.out))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "OK" else 2


def run_canonical_backfill(
    *,
    db_path: Path,
    apply: bool = False,
    include_non_lightnovel: bool = False,
    limit: int = 1000,
) -> dict[str, Any]:
    if not db_path.exists():
        return {"status": "FAILED", "reason_code": "DB_NOT_FOUND", "db_path": str(db_path)}
    ledger = SQLiteLedger(db_path=str(db_path))
    try:
        store = LifeMemoryStore(ledger=ledger)
        preview = store.backfill_collection_item_canonical_index(
            limit=limit,
            dry_run=True,
            include_non_lightnovel=include_non_lightnovel,
        )
        backup_path = None
        applied: dict[str, Any] | None = None
        if apply and int(preview.get("planned_count") or 0) > 0:
            backup_path = _backup_db(db_path)
            applied = store.backfill_collection_item_canonical_index(
                limit=limit,
                dry_run=False,
                include_non_lightnovel=include_non_lightnovel,
            )
        return {
            "status": "OK",
            "apply": bool(apply),
            "db_path": str(db_path),
            "backup_path": str(backup_path) if backup_path else None,
            "preview": preview,
            "applied": applied,
        }
    finally:
        ledger.close()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill canonical collection item metadata and entity mentions.")
    parser.add_argument("--db-path", default="")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--include-non-lightnovel", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--out", default="")
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


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
