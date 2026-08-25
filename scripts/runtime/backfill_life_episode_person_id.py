#!/usr/bin/env python
"""Backfill canonical person_id for gateway-backed life episodes."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from host_brain.core.ids import now_ms as _now_ms
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEFAULT_DB_PATH = "host_brain/data/audit.sqlite3"
DEFAULT_ENV_FILE = "runtime/host/host.runtime.env"
MIGRATION_ID = "life_episode_person_id_gateway_backfill_20260528"
GATEWAY_SESSION_PREFIXES = ("qq_", "wechat_", "wx_", "wecom_", "feishu_")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill life_episode.person_id from gateway context.")
    parser.add_argument("--db-path", default="")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--backup-dir", default="runtime/host/backups")
    parser.add_argument("--no-backup", action="store_true")
    return parser.parse_args()


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


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _nested_mapping(meta: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = meta.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def _gateway_context(row: Mapping[str, Any], meta: Mapping[str, Any]) -> dict[str, str] | None:
    raw_extra = _nested_mapping(meta, "raw_extra")
    payload = _nested_mapping(meta, "payload")
    session_id = _clean(row.get("session_id") or meta.get("session_id") or raw_extra.get("session_id"))
    conversation_id = _clean(
        meta.get("conversation_id")
        or raw_extra.get("conversation_id")
        or payload.get("conversation_id")
        or session_id
    )
    platform = _clean(meta.get("platform") or raw_extra.get("platform") or payload.get("platform")).lower()
    from_id = _clean(
        meta.get("from_id")
        or meta.get("sender_id")
        or meta.get("user_id")
        or raw_extra.get("from_id")
        or raw_extra.get("sender_id")
        or raw_extra.get("user_id")
        or payload.get("from_id")
        or payload.get("sender_id")
        or payload.get("person_id")
    )
    if not platform:
        if conversation_id.startswith("qq_") or session_id.startswith("qq_"):
            platform = "qq"
        elif conversation_id.startswith(("wechat_", "wx_")) or session_id.startswith(("wechat_", "wx_")):
            platform = "wechat"
        elif conversation_id.startswith("wecom_") or session_id.startswith("wecom_"):
            platform = "wecom"
        elif conversation_id.startswith("feishu_") or session_id.startswith("feishu_"):
            platform = "feishu"
    has_gateway_context = bool(platform or conversation_id.startswith(GATEWAY_SESSION_PREFIXES))
    if not has_gateway_context:
        return None
    return {
        "platform": platform,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "from_id": from_id,
    }


def _select_candidates(conn: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT episode_id, episode_kind, person_id, session_id, action, result,
               summary, created_ts_ms, metadata
        FROM life_episode
        WHERE person_id IS NULL OR TRIM(person_id) = ''
        ORDER BY created_ts_ms DESC
        """
    ).fetchall()
    selected: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        meta = _json_loads(row.get("metadata"))
        context = _gateway_context(row, meta)
        if context is None:
            continue
        row["metadata_dict"] = meta
        row["gateway_context"] = context
        selected.append(row)
        if limit > 0 and len(selected) >= limit:
            break
    return selected


def _resolve_candidate_person_id(candidate: Mapping[str, Any]) -> str | None:
    from host_brain.bond.user_identity import resolve_person_id  # noqa: PLC0415

    context = dict(candidate.get("gateway_context") or {})
    resolved = resolve_person_id(
        None,
        session_id=_clean(context.get("session_id")) or None,
        platform=_clean(context.get("platform")) or None,
        from_id=_clean(context.get("from_id")) or None,
        conversation_id=_clean(context.get("conversation_id")) or None,
    )
    return _clean(resolved) or None


def _metadata_with_backfill(meta: Mapping[str, Any], *, person_id: str, ts_ms: int) -> str:
    updated = dict(meta)
    refs_raw = updated.get("evidence_refs")
    refs = [str(item).strip() for item in refs_raw] if isinstance(refs_raw, list) else []
    person_ref = f"chat://person/{person_id}"
    if person_ref not in refs:
        refs.append(person_ref)
    updated["evidence_refs"] = refs
    updated["resolved_person_id_source"] = updated.get("resolved_person_id_source") or "historical_gateway_backfill"
    updated["person_id_backfill"] = {
        "migration_id": MIGRATION_ID,
        "resolved_person_id": person_id,
        "source": "gateway_context",
        "ts_ms": ts_ms,
    }
    return json.dumps(updated, ensure_ascii=False, sort_keys=True)


