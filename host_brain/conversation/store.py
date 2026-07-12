from __future__ import annotations

import json
import hashlib
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persistence.context_repository import ContextRepository

from .schema import Turn, TurnRole


class TurnStore:
    """Compatibility API backed by the canonical ContextRepository writer."""

    def __init__(self, ledger: SQLiteLedger) -> None:
        self._ledger = ledger
        self._repository = ContextRepository.from_ledger(ledger)

    def append(self, turn: Turn) -> None:
        self._repository.append_turn(self._turn_record(turn))

    def append_generated(
        self,
        *,
        session_id: str,
        person_id: str,
        request_id: str,
        role: TurnRole,
        content: str,
        ts_ms: int,
        lang: str = "zh",
        token_count: int | None = None,
        corrupted: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> Turn:
        row = self._repository.append_generated_turn(
            {
                "turn_id": self._generated_turn_id(
                    session_id=session_id,
                    request_id=request_id,
                    role=str(role),
                ),
                "session_id": str(session_id or "").strip(),
                "person_id": str(person_id or "").strip(),
                "request_id": str(request_id or "").strip(),
                "role": role,
                "content": str(content or ""),
                "ts_ms": int(ts_ms),
                "lang": str(lang or "zh").strip() or "zh",
                "token_count": token_count,
                "corrupted": bool(corrupted),
                "metadata": dict(metadata or {}) or None,
            }
        )
        return self._row_to_turn(row)

    @staticmethod
    def _generated_turn_id(*, session_id: str, request_id: str, role: str) -> str:
        material = "\x1f".join(
            (
                str(session_id or "").strip(),
                str(request_id or "").strip(),
                str(role or "").strip(),
            )
        )
        return "turn::sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()

    def query_recent(
        self,
        *,
        session_id: str,
        limit: int,
        exclude_request_id: str | None = None,
        before_ts_ms: int | None = None,
        before_turn_index: int | None = None,
    ) -> list[Turn]:
        rows = self._repository.query_recent_turns(
            session_id=session_id,
            limit=limit,
            exclude_request_id=exclude_request_id,
            before_ts_ms=before_ts_ms,
            before_turn_index=before_turn_index,
        )
        return [self._row_to_turn(row) for row in rows]

    def find_by_request(
        self,
        *,
        session_id: str,
        request_id: str,
        role: TurnRole | None = None,
    ) -> Turn | None:
        row = self._repository.find_turn(
            session_id=session_id,
            request_id=request_id,
            role=str(role) if role else None,
        )
        return self._row_to_turn(row) if row is not None else None

    def query_before_turn_index(
        self,
        *,
        session_id: str,
        before_turn_index: int,
        limit: int,
        exclude_request_id: str | None = None,
    ) -> list[Turn]:
        rows = self._repository.query_turns_before_index(
            session_id=session_id,
            before_turn_index=before_turn_index,
            limit=limit,
            exclude_request_id=exclude_request_id,
        )
        return [self._row_to_turn(row) for row in rows]

    def count_before_turn_index(
        self,
        *,
        session_id: str,
        before_turn_index: int,
        exclude_request_id: str | None = None,
    ) -> int:
        return self._repository.count_turns_before_index(
            session_id=session_id,
            before_turn_index=before_turn_index,
            exclude_request_id=exclude_request_id,
        )

    def next_turn_index(self, *, session_id: str) -> int:
        return self._repository.next_turn_index(session_id=session_id)

    def query_recent_assistant(
        self,
        *,
        limit: int,
        lang: str | None = None,
    ) -> list[Turn]:
        rows = self._repository.query_recent_assistant_turns(limit=limit, lang=lang)
        return [self._row_to_turn(row) for row in rows]

    def delete_by_session(self, *, session_id: str) -> int:
        return self._repository.delete_conversation_session(session_id=session_id)

    @staticmethod
    def _turn_record(turn: Turn) -> dict[str, Any]:
        return {
            "turn_id": turn.turn_id,
            "session_id": turn.session_id,
            "person_id": turn.person_id,
            "request_id": turn.request_id,
            "role": turn.role,
            "content": turn.content,
            "ts_ms": int(turn.ts_ms),
            "turn_index": int(turn.turn_index),
            "lang": str(turn.lang or "zh"),
            "token_count": turn.token_count,
            "corrupted": bool(turn.corrupted),
            "metadata": turn.metadata,
        }

    @staticmethod
    def _row_to_turn(row: Any) -> Turn:
        metadata_raw = row.get("metadata") if isinstance(row, dict) else row["metadata"]
        metadata: dict[str, Any] | None = None
        if metadata_raw:
            try:
                parsed = json.loads(str(metadata_raw))
                if isinstance(parsed, dict):
                    metadata = parsed
            except (TypeError, json.JSONDecodeError):
                metadata = None
        return Turn(
            turn_id=str(row["turn_id"]),
            session_id=str(row["session_id"]),
            person_id=str(row["person_id"]),
            request_id=str(row["request_id"]),
            role=str(row["role"]),
            content=str(row["content"] or ""),
            ts_ms=int(row["ts_ms"] or 0),
            turn_index=int(row["turn_index"] or 0),
            lang=str(row["lang"] or "zh"),
            token_count=int(row["token_count"]) if row["token_count"] is not None else None,
            corrupted=bool(row["corrupted"]),
            metadata=metadata,
        )
