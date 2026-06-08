"""Persistent conversation experience store.

The store keeps the reasoning trail behind a reply: belief state, resolved
references, intent hypotheses, correction signals, and distilled lessons. It is
not a hard-rule responder; it gives later turns experience to reason from.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Mapping, Sequence


_OUTCOME_SUCCESS_RE = re.compile(
    r"^\s*(对了|这次对了|对的|是这个|就是这个|可以了|可以|行了|好了|没问题|这次可以|这次行|这就对了|嗯对|对)\s*[。.!！?？]*\s*$",
    re.IGNORECASE,
)
_OUTCOME_FAILURE_RE = re.compile(
    r"(还是不对|还不对|不对|不是这个|不是这个意思|又错了|还是错|没接上|接不上|承接不上|牛头不对马嘴|跑偏|理解错|误解|不太对|不行)",
    re.IGNORECASE,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return "{}"


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value or []) if isinstance(value, list) else []


def _belief_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    try:
        raw = row["belief_json"]
    except Exception:
        raw = row[0]
    return _mapping(_loads(raw, {}))


def _belief_can_drive_reply(belief: Mapping[str, Any], *, now_ms: int | None = None) -> bool:
    if not belief:
        return False
    flag = belief.get("can_drive_reply")
    if flag is False or str(flag).strip().lower() in {"0", "false", "no"}:
        return False
    expires_at = 0
    try:
        expires_at = int(belief.get("expires_at_ms") or 0)
    except Exception:
        expires_at = 0
    if expires_at and expires_at < int(now_ms or _now_ms()):
        return False
    return True


def _usable_belief_from_rows(rows: Sequence[Any]) -> dict[str, Any]:
    for row in rows:
        belief = _belief_from_row(row)
        if not _belief_can_drive_reply(belief):
            continue
        confidence = float(belief.get("confidence") or 0.0)
        selected_intent = _text(belief.get("selected_intent"), limit=120)
        active_topic = _text(belief.get("active_topic"), limit=160)
        if confidence >= 0.55:
            return belief
        if active_topic and selected_intent not in {"open_chat", "smalltalk"}:
            return belief
    return {}


def classify_outcome_signal(text: str | None) -> str:
    raw = str(text or "").strip()
    if not raw:
        return "unknown"
    if _OUTCOME_FAILURE_RE.search(raw):
        return "failure"
    if _OUTCOME_SUCCESS_RE.search(raw):
        return "success"
    return "unknown"


def _get_ledger(ledger: Any | None = None) -> Any:
    if ledger is not None:
        return ledger
    from host_brain.app.dependencies import get_ledger  # noqa: PLC0415

    return get_ledger()


def _conn_and_lock(ledger: Any) -> tuple[Any | None, Any | None]:
    return getattr(ledger, "_conn", None), getattr(ledger, "_write_lock", None)


class ConversationExperienceStore:
    def __init__(self, *, ledger: Any | None = None) -> None:
        self.ledger = _get_ledger(ledger)
        self.conn, self.write_lock = _conn_and_lock(self.ledger)
        self.ensure_schema()

    def ensure_schema(self) -> None:
        if self.conn is None:
            return
        statements = (
            """
CREATE TABLE IF NOT EXISTS conversation_belief_state (
    session_id TEXT NOT NULL,
    person_id TEXT NOT NULL DEFAULT 'default',
    version INTEGER NOT NULL DEFAULT 1,
    belief_json TEXT NOT NULL DEFAULT '{}',
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (session_id, person_id)
)
""",
            """
CREATE TABLE IF NOT EXISTS conversation_experience_event (
    event_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    person_id TEXT NOT NULL DEFAULT 'default',
    user_text TEXT NOT NULL DEFAULT '',
    selected_intent TEXT NOT NULL DEFAULT '',
    response_policy TEXT NOT NULL DEFAULT '',
    understanding_json TEXT NOT NULL DEFAULT '{}',
    belief_before_json TEXT NOT NULL DEFAULT '{}',
    belief_after_json TEXT NOT NULL DEFAULT '{}',
    assistant_reply TEXT NOT NULL DEFAULT '',
    outcome_signal TEXT NOT NULL DEFAULT 'unknown',
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
)
""",
            """
