from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from shutil import copy2
from typing import Any
from collections.abc import Mapping


VISIBILITIES = {"visible", "private", "system", "projection"}
VISIBLE_EVENT_KINDS = {"user_message", "assistant_reply", "life_proactive_sent", "life_proactive_user_response"}
PRIVATE_EVENT_KINDS = {"inner_stream_tick", "dream_generated", "diary_generated"}
USER_EVENT_KINDS = {"user_message", "life_proactive_user_response"}
ASSISTANT_EVENT_KINDS = {"assistant_reply", "life_proactive_sent"}


MEMORY_EVENT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memory_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    person_id TEXT NOT NULL DEFAULT 'default',
    session_id TEXT,
    conversation_id TEXT,
    source_kind TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK(visibility IN ('visible', 'private', 'system', 'projection')),
    role TEXT CHECK(role IN ('user', 'assistant') OR role IS NULL OR role = ''),
    content TEXT NOT NULL DEFAULT '',
    content_summary TEXT NOT NULL DEFAULT '',
    causality_json TEXT NOT NULL DEFAULT '{}',
    refs_json TEXT NOT NULL DEFAULT '{}',
    salience REAL NOT NULL DEFAULT 0.0,
    valence REAL NOT NULL DEFAULT 0.0,
    uncertainty REAL NOT NULL DEFAULT 0.0,
    confidence REAL NOT NULL DEFAULT 1.0,
    projection_status_json TEXT NOT NULL DEFAULT '{}',
    created_at_ms INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_event_id ON memory_event(event_id);
