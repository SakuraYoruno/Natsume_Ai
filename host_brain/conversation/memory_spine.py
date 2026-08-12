from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

from host_brain.digital_life.application_boundary import (
    LifeCommandServiceLike,
    LifeSnapshotReaderLike,
)
from host_brain.persistence.context_repository import ContextRepository
from host_brain.persistence.context_memory_repository import MemoryEventRepository


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
    # 稳定身份只保留重放判定所需的会话、角色、时间与内容摘要；正文以哈希表示，
    # 既让幂等键随同一事件稳定，也避免把可见内容复制进命令标识。
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
        # 请求 ID 是首选幂等来源；超长 ID 改用规范 JSON 的摘要，保证键长仍符合
        # 下游存储约束而不会因截断两个不同请求产生相同命令。
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
    # 没有请求 ID 的离线或旧数据以稳定字段摘要回退，时间戳参与身份以区分同一
    # 会话中内容相同但确为不同发生的两次可见交互。
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
    reader: LifeSnapshotReaderLike,
    command_id: str,
) -> bool:
    return reader.get_receipt(command_id) is not None


def _turn_source_kind(role: str, metadata: Mapping[str, Any]) -> tuple[str, str]:
    source_kind = str(metadata.get("source_kind") or "").strip()
    if role == "assistant" and source_kind == "life_proactive":
        return "conversation_turn.life_proactive", "life_proactive_sent"
    if role == "user":
        return "conversation_turn.user", "user_message"
    if role == "assistant":
        return "conversation_turn.assistant", "assistant_reply"
    return "conversation_turn.other", "conversation_turn"


def append_visible_conversation_turn(
    *,
    ledger: Any,
    life_service: LifeCommandServiceLike,
    life_reader: LifeSnapshotReaderLike,
    request_id: str,
    session_id: str,
    person_id: str | None,
    role: str,
    content: str,
    ts_ms: int,
    lang: str,
    metadata: Mapping[str, Any] | None = None,
    token_count: int | None = None,
    corrupted: bool = False,
) -> dict[str, Any]:
    """Atomically admit only the canonical visible MemoryEvent/turn.

    Preference extraction and Digital Life projection are independent durable
    post-Acceptance effects.  This writer must never cross either boundary.
    """

    session = _text(session_id, limit=180)
    clean_role = str(role or "").strip().lower()
    clean_content = _text(content, limit=4000)
    if not session or clean_role not in {"user", "assistant"} or not clean_content:
        return {"status": "SKIPPED", "reason_code": "EMPTY_VISIBLE_TURN"}
    # 这里的“可见”是写入记忆事件与 turn 绑定的唯一准入面；偏好提取、生命投影
    # 等派生副作用必须在此提交之后由各自通道处理，不能混进同一写入职责。
    meta = _mapping(metadata)
    source_kind, event_kind = _turn_source_kind(clean_role, meta)
    salience, valence, uncertainty = _signal_weights(
        role=clean_role,
        source_kind=source_kind,
        content=clean_content,
        metadata=meta,
    )
    resolved_person = _text(person_id or "default", limit=120) or "default"
    timestamp = max(0, int(ts_ms or 0))
    turn_id = "turn::sha256:" + hashlib.sha256(
        "\x1f".join((session, str(request_id or "").strip(), clean_role)).encode("utf-8")
    ).hexdigest()
    memory_event_id = f"memevt_{uuid.uuid4().hex}"
    # Host startup owns migration/schema readiness. Rechecking the complete
    # migration catalog and index inventory on every visible turn adds a cold
    # SQLite scan to the interactive path without increasing write safety.
    repository = MemoryEventRepository.from_ledger(
        ledger,
        verify_schema=False,
    )
    admitted = repository.append_visible_turn(
        event={
            "event_id": memory_event_id,
            "person_id": resolved_person,
            "session_id": session,
            "conversation_id": session,
            "source_kind": source_kind,
            "event_kind": event_kind,
            "role": clean_role,
            "content": clean_content,
            "content_summary": clean_content,
            "refs": {
                "request_id": str(request_id or "").strip(),
                "lang": str(lang or "zh").strip() or "zh",
                "metadata": meta,
            },
            "salience": salience,
            "valence": valence,
            "uncertainty": uncertainty,
            "confidence": 1.0,
            "created_at_ms": timestamp,
        },
        turn={
            "turn_id": turn_id,
            "session_id": session,
            "person_id": resolved_person,
            "request_id": str(request_id or "").strip(),
            "role": clean_role,
            "content": clean_content,
            "ts_ms": timestamp,
            "lang": str(lang or "zh").strip() or "zh",
            "token_count": token_count,
            "corrupted": bool(corrupted),
            "metadata": {**meta, "memory_event_id": memory_event_id},
        },
    )
    if admitted.get("status") == "DUPLICATE":
        # 仓储以原子写入裁决重复；调用方看到 SKIPPED 后可以安全重放请求，
        # 不会生成第二条 MemoryEvent 或推进投影头。
        return {
            **admitted,
            "status": "SKIPPED",
            "reason_code": "DUPLICATE_VISIBLE_TURN",
        }
    # ``append_visible_turn`` already commits the MemoryEvent, turn binding,
    # refs and projection head atomically. Do not reopen two extra write
    # transactions for data that is already canonical.
    turn = dict(admitted.get("turn") or {})
    projection_status = dict(admitted.get("projection_status") or {})
    return {
        "status": "OK",
        "memory_event_id": memory_event_id,
        "turn": turn,
        "projection_status": projection_status,
    }