CREATE TABLE IF NOT EXISTS conversation_correction_event (
    correction_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    person_id TEXT NOT NULL DEFAULT 'default',
    raw_text TEXT NOT NULL DEFAULT '',
    wrong_inference_json TEXT NOT NULL DEFAULT '{}',
    correct_reference_json TEXT NOT NULL DEFAULT '{}',
    repair_lesson TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    created_at_ms INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
)
""",
            """
CREATE TABLE IF NOT EXISTS conversation_experience_lesson (
    lesson_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL DEFAULT 'default',
    domain TEXT NOT NULL DEFAULT 'general',
    pattern TEXT NOT NULL DEFAULT '',
    lesson TEXT NOT NULL DEFAULT '',
    source_event_ids_json TEXT NOT NULL DEFAULT '[]',
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.0,
    updated_at_ms INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
)
""",
            "CREATE INDEX IF NOT EXISTS idx_conversation_experience_event_session ON conversation_experience_event(session_id, person_id, created_at_ms)",
            "CREATE INDEX IF NOT EXISTS idx_conversation_correction_session ON conversation_correction_event(session_id, person_id, created_at_ms)",
            "CREATE INDEX IF NOT EXISTS idx_conversation_lesson_domain ON conversation_experience_lesson(person_id, domain, updated_at_ms)",
        )
        lock = self.write_lock
        if lock is not None:
            with lock:
                for statement in statements:
                    self.conn.execute(statement)
                self.conn.commit()
            return
        for statement in statements:
            self.conn.execute(statement)
        self.conn.commit()

    def get_belief(self, *, session_id: str | None, person_id: str | None = None) -> dict[str, Any]:
        if self.conn is None:
            return {}
        sid = _text(session_id, limit=160)
        pid = _text(person_id, limit=80) or "default"
        if not sid:
            return {}
        row = self.conn.execute(
            """
SELECT belief_json FROM conversation_belief_state
WHERE session_id=? AND person_id=?
LIMIT 1
""",
            (sid, pid),
        ).fetchone()
        if not row:
            return {}
        belief = _belief_from_row(row)
        if not _belief_can_drive_reply(belief):
            return {}
        return belief

    def record_inference(
        self,
        *,
        request_id: str | None,
        session_id: str | None,
        person_id: str | None,
        user_text: str,
        understanding: Mapping[str, Any],
        belief_before: Mapping[str, Any] | None = None,
        assistant_reply: str | None = None,
        outcome_signal: str = "unknown",
    ) -> dict[str, Any]:
        if self.conn is None:
            return {"ok": False, "reason": "NO_CONN"}
        sid = _text(session_id, limit=160)
        pid = _text(person_id, limit=80) or "default"
        if not sid:
            return {"ok": False, "reason": "NO_SESSION"}
        now = _now_ms()
        understanding_dict = _mapping(understanding)
        belief_after = _mapping(understanding_dict.get("belief_state"))
        selected_intent = _text(
            understanding_dict.get("selected_intent") or belief_after.get("selected_intent"),
            limit=120,
        )
        response_policy = self._response_policy_from_understanding(understanding_dict)
        event_id = f"convexp_{uuid.uuid4().hex}"
        lock = self.write_lock
        if lock is not None:
            with lock:
                self._record_inference_locked(
                    event_id=event_id,
                    request_id=_text(request_id, limit=120),
                    session_id=sid,
                    person_id=pid,
                    user_text=_text(user_text, limit=1000),
                    selected_intent=selected_intent,
                    response_policy=response_policy,
                    understanding=understanding_dict,
                    belief_before=_mapping(belief_before),
                    belief_after=belief_after,
                    assistant_reply=_text(assistant_reply, limit=2000),
                    outcome_signal=_text(outcome_signal, limit=80) or "unknown",
                    now=now,
                )
                self.conn.commit()
        else:
            self._record_inference_locked(
                event_id=event_id,
                request_id=_text(request_id, limit=120),
                session_id=sid,
                person_id=pid,
                user_text=_text(user_text, limit=1000),
                selected_intent=selected_intent,
                response_policy=response_policy,
                understanding=understanding_dict,
                belief_before=_mapping(belief_before),
                belief_after=belief_after,
                assistant_reply=_text(assistant_reply, limit=2000),
                outcome_signal=_text(outcome_signal, limit=80) or "unknown",
                now=now,
            )
            self.conn.commit()
        correction = {}
        if self._looks_like_correction(understanding_dict):
            correction = self.record_correction_from_understanding(
                event_id=event_id,
                request_id=request_id,
                session_id=sid,
                person_id=pid,
                raw_text=user_text,
                understanding=understanding_dict,
            )
        return {"ok": True, "event_id": event_id, "correction": correction}

    def update_assistant_reply(
        self,
        *,
        request_id: str | None,
        session_id: str | None = None,
        person_id: str | None = None,
        assistant_reply: str,
        outcome_signal: str = "replied",
    ) -> dict[str, Any]:
        if self.conn is None:
            return {"ok": False, "reason": "NO_CONN"}
        req = _text(request_id, limit=120)
        sid = _text(session_id, limit=160)
        pid = _text(person_id, limit=80) or "default"
        if not req and not sid:
            return {"ok": False, "reason": "NO_KEY"}
        now = _now_ms()
        reply = _text(assistant_reply, limit=2000)
        outcome = _text(outcome_signal, limit=80) or "replied"
        lock = self.write_lock
        if lock is not None:
            with lock:
                changed = self._update_assistant_reply_locked(
                    request_id=req,
                    session_id=sid,
                    person_id=pid,
                    assistant_reply=reply,
                    outcome_signal=outcome,
                    now=now,
                )
                self.conn.commit()
        else:
            changed = self._update_assistant_reply_locked(
                request_id=req,
                session_id=sid,
                person_id=pid,
                assistant_reply=reply,
                outcome_signal=outcome,
                now=now,
            )
            self.conn.commit()
        return {"ok": bool(changed), "updated": int(changed)}

    def recall_lessons(
        self,
        *,
        person_id: str | None = None,
        domain: str | None = None,
        query_text: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if self.conn is None:
            return []
        pid = _text(person_id, limit=80) or "default"
        query = _text(query_text, limit=500)
        domain_text = _text(domain, limit=120)
        rows = self.conn.execute(
            """
