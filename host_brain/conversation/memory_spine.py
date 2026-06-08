from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any


def _text(value: Any, *, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _conn(ledger: Any) -> Any:
    return getattr(ledger, "_conn", None)


def _already_in_life_stream(*, ledger: Any, request_id: str, role: str) -> bool:
    conn = _conn(ledger)
    if conn is None or not request_id:
        return False
    try:
        row = conn.execute(
            """
SELECT 1
FROM life_experience_event
WHERE source_kind LIKE 'conversation_turn.%'
  AND json_extract(evidence_json, '$.request_id') = ?
  AND json_extract(evidence_json, '$.role') = ?
LIMIT 1
""",
            (request_id, role),
        ).fetchone()
        return bool(row)
    except Exception:
        needle_req = f'"request_id":"{request_id}"'
        needle_role = f'"role":"{role}"'
        try:
            row = conn.execute(
                """
SELECT 1
FROM life_experience_event
WHERE source_kind LIKE 'conversation_turn.%'
  AND REPLACE(evidence_json, ' ', '') LIKE ?
  AND REPLACE(evidence_json, ' ', '') LIKE ?
LIMIT 1
""",
                (f"%{needle_req}%", f"%{needle_role}%"),
            ).fetchone()
            return bool(row)
        except Exception:
            return False


def _turn_source_kind(role: str, metadata: Mapping[str, Any]) -> tuple[str, str]:
    source_kind = str(metadata.get("source_kind") or "").strip()
    if role == "assistant" and source_kind == "life_proactive":
        return "conversation_turn.life_proactive", "life_proactive_sent"
    if role == "user":
        return "conversation_turn.user", "user_message"
    if role == "assistant":
        return "conversation_turn.assistant", "assistant_reply"
    return "conversation_turn.other", "conversation_turn"


def _signal_weights(*, role: str, source_kind: str, content: str, metadata: Mapping[str, Any]) -> tuple[float, float, float]:
    input_source = str(metadata.get("input_source") or "").strip().lower()
    text = str(content or "")
    if role == "user" and input_source in {"asr", "audio", "voice", "speech", "mic", "microphone", "voice_chat"}:
        salience = 0.28
        uncertainty = 0.35
    elif role == "user":
        salience = 0.62
        uncertainty = 0.08
    elif source_kind == "conversation_turn.life_proactive":
        salience = 0.56
        uncertainty = 0.06
    else:
        salience = 0.36
        uncertainty = 0.08
    negative = any(token in text for token in ("不对", "没效果", "牛头不对马嘴", "负优化", "莫名其妙", "烦", "抽象"))
    positive = any(token in text for token in ("可以了", "不错", "好了", "对了", "喜欢", "合理"))
    valence = -0.35 if negative else 0.24 if positive else 0.03 if role == "assistant" else 0.05
    return salience, valence, uncertainty


def record_conversation_turn_memory_spine(
    *,
    request_id: str,
    session_id: str,
    person_id: str | None,
    role: str,
    content: str,
    ts_ms: int,
    lang: str,
    metadata: Mapping[str, Any] | None = None,
    turn_id: str = "",
    turn_index: int | None = None,
) -> dict[str, Any]:
    """Bridge canonical conversation turns into the digital-life experience stream."""

    clean_session = _text(session_id, limit=180)
    clean_content = _text(content, limit=1000)
    clean_role = str(role or "").strip().lower()
    if not clean_session or not clean_role or not clean_content:
        return {"status": "SKIPPED", "reason_code": "EMPTY_CONVERSATION_TURN"}
    meta = _mapping(metadata)
    resolved_person = _text(person_id or "default", limit=120) or "default"
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
        from host_brain.runtime.life_continuity import ensure_life_schema, record_life_experience  # noqa: PLC0415

        ledger = get_ledger()
        ensure_life_schema(ledger)
        if _already_in_life_stream(ledger=ledger, request_id=str(request_id or ""), role=clean_role):
            return {"status": "SKIPPED", "reason_code": "DUPLICATE_CONVERSATION_TURN"}
        source_kind, event_kind = _turn_source_kind(clean_role, meta)
        salience, valence, uncertainty = _signal_weights(role=clean_role, source_kind=source_kind, content=clean_content, metadata=meta)
        evidence = {
            "memory_spine": "conversation_turn",
            "request_id": str(request_id or "").strip(),
            "turn_id": str(turn_id or "").strip(),
            "turn_index": turn_index,
            "session_id": clean_session,
            "role": clean_role,
            "lang": str(lang or "").strip(),
            "ts_ms": int(ts_ms or 0),
            "source_kind": str(meta.get("source_kind") or "").strip(),
            "input_source": str(meta.get("input_source") or "").strip(),
            "reply_source": str(meta.get("reply_source") or "").strip(),
        }
        result = record_life_experience(
            ledger=ledger,
            person_id=resolved_person,
            session_id=clean_session,
            source_kind=source_kind,
            event_kind=event_kind,
            content_summary=clean_content,
            evidence=evidence,
            salience=salience,
            valence=valence,
            uncertainty=uncertainty,
            consume=True,
        )
        return {
            "status": "OK",
            "source_kind": source_kind,
            "event_kind": event_kind,
            "event_id": str(result.get("event_id") or ""),
        }
    except Exception as exc:
        return {"status": "DEGRADED", "reason_code": type(exc).__name__, "detail": str(exc)[:180]}


def backfill_conversation_turn_memory_spine(
    *,
    person_id: str | None = "default",
    session_id: str = "",
    limit: int = 500,
    dry_run: bool = True,
    include_asr_sessions: bool = False,
) -> dict[str, Any]:
    """Idempotently bridge existing conversation_turn rows into life_experience_event."""

    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
        from host_brain.runtime.life_continuity import ensure_life_schema  # noqa: PLC0415

        ledger = get_ledger()
        ensure_life_schema(ledger)
        conn = _conn(ledger)
        if conn is None:
            return {"status": "DEGRADED", "reason_code": "LEDGER_CONNECTION_MISSING"}
        clean_person = _text(person_id or "", limit=120)
        clean_session = _text(session_id, limit=180)
        capped = max(1, min(int(limit or 500), 5000))
        where = ["role IN ('user', 'assistant')", "COALESCE(content, '') != ''"]
        params: list[Any] = []
        if clean_person:
            where.append("(person_id = ? OR COALESCE(person_id, '') = '')")
            params.append(clean_person)
        if clean_session:
            where.append("session_id = ?")
            params.append(clean_session)
        elif not include_asr_sessions:
            where.append("session_id NOT LIKE 'asr_req_%'")
            where.append("session_id NOT LIKE 'codex_%'")
            where.append("session_id NOT LIKE '__codex_%'")
        rows = conn.execute(
            f"""
SELECT turn_id, session_id, person_id, request_id, role, content, ts_ms, lang, metadata, turn_index
FROM conversation_turn
WHERE {' AND '.join(where)}
ORDER BY ts_ms ASC
LIMIT ?
""",
            (*params, capped),
        ).fetchall()
        scanned = 0
        eligible = 0
        written = 0
        skipped_existing = 0
        degraded = 0
        samples: list[dict[str, Any]] = []
        for row in rows:
            scanned += 1
            role = str(row["role"] or "").strip().lower()
            request_id = str(row["request_id"] or "").strip()
            if _already_in_life_stream(ledger=ledger, request_id=request_id, role=role):
                skipped_existing += 1
                continue
            eligible += 1
            if len(samples) < 8:
                samples.append(
                    {
                        "request_id": request_id,
                        "session_id": str(row["session_id"] or ""),
                        "role": role,
                        "content": _text(row["content"], limit=80),
                    }
                )
            if dry_run:
                continue
            metadata = {}
            try:
                loaded = json.loads(str(row["metadata"] or "{}"))
                metadata = dict(loaded or {}) if isinstance(loaded, Mapping) else {}
            except Exception:
                metadata = {}
            result = record_conversation_turn_memory_spine(
                request_id=request_id,
                session_id=str(row["session_id"] or ""),
                person_id=str(row["person_id"] or clean_person or "default"),
                role=role,
                content=str(row["content"] or ""),
                ts_ms=int(row["ts_ms"] or 0),
                lang=str(row["lang"] or "zh"),
                metadata=metadata,
                turn_id=str(row["turn_id"] or ""),
                turn_index=int(row["turn_index"] or 0),
            )
            if str(result.get("status") or "") == "OK":
                written += 1
            elif str(result.get("reason_code") or "") == "DUPLICATE_CONVERSATION_TURN":
                skipped_existing += 1
            else:
                degraded += 1
        return {
            "status": "OK",
            "dry_run": bool(dry_run),
            "include_asr_sessions": bool(include_asr_sessions),
            "person_id": clean_person or None,
            "session_id": clean_session or None,
            "limit": capped,
            "scanned": scanned,
            "eligible": eligible,
            "written": written,
            "skipped_existing": skipped_existing,
            "degraded": degraded,
            "samples": samples,
        }
    except Exception as exc:
        return {"status": "DEGRADED", "reason_code": type(exc).__name__, "detail": str(exc)[:240]}


def count_conversation_turn_memory_spine_gaps(*, person_id: str | None = "default", session_id: str = "", limit: int = 2000, include_asr_sessions: bool = False) -> int:
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
        from host_brain.runtime.life_continuity import ensure_life_schema  # noqa: PLC0415

        ledger = get_ledger()
        ensure_life_schema(ledger)
        conn = _conn(ledger)
        if conn is None:
            return 0
        clean_person = _text(person_id or "", limit=120)
        clean_session = _text(session_id, limit=180)
        capped = max(1, min(int(limit or 2000), 10000))
        where = ["t.role IN ('user', 'assistant')", "COALESCE(t.content, '') != ''"]
        params: list[Any] = []
        if clean_person:
            where.append("(t.person_id = ? OR COALESCE(t.person_id, '') = '')")
            params.append(clean_person)
        if clean_session:
            where.append("t.session_id = ?")
            params.append(clean_session)
        elif not include_asr_sessions:
            where.append("t.session_id NOT LIKE 'asr_req_%'")
            where.append("t.session_id NOT LIKE 'codex_%'")
            where.append("t.session_id NOT LIKE '__codex_%'")
        row = conn.execute(
            f"""
SELECT COUNT(*)
FROM (
  SELECT t.request_id, t.role
  FROM conversation_turn t
  WHERE {' AND '.join(where)}
  ORDER BY t.ts_ms DESC
  LIMIT ?
) t
WHERE NOT EXISTS (
  SELECT 1
  FROM life_experience_event e
  WHERE e.source_kind LIKE 'conversation_turn.%'
    AND json_extract(e.evidence_json, '$.request_id') = t.request_id
    AND json_extract(e.evidence_json, '$.role') = t.role
)
""",
            (*params, capped),
        ).fetchone()
        return int(row[0] or 0)
    except Exception:
        return 0


__all__ = [
    "backfill_conversation_turn_memory_spine",
    "count_conversation_turn_memory_spine_gaps",
    "record_conversation_turn_memory_spine",
]