def _signal_weights(*, role: str, source_kind: str, content: str, metadata: Mapping[str, Any]) -> tuple[float, float, float]:
    input_source = str(metadata.get("input_source") or "").strip().lower()
    text = str(content or "")
    # ASR 输入天然含转写歧义，降低显著性并提高不确定度；这不是拒绝保存，而是
    # 约束后续记忆/生命系统不能把可能听错的话当作强事实。
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
    ledger: Any,
    life_service: LifeCommandServiceLike,
    life_reader: LifeSnapshotReaderLike,
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
        from host_brain.digital_life.experience_ingress import (  # noqa: PLC0415
            record_life_experience,
        )

        if _already_in_life_stream(
            reader=life_reader,
            command_id=command_id,
        ):
            return {"status": "SKIPPED", "reason_code": "DUPLICATE_CONVERSATION_TURN"}
        # receipt 是数字生命流的幂等权威；先检查再投影，保证重试不会把同一轮
        # 对话重复消费成多次体验事件。
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
            service=life_service,
            reader=life_reader,
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
        # 对话主记录已独立持久化；生命投影失败只能降级回报，不能反向阻断用户
        # 可见交流或把底层异常细节扩散到该边界之外。
        return {"status": "DEGRADED", "reason_code": type(exc).__name__, "detail": str(exc)[:180]}


def backfill_conversation_turn_memory_spine(
    *,
    ledger: Any,
    life_service: LifeCommandServiceLike,
    life_reader: LifeSnapshotReaderLike,
    person_id: str | None = "default",
    session_id: str = "",
    limit: int = 500,
    dry_run: bool = True,
    include_asr_sessions: bool = False,
) -> dict[str, Any]:
    """Find or bridge conversation turns using canonical command receipts as authority."""

    try:
        turn_repository = ContextRepository.from_ledger(ledger)
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
                reader=life_reader,
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
                ledger=ledger,
                life_service=life_service,
                life_reader=life_reader,
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


def count_conversation_turn_memory_spine_gaps(*, ledger: Any, life_reader: LifeSnapshotReaderLike, person_id: str | None = "default", session_id: str = "", limit: int = 2000, include_asr_sessions: bool = False) -> int:
    try:
        turn_repository = ContextRepository.from_ledger(ledger)
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
                reader=life_reader,
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
    "append_visible_conversation_turn",
    "backfill_conversation_turn_memory_spine",
    "count_conversation_turn_memory_spine_gaps",
    "record_conversation_turn_memory_spine",
]