SELECT lesson_id, person_id, domain, pattern, lesson, source_event_ids_json,
       success_count, failure_count, confidence, updated_at_ms
FROM conversation_experience_lesson
WHERE person_id=? AND active=1
ORDER BY updated_at_ms DESC
LIMIT 80
""",
            (pid,),
        ).fetchall()
        if not rows:
            rows = self.conn.execute(
                """
SELECT lesson_id, person_id, domain, pattern, lesson, source_event_ids_json,
       success_count, failure_count, confidence, updated_at_ms
FROM conversation_experience_lesson
WHERE active=1
ORDER BY updated_at_ms DESC
LIMIT 80
"""
            ).fetchall()
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            item = dict(row)
            score = float(item.get("confidence") or 0.0)
            lesson_domain = _text(item.get("domain"), limit=120)
            pattern = _text(item.get("pattern"), limit=160)
            lesson = _text(item.get("lesson"), limit=500)
            if domain_text and (domain_text in lesson_domain or lesson_domain in domain_text):
                score += 0.35
            if query:
                overlap = self._text_overlap_score(query, " ".join([lesson_domain, pattern, lesson]))
                score += min(0.45, overlap)
            failures = int(item.get("failure_count") or 0)
            successes = int(item.get("success_count") or 0)
            if failures > successes and float(item.get("confidence") or 0.0) < 0.85:
                continue
            score += min(0.3, successes * 0.08)
            score -= min(0.18, max(0, failures - successes) * 0.03)
            item["source_event_ids"] = _loads(item.pop("source_event_ids_json", "[]"), [])
            item["relevance"] = round(score, 4)
            scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[: max(1, int(limit or 5))]]

    def record_outcome_feedback(
        self,
        *,
        session_id: str | None,
        person_id: str | None = None,
        raw_text: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        signal = classify_outcome_signal(raw_text)
        if signal == "unknown":
            return {"ok": False, "reason": "NO_OUTCOME_SIGNAL", "signal": signal}
        if self.conn is None:
            return {"ok": False, "reason": "NO_CONN", "signal": signal}
        sid = _text(session_id, limit=160)
        pid = _text(person_id, limit=80) or "default"
        if not sid:
            return {"ok": False, "reason": "NO_SESSION", "signal": signal}
        now = _now_ms()
        lock = self.write_lock
        if lock is not None:
            with lock:
                result = self._record_outcome_feedback_locked(
                    session_id=sid,
                    person_id=pid,
                    request_id=_text(request_id, limit=120),
                    signal=signal,
                    now=now,
                )
                self.conn.commit()
            return result
        result = self._record_outcome_feedback_locked(
            session_id=sid,
            person_id=pid,
            request_id=_text(request_id, limit=120),
            signal=signal,
            now=now,
        )
        self.conn.commit()
        return result

    def _record_outcome_feedback_locked(
        self,
        *,
        session_id: str,
        person_id: str,
        request_id: str,
        signal: str,
        now: int,
    ) -> dict[str, Any]:
        row = self.conn.execute(
            """
