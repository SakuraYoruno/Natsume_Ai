#!/usr/bin/env python
"""Backfill gateway://message refs for historical LifeMemory voice anchors."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = "host_brain/data/audit.sqlite3"
DEFAULT_ENV_FILE = "runtime/host/host.runtime.env"
GATEWAY_SESSION_PREFIXES = ("qq_", "wechat_", "wx_", "wecom_", "feishu_")
MIGRATION_ID = "life_gateway_evidence_refs_backfill_20260528"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill gateway evidence refs for LifeMemory episodes.")
    parser.add_argument("--db-path", default="")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
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


def _refs(metadata: Mapping[str, Any]) -> list[str]:
    raw = metadata.get("evidence_refs")
    if not isinstance(raw, list):
        return []
    refs: list[str] = []
    for item in raw:
        ref = _clean(item)
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def _is_synthetic(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> bool:
    person_id = _clean(row.get("person_id")).lower()
    session_id = _clean(row.get("session_id")).lower()
    if bool(metadata.get("e2e_probe") or metadata.get("synthetic_probe") or metadata.get("test_probe")):
        return True
    markers = ("surface-memory-recall-e2e", "pytest", "test-", "probe")
    return any(marker in person_id or marker in session_id for marker in markers)


def _is_gateway_context(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> bool:
    payload = _json_loads(metadata.get("payload"))
    session_id = _clean(row.get("session_id"))
    conversation_id = _clean(
        metadata.get("conversation_id")
        or metadata.get("reply_target")
        or payload.get("conversation_id")
        or payload.get("reply_target")
        or session_id
    )
    platform = _clean(metadata.get("platform") or metadata.get("reply_channel") or payload.get("platform") or payload.get("reply_channel")).lower()
    source = _clean(payload.get("source") or payload.get("input_source")).lower()
    return (
        platform in {"qq", "wechat", "gateway", "onebot", "wecom", "feishu"}
        or source == "gateway"
        or session_id.startswith(GATEWAY_SESSION_PREFIXES)
        or conversation_id.startswith(GATEWAY_SESSION_PREFIXES)
    )


def _derive_gateway_ref(row: Mapping[str, Any], metadata: Mapping[str, Any], evidence_refs: list[str]) -> str:
    payload = _json_loads(metadata.get("payload"))
    msg_id = _clean(payload.get("msg_id") or payload.get("message_id") or payload.get("message_seq"))
    if msg_id:
        return f"gateway://message/{msg_id}"
    for ref in evidence_refs:
        if ref.startswith("voice://event/"):
            return "gateway://message/" + ref.rsplit("/", 1)[-1]
    primary_ref = _clean(row.get("primary_ref"))
    if primary_ref.startswith("voice://event/"):
        return "gateway://message/" + primary_ref.rsplit("/", 1)[-1]
    return f"gateway://message/{_clean(row.get('episode_id'))}"


def _derive_evidence_id(episode_id: str, source_ref: str) -> str:
    import hashlib

    key = f"{episode_id}|gateway_message|{source_ref}"
    return "ev_" + hashlib.sha256(("life_evidence:v1:" + key).encode("utf-8")).hexdigest()[:32]


def _backup_db(db_path: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = ROOT / "runtime" / "host" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{db_path.name}.{MIGRATION_ID}.{stamp}.sqlite3"
    shutil.copy2(db_path, backup_path)
    return backup_path


def main() -> int:
    args = _parse_args()
    _load_env_file(_resolve_path(str(args.env_file or DEFAULT_ENV_FILE)))
    db_path = _resolve_db_path(str(args.db_path or ""))
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT ep.*, GROUP_CONCAT(DISTINCT ev.source_ref) AS evidence_source_refs
        FROM life_episode ep
        LEFT JOIN life_evidence ev ON ev.episode_id = ep.episode_id
        GROUP BY ep.episode_id
        ORDER BY ep.created_ts_ms DESC
        """
    ).fetchall()
    updates: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    for raw_row in rows:
        row = dict(raw_row)
        metadata = _json_loads(row["metadata"])
        refs = _refs(metadata)
        if _is_synthetic(row, metadata):
            continue
        if not _is_gateway_context(row, metadata):
            continue
        joined_refs = "\n".join(refs + [_clean(row["evidence_source_refs"])])
        if "gateway://message/" in joined_refs:
            continue
        gateway_ref = _derive_gateway_ref(row, metadata, refs)
        updates.append((row, metadata, gateway_ref))
        if args.limit and len(updates) >= args.limit:
            break

    backup_path = None
    if args.apply and updates:
        backup_path = _backup_db(db_path)
        now_ms = int(time.time() * 1000)
        with conn:
            for row, metadata, gateway_ref in updates:
                refs = _refs(metadata)
                if gateway_ref not in refs:
                    refs.append(gateway_ref)
                metadata["evidence_refs"] = refs
                metadata["gateway_evidence_ref_backfill"] = {
                    "migration_id": MIGRATION_ID,
                    "gateway_ref": gateway_ref,
                    "ts_ms": now_ms,
                }
                conn.execute(
                    """
                    UPDATE life_episode
                    SET metadata = ?, last_touched_ts_ms = MAX(COALESCE(last_touched_ts_ms, 0), ?)
                    WHERE episode_id = ?
                    """,
                    (json.dumps(metadata, ensure_ascii=False, sort_keys=True), now_ms, row["episode_id"]),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO life_evidence (
                        evidence_id, episode_id, evidence_kind, source_ref, request_id,
                        content, payload, trust_level, created_ts_ms
                    )
                    VALUES (?, ?, 'gateway_message', ?, ?, ?, ?, 'VERIFIED', ?)
                    """,
                    (
                        _derive_evidence_id(str(row["episode_id"]), gateway_ref),
                        row["episode_id"],
                        gateway_ref,
                        row["primary_ref"] or row["episode_id"],
                        row["summary"] or "",
                        json.dumps({"migration_id": MIGRATION_ID}, ensure_ascii=False),
                        now_ms,
                    ),
                )

    samples = [
        {
            "episode_id": row["episode_id"],
            "episode_kind": row["episode_kind"],
            "action": row["action"],
            "session_id": row["session_id"],
            "gateway_ref": gateway_ref,
        }
        for row, _metadata, gateway_ref in updates[:10]
    ]
    print(
        json.dumps(
            {
                "status": "OK",
                "apply": bool(args.apply),
                "db_path": str(db_path),
                "planned_updates": len(updates),
                "backup_path": str(backup_path) if backup_path else None,
                "samples": samples,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
