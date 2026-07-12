from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import re
import time
import uuid
from typing import Any

from host_brain.persistence.context_memory_repository import MemoryEventRepository
from host_brain.persistence.sqlite_backup import SQLiteBackupManager


VISIBILITIES = {"visible", "private", "system", "projection"}
VISIBLE_EVENT_KINDS = {
    "user_message",
    "assistant_reply",
    "life_proactive_sent",
    "life_proactive_user_response",
}
PRIVATE_EVENT_KINDS = {"inner_stream_tick", "dream_generated", "diary_generated"}
USER_EVENT_KINDS = {"user_message", "life_proactive_user_response"}
ASSISTANT_EVENT_KINDS = {"assistant_reply", "life_proactive_sent"}


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
    except (TypeError, json.JSONDecodeError):
        return default


def text(value: Any, *, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[: max(1, int(limit))]


def mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def clamp(value: Any, default: float = 0.0) -> float:
    try:
        raw = float(value)
    except (TypeError, ValueError):
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


def _is_asr_media_cta_noise(
    content: str,
    metadata: Mapping[str, Any] | None = None,
) -> bool:
    meta = mapping(metadata)
    input_source = str(meta.get("input_source") or "").strip().lower()
    if input_source not in {
        "asr",
        "audio",
        "voice",
        "speech",
        "mic",
        "microphone",
        "voice_chat",
    }:
        return False
    text_value = text(content, limit=500)
    if not text_value:
        return False
    normalized = re.sub(r"\s+", "", text_value)
    cta_terms = ("点赞", "订阅", "转发", "打赏", "栏目", "收看", "关注", "投币", "一键三连")
    if sum(1 for term in cta_terms if term in normalized) >= 2:
        return True
    return bool(re.search(r"(请不吝|欢迎).{0,8}(点赞|订阅|转发|打赏|关注)", normalized))


def _stable_turn_id(*, session_id: str, request_id: str, role: str) -> str:
    material = "\x1f".join((session_id.strip(), request_id.strip(), role.strip()))
    return "turn::sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


class MemoryEventStore:
    """Compatibility facade over the canonical MemoryEventRepository writer."""

    def __init__(self, ledger: Any) -> None:
        self.ledger = ledger
        self.repository = MemoryEventRepository.from_ledger(ledger)

    def ensure_schema(self) -> None:
        self.repository.assert_schema_ready(
            require_catalog=self.repository.db_path != ":memory:"
        )

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
        row = self.repository.append_event(
            {
                "event_id": event_id(),
                "person_id": text(person_id or "default", limit=120) or "default",
                "session_id": text(session_id, limit=180),
                "conversation_id": text(conversation_id or session_id, limit=180),
                "source_kind": text(source_kind, limit=120) or "unknown",
                "event_kind": kind,
                "visibility": resolved_visibility,
                "role": resolved_role,
                "content": resolved_content,
                "content_summary": text(content_summary or resolved_content, limit=1000),
                "causality": dict(causality or {}),
                "refs": dict(refs or {}),
                "salience": clamp(salience),
                "valence": max(-1.0, min(1.0, float(valence or 0.0))),
                "uncertainty": clamp(uncertainty),
                "confidence": clamp(confidence, default=1.0),
                "projection_status": dict(projection_status or {}),
                "created_at_ms": ts,
                "active": bool(active),
            }
        )
        return {
            "event_id": row["event_id"],
            "person_id": row["person_id"],
            "session_id": row["session_id"],
            "conversation_id": row["conversation_id"],
            "source_kind": row["source_kind"],
            "event_kind": row["event_kind"],
            "visibility": row["visibility"],
            "role": row["role"],
            "created_at_ms": int(row["created_at_ms"]),
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
        meta = mapping(metadata)
        if clean_role == "user" and _is_asr_media_cta_noise(clean_content, meta):
            return {"status": "SKIPPED", "reason_code": "ASR_MEDIA_CTA_NOISE"}
        existing = self._find_visible_event_by_request(
            session_id=sid,
            request_id=request_id,
            role=clean_role,
        )
        if existing:
            return {**existing, "status": "SKIPPED", "reason_code": "DUPLICATE_VISIBLE_TURN"}
        source = str(meta.get("source_kind") or "").strip()
        event_kind = (
            "life_proactive_sent"
            if clean_role == "assistant" and source == "life_proactive"
            else "user_message"
            if clean_role == "user"
            else "assistant_reply"
        )
        refs = {
            "request_id": text(request_id, limit=180),
            "lang": text(lang, limit=30) or "zh",
            "metadata": meta,
        }
        refs.update(mapping(meta.get("refs")))
        for key in (
            "proactive_outbox_id",
            "proactive_episode_id",
            "work_id",
            "recommendation_id",
            "delivery_id",
        ):
            if meta.get(key):
                refs[key] = meta[key]
        timestamp = int(ts_ms or now_ms())
        memory_event_id = event_id()
        admitted = self.repository.append_visible_turn(
            event={
                "event_id": memory_event_id,
                "person_id": person_id or "default",
                "session_id": sid,
                "conversation_id": sid,
                "source_kind": "conversation_turn",
                "event_kind": event_kind,
                "role": clean_role,
                "content": clean_content,
                "content_summary": clean_content,
                "refs": refs,
                "salience": 0.62
                if clean_role == "user"
                else 0.56
                if event_kind == "life_proactive_sent"
                else 0.36,
                "valence": self._valence_for_text(clean_content, role=clean_role),
                "uncertainty": 0.08,
                "confidence": 1.0,
                "created_at_ms": timestamp,
            },
            turn={
                "turn_id": _stable_turn_id(
                    session_id=sid,
                    request_id=str(request_id or "").strip(),
                    role=clean_role,
                ),
                "session_id": sid,
                "person_id": person_id or "default",
                "request_id": str(request_id or "").strip(),
                "role": clean_role,
                "content": clean_content,
                "ts_ms": timestamp,
                "lang": lang,
                "token_count": token_count,
                "corrupted": corrupted,
                "metadata": {**meta, "memory_event_id": memory_event_id},
            },
        )
        if admitted["status"] == "DUPLICATE":
            return {
                **admitted,
                "status": "SKIPPED",
                "reason_code": "DUPLICATE_VISIBLE_TURN",
            }
        memory_event_id = str(admitted["memory_event_id"])
        turn = dict(admitted["turn"])
        life = self.project_life_experience_from_event(memory_event_id, consume=True)
        projection_status = {
            "conversation_turn": {"status": "OK", "turn_id": turn.get("turn_id")},
            "life_experience": {
                "status": life.get("status", "OK"),
                "event_id": life.get("event_id"),
            },
        }
        self.merge_event_refs(
            memory_event_id,
            {
                "turn_id": turn.get("turn_id"),
                "life_experience_event_id": life.get("event_id"),
            },
        )
        self.update_projection_status(memory_event_id, projection_status)
        return {
            "status": "OK",
            "memory_event_id": memory_event_id,
            "turn": turn,
            "life_experience_event_id": life.get("event_id"),
            "projection_status": projection_status,
        }

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
        return self.repository.project_turn(
            memory_event_id=memory_event_id,
            turn={
                "turn_id": _stable_turn_id(
                    session_id=session_id,
                    request_id=request_id,
                    role=role,
                ),
                "request_id": str(request_id or "").strip(),
                "session_id": session_id,
                "person_id": person_id or "default",
                "role": role,
                "content": content,
                "ts_ms": int(ts_ms),
                "lang": str(lang or "zh") or "zh",
                "token_count": token_count,
                "corrupted": bool(corrupted),
                "metadata": dict(metadata or {}),
            },
        )

    def append_visible_assistant_message(
        self,
        *,
        request_id: str,
        session_id: str,
        person_id: str | None,
        content: str,
        ts_ms: int,
        source_kind: str,
        metadata: Mapping[str, Any] | None = None,
        refs: Mapping[str, Any] | None = None,
        lang: str = "zh",
        token_count: int | None = None,
    ) -> dict[str, Any]:
        clean_content = text(content, limit=4000)
        if not clean_content:
            return {"status": "SKIPPED", "reason_code": "EMPTY_VISIBLE_ASSISTANT_MESSAGE"}
        meta = {
            **mapping(metadata),
            "source_kind": text(source_kind, limit=120) or "assistant_visible_message",
        }
        if refs:
            meta["refs"] = dict(refs)
        return self.append_visible_turn(
            request_id=request_id,
            session_id=session_id,
            person_id=person_id or "default",
            role="assistant",
            content=clean_content,
            ts_ms=int(ts_ms or now_ms()),
            lang=lang,
            metadata=meta,
            token_count=token_count if token_count is not None else max(1, len(clean_content) // 2),
        )

    def project_life_experience_from_event(
        self,
        memory_event_id: str,
        *,
        consume: bool = True,
    ) -> dict[str, Any]:
        row = self.repository.get_event(event_id=memory_event_id, active_only=True)
        if row is None:
            return {"status": "NOT_FOUND"}
        refs = mapping(loads(row["refs_json"], {}))
        canonical_command_id = f"life::memory_event::{memory_event_id}"
        if len(canonical_command_id) > 240:
            canonical_command_id = (
                "life::memory_event::"
                + hashlib.sha256(memory_event_id.encode("utf-8")).hexdigest()
            )
        try:
            from host_brain.persistence.life_command_repository import (  # noqa: PLC0415
                LifeCommandRepository,
            )
            from host_brain.digital_life.experience_ingress import (  # noqa: PLC0415
                record_life_experience,
            )

            receipt = LifeCommandRepository.from_ledger(self.ledger).get_receipt(
                canonical_command_id
            )
            if (
                receipt is not None
                and str(receipt.status.value) == "APPLIED"
                and receipt.event_id
            ):
                return {
                    "status": "EXISTS",
                    "event_id": receipt.event_id,
                    "state_event_id": receipt.event_id,
                    "command_id": receipt.command_id,
                    "replayed": True,
                }
            result = record_life_experience(
                ledger=self.ledger,
                person_id=row["person_id"],
                session_id=row["session_id"] or "",
                source_kind=f"memory_event.{row['event_kind']}",
                event_kind=row["event_kind"],
                content_summary=row["content_summary"] or row["content"],
                evidence={
                    **refs,
                    "command_id": canonical_command_id,
                    "canonical_life_command_id": canonical_command_id,
                    "memory_event_id": memory_event_id,
                    "memory_event_kind": row["event_kind"],
                    "memory_event_visibility": row["visibility"],
                    "memory_event_created_at_ms": int(row["created_at_ms"] or 0),
                },
                salience=float(row["salience"] or 0.0),
                valence=float(row["valence"] or 0.0),
                uncertainty=float(row["uncertainty"] or 0.0),
                consume=consume,
            )
            return {
                "status": "OK",
                "event_id": result.get("event_id"),
                "state_event_id": result.get("state_event_id"),
                "command_id": mapping(result.get("experience")).get("command_id"),
                "replayed": bool(mapping(result.get("experience")).get("replayed")),
                "organ_processing": result.get("organ_processing", {}),
            }
        except Exception as exc:
            failure = {
                "life_experience": {
                    "status": "FAILED",
                    "reason_code": type(exc).__name__,
                    "detail": str(exc)[:160],
                }
            }
            self.update_projection_status(memory_event_id, failure)
            return failure["life_experience"]

    def merge_event_refs(self, event_id_value: str, refs: Mapping[str, Any]) -> None:
        self.repository.merge_refs(event_id=event_id_value, refs=refs)

    def update_projection_status(
        self,
        event_id_value: str,
        projection_status: Mapping[str, Any],
    ) -> None:
        self.repository.merge_projection_status(
            event_id=event_id_value,
            projection_status=projection_status,
        )

    def _find_visible_event_by_request(
        self,
        *,
        session_id: str,
        request_id: str,
        role: str,
    ) -> dict[str, Any]:
        row = self.repository.find_visible_event(
            session_id=session_id,
            request_id=request_id,
            role=role,
        )
        if row is None:
            return {}
        refs = mapping(loads(row["refs_json"], {}))
        return {
            "memory_event_id": row["event_id"],
            "turn": {"turn_id": refs.get("turn_id")},
            "projection_status": loads(row["projection_status_json"], {}),
        }

    @staticmethod
    def _valence_for_text(value: str, *, role: str) -> float:
        negative = any(
            token in value
            for token in ("不对", "没效果", "牛头不对马嘴", "负优化", "莫名其妙", "烦", "抽象")
        )
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
    rows = MemoryEventStore(ledger).repository.list_events(
        person_id=text(person_id or "default", limit=120) or "default",
        session_id=text(session_id, limit=180),
        visibility=text(visibility, limit=40),
        limit=max(1, min(int(limit or 50), 200)),
    )
    return {"person_id": person_id, "items": [_row_to_event(row) for row in rows]}


def _row_to_event(row: Mapping[str, Any]) -> dict[str, Any]:
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
    return MemoryEventStore(ledger).repository.health(
        person_id=text(person_id or "default", limit=120) or "default"
    )


def rebuild_memory_system(*, ledger: Any, person_id: str = "default") -> dict[str, Any]:
    store = MemoryEventStore(ledger)
    ts = now_ms()
    backup_path = ""
    backup_receipt: dict[str, Any] | None = None
    if store.repository.db_path != ":memory:":
        backup_dir = (
            Path("artifacts")
            / "memory_rebuild_backup"
            / f"{ts}-{uuid.uuid4().hex[:8]}"
        )
        receipt = SQLiteBackupManager().create_verified_backup(
            source_path=store.repository.db_path,
            backup_path=backup_dir / "audit.sqlite3",
            created_at_ms=ts,
        )
        backup_path = receipt.backup_path
        backup_receipt = receipt.as_dict()
    cleared = store.repository.reset()
    epoch = store.append_event(
        person_id=person_id or "default",
        source_kind="memory_rebuild",
        event_kind="memory_system_rebuilt",
        visibility="system",
        content_summary="memory event source rebuilt; fragmented runtime memory cleared",
        refs={
            "backup_path": backup_path,
            "backup_sha256": (backup_receipt or {}).get("sha256", ""),
            "cleared_tables": cleared,
        },
        salience=0.4,
        valence=0.0,
        uncertainty=0.02,
        confidence=1.0,
        created_at_ms=ts,
    )
    return {
        "backup_path": backup_path,
        "backup_receipt": backup_receipt,
        "cleared_tables": cleared,
        "initialized_tables": ["memory_event"],
        "epoch_event_id": epoch["event_id"],
    }


def restore_memory_backup(*, backup_path: str, target_path: str) -> dict[str, Any]:
    """Restore a verified backup to a new, offline SQLite target."""

    return SQLiteBackupManager().restore_verified_backup(
        backup_path=backup_path,
        target_path=target_path,
    ).as_dict()


__all__ = [
    "MemoryEventStore",
    "get_recent_memory_events",
    "infer_role",
    "infer_visibility",
    "memory_event_health",
    "rebuild_memory_system",
    "restore_memory_backup",
]