SELECT event_id, understanding_json
FROM conversation_experience_event
WHERE session_id=? AND person_id=? AND active=1
ORDER BY created_at_ms DESC
LIMIT 1
""",
            (session_id, person_id),
        ).fetchone()
        if not row:
            row = self.conn.execute(
                """
SELECT event_id, understanding_json
FROM conversation_experience_event
WHERE person_id=? AND active=1
ORDER BY created_at_ms DESC
LIMIT 1
""",
                (person_id,),
            ).fetchone()
        if not row:
            row = self.conn.execute(
                """
SELECT event_id, understanding_json
FROM conversation_experience_event
WHERE active=1
ORDER BY created_at_ms DESC
LIMIT 1
"""
            ).fetchone()
        if not row:
            return {"ok": False, "reason": "NO_PREVIOUS_EVENT", "signal": signal}
        try:
            event_id = str(row["event_id"])
            understanding = _mapping(_loads(row["understanding_json"], {}))
        except Exception:
            event_id = str(row[0])
            understanding = _mapping(_loads(row[1], {}))
        self.conn.execute(
            """
UPDATE conversation_experience_event
SET outcome_signal=?, updated_at_ms=?
WHERE event_id=?
""",
            (signal, now, event_id),
        )
        lesson_updates = self._update_lessons_for_outcome_locked(
            event_id=event_id,
            understanding=understanding,
            signal=signal,
            now=now,
        )
        return {
            "ok": True,
            "signal": signal,
            "event_id": event_id,
            "request_id": request_id,
            "lesson_updates": lesson_updates,
        }

    def _update_lessons_for_outcome_locked(
        self,
        *,
        event_id: str,
        understanding: Mapping[str, Any],
        signal: str,
        now: int,
    ) -> list[dict[str, Any]]:
        lessons = [
            _mapping(item)
            for item in _list(_mapping(understanding).get("experience_lessons"))
            if isinstance(item, Mapping)
        ]
        lesson_ids = [_text(item.get("lesson_id"), limit=220) for item in lessons if _text(item.get("lesson_id"), limit=220)]
        if not lesson_ids and event_id:
            rows = self.conn.execute(
                """
SELECT lesson_id FROM conversation_experience_lesson
WHERE source_event_ids_json LIKE ? AND active=1
LIMIT 20
""",
                (f"%{event_id}%",),
            ).fetchall()
            for row in rows:
                try:
                    lesson_ids.append(str(row["lesson_id"]))
                except Exception:
                    lesson_ids.append(str(row[0]))
        seen: set[str] = set()
        updates: list[dict[str, Any]] = []
        for lesson_id in lesson_ids:
            if not lesson_id or lesson_id in seen:
                continue
            seen.add(lesson_id)
            row = self.conn.execute(
                """
