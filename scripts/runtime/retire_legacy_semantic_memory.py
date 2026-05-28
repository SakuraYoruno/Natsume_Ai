#!/usr/bin/env python
"""Soft-retire legacy semantic_memory rows that bypass LifeMemory SSOT."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.audit.sqlite_ledger import SQLiteLedger  # noqa: E402


DEFAULT_DB_PATH = "host_brain/data/audit.sqlite3"
DEFAULT_ENV_FILE = "runtime/host/host.runtime.env"
MIGRATION_ID = "retire_legacy_semantic_memory_20260529"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _load_env_file(_resolve_path(str(args.env_file or DEFAULT_ENV_FILE)))
    db_path = _resolve_db_path(str(args.db_path or ""))
    result = retire_legacy_semantic_memory(
        db_path=db_path,
        apply=bool(args.apply),
        limit=max(0, int(args.limit or 0)),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "OK" else 2


def retire_legacy_semantic_memory(
    *,
    db_path: Path,
    apply: bool = False,
    limit: int = 0,
) -> dict[str, Any]:
    if not db_path.exists():
        return {"status": "FAILED", "reason_code": "DB_NOT_FOUND", "db_path": str(db_path)}
    ledger = SQLiteLedger(db_path=db_path)
    try:
        planned = _planned_rows(ledger._conn, limit=limit)
        backup_path = None
        retired: list[dict[str, Any]] = []
        retired_ts_ms = int(time.time() * 1000)
        if apply and planned:
            backup_path = _backup_db(db_path)
            with ledger._write_lock:
                with ledger._conn:
                    for row in planned:
                        semantic_id = str(row["semantic_id"])
                        ledger._conn.execute(
                            "UPDATE semantic_memory SET deleted_at = ? WHERE semantic_id = ? AND deleted_at IS NULL",
                            (retired_ts_ms, semantic_id),
                        )
                        ledger.log_memory_op(
                            {
                                "request_id": f"{MIGRATION_ID}:{retired_ts_ms}",
                                "op_kind": "semantic_memory_retire",
                                "memory_id": semantic_id,
                                "source_memory_ids": _json_list(row["source_memory_ids"]),
                                "evidence_refs": [],
                                "status": "SUCCESS",
                                "reason_code": row["reason_code"],
                                "detail": "Soft-retired legacy semantic_memory row not produced by LifeMemoryConsolidator.",
                                "ts_ms": retired_ts_ms,
                            }
                        )
                        retired.append({"semantic_id": semantic_id, "reason_code": row["reason_code"]})
        active_after = _active_count(ledger._conn)
        return {
            "status": "OK",
            "apply": bool(apply),
            "db_path": str(db_path),
            "planned_count": len(planned),
            "retired_count": len(retired),
            "active_after": active_after,
            "backup_path": str(backup_path) if backup_path else None,
            "samples": [_compact_row(row) for row in planned[:20]],
            "retired_samples": retired[:20],
        }
    finally:
        ledger.close()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Soft-retire legacy semantic_memory rows.")
    parser.add_argument("--db-path", default="")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--apply", action="store_true")
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


def _planned_rows(conn: sqlite3.Connection, *, limit: int) -> list[Mapping[str, Any]]:
    rows = conn.execute(
        """
        SELECT semantic_id, summary, source_memory_ids, tags, distill_strategy, operator_node_id
        FROM semantic_memory
        WHERE deleted_at IS NULL
        ORDER BY ts_ms ASC, semantic_id ASC
        """
    ).fetchall()
    planned: list[Mapping[str, Any]] = []
    for row in rows:
        payload = dict(row)
        tags = {str(item or "").strip() for item in _json_list(payload["tags"])}
        if "life_consolidated" in tags:
            continue
        payload["reason_code"] = _reason_code(payload)
        planned.append(payload)
        if limit and len(planned) >= limit:
            break
    return planned


def _reason_code(row: Mapping[str, Any]) -> str:
    source_ids = [str(item or "") for item in _json_list(row.get("source_memory_ids"))]
    reasons: list[str] = ["LEGACY_NOT_LIFE_CONSOLIDATED"]
    if not any(item.startswith(("life_episode::", "life_memory_collection::")) for item in source_ids):
        reasons.append("NON_LIFE_SOURCE")
    if _summary_has_noise(row.get("summary")):
        reasons.append("SUMMARY_NOISE")
    return "+".join(reasons)


def _summary_has_noise(summary: Any) -> bool:
    compact = str(summary or "").lower().replace(" ", "")
    return any(
        token in compact
        for token in (
            "当前回合摘要：用户提到",
            "metrics test",
            "emotion_decay_tick",
            "inner_world_numeric_tick",
            "发送了text消息",
            "发送了image消息",
            "latency_ms",
            "surface_production_result_recall",
        )
    )


def _compact_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "semantic_id": row.get("semantic_id"),
        "reason_code": row.get("reason_code"),
        "distill_strategy": row.get("distill_strategy"),
        "operator_node_id": row.get("operator_node_id"),
        "summary": str(row.get("summary") or "")[:180],
        "source_memory_ids": _json_list(row.get("source_memory_ids"))[:8],
        "tags": _json_list(row.get("tags")),
    }


def _active_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(1) AS c FROM semantic_memory WHERE deleted_at IS NULL").fetchone()
    return int(row["c"] if row is not None else 0)


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return list(parsed) if isinstance(parsed, list) else []
    return []


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
