#!/usr/bin/env python
"""Suppress bad lightnovel deep-review life episodes.

This keeps audit rows intact via life_memory_correction, but removes stale
no-card/template deep-review memories from active recall.
"""

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
sys.path.insert(0, str(ROOT))

from host_brain.audit.sqlite_ledger import SQLiteLedger  # noqa: E402
from host_brain.memory.life_memory import LifeMemoryStore  # noqa: E402


DEFAULT_DB_PATH = "host_brain/data/audit.sqlite3"
BAD_REASON_CODES = {
    "LIGHTNOVEL_DEEP_REVIEW_NO_CARDS",
    "LIGHTNOVEL_DEEP_REVIEW_FETCH_FAILED",
    "LIGHTNOVEL_DEEP_REVIEW_FAILED",
}
BAD_TEXT_MARKERS = (
    "没拿到刚才那几本书",
    "不能装作已经翻看过",
    "没有拿到可读网页或 epub",
    "没有拿到可读网页或epub",
    "追问时先看第",
    "追问时先看 第",
)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _resolve_db_path(value: str) -> Path:
    raw = str(value or os.environ.get("Natsume_AUDIT_DB") or DEFAULT_DB_PATH).strip() or DEFAULT_DB_PATH
    return _resolve_path(raw)


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _is_bad_deep_review(row: sqlite3.Row) -> tuple[bool, str]:
    metadata = _json_dict(row["metadata"])
    reason_code = str(metadata.get("reason_code") or "").strip()
    result = str(row["result"] or "").strip().upper()
    action = str(row["action"] or metadata.get("action_kind") or "").strip()
    summary = str(row["summary"] or "")
    fact = str(metadata.get("fact") or "")
    memory_anchor = str(metadata.get("memory_anchor") or "")
    text = "\n".join([summary, fact, memory_anchor])
    items = metadata.get("items")
    selected = metadata.get("selected")

    if action != "lightnovel_recommendation_deep_review":
        return False, ""
    if reason_code in BAD_REASON_CODES:
        return True, f"bad_reason_code:{reason_code}"
    if result in {"SKIPPED", "DEGRADED", "FAILED"}:
        return True, f"bad_result:{result}"
    if isinstance(items, list) and not items:
        return True, "empty_items"
    if not selected and "当前更推荐第" not in text:
        return True, "missing_selection"
    for marker in BAD_TEXT_MARKERS:
        if marker in text:
            return True, f"bad_marker:{marker}"
    return False, ""


def _find_candidates(conn: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    rows = list(
        conn.execute(
            """
            SELECT episode_id, episode_kind, action, result, summary, metadata,
                   truth_state, status, created_ts_ms, last_touched_ts_ms
            FROM life_episode
            WHERE COALESCE(status, '') != 'SUPPRESSED'
              AND COALESCE(truth_state, '') != 'SUPPRESSED'
              AND (
                    COALESCE(action, '') = 'lightnovel_recommendation_deep_review'
                    OR COALESCE(summary, '') LIKE '%lightnovel_recommendation_deep_review%'
                    OR COALESCE(summary, '') LIKE '%网页深读结果%'
                    OR COALESCE(summary, '') LIKE '%追问时先看第%'
                  )
            ORDER BY last_touched_ts_ms DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    )
    candidates: list[dict[str, Any]] = []
    for row in rows:
        bad, reason = _is_bad_deep_review(row)
        if not bad:
            continue
        candidates.append(
            {
                "episode_id": str(row["episode_id"] or ""),
                "episode_kind": str(row["episode_kind"] or ""),
                "action": str(row["action"] or ""),
                "result": str(row["result"] or ""),
                "truth_state": str(row["truth_state"] or ""),
                "status": str(row["status"] or ""),
                "reason": reason,
                "summary": str(row["summary"] or "")[:260],
                "last_touched_ts_ms": int(row["last_touched_ts_ms"] or 0),
            }
        )
    return candidates


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Suppress stale/bad lightnovel deep-review life memories.")
    parser.add_argument("--db-path", default="")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--apply", action="store_true", help="Write suppression corrections. Default is dry-run.")
    parser.add_argument("--out", default="")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _load_env_file(ROOT / ".env")
    _load_env_file(ROOT / "runtime" / "host" / "host.runtime.env")
    db_path = _resolve_db_path(str(args.db_path or ""))
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        candidates = _find_candidates(conn, limit=int(args.limit))
    finally:
        conn.close()

    suppressed: list[dict[str, Any]] = []
    if args.apply and candidates:
        ledger = SQLiteLedger(db_path=db_path)
        try:
            store = LifeMemoryStore(ledger=ledger)
            ts_ms = int(time.time() * 1000)
            for item in candidates:
                result = store.correct_episode(
                    episode_id=item["episode_id"],
                    correction_kind="suppression",
                    reason_code="BAD_LIGHTNOVEL_DEEP_REVIEW_MEMORY",
                    detail=f"Suppressed stale lightnovel deep-review memory: {item['reason']}",
                    suppress=True,
                    ts_ms=ts_ms,
                    metadata={"cleanup_script": "suppress_bad_lightnovel_memory", "match_reason": item["reason"]},
                )
                suppressed.append({**item, "correction": result})
        finally:
            ledger.close()

    report = {
        "db_path": str(db_path),
        "dry_run": not bool(args.apply),
        "candidate_count": len(candidates),
        "suppressed_count": len(suppressed),
        "candidates": candidates,
        "suppressed": suppressed,
    }
    if args.out:
        out_path = _resolve_path(str(args.out))
    else:
        out_path = ROOT / "runtime" / "host" / "logs" / f"bad_lightnovel_memory_cleanup_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY " + json.dumps({k: report[k] for k in ("db_path", "dry_run", "candidate_count", "suppressed_count")}, ensure_ascii=False))
    print(f"WROTE {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