SELECT success_count, failure_count, confidence
FROM conversation_experience_lesson
WHERE lesson_id=? AND active=1
LIMIT 1
""",
                (lesson_id,),
            ).fetchone()
            if not row:
                continue
            try:
                success_count = int(row["success_count"] or 0)
                failure_count = int(row["failure_count"] or 0)
                confidence = float(row["confidence"] or 0.0)
            except Exception:
                success_count = int(row[0] or 0)
                failure_count = int(row[1] or 0)
                confidence = float(row[2] or 0.0)
            if signal == "success":
                success_count += 1
                confidence = min(1.0, confidence + 0.04)
            elif signal == "failure":
                failure_count += 1
                confidence = max(0.05, confidence - 0.05)
            self.conn.execute(
                """
UPDATE conversation_experience_lesson
SET success_count=?, failure_count=?, confidence=?, updated_at_ms=?
WHERE lesson_id=?
""",
                (success_count, failure_count, round(confidence, 4), now, lesson_id),
            )
            updates.append(
                {
                    "lesson_id": lesson_id,
                    "success_count": success_count,
                    "failure_count": failure_count,
                    "confidence": round(confidence, 4),
                }
            )
        return updates

    def _text_overlap_score(self, query: str, haystack: str) -> float:
        q_tokens = self._tokens(query)
        if not q_tokens:
            return 0.0
        h = str(haystack or "")
        hits = sum(1 for token in q_tokens if token and token in h)
        return hits / max(1, len(q_tokens))

    def _tokens(self, text: str) -> list[str]:
        raw = str(text or "").strip()
        tokens = []
        for marker in (
            "第三本",
            "第3本",
            "推荐",
            "轻小说",
            "小说",
            "偏好",
            "喜欢",
            "不是这个",
            "理解错",
            "接不上",
            "意图",
            "聪明",
            "人类",
            "继续",
            "下一步",
        ):
            if marker in raw:
                tokens.append(marker)
        compact = raw.replace(" ", "")
        for size in (4, 3, 2):
            for i in range(0, max(0, len(compact) - size + 1)):
                chunk = compact[i : i + size]
                if chunk and chunk not in tokens:
                    tokens.append(chunk)
                if len(tokens) >= 18:
                    return tokens
        return tokens

    def _update_assistant_reply_locked(
        self,
        *,
        request_id: str,
        session_id: str,
        person_id: str,
        assistant_reply: str,
        outcome_signal: str,
        now: int,
    ) -> int:
        if request_id:
            cursor = self.conn.execute(
                """
UPDATE conversation_experience_event
SET assistant_reply=?, outcome_signal=?, updated_at_ms=?
WHERE request_id=? AND active=1
""",
                (assistant_reply, outcome_signal, now, request_id),
            )
            if int(cursor.rowcount or 0) > 0:
                return int(cursor.rowcount or 0)
        if session_id:
            row = self.conn.execute(
                """
SELECT event_id FROM conversation_experience_event
WHERE session_id=? AND person_id=? AND active=1
ORDER BY created_at_ms DESC
LIMIT 1
""",
                (session_id, person_id),
            ).fetchone()
            if not row:
                row = self.conn.execute(
                    """
SELECT event_id FROM conversation_experience_event
WHERE person_id=? AND active=1
ORDER BY created_at_ms DESC
LIMIT 1
""",
                    (person_id,),
                ).fetchone()
            if not row:
                row = self.conn.execute(
                    """
SELECT event_id FROM conversation_experience_event
WHERE active=1
ORDER BY created_at_ms DESC
LIMIT 1
"""
                ).fetchone()
            if not row:
                return 0
            try:
                event_id = str(row["event_id"])
            except Exception:
                event_id = str(row[0])
            cursor = self.conn.execute(
                """
UPDATE conversation_experience_event
SET assistant_reply=?, outcome_signal=?, updated_at_ms=?
WHERE event_id=?
""",
                (assistant_reply, outcome_signal, now, event_id),
            )
            return int(cursor.rowcount or 0)
        return 0

    def _record_inference_locked(
        self,
        *,
        event_id: str,
        request_id: str,
        session_id: str,
        person_id: str,
        user_text: str,
        selected_intent: str,
        response_policy: str,
        understanding: Mapping[str, Any],
        belief_before: Mapping[str, Any],
        belief_after: Mapping[str, Any],
        assistant_reply: str,
        outcome_signal: str,
        now: int,
    ) -> None:
        self.conn.execute(
            """
