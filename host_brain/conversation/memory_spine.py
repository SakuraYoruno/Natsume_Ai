from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from host_brain.persistence.context_repository import ContextRepository
from host_brain.persistence.life_command_repository import LifeCommandRepository


def _text(value: Any, *, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _conversation_turn_stable_identity(
    *,
    request_id: str,
    session_id: str,
    turn_id: str,
    role: str,
    content: str,
    ts_ms: int,
    turn_index: int | None = None,
) -> dict[str, Any]:
    clean_content = _text(content, limit=1000)
    return {
        "request_id": _text(request_id, limit=1000),
        "session_id": _text(session_id, limit=180),
        "turn_id": _text(turn_id, limit=240),
        "turn_index": turn_index,
        "role": _text(role, limit=40).lower(),
        "content_sha256": hashlib.sha256(clean_content.encode("utf-8")).hexdigest(),
        "ts_ms": max(0, int(ts_ms or 0)),
    }


def _conversation_turn_command_id(
    *,
    request_id: str,
    session_id: str,
    turn_id: str,
    role: str,
    content: str,
    ts_ms: int,
) -> str:
    identity = _conversation_turn_stable_identity(
        request_id=request_id,
        session_id=session_id,
        turn_id=turn_id,
        role=role,
        content=content,
        ts_ms=ts_ms,
    )
    clean_request_id = str(identity["request_id"])
    clean_role = str(identity["role"])
    if clean_request_id:
        candidate = f"life::conversation_turn::{clean_request_id}::{clean_role}"
        if len(candidate) <= 240:
            return candidate
        request_digest = hashlib.sha256(
            json.dumps(
                {"request_id": clean_request_id, "role": clean_role},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return f"life::conversation_turn::request::{request_digest}"
    fallback_material = {
        key: identity[key]
        for key in ("session_id", "turn_id", "role", "content_sha256", "ts_ms")
    }
    fallback_digest = hashlib.sha256(
        json.dumps(
            fallback_material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"life::conversation_turn::derived::{fallback_digest}"


def _already_in_life_stream(
    *,
    repository: LifeCommandRepository,
    command_id: str,
) -> bool:
    return repository.get_receipt(command_id) is not None


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
    stable_identity = _conversation_turn_stable_identity(
        request_id=request_id,
        session_id=clean_session,
        turn_id=turn_id,
        role=clean_role,
        content=clean_content,
        ts_ms=ts_ms,
        turn_index=turn_index,
    )
    command_id = _conversation_turn_command_id(
        request_id=request_id,
        session_id=clean_session,
        turn_id=turn_id,
        role=clean_role,
        content=clean_content,
        ts_ms=ts_ms,
    )
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
        from host_brain.digital_life.experience_ingress import (  # noqa: PLC0415
            record_life_experience,
        )

        ledger = get_ledger()
        command_repository = LifeCommandRepository.from_ledger(ledger)
        if _already_in_life_stream(
            repository=command_repository,
            command_id=command_id,
        ):
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
            "command_id": command_id,
            "idempotency_key": command_id,
            "canonical_life_command_id": command_id,
            "canonical_life_command_type": "conversation_turn_recorded",
            "canonical_idempotency_strategy": (
                "request_id_role"
                if str(stable_identity.get("request_id") or "")
                else "session_turn_content_ts_digest"
            ),
            "canonical_turn_identity": stable_identity,
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
    """Find or bridge conversation turns using canonical command receipts as authority."""

    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415

        ledger = get_ledger()
        turn_repository = ContextRepository.from_ledger(ledger)
        command_repository = LifeCommandRepository.from_ledger(ledger)
        clean_person = _text(person_id or "", limit=120)
        clean_session = _text(session_id, limit=180)
        capped = max(1, min(int(limit or 500), 5000))
        rows = turn_repository.list_memory_spine_turns(
            person_id=clean_person or None,
            session_id=clean_session,
            limit=capped,
            include_asr_sessions=bool(include_asr_sessions),
        )
        scanned = 0
        eligible = 0
        written = 0
        skipped_existing = 0
        degraded = 0
        samples: list[dict[str, Any]] = []
        for row in rows:
            scanned += 1
            role = str(row.get("role") or "").strip().lower()
            request_id = str(row.get("request_id") or "").strip()
            command_id = _conversation_turn_command_id(
                request_id=request_id,
                session_id=str(row.get("session_id") or ""),
                turn_id=str(row.get("turn_id") or ""),
                role=role,
                content=str(row.get("content") or ""),
                ts_ms=int(row.get("ts_ms") or 0),
            )
            if _already_in_life_stream(
                repository=command_repository,
                command_id=command_id,
            ):
                skipped_existing += 1
                continue
            eligible += 1
            if len(samples) < 8:
                samples.append(
                    {
                        "request_id": request_id,
                        "session_id": str(row.get("session_id") or ""),
                        "role": role,
                        "content": _text(row.get("content"), limit=80),
                    }
                )
            if dry_run:
                continue
            metadata = {}
            try:
                loaded = json.loads(str(row.get("metadata") or "{}"))
                metadata = dict(loaded or {}) if isinstance(loaded, Mapping) else {}
            except Exception:
                metadata = {}
            result = record_conversation_turn_memory_spine(
                request_id=request_id,
                session_id=str(row.get("session_id") or ""),
                person_id=str(row.get("person_id") or clean_person or "default"),
                role=role,
                content=str(row.get("content") or ""),
                ts_ms=int(row.get("ts_ms") or 0),
                lang=str(row.get("lang") or "zh"),
                metadata=metadata,
                turn_id=str(row.get("turn_id") or ""),
                turn_index=int(row.get("turn_index") or 0),
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

        ledger = get_ledger()
        turn_repository = ContextRepository.from_ledger(ledger)
        command_repository = LifeCommandRepository.from_ledger(ledger)
        clean_person = _text(person_id or "", limit=120)
        clean_session = _text(session_id, limit=180)
        capped = max(1, min(int(limit or 2000), 10000))
        rows = turn_repository.list_memory_spine_turns(
            person_id=clean_person or None,
            session_id=clean_session,
            limit=capped,
            include_asr_sessions=bool(include_asr_sessions),
        )
        return sum(
            1
            for row in rows
            if not _already_in_life_stream(
                repository=command_repository,
                command_id=_conversation_turn_command_id(
                    request_id=str(row.get("request_id") or ""),
                    session_id=str(row.get("session_id") or ""),
                    turn_id=str(row.get("turn_id") or ""),
                    role=str(row.get("role") or ""),
                    content=str(row.get("content") or ""),
                    ts_ms=int(row.get("ts_ms") or 0),
                ),
            )
        )
    except Exception:
        return 0


__all__ = [
    "backfill_conversation_turn_memory_spine",
    "count_conversation_turn_memory_spine_gaps",
    "record_conversation_turn_memory_spine",
]
