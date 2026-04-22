from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persona.reply_tactics import canonical_tactic_signature, tactic_key


@dataclass(frozen=True)
class TacticLedgerEntry:
    session_id: str
    request_id: str
    topic_key: str
    interaction_move: str = ""
    tactic_family: str = ""
    tactic_variant: str = ""
    trajectory_stage: str = ""
    ts_ms: int = 0

    @property
    def tactic_key(self) -> str:
        return tactic_key(self.tactic_family, self.tactic_variant)

    @property
    def tactic_signature(self) -> str:
        return canonical_tactic_signature(
            topic=str(self.topic_key or "").strip(),
            family=str(self.tactic_family or "").strip(),
            variant=str(self.tactic_variant or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "request_id": self.request_id,
            "topic_key": self.topic_key,
            "interaction_move": self.interaction_move,
            "tactic_family": self.tactic_family,
            "tactic_variant": self.tactic_variant,
            "trajectory_stage": self.trajectory_stage,
            "ts_ms": int(self.ts_ms or 0),
            "tactic_key": self.tactic_key,
            "tactic_signature": self.tactic_signature,
        }


@dataclass(frozen=True)
class TacticLedgerBlock:
    entries: tuple[TacticLedgerEntry, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "entries": [entry.as_dict() for entry in self.entries],
            "recent_topics": list(self.recent_topics()),
        }

    def recent_topics(self) -> tuple[str, ...]:
        seen: list[str] = []
        for entry in self.entries:
            if entry.topic_key and entry.topic_key not in seen:
                seen.append(entry.topic_key)
        return tuple(seen)

    def recent_for_topic(self, topic_key: str, *, limit: int = 3) -> tuple[TacticLedgerEntry, ...]:
        normalized = str(topic_key or "").strip()
        if not normalized:
            return ()
        matches = [entry for entry in self.entries if entry.topic_key == normalized]
        return tuple(matches[: max(1, int(limit or 1))])

    def recent_tactic_families(self, topic_key: str, *, limit: int = 3) -> tuple[str, ...]:
        seen: list[str] = []
        for entry in self.recent_for_topic(topic_key, limit=max(limit * 2, 3)):
            family = str(entry.tactic_family or "").strip()
            if not family or family.startswith("fallback_") or family.startswith("invalid_"):
                continue
            if family not in seen:
                seen.append(family)
            if len(seen) >= max(1, int(limit or 1)):
                break
        return tuple(seen)

    def recent_tactic_keys(self, topic_key: str, *, limit: int = 3) -> tuple[str, ...]:
        seen: list[str] = []
        for entry in self.recent_for_topic(topic_key, limit=max(limit * 3, 6)):
            family = str(entry.tactic_family or "").strip()
            key = str(entry.tactic_key or "").strip()
            if not family or family.startswith("fallback_") or family.startswith("invalid_") or not key:
                continue
            if key not in seen:
                seen.append(key)
            if len(seen) >= max(1, int(limit or 1)):
                break
        return tuple(seen)

    def recent_tactic_signatures(self, topic_key: str, *, limit: int = 3) -> tuple[str, ...]:
        seen: list[str] = []
        for entry in self.recent_for_topic(topic_key, limit=max(limit * 3, 6)):
            family = str(entry.tactic_family or "").strip()
            signature = str(entry.tactic_signature or "").strip()
            if not family or family.startswith("fallback_") or family.startswith("invalid_") or not signature:
                continue
            if signature not in seen:
                seen.append(signature)
            if len(seen) >= max(1, int(limit or 1)):
                break
        return tuple(seen)


class SessionTacticLedgerStore:
    """Persist recent response trajectories so planners can avoid repeating tactics."""

    def __init__(self, ledger: SQLiteLedger) -> None:
        self._ledger = ledger
        self._conn = ledger._conn
        self._write_lock = ledger._write_lock
        self._ledger._ensure_schema()

    def record_reply_plan(
        self,
        *,
        session_id: str,
        request_id: str,
        topic_key: str,
        interaction_move: str,
        tactic_family: str,
        tactic_variant: str,
        trajectory_stage: str,
        ts_ms: int | None = None,
    ) -> None:
        normalized_session = str(session_id or "").strip()
        normalized_request = str(request_id or "").strip()
        normalized_topic = str(topic_key or "").strip()
        normalized_family = str(tactic_family or "").strip()
        if not normalized_session or not normalized_request or not normalized_topic or not normalized_family:
            return
        created_ts_ms = int(ts_ms or int(time.time() * 1000))
        with self._write_lock:
            self._conn.execute(
                """
                INSERT INTO session_tactic_ledger (
                    session_id,
                    request_id,
                    topic_key,
                    interaction_move,
                    tactic_family,
                    tactic_variant,
                    trajectory_stage,
                    ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_session,
                    normalized_request,
                    normalized_topic,
                    str(interaction_move or "").strip(),
                    normalized_family,
                    str(tactic_variant or "").strip(),
                    str(trajectory_stage or "").strip(),
                    created_ts_ms,
                ),
            )
            self._conn.commit()

    def fetch_block(self, *, session_id: str, limit: int = 24) -> TacticLedgerBlock:
        normalized_session = str(session_id or "").strip()
        if not normalized_session:
            return TacticLedgerBlock()
        rows = self._conn.execute(
            """
            SELECT
                session_id,
                request_id,
                topic_key,
                interaction_move,
                tactic_family,
                tactic_variant,
                trajectory_stage,
                ts_ms
            FROM session_tactic_ledger
            WHERE session_id = ?
            ORDER BY ts_ms DESC, id DESC
            LIMIT ?
            """,
            (normalized_session, max(1, int(limit or 24))),
        ).fetchall()
        entries = tuple(
            TacticLedgerEntry(
                session_id=str(row["session_id"] or "").strip(),
                request_id=str(row["request_id"] or "").strip(),
                topic_key=str(row["topic_key"] or "").strip(),
                interaction_move=str(row["interaction_move"] or "").strip(),
                tactic_family=str(row["tactic_family"] or "").strip(),
                tactic_variant=str(row["tactic_variant"] or "").strip(),
                trajectory_stage=str(row["trajectory_stage"] or "").strip(),
                ts_ms=int(row["ts_ms"] or 0),
            )
            for row in rows
            if str(row["topic_key"] or "").strip()
        )
        return TacticLedgerBlock(entries=entries)

    def delete_by_session(self, *, session_id: str) -> int:
        normalized_session = str(session_id or "").strip()
        if not normalized_session:
            return 0
        with self._write_lock:
            cursor = self._conn.execute(
                "DELETE FROM session_tactic_ledger WHERE session_id = ?",
                (normalized_session,),
            )
            self._conn.commit()
            return int(cursor.rowcount or 0)


def coerce_tactic_ledger_block(raw: Any) -> TacticLedgerBlock:
    payload = dict(raw or {}) if isinstance(raw, dict) else {}
    entries: list[TacticLedgerEntry] = []
    for item in list(payload.get("entries") or []):
        if not isinstance(item, dict):
            continue
        topic_key = str(item.get("topic_key") or "").strip()
        tactic_family = str(item.get("tactic_family") or "").strip()
        if not topic_key or not tactic_family:
            continue
        entries.append(
            TacticLedgerEntry(
                session_id=str(item.get("session_id") or "").strip(),
                request_id=str(item.get("request_id") or "").strip(),
                topic_key=topic_key,
                interaction_move=str(item.get("interaction_move") or "").strip(),
                tactic_family=tactic_family,
                tactic_variant=str(item.get("tactic_variant") or "").strip(),
                trajectory_stage=str(item.get("trajectory_stage") or "").strip(),
                ts_ms=int(item.get("ts_ms") or 0),
            )
        )
    return TacticLedgerBlock(entries=tuple(entries))


__all__ = [
    "SessionTacticLedgerStore",
    "TacticLedgerBlock",
    "TacticLedgerEntry",
    "coerce_tactic_ledger_block",
]