INSERT INTO conversation_experience_event (
    event_id, request_id, session_id, person_id, user_text, selected_intent,
    response_policy, understanding_json, belief_before_json, belief_after_json,
    assistant_reply, outcome_signal, created_at_ms, updated_at_ms, active
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
""",
            (
                event_id,
                request_id,
                session_id,
                person_id,
                user_text,
                selected_intent,
                response_policy,
                _json(understanding),
                _json(belief_before),
                _json(belief_after),
                assistant_reply,
                outcome_signal,
                now,
                now,
            ),
        )
        belief_after = dict(belief_after or {})
        belief_after.setdefault("scope", "session")
        belief_after.setdefault("ttl_ms", 30 * 60 * 1000)
        belief_after.setdefault("expires_at_ms", int(now) + int(belief_after.get("ttl_ms") or 30 * 60 * 1000))
        belief_after.setdefault("evidence_turn_ids", [])
        belief_after.setdefault("source_turn_id", _text(request_id, limit=160))
        if str(session_id or "").startswith("asr_req_"):
            belief_after["can_drive_reply"] = False
        else:
            belief_after.setdefault("can_drive_reply", True)
        current = self.conn.execute(
            """
SELECT version FROM conversation_belief_state
WHERE session_id=? AND person_id=?
LIMIT 1
""",
            (session_id, person_id),
        ).fetchone()
        version = 1
        if current:
            try:
                version = int(current["version"]) + 1
            except Exception:
                version = int(current[0]) + 1
        self.conn.execute(
            """
INSERT INTO conversation_belief_state(session_id, person_id, version, belief_json, updated_at_ms)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(session_id, person_id) DO UPDATE SET
    version=excluded.version,
    belief_json=excluded.belief_json,
    updated_at_ms=excluded.updated_at_ms
""",
            (session_id, person_id, version, _json(belief_after), now),
        )

    def record_correction_from_understanding(
        self,
        *,
        event_id: str | None,
        request_id: str | None,
        session_id: str | None,
        person_id: str | None,
        raw_text: str,
        understanding: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self.conn is None:
            return {"ok": False, "reason": "NO_CONN"}
        sid = _text(session_id, limit=160)
        pid = _text(person_id, limit=80) or "default"
        if not sid:
            return {"ok": False, "reason": "NO_SESSION"}
        understanding_dict = _mapping(understanding)
        refs = [_mapping(item) for item in _list(understanding_dict.get("resolved_references")) if isinstance(item, Mapping)]
        best_ref = refs[0] if refs else {}
        belief = _mapping(understanding_dict.get("belief_state"))
        lesson = self._repair_lesson(raw_text=raw_text, selected_intent=_text(belief.get("selected_intent")), reference=best_ref)
        correction_id = f"convcorr_{uuid.uuid4().hex}"
        now = _now_ms()
        lock = self.write_lock
        if lock is not None:
            with lock:
                self._record_correction_locked(
                    correction_id=correction_id,
                    event_id=_text(event_id, limit=120),
                    request_id=_text(request_id, limit=120),
                    session_id=sid,
                    person_id=pid,
                    raw_text=_text(raw_text, limit=1000),
                    wrong_inference=_mapping(belief.get("previous_wrong_inference")),
                    correct_reference=best_ref,
                    repair_lesson=lesson,
                    confidence=float(best_ref.get("confidence") or belief.get("confidence") or 0.75),
                    now=now,
                )
                self._upsert_lesson_locked(
                    person_id=pid,
                    domain=_text(understanding_dict.get("domain"), limit=120)
                    or _text(belief.get("active_topic"), limit=80)
                    or "conversation",
                    pattern=_text(belief.get("selected_intent"), limit=120) or "conversation_repair",
                    lesson=lesson,
                    source_event_id=_text(event_id, limit=120),
                    confidence=0.72,
                    now=now,
                )
                self._upsert_distilled_lesson_locked(
                    person_id=pid,
                    understanding=understanding_dict,
                    raw_text=raw_text,
                    source_event_id=_text(event_id, limit=120),
                    now=now,
                )
                self.conn.commit()
        else:
            self._record_correction_locked(
                correction_id=correction_id,
                event_id=_text(event_id, limit=120),
                request_id=_text(request_id, limit=120),
                session_id=sid,
                person_id=pid,
                raw_text=_text(raw_text, limit=1000),
                wrong_inference=_mapping(belief.get("previous_wrong_inference")),
                correct_reference=best_ref,
                repair_lesson=lesson,
                confidence=float(best_ref.get("confidence") or belief.get("confidence") or 0.75),
                now=now,
            )
            self._upsert_lesson_locked(
                person_id=pid,
                domain=_text(understanding_dict.get("domain"), limit=120)
                or _text(belief.get("active_topic"), limit=80)
                or "conversation",
                pattern=_text(belief.get("selected_intent"), limit=120) or "conversation_repair",
                lesson=lesson,
                source_event_id=_text(event_id, limit=120),
                confidence=0.72,
                now=now,
            )
            self._upsert_distilled_lesson_locked(
                person_id=pid,
                understanding=understanding_dict,
                raw_text=raw_text,
                source_event_id=_text(event_id, limit=120),
                now=now,
            )
            self.conn.commit()
        return {"ok": True, "correction_id": correction_id, "lesson": lesson}

    def _record_correction_locked(
        self,
        *,
        correction_id: str,
        event_id: str,
        request_id: str,
        session_id: str,
        person_id: str,
        raw_text: str,
        wrong_inference: Mapping[str, Any],
        correct_reference: Mapping[str, Any],
        repair_lesson: str,
        confidence: float,
        now: int,
    ) -> None:
        self.conn.execute(
            """
INSERT INTO conversation_correction_event (
    correction_id, event_id, request_id, session_id, person_id, raw_text,
    wrong_inference_json, correct_reference_json, repair_lesson, confidence,
    created_at_ms, active
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
""",
            (
                correction_id,
                event_id,
                request_id,
                session_id,
                person_id,
                raw_text,
                _json(wrong_inference),
                _json(correct_reference),
                repair_lesson,
                round(float(confidence), 4),
                now,
            ),
        )

    def _upsert_lesson_locked(
        self,
        *,
        person_id: str,
        domain: str,
        pattern: str,
        lesson: str,
        source_event_id: str,
        confidence: float,
        now: int,
    ) -> None:
        lesson_id = f"lesson::{person_id}::{domain}::{pattern}".replace(" ", "_")[:180]
        current = self.conn.execute(
            """
SELECT source_event_ids_json, failure_count, confidence
FROM conversation_experience_lesson
WHERE lesson_id=?
LIMIT 1
""",
            (lesson_id,),
        ).fetchone()
        source_ids: list[str] = []
        failure_count = 0
        old_conf = 0.0
        if current:
            try:
                source_ids = [str(item) for item in _loads(current["source_event_ids_json"], [])]
                failure_count = int(current["failure_count"])
                old_conf = float(current["confidence"])
            except Exception:
                source_ids = [str(item) for item in _loads(current[0], [])]
                failure_count = int(current[1])
                old_conf = float(current[2])
        if source_event_id and source_event_id not in source_ids:
            source_ids.append(source_event_id)
        source_ids = source_ids[-20:]
        self.conn.execute(
            """
INSERT INTO conversation_experience_lesson (
    lesson_id, person_id, domain, pattern, lesson, source_event_ids_json,
    success_count, failure_count, confidence, updated_at_ms, active
) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 1)
ON CONFLICT(lesson_id) DO UPDATE SET
    lesson=excluded.lesson,
    source_event_ids_json=excluded.source_event_ids_json,
    failure_count=excluded.failure_count,
    confidence=excluded.confidence,
    updated_at_ms=excluded.updated_at_ms,
    active=1
""",
            (
                lesson_id,
                person_id,
                domain or "general",
                pattern or "conversation_repair",
                lesson,
                _json(source_ids),
                failure_count + 1,
                max(float(confidence), old_conf),
                now,
            ),
        )

    def _upsert_distilled_lesson_locked(
        self,
        *,
        person_id: str,
        understanding: Mapping[str, Any],
        raw_text: str,
        source_event_id: str,
        now: int,
    ) -> None:
        lesson_spec = self._distill_lesson_spec(understanding=understanding, raw_text=raw_text)
        if not lesson_spec:
            return
        self._upsert_lesson_locked(
            person_id=person_id,
            domain=_text(lesson_spec.get("domain"), limit=120) or "conversation",
            pattern=_text(lesson_spec.get("pattern"), limit=120) or "distilled_repair",
            lesson=_text(lesson_spec.get("lesson"), limit=600),
            source_event_id=source_event_id,
            confidence=float(lesson_spec.get("confidence") or 0.78),
            now=now,
        )

    def _distill_lesson_spec(self, *, understanding: Mapping[str, Any], raw_text: str) -> dict[str, Any]:
        data = _mapping(understanding)
        belief = _mapping(data.get("belief_state"))
        refs = [_mapping(item) for item in _list(data.get("resolved_references")) if isinstance(item, Mapping)]
        domain = _text(data.get("domain"), limit=120) or "conversation"
        selected_intent = _text(data.get("selected_intent") or belief.get("selected_intent"), limit=120)
        raw = str(raw_text or "")
        ref_kinds = {_text(item.get("target_kind"), limit=80) for item in refs}
        has_rank_reference = any("第" in _text(item.get("reference_text"), limit=80) for item in refs) or "第三本" in raw or "第3本" in raw
        has_recommendation_item = "recommendation_item" in ref_kinds or domain == "lightnovel_recommendation"
        is_repair = selected_intent in {"conversation_repair", "repair_misunderstanding"} or bool(belief.get("repair_requested"))

        if has_recommendation_item and (is_repair or has_rank_reference):
            return {
                "domain": "lightnovel_recommendation",
                "pattern": "distilled_recommendation_reference_repair",
                "lesson": (
                    "当用户用“第几本/这本/那本”追问或纠正轻小说推荐时，先绑定最近推荐集合里的具体作品；"
                    "如果上一轮答歪，先承认错位并重绑作品，再给判断。不要泛化成长期偏好或其他媒介。"
                ),
                "confidence": 0.82,
            }
        if is_repair:
            return {
                "domain": domain or "conversation",
                "pattern": "distilled_conversation_repair",
                "lesson": (
                    "用户指出“不对/不是这个/接不上”时，优先把它当作上一轮理解错位；"
                    "先找被纠正的对象和新的绑定目标，再继续回答，不要用空泛道歉糊过去。"
                ),
                "confidence": 0.78,
            }
        return {}

    def _response_policy_from_understanding(self, understanding: Mapping[str, Any]) -> str:
        hypotheses = [_mapping(item) for item in _list(understanding.get("intent_hypotheses")) if isinstance(item, Mapping)]
        if hypotheses:
            return _text(hypotheses[0].get("response_policy"), limit=280)
        belief = _mapping(understanding.get("belief_state"))
        return _text(belief.get("user_goal"), limit=280)

    def _looks_like_correction(self, understanding: Mapping[str, Any]) -> bool:
        belief = _mapping(understanding.get("belief_state"))
        selected = _text(understanding.get("selected_intent") or belief.get("selected_intent"), limit=120)
        return bool(belief.get("repair_requested")) or selected in {
            "repair_misunderstanding",
            "conversation_repair",
            "criticize_reasoning_quality",
            "meta_quality_feedback",
            "challenge_value_or_direction",
        }

    def _repair_lesson(self, *, raw_text: str, selected_intent: str, reference: Mapping[str, Any]) -> str:
        target = _text(reference.get("target_label"), limit=140)
        if target:
            return f"用户纠正或质疑时，先重新绑定到“{target}”，再回答真实问题；不要沿用上一轮错误理解。"
        if selected_intent in {"criticize_reasoning_quality", "meta_quality_feedback"}:
            return "用户批评理解方式不够像人时，不能只加分类；要说明推理缺口，并升级引用、候选意图和信念更新。"
        return "用户发出纠错或质疑时，先识别上一轮错位，再基于当前上下文重建理解。"


def get_conversation_experience_store(*, ledger: Any | None = None) -> ConversationExperienceStore:
    return ConversationExperienceStore(ledger=ledger)


__all__ = ["ConversationExperienceStore", "get_conversation_experience_store"]