CREATE INDEX IF NOT EXISTS idx_memory_event_person_ts ON memory_event(person_id, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_memory_event_session_ts ON memory_event(session_id, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_memory_event_visibility_ts ON memory_event(person_id, visibility, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_memory_event_kind_ts ON memory_event(event_kind, created_at_ms DESC);
"""


MEMORY_REBUILD_CLEAR_TABLES: tuple[str, ...] = (
    "memory_event",
    "conversation_turn",
    "life_experience_event",
    "proactive_interaction_episode",
    "proactive_life_outbox",
    "conversation_belief_state",
    "semantic_memory",
    "life_memory_entity_mention",
    "life_memory_collection_item",
    "life_memory_correction",
    "life_memory_compaction",
    "life_open_loop",
    "life_episode_link",
    "life_evidence",
    "life_memory_entity",
    "life_memory_collection",
    "life_episode",
    "user_preference_event",
    "user_preference_profile",
    "recommendation_delivery_log",
    "digital_life_state",
    "digital_life_state_event",
    "dialogue_life_influence_log",
    "embodiment_dispatch_log",
    "work_environment_event",
    "organ_runtime_state",
    "organ_runtime_event",
    "organ_coupling_event",
    "life_state_synthesis_log",
)


def now_ms() -> int:
    return int(time.time() * 1000)


def event_id(prefix: str = "memevt") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def loads(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def text(value: Any, *, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[: max(1, int(limit))]


def mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def clamp(value: Any, default: float = 0.0) -> float:
    try:
        raw = float(value)
    except Exception:
        raw = float(default)
    return max(0.0, min(1.0, raw))


def infer_visibility(event_kind: str, source_kind: str = "") -> str:
    kind = str(event_kind or "").strip()
    source = str(source_kind or "").strip()
    if kind in VISIBLE_EVENT_KINDS or source.startswith("conversation_turn."):
        return "visible"
    if kind in PRIVATE_EVENT_KINDS or "inner" in source or "dream" in source or "diary" in source:
        return "private"
    return "system"


def infer_role(event_kind: str, role: str = "") -> str:
    raw = str(role or "").strip().lower()
    if raw in {"user", "assistant"}:
        return raw
    kind = str(event_kind or "").strip()
    if kind in USER_EVENT_KINDS:
        return "user"
    if kind in ASSISTANT_EVENT_KINDS:
        return "assistant"
    return ""


def table_exists(conn: Any, name: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)).fetchone()
    return bool(row)


class MemoryEventStore:
    """Single event-source store for conversation, life, work, body, and preference memory."""

    def __init__(self, ledger: Any) -> None:
        self.ledger = ledger
        self.conn = getattr(ledger, "_conn", None)
        if self.conn is None:
            raise RuntimeError("ledger sqlite connection unavailable")
        self.write_lock = getattr(ledger, "_write_lock", None)
        self.ensure_schema()

    def ensure_schema(self) -> None:
        self.conn.executescript(MEMORY_EVENT_SCHEMA_SQL)
        self.conn.commit()

    def append_event(
        self,
        *,
        person_id: str = "default",
        session_id: str = "",
        conversation_id: str = "",
        source_kind: str,
        event_kind: str,
        visibility: str = "",
        role: str = "",
        content: str = "",
        content_summary: str = "",
        causality: Mapping[str, Any] | None = None,
        refs: Mapping[str, Any] | None = None,
        salience: float = 0.0,
        valence: float = 0.0,
        uncertainty: float = 0.0,
        confidence: float = 1.0,
        projection_status: Mapping[str, Any] | None = None,
        created_at_ms: int | None = None,
        active: bool = True,
    ) -> dict[str, Any]:
        ts = int(created_at_ms or now_ms())
        kind = text(event_kind, limit=120) or "memory_event"
        resolved_visibility = text(visibility, limit=40) or infer_visibility(kind, source_kind)
        if resolved_visibility not in VISIBILITIES:
            resolved_visibility = infer_visibility(kind, source_kind)
        resolved_role = infer_role(kind, role)
        resolved_content = text(content, limit=4000)
        summary = text(content_summary or resolved_content, limit=1000)
        eid = event_id()
        payload = (
            eid,
            text(person_id or "default", limit=120) or "default",
            text(session_id, limit=180),
            text(conversation_id or session_id, limit=180),
            text(source_kind, limit=120) or "unknown",
            kind,
            resolved_visibility,
            resolved_role,
            resolved_content,
            summary,
            dumps(dict(causality or {})),
            dumps(dict(refs or {})),
            clamp(salience),
            max(-1.0, min(1.0, float(valence or 0.0))),
            clamp(uncertainty),
            clamp(confidence, default=1.0),
            dumps(dict(projection_status or {})),
            ts,
            1 if active else 0,
        )
        lock = self.write_lock
        if lock is None:
            self.conn.execute(
                """
INSERT INTO memory_event(
    event_id, person_id, session_id, conversation_id, source_kind, event_kind,
    visibility, role, content, content_summary, causality_json, refs_json,
    salience, valence, uncertainty, confidence, projection_status_json, created_at_ms, active
) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
                payload,
            )
            self.conn.commit()
        else:
            with lock:
                self.conn.execute(
                    """
INSERT INTO memory_event(
    event_id, person_id, session_id, conversation_id, source_kind, event_kind,
    visibility, role, content, content_summary, causality_json, refs_json,
    salience, valence, uncertainty, confidence, projection_status_json, created_at_ms, active
) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
                    payload,
                )
                self.conn.commit()
        return {
            "event_id": eid,
            "person_id": payload[1],
            "session_id": payload[2],
            "conversation_id": payload[3],
            "source_kind": payload[4],
            "event_kind": kind,
            "visibility": resolved_visibility,
            "role": resolved_role,
            "created_at_ms": ts,
        }

    def append_visible_turn(
        self,
        *,
        request_id: str,
        session_id: str,
        person_id: str | None,
        role: str,
        content: str,
        ts_ms: int,
        lang: str = "zh",
        metadata: Mapping[str, Any] | None = None,
        token_count: int | None = None,
        corrupted: bool = False,
    ) -> dict[str, Any]:
        sid = text(session_id, limit=180)
        clean_role = infer_role("user_message" if role == "user" else "assistant_reply", role)
        clean_content = text(content, limit=4000)
        if not sid or not clean_role or not clean_content:
            return {"status": "SKIPPED", "reason_code": "EMPTY_VISIBLE_TURN"}
        existing = self._find_visible_event_by_request(session_id=sid, request_id=request_id, role=clean_role)
        if existing:
            return {**existing, "status": "SKIPPED", "reason_code": "DUPLICATE_VISIBLE_TURN"}
        meta = mapping(metadata)
        source = str(meta.get("source_kind") or "").strip()
        event_kind = "life_proactive_sent" if clean_role == "assistant" and source == "life_proactive" else "user_message" if clean_role == "user" else "assistant_reply"
        refs = {
            "request_id": text(request_id, limit=180),
            "lang": text(lang, limit=30) or "zh",
            "metadata": meta,
        }
        for key in ("proactive_outbox_id", "proactive_episode_id", "work_id", "recommendation_id", "delivery_id"):
            if meta.get(key):
                refs[key] = meta.get(key)
        mem = self.append_event(
            person_id=person_id or "default",
            session_id=sid,
            conversation_id=sid,
            source_kind="conversation_turn",
            event_kind=event_kind,
            visibility="visible",
            role=clean_role,
            content=clean_content,
            content_summary=clean_content,
            refs=refs,
            salience=0.62 if clean_role == "user" else 0.56 if event_kind == "life_proactive_sent" else 0.36,
            valence=self._valence_for_text(clean_content, role=clean_role),
            uncertainty=0.08,
            confidence=1.0,
            created_at_ms=int(ts_ms or now_ms()),
        )
        turn = self.project_conversation_turn(
            memory_event_id=mem["event_id"],
            request_id=request_id,
            session_id=sid,
            person_id=person_id or "default",
            role=clean_role,
            content=clean_content,
            ts_ms=int(ts_ms or now_ms()),
            lang=lang,
            token_count=token_count,
            corrupted=corrupted,
            metadata={**meta, "memory_event_id": mem["event_id"]},
        )
        life = self.project_life_experience_from_event(mem["event_id"], consume=True)
        projection_status = {
            "conversation_turn": {"status": "OK", "turn_id": turn.get("turn_id")},
            "life_experience": {"status": life.get("status", "OK"), "event_id": life.get("event_id")},
        }
        self.merge_event_refs(mem["event_id"], {"turn_id": turn.get("turn_id"), "life_experience_event_id": life.get("event_id")})
        self.update_projection_status(mem["event_id"], projection_status)
        return {"status": "OK", "memory_event_id": mem["event_id"], "turn": turn, "life_experience_event_id": life.get("event_id"), "projection_status": projection_status}

    def project_conversation_turn(
        self,
        *,
        memory_event_id: str,
        request_id: str,
        session_id: str,
        person_id: str,
        role: str,
        content: str,
        ts_ms: int,
        lang: str,
        token_count: int | None = None,
        corrupted: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not table_exists(self.conn, "conversation_turn"):
            return {"status": "MISSING_TABLE"}
        existing = self.conn.execute(
            "SELECT turn_id, turn_index FROM conversation_turn WHERE session_id=? AND request_id=? AND role=? LIMIT 1",
            (session_id, str(request_id or "").strip(), role),
        ).fetchone()
        if existing:
            return {"status": "EXISTS", "turn_id": existing["turn_id"], "turn_index": int(existing["turn_index"] or 0)}
        row = self.conn.execute("SELECT COALESCE(MAX(turn_index), -1) + 1 AS next_idx FROM conversation_turn WHERE session_id=?", (session_id,)).fetchone()
        turn_index = int(row["next_idx"] if row else 0)
        turn_id = f"turn::{uuid.uuid4().hex}"
        metadata_json = dumps(dict(metadata or {}))
        self.conn.execute(
            """
INSERT OR IGNORE INTO conversation_turn(
    turn_id, session_id, person_id, request_id, role, content,
    ts_ms, turn_index, lang, token_count, corrupted, metadata, created_at_ms
) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
            (
                turn_id,
                session_id,
                person_id or "default",
                str(request_id or "").strip(),
                role,
                content,
                int(ts_ms),
                turn_index,
                str(lang or "zh") or "zh",
                token_count,
                1 if corrupted else 0,
                metadata_json,
                int(ts_ms),
            ),
        )
        self.conn.commit()
        return {"status": "OK", "turn_id": turn_id, "turn_index": turn_index, "memory_event_id": memory_event_id}

    def project_life_experience_from_event(self, memory_event_id: str, *, consume: bool = True) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM memory_event WHERE event_id=? AND active=1 LIMIT 1",
            (memory_event_id,),
        ).fetchone()
        if not row:
            return {"status": "NOT_FOUND"}
        refs = mapping(loads(row["refs_json"], {}))
        if refs.get("life_experience_event_id"):
            return {"status": "EXISTS", "event_id": refs.get("life_experience_event_id")}
        try:
            from host_brain.runtime.life_organs import append_life_experience  # noqa: PLC0415

            evidence = {
                "memory_event_id": memory_event_id,
                "memory_event_kind": row["event_kind"],
                "memory_event_visibility": row["visibility"],
                **refs,
            }
            result = append_life_experience(
                ledger=self.ledger,
                person_id=row["person_id"],
                session_id=row["session_id"] or "",
                source_kind=f"memory_event.{row['event_kind']}",
                event_kind=row["event_kind"],
                content_summary=row["content_summary"] or row["content"],
                evidence=evidence,
                salience=float(row["salience"] or 0.0),
                valence=float(row["valence"] or 0.0),
                uncertainty=float(row["uncertainty"] or 0.0),
                now_ms=int(row["created_at_ms"] or now_ms()),
                consume=consume,
            )
            return {"status": "OK", "event_id": result.get("event_id"), "organ_processing": result.get("organ_processing", {})}
        except Exception as exc:
            self.update_projection_status(memory_event_id, {"life_experience": {"status": "FAILED", "reason_code": type(exc).__name__, "detail": str(exc)[:160]}})
            return {"status": "FAILED", "reason_code": type(exc).__name__, "detail": str(exc)[:160]}

    def merge_event_refs(self, event_id_value: str, refs: Mapping[str, Any]) -> None:
        row = self.conn.execute("SELECT refs_json FROM memory_event WHERE event_id=? LIMIT 1", (event_id_value,)).fetchone()
        if not row:
            return
        current = mapping(loads(row["refs_json"], {}))
        current.update({k: v for k, v in dict(refs or {}).items() if v not in (None, "")})
        self.conn.execute("UPDATE memory_event SET refs_json=? WHERE event_id=?", (dumps(current), event_id_value))
        self.conn.commit()

    def update_projection_status(self, event_id_value: str, projection_status: Mapping[str, Any]) -> None:
        row = self.conn.execute("SELECT projection_status_json FROM memory_event WHERE event_id=? LIMIT 1", (event_id_value,)).fetchone()
        if not row:
            return
        current = mapping(loads(row["projection_status_json"], {}))
        current.update(dict(projection_status or {}))
        self.conn.execute("UPDATE memory_event SET projection_status_json=? WHERE event_id=?", (dumps(current), event_id_value))
        self.conn.commit()

    def _find_visible_event_by_request(self, *, session_id: str, request_id: str, role: str) -> dict[str, Any]:
        if not request_id:
            return {}
        try:
            row = self.conn.execute(
                """
SELECT event_id, refs_json, projection_status_json
FROM memory_event
WHERE session_id=? AND role=? AND visibility='visible'
  AND json_extract(refs_json, '$.request_id')=?
ORDER BY created_at_ms DESC
LIMIT 1
""",
                (session_id, role, request_id),
            ).fetchone()
        except Exception:
            row = None
        if not row:
            return {}
        refs = mapping(loads(row["refs_json"], {}))
        return {"memory_event_id": row["event_id"], "turn": {"turn_id": refs.get("turn_id")}, "projection_status": loads(row["projection_status_json"], {})}

    @staticmethod
    def _valence_for_text(value: str, *, role: str) -> float:
        negative = any(token in value for token in ("不对", "没效果", "牛头不对马嘴", "负优化", "莫名其妙", "烦", "抽象"))
        positive = any(token in value for token in ("可以了", "不错", "好了", "对了", "喜欢", "合理"))
        return -0.35 if negative else 0.24 if positive else 0.03 if role == "assistant" else 0.05


def get_recent_memory_events(
    *,
    ledger: Any,
    person_id: str = "default",
    session_id: str = "",
    visibility: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    store = MemoryEventStore(ledger)
    clauses = ["person_id=?", "active=1"]
    params: list[Any] = [text(person_id or "default", limit=120) or "default"]
    sid = text(session_id, limit=180)
    if sid:
        clauses.append("session_id=?")
        params.append(sid)
    vis = text(visibility, limit=40)
    if vis:
        clauses.append("visibility=?")
        params.append(vis)
    params.append(max(1, min(int(limit or 50), 200)))
    rows = store.conn.execute(
        f"""
SELECT event_id, person_id, session_id, conversation_id, source_kind, event_kind,
       visibility, role, content, content_summary, causality_json, refs_json,
       salience, valence, uncertainty, confidence, projection_status_json, created_at_ms, active
FROM memory_event
WHERE {' AND '.join(clauses)}
ORDER BY created_at_ms DESC, id DESC
LIMIT ?
""",
        params,
    ).fetchall()
    return {"person_id": person_id, "items": [_row_to_event(row) for row in rows]}


def _row_to_event(row: Any) -> dict[str, Any]:
    return {
        "event_id": row["event_id"],
        "person_id": row["person_id"],
        "session_id": row["session_id"],
        "conversation_id": row["conversation_id"],
        "source_kind": row["source_kind"],
        "event_kind": row["event_kind"],
        "visibility": row["visibility"],
        "role": row["role"],
        "content": row["content"],
        "content_summary": row["content_summary"],
        "causality": loads(row["causality_json"], {}),
        "refs": loads(row["refs_json"], {}),
        "salience": float(row["salience"] or 0.0),
        "valence": float(row["valence"] or 0.0),
        "uncertainty": float(row["uncertainty"] or 0.0),
        "confidence": float(row["confidence"] or 0.0),
        "projection_status": loads(row["projection_status_json"], {}),
        "created_at_ms": int(row["created_at_ms"] or 0),
        "active": bool(row["active"]),
    }


def memory_event_health(*, ledger: Any, person_id: str = "default") -> dict[str, int]:
    store = MemoryEventStore(ledger)
    conn = store.conn
    pid = text(person_id or "default", limit=120) or "default"
    memory_event_projection_gaps = conn.execute(
        """
SELECT COUNT(*)
FROM memory_event
WHERE person_id=? AND active=1
  AND (
    projection_status_json LIKE '%FAILED%'
    OR projection_status_json LIKE '%MISSING%'
  )
""",
        (pid,),
    ).fetchone()[0]
    visible_turn_projection_gaps = conn.execute(
        """
SELECT COUNT(*)
FROM memory_event e
WHERE e.person_id=? AND e.active=1 AND e.visibility='visible' AND e.role IN ('user', 'assistant')
  AND COALESCE(json_extract(e.projection_status_json, '$.conversation_turn.status'), '') NOT IN ('OK', 'COVERED_BY_USER_MESSAGE')
  AND NOT EXISTS (
    SELECT 1 FROM conversation_turn t
    WHERE json_extract(t.metadata, '$.memory_event_id') = e.event_id
       OR (t.session_id=e.session_id AND t.role=e.role AND t.request_id=json_extract(e.refs_json, '$.request_id'))
  )
""",
        (pid,),
    ).fetchone()[0] if table_exists(conn, "conversation_turn") else 0
    proactive_episode_projection_gaps = conn.execute(
        """
SELECT COUNT(*)
FROM memory_event e
WHERE e.person_id=? AND e.active=1 AND e.event_kind='life_proactive_sent'
  AND NOT EXISTS (
    SELECT 1 FROM proactive_interaction_episode p
    WHERE p.episode_id=json_extract(e.refs_json, '$.proactive_episode_id')
       OR p.source_outbox_id=json_extract(e.refs_json, '$.proactive_outbox_id')
  )
""",
        (pid,),
    ).fetchone()[0] if table_exists(conn, "proactive_interaction_episode") else 0
    private_life_event_projection_gaps = conn.execute(
        """
SELECT COUNT(*)
FROM memory_event e
WHERE e.person_id=? AND e.active=1 AND e.visibility='private'
  AND NOT EXISTS (
    SELECT 1 FROM life_experience_event l
    WHERE json_extract(l.evidence_json, '$.memory_event_id') = e.event_id
  )
""",
        (pid,),
    ).fetchone()[0] if table_exists(conn, "life_experience_event") else 0
    orphan_projection_rows = conn.execute(
        """
SELECT COUNT(*)
FROM conversation_turn t
WHERE t.person_id=? AND COALESCE(json_extract(t.metadata, '$.memory_event_id'), '') = ''
""",
        (pid,),
    ).fetchone()[0] if table_exists(conn, "conversation_turn") else 0
    return {
        "memory_event_projection_gaps": int(memory_event_projection_gaps or 0),
        "visible_turn_projection_gaps": int(visible_turn_projection_gaps or 0),
        "proactive_episode_projection_gaps": int(proactive_episode_projection_gaps or 0),
        "private_life_event_projection_gaps": int(private_life_event_projection_gaps or 0),
        "orphan_projection_rows": int(orphan_projection_rows or 0),
    }


def rebuild_memory_system(*, ledger: Any, person_id: str = "default") -> dict[str, Any]:
    store = MemoryEventStore(ledger)
    conn = store.conn
    ts = now_ms()
    backup_path = ""
    db_path = ""
    try:
        db_row = conn.execute("PRAGMA database_list").fetchone()
        db_path = str(db_row["file"] if hasattr(db_row, "keys") and "file" in db_row.keys() else db_row[2] if db_row else "")
    except Exception:
        db_path = ""
    if db_path and db_path != ":memory:":
        backup_dir = Path("artifacts") / "memory_rebuild_backup" / str(ts)
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / "audit.sqlite3"
        try:
            source = sqlite3.connect(db_path)
            dest = sqlite3.connect(str(backup))
            source.backup(dest)
            dest.close()
            source.close()
        except Exception:
            copy2(db_path, backup)
        backup_path = str(backup.resolve())
    cleared: dict[str, int] = {}
    for table in MEMORY_REBUILD_CLEAR_TABLES:
        if not table_exists(conn, table):
            continue
        cur = conn.execute(f"DELETE FROM {table}")
        cleared[table] = int(cur.rowcount or 0)
    conn.commit()
    store.ensure_schema()
    epoch = store.append_event(
        person_id=person_id or "default",
        source_kind="memory_rebuild",
        event_kind="memory_system_rebuilt",
        visibility="system",
        content_summary="memory event source rebuilt; fragmented runtime memory cleared",
        refs={"backup_path": backup_path, "cleared_tables": cleared},
        salience=0.4,
        valence=0.0,
        uncertainty=0.02,
        confidence=1.0,
        created_at_ms=ts,
    )
    return {
        "backup_path": backup_path,
        "cleared_tables": cleared,
        "initialized_tables": ["memory_event"],
        "epoch_event_id": epoch["event_id"],
    }


__all__ = [
    "MemoryEventStore",
    "get_recent_memory_events",
    "memory_event_health",
    "rebuild_memory_system",
    "infer_visibility",
    "infer_role",
]