def _backup_db(db_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{db_path.name}.{MIGRATION_ID}.{stamp}.sqlite3"
    source = sqlite3.connect(str(db_path), timeout=30)
    try:
        dest = sqlite3.connect(str(backup_path), timeout=30)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    return backup_path


def _log_migration(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    status: str,
    reason_code: str | None,
    detail: str,
    latency_ms: int,
    ts_ms: int,
) -> None:
    conn.execute(
        """
        INSERT INTO migration_log(request_id, scope, migration_id, status, reason_code, stage, detail, latency_ms, ts_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_id,
            "life_memory",
            MIGRATION_ID,
            status,
            reason_code,
            "life_memory.person_id_backfill",
            detail[:500],
            int(latency_ms),
            int(ts_ms),
        ),
    )


def main() -> int:
    args = _parse_args()
    _load_env_file(_resolve_path(str(args.env_file or DEFAULT_ENV_FILE)))
    db_path = _resolve_db_path(args.db_path)
    if not db_path.exists():
        print(json.dumps({"status": "FAILED", "reason_code": "DB_NOT_FOUND", "db_path": str(db_path)}, ensure_ascii=False))
        return 2

    started_ms = _now_ms()
    request_id = f"req_{MIGRATION_ID}_{started_ms}"
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        candidates = _select_candidates(conn, limit=max(0, int(args.limit or 0)))
        planned: list[dict[str, Any]] = []
        for candidate in candidates:
            resolved_person_id = _resolve_candidate_person_id(candidate)
            if not resolved_person_id:
                continue
            context = dict(candidate.get("gateway_context") or {})
            planned.append(
                {
                    "episode_id": candidate.get("episode_id"),
                    "episode_kind": candidate.get("episode_kind"),
                    "session_id": candidate.get("session_id"),
                    "action": candidate.get("action"),
                    "created_ts_ms": candidate.get("created_ts_ms"),
                    "resolved_person_id": resolved_person_id,
                    "platform": context.get("platform"),
                    "conversation_id": context.get("conversation_id"),
                }
            )
        if not args.apply:
            print(
                json.dumps(
                    {
                        "status": "DRY_RUN",
                        "db_path": str(db_path),
                        "candidate_count": len(candidates),
                        "planned_update_count": len(planned),
                        "sample": planned[:12],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        backup_path = None
        if not args.no_backup:
            backup_path = _backup_db(db_path, _resolve_path(str(args.backup_dir or "runtime/host/backups")))

        update_ts = _now_ms()
        applied = 0
        with conn:
            for candidate in candidates:
                resolved_person_id = _resolve_candidate_person_id(candidate)
                if not resolved_person_id:
                    continue
                updated_metadata = _metadata_with_backfill(
                    dict(candidate.get("metadata_dict") or {}),
                    person_id=resolved_person_id,
                    ts_ms=update_ts,
                )
                cur = conn.execute(
                    """
                    UPDATE life_episode
                    SET person_id = ?, metadata = ?
                    WHERE episode_id = ? AND (person_id IS NULL OR TRIM(person_id) = '')
                    """,
                    (resolved_person_id, updated_metadata, candidate.get("episode_id")),
                )
                applied += int(cur.rowcount or 0)
            latency_ms = _now_ms() - started_ms
            _log_migration(
                conn,
                request_id=request_id,
                status="OK",
                reason_code=None,
                detail=f"backfilled life_episode person_id from gateway context rows={applied} backup={backup_path}",
                latency_ms=latency_ms,
                ts_ms=_now_ms(),
            )
        print(
            json.dumps(
                {
                    "status": "OK",
                    "db_path": str(db_path),
                    "backup_path": str(backup_path) if backup_path else None,
                    "candidate_count": len(candidates),
                    "updated_count": applied,
                    "request_id": request_id,
                    "latency_ms": _now_ms() - started_ms,
                    "sample": planned[:12],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
