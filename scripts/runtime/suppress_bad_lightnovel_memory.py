#!/usr/bin/env python
"""Suppress bad lightnovel quality-judgment life episodes.

This keeps audit rows intact via life_memory_correction, but removes stale
unsupported or polluted lightnovel quality judgments from active recall.
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
from host_brain.memory.life_memory.store import LifeMemoryStore  # noqa: E402


DEFAULT_DB_PATH = "host_brain/data/audit.sqlite3"
BAD_REASON_CODES = {
    "LIGHTNOVEL_DEEP_REVIEW_NO_CARDS",
    "LIGHTNOVEL_DEEP_REVIEW_FETCH_FAILED",
    "LIGHTNOVEL_DEEP_REVIEW_FAILED",
}
BAD_TEXT_MARKERS = (
    "《404》",
    "第1本《404》",
    "第2本《404》",
    "第3本《404》",
    "輕之國度是專註于分享",
    "轻之国度是专注于分享",
    "没拿到刚才那几本书",
    "追问时先看第",
    "追问时先看 第",
)
UNSUPPORTED_QUALITY_MARKERS = (
    "偏冷峻",
    "社会观察",
    "文笔细腻",
    "宗教/哲思",
    "心理描写",
    "吃设定",
    "吃文风",
    "神秘感",
    "寓言感",
    "沉静、思辨",
    "细节和氛围推进",
    "深度氛围",
    "通常是好看的",
    "通常算",
    "整体口碑不错",
    "大概率是“好看”",
    "大概率是好看",
)
QUALITY_CLAIM_MARKERS = (
    "我会选",
    "我会先看",
    "更推荐",
    "通常是好看的",
    "通常算",
    "整体口碑不错",
    "大概率",
    "值得读",
    "值不值得读",
)
LIGHTNOVEL_CONTEXT_MARKERS = (
    "轻小说",
    "輕小說",
    "与神在河岸",
    "與神在河岸",
    "昨天推荐",
    "今天推荐",
    "推荐 B",
    "推荐B",
    "这三本",
    "那三本",
    "哪本",
    "好看",
)
NOT_READY_MARKERS = (
    "没有足够详情或正文证据",
    "不能直接判断哪本更好看",
    "不能负责任地说哪本更好",
    "不能装作已经翻看过",
)
FAILED_REVIEW_MARKERS = (
    "没有拿到可读网页或 epub",
    "没有拿到可读网页或epub",
    "没拿到可读网页",
    "详情页未读到",
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


def _combined_episode_text(row: sqlite3.Row, metadata: dict[str, Any]) -> str:
    parts = [
        str(row["summary"] or ""),
        str(metadata.get("fact") or ""),
        str(metadata.get("memory_anchor") or ""),
        str(metadata.get("action_summary") or ""),
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str),
    ]
    return "\n".join(part for part in parts if part)


def _has_lightnovel_context(text: str) -> bool:
    return any(marker in text for marker in LIGHTNOVEL_CONTEXT_MARKERS)


def _has_valid_deep_review_evidence(metadata: dict[str, Any], text: str) -> bool:
    if any(marker in text for marker in BAD_TEXT_MARKERS):
        return False
    if any(marker in text for marker in FAILED_REVIEW_MARKERS):
        return False
    selected = metadata.get("selected")
    items = metadata.get("items")
    if isinstance(selected, dict) and selected and isinstance(items, list):
        statuses = {str(item.get("review_status") or "").strip().upper() for item in items if isinstance(item, dict)}
        if statuses.intersection({"OK", "PARTIAL"}):
            return True
    trace = metadata.get("trace_stage_meta") if isinstance(metadata.get("trace_stage_meta"), dict) else {}
    packet = trace.get("surface_memory_recall_packet") if isinstance(trace.get("surface_memory_recall_packet"), dict) else {}
    packet_facts = "\n".join(str(item or "") for item in list(packet.get("facts") or []))
    if "网页深读结果" in packet_facts and not any(marker in packet_facts for marker in (*BAD_TEXT_MARKERS, *FAILED_REVIEW_MARKERS)):
        return True
    packet_trace = packet.get("trace") if isinstance(packet.get("trace"), dict) else {}
    deep = packet_trace.get("lightnovel_deep_review") if isinstance(packet_trace.get("lightnovel_deep_review"), dict) else {}
    if str(deep.get("status") or "").strip().upper() == "OK" and deep.get("selected"):
        return True
    return "网页深读结果" in text and not any(marker in text for marker in FAILED_REVIEW_MARKERS)


def _is_bad_deep_review(row: sqlite3.Row, *, include_failed_deep_review: bool) -> tuple[bool, str]:
    metadata = _json_dict(row["metadata"])
    reason_code = str(metadata.get("reason_code") or "").strip()
    result = str(row["result"] or "").strip().upper()
    action = str(row["action"] or metadata.get("action_kind") or "").strip()
    text = _combined_episode_text(row, metadata)
    items = metadata.get("items")
    selected = metadata.get("selected")

    if action != "lightnovel_recommendation_deep_review":
        return False, ""
    for marker in BAD_TEXT_MARKERS:
        if marker in text:
            return True, f"bad_marker:{marker}"
    if reason_code in BAD_REASON_CODES and include_failed_deep_review:
        return True, f"bad_reason_code:{reason_code}"
    if result in {"SKIPPED", "DEGRADED", "FAILED"} and include_failed_deep_review:
        return True, f"bad_result:{result}"
    if isinstance(items, list) and not items:
        return True, "empty_items"
    if not selected and "当前更推荐第" in text:
        return True, "missing_selection_for_quality_claim"
    if not selected and include_failed_deep_review:
        return True, "missing_selection"
    return False, ""


def _is_bad_lightnovel_quality_memory(row: sqlite3.Row, *, include_failed_deep_review: bool) -> tuple[bool, str]:
    bad_deep, deep_reason = _is_bad_deep_review(row, include_failed_deep_review=include_failed_deep_review)
    if bad_deep:
        return True, deep_reason

    metadata = _json_dict(row["metadata"])
    text = _combined_episode_text(row, metadata)
    if not _has_lightnovel_context(text):
        return False, ""
    if any(marker in text for marker in NOT_READY_MARKERS) and not any(marker in text for marker in BAD_TEXT_MARKERS):
        return False, ""
    for marker in BAD_TEXT_MARKERS:
        if marker in text:
            return True, f"bad_marker:{marker}"
    for marker in UNSUPPORTED_QUALITY_MARKERS:
        if marker in text:
            return True, f"unsupported_quality_marker:{marker}"
    if any(marker in text for marker in QUALITY_CLAIM_MARKERS) and not _has_valid_deep_review_evidence(metadata, text):
        return True, "quality_claim_without_detail_evidence"
    return False, ""


def _candidate_where_clause() -> tuple[str, list[str]]:
    markers = [
        *BAD_TEXT_MARKERS,
        *UNSUPPORTED_QUALITY_MARKERS,
        *QUALITY_CLAIM_MARKERS,
        "lightnovel_recommendation_deep_review",
        "网页深读结果",
        "这三本书，你喜欢哪本",
        "这三本哪本",
        "哪本好看",
        "好看吗",
    ]
    clauses: list[str] = ["COALESCE(action, '') = 'lightnovel_recommendation_deep_review'"]
    params: list[str] = []
    for marker in dict.fromkeys(markers):
        clauses.append("(COALESCE(summary, '') LIKE ? OR COALESCE(metadata, '') LIKE ?)")
        pattern = f"%{marker}%"
        params.extend([pattern, pattern])
    return " OR ".join(clauses), params


def _find_candidates(conn: sqlite3.Connection, *, limit: int, include_failed_deep_review: bool = False) -> list[dict[str, Any]]:
    candidate_where, candidate_params = _candidate_where_clause()
    rows = list(
        conn.execute(
            f"""
            SELECT episode_id, episode_kind, action, result, summary, metadata,
                   truth_state, status, created_ts_ms, last_touched_ts_ms
            FROM life_episode
            WHERE COALESCE(status, '') != 'SUPPRESSED'
              AND COALESCE(truth_state, '') NOT IN ('SUPPRESSED', 'CORRECTED')
              AND ({candidate_where})
            ORDER BY last_touched_ts_ms DESC
            LIMIT ?
            """,
            (*candidate_params, max(1, int(limit))),
        ).fetchall()
    )
    candidates: list[dict[str, Any]] = []
    for row in rows:
        bad, reason = _is_bad_lightnovel_quality_memory(row, include_failed_deep_review=include_failed_deep_review)
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
    parser = argparse.ArgumentParser(description="Suppress stale/bad lightnovel quality-judgment life memories.")
    parser.add_argument("--db-path", default="")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument(
        "--include-failed-deep-review",
        action="store_true",
        help="Also suppress pure failed deep-review attempts. Default keeps failure episodes as audit/action memory.",
    )
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
        candidates = _find_candidates(
            conn,
            limit=int(args.limit),
            include_failed_deep_review=bool(args.include_failed_deep_review),
        )
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
        "include_failed_deep_review": bool(args.include_failed_deep_review),
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
