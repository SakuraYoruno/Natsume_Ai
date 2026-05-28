#!/usr/bin/env python
"""Backfill LifeMemory management policy metadata for historical episodes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.audit.sqlite_ledger import SQLiteLedger  # noqa: E402
from host_brain.memory.life_memory import LifeMemoryStore  # noqa: E402


DEFAULT_DB_PATH = "host_brain/data/audit.sqlite3"


def run_policy_backfill(
    *,
    db_path: Path,
    batch_limit: int = 20000,
    include_existing: bool = False,
) -> dict[str, Any]:
    if not db_path.exists():
        return {
            "status": "FAILED",
            "reason_code": "DB_NOT_FOUND",
            "db_path": str(db_path),
        }
    ledger = SQLiteLedger(db_path=db_path)
    try:
        result = LifeMemoryStore(ledger=ledger).backfill_memory_policy(
            batch_limit=max(1, int(batch_limit)),
            include_existing=bool(include_existing),
        )
        return {
            "status": result.get("status") or "OK",
            "db_path": str(db_path),
            **result,
        }
    finally:
        ledger.close()


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill LifeMemory memory_policy metadata.")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--batch-limit", type=int, default=20000)
    parser.add_argument("--include-existing", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = run_policy_backfill(
        db_path=_resolve_path(str(args.db_path or DEFAULT_DB_PATH)),
        batch_limit=max(1, int(args.batch_limit or 1)),
        include_existing=bool(args.include_existing),
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        compact = {
            "status": report.get("status"),
            "updated": report.get("updated"),
            "skipped_existing": report.get("skipped_existing"),
            "class_counts": report.get("class_counts"),
        }
        print(json.dumps(compact, ensure_ascii=False))
    return 0 if report.get("status") == "OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
