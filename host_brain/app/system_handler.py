"""Edge event ingress handler for the Host HTTP boundary."""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from typing import Any, Callable, Mapping

from fastapi import HTTPException, status

from host_brain.app.schemas import (
    EdgeEventsRequest,
    EdgeEventsResponse,
)
from host_brain.core.errors import (
    RPC_EVENT_BATCH_PARTIAL_FAIL,
    RPC_EVENT_SCHEMA_INVALID,
)
from host_brain.core.runtime_helpers import (
    _coerce_bool,
    _coerce_optional_float,
    parse_bool_env as _parse_bool_env,
)

ALLOWED_EDGE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "ASR_CHUNK",
        "ASR_FINAL",
        "ASR_PARTIAL",
        "ASR_RESULT",
        "EMOTION_DETECTED",
        "USER_REACTION",
        "WECHAT_MSG_RECEIVED",
        "USER_INPUT_FINAL",
        "SPEAK",
        "BARGE_IN",
        "TTS_PLAY",
        "TTS_SYNTH",
        "TTS_STARTED",
        "TTS_DONE",
        "TTS_QUEUED",
        "TTS_SYNTH_READY",
        "TTS_SYNTH_FAILED",
        "TTS_PLAYBACK_STARTED",
        "TTS_PLAYBACK_FINISHED",
        "TTS_PLAYBACK_INTERRUPTED",
        "VTS_EXPRESSION",
        "VTS_EXPRESSION_SET",
        "VOICE_RESOURCE_GATE",
        "EDGE_HEALTH_PING",
    }
)

ALLOWED_EDGE_EVENT_SOURCES: frozenset[str] = frozenset({"edge", "edge_client", "host_edge_bridge"})

# ASR_CHUNK is a short-lived transport fragment.  It may still feed the
# in-memory ASR bridge when it represents a final utterance, but persisting
# every fragment creates an unbounded privacy/IO hotspot after Edge replay.
_NON_PERSISTED_EDGE_EVENT_TYPES: frozenset[str] = frozenset({"ASR_CHUNK"})

_EDGE_ASR_BRIDGE_EVENT_TYPES: frozenset[str] = frozenset(
    {"ASR_CHUNK", "ASR_FINAL", "ASR_RESULT", "USER_INPUT_FINAL"}
)
_EDGE_ASR_BRIDGE_FINAL_EVENT_TYPES: frozenset[str] = frozenset(
    {"ASR_FINAL", "ASR_RESULT", "USER_INPUT_FINAL"}
)
_EDGE_ASR_BRIDGE_PLACEHOLDER_TEXTS: frozenset[str] = frozenset({"[ASR_VAD_NO_SPEECH]"})
_EDGE_ASR_BRIDGE_PLACEHOLDER_PATTERN = re.compile(r"^\[[A-Z0-9_:\-]{3,}\]$")
_EDGE_ASR_BRIDGE_SPEECH_CHAR_PATTERN = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")
_EDGE_ASR_BRIDGE_RECENT: dict[str, int] = {}
_EDGE_ASR_BRIDGE_RECENT_LOCK = threading.Lock()
# 同 session_id 下最近一次 bridge 的文本和时间戳（用于文本相似度去重）
_EDGE_ASR_BRIDGE_LAST_TEXT: dict[str, tuple[str, int, int | None]] = {}


def _parse_float_env(name: str, *, default: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _parse_int_env(name: str, *, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _looks_like_asr_placeholder_text(text: str) -> bool:
    normalized = str(text or "").strip().upper()
    if not normalized:
        return True
    if normalized in _EDGE_ASR_BRIDGE_PLACEHOLDER_TEXTS:
        return True
    return _EDGE_ASR_BRIDGE_PLACEHOLDER_PATTERN.fullmatch(normalized) is not None


def _contains_speech_chars(text: str) -> bool:
    return _EDGE_ASR_BRIDGE_SPEECH_CHAR_PATTERN.search(str(text or "")) is not None


def _log_if_available(logger: Any, level: str, message: str, *args: Any) -> None:
    log_fn = getattr(logger, level, None)
    if callable(log_fn):
        log_fn(message, *args)


def _extract_edge_asr_bridge_candidate(
    *,
    raw: Mapping[str, Any],
    normalized_event_type: str,
) -> dict[str, Any] | None:
    if not _parse_bool_env("HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_ENABLED", default=True):
        return None
    if normalized_event_type not in _EDGE_ASR_BRIDGE_EVENT_TYPES:
        return None
    allow_chunk = _parse_bool_env("HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_ALLOW_CHUNK", default=True)
    if not allow_chunk and normalized_event_type not in _EDGE_ASR_BRIDGE_FINAL_EVENT_TYPES:
        return None

    payload_raw = raw.get("payload")
    payload = dict(payload_raw) if isinstance(payload_raw, Mapping) else {}
    text = str(
        payload.get("text")
        or raw.get("text")
        or payload.get("content")
        or raw.get("content")
        or ""
    ).strip()
    if not text:
        return None
    if _looks_like_asr_placeholder_text(text):
        return None

    min_text_len = max(
        1,
        _parse_int_env("HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_MIN_TEXT_LEN", default=2),
    )
    if len(text) < min_text_len:
        return None
    require_speech_chars = _parse_bool_env(
        "HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_REQUIRE_SPEECH_CHARS",
        default=True,
    )
    if require_speech_chars and not _contains_speech_chars(text):
        return None

    reason_code = str(payload.get("reason_code") or raw.get("reason_code") or "").strip().upper()
    if reason_code in {"ASR_VAD_NO_SPEECH", "ASR_LOW_CONFIDENCE"}:
        return None

    low_confidence = _coerce_bool(
        payload.get("low_confidence", raw.get("low_confidence")),
        default=False,
    )
    if low_confidence:
        return None

    confidence = _coerce_optional_float(payload.get("confidence"))
    if confidence is None:
        confidence = _coerce_optional_float(raw.get("confidence"))
    if normalized_event_type.startswith("ASR_"):
        min_confidence = max(
            0.0,
            min(
                1.0,
                _parse_float_env(
                    "HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_MIN_CONFIDENCE",
                    default=0.60,
                ),
            ),
        )
        if confidence is None or confidence < min_confidence:
            return None

    is_final = _coerce_bool(
        payload.get("is_final", payload.get("final", raw.get("is_final", raw.get("final")))),
        default=(normalized_event_type in _EDGE_ASR_BRIDGE_FINAL_EVENT_TYPES),
    )
    if not is_final:
        return None

    request_id = str(raw.get("request_id") or "").strip()
    session_id = str(raw.get("session_id") or "").strip()
    if not request_id or not session_id:
        return None

    seq_raw = payload.get("seq", raw.get("seq"))
    try:
        seq_value = int(seq_raw) if seq_raw is not None else None
    except (TypeError, ValueError):
        seq_value = None

    ts_raw = raw.get("ts_ms")
    try:
        ts_ms = int(ts_raw) if ts_raw is not None else int(time.time() * 1000)
    except (TypeError, ValueError):
        ts_ms = int(time.time() * 1000)

    bridge_payload = dict(payload)
    bridge_payload["session_id"] = session_id
    bridge_payload["text"] = text
    bridge_payload["is_final"] = is_final
    bridge_payload["final"] = is_final
    bridge_payload["edge_event_type"] = normalized_event_type
    bridge_payload["edge_source"] = str(raw.get("source") or "edge").strip().lower() or "edge"
    bridge_payload["edge_request_id"] = request_id
    bridge_payload["edge_ts_ms"] = ts_ms
    if confidence is not None:
        bridge_payload["confidence"] = confidence
    if seq_value is not None:
        bridge_payload["seq"] = seq_value
    if "emotion_tag" not in bridge_payload and raw.get("emotion_tag") is not None:
        bridge_payload["emotion_tag"] = raw.get("emotion_tag")
    if "emotion_intensity" not in bridge_payload and raw.get("emotion_intensity") is not None:
        bridge_payload["emotion_intensity"] = raw.get("emotion_intensity")

    return {
        "request_id": request_id,
        "session_id": session_id,
        "text": text,
        "ts_ms": ts_ms,
        "event_type": normalized_event_type,
        "payload": bridge_payload,
    }


def _schedule_edge_asr_bridge(
    *,
    candidate: Mapping[str, Any],
    logger: Any,
    input_dispatcher: Callable[..., Any],
    inline_waiters: list[Any] | None = None,
    main_loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    request_id = str(candidate.get("request_id") or "").strip()
    session_id = str(candidate.get("session_id") or "").strip()
    text = str(candidate.get("text") or "").strip()
    payload = candidate.get("payload")
    ts_ms_raw = candidate.get("ts_ms")
    if not request_id or not session_id or not text or not isinstance(payload, Mapping):
        return
    try:
        ts_ms = int(ts_ms_raw) if ts_ms_raw is not None else int(time.time() * 1000)
    except (TypeError, ValueError):
        ts_ms = int(time.time() * 1000)
    seq_raw = payload.get("seq", candidate.get("seq"))
    try:
        seq_value = int(seq_raw) if seq_raw is not None else None
    except (TypeError, ValueError):
        seq_value = None

    dedup_window_ms = max(
        0,
        _parse_int_env("HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_DEDUP_WINDOW_MS", default=15000),
    )
    if dedup_window_ms > 0:
        dedup_key = (
            f"{session_id}::{text.casefold()}::{seq_value}"
            if seq_value is not None
            else f"{session_id}::{text.casefold()}"
        )
        with _EDGE_ASR_BRIDGE_RECENT_LOCK:
            cutoff = ts_ms - dedup_window_ms
            stale_keys = [key for key, seen_at in _EDGE_ASR_BRIDGE_RECENT.items() if seen_at < cutoff]
            for stale_key in stale_keys:
                _EDGE_ASR_BRIDGE_RECENT.pop(stale_key, None)
            # 精确文本去重
            last_seen = _EDGE_ASR_BRIDGE_RECENT.get(dedup_key)
            if last_seen is not None and (ts_ms - last_seen) <= dedup_window_ms:
                _log_if_available(
                    logger,
                    "debug",
                    "edge event ASR bridge deduped (exact) request_id=%s session_id=%s",
                    request_id,
                    session_id,
                )
                return
            # 同 session_id 文本相似度去重：如果上一次 bridge 的文本与本次相同或为子串关系，
            # 且时间差在窗口内，视为重复（防止 ASR 反复识别同一段语音产生噪声链路）
            last_entry = _EDGE_ASR_BRIDGE_LAST_TEXT.get(session_id)
            if last_entry is not None:
                last_text, last_ts, last_seq = last_entry
                if (ts_ms - last_ts) <= dedup_window_ms:
                    same_seq = (
                        seq_value is not None
                        and last_seq is not None
                        and seq_value == last_seq
                    )
                    missing_seq = seq_value is None or last_seq is None
                    if same_seq or missing_seq:
                        cur = text.casefold()
                        prev = last_text.casefold()
                        if cur == prev or cur in prev or prev in cur:
                            _log_if_available(
                                logger,
                                "debug",
                                "edge event ASR bridge deduped (similar) request_id=%s session_id=%s text=%.40s prev=%.40s",
                                request_id,
                                session_id,
                                text,
                                last_text,
                            )
                            return
            _EDGE_ASR_BRIDGE_RECENT[dedup_key] = ts_ms
            _EDGE_ASR_BRIDGE_LAST_TEXT[session_id] = (text, ts_ms, seq_value)

    async def _dispatch() -> None:
        try:
            await input_dispatcher(
                request_id=request_id,
                session_id=session_id,
                source="ASR",
                text=text,
                payload=dict(payload),
                ts_ms=ts_ms,
            )
        except Exception as exc:
            _log_if_available(
                logger,
                "warning",
                "edge event ASR bridge failed request_id=%s: %s",
                request_id,
                exc,
            )

    run_in_thread = _parse_bool_env("HOST_INPUT_DISPATCH_IN_THREAD", default=True)
    if not run_in_thread and inline_waiters is not None:
        inline_waiters.append(_dispatch())
        return

    if main_loop is not None and main_loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(_dispatch(), main_loop)
            return
        except Exception as exc:
            _log_if_available(
                logger,
                "debug",
                "edge event ASR bridge thread-safe schedule failed request_id=%s: %s",
                candidate.get("request_id"),
                exc,
            )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _log_if_available(
            logger,
            "debug",
            "edge event ASR bridge skipped: no running loop request_id=%s",
            candidate.get("request_id"),
        )
        return

    loop.create_task(_dispatch())


def receive_edge_events_response(
    *,
    request_body: EdgeEventsRequest,
    get_ledger: Callable[[], Any],
    logger: Any,
    input_dispatcher: Callable[..., Any],
    inline_waiters: list[Any] | None = None,
    main_loop: asyncio.AbstractEventLoop | None = None,
) -> EdgeEventsResponse:
    ts_ms = int(time.time() * 1000)
    raw_events = request_body.events
    if not raw_events:
        return EdgeEventsResponse(
            success=True, total=0, accepted=0, rejected=0,
            detail="empty batch", timestamp=ts_ms,
        )

    required_fields = {"event_type", "ts_ms", "request_id", "session_id"}
    accepted_count = 0
    rejected_indices: list[int] = []
    ledger = get_ledger()

    # Edge ingress is observational.  It may be durably audited and a final
    # ASR result may enter the normal Host input path below, but an inbound
    # sensor/user-affect hint is never authority to mutate Natsume's
    # relationship or self-affect, nor to drive an Avatar expression.  Those
    # effects must originate from an accepted AffectiveTurnPlan instead.
    bridge_candidates: list[dict[str, Any]] = []

    for idx, raw in enumerate(raw_events):
        if not isinstance(raw, dict):
            rejected_indices.append(idx)
            continue
        missing = required_fields - set(raw.keys())
        event_type = str(raw.get("event_type") or "").strip()
        request_id = str(raw.get("request_id") or "").strip()
        session_id = str(raw.get("session_id") or "").strip()
        source = str(raw.get("source") or "edge").strip().lower()
        ts_val = raw.get("ts_ms")
        if missing or not event_type or not request_id or not session_id or not isinstance(ts_val, (int, float)):
            rejected_indices.append(idx)
            continue
        if source not in ALLOWED_EDGE_EVENT_SOURCES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown edge event source: {source}",
            )
        normalized_event_type = event_type.upper()
        if normalized_event_type not in ALLOWED_EDGE_EVENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown edge event_type: {event_type}",
            )
        try:
            if normalized_event_type not in _NON_PERSISTED_EDGE_EVENT_TYPES:
                ledger.log_voice_event({
                    "request_id": request_id,
                    "session_id": session_id,
                    "event_type": normalized_event_type,
                    "direction": str(raw.get("direction") or "EDGE_TO_HOST"),
                    "seq": raw.get("seq") if isinstance(raw.get("seq"), int) else None,
                    "status": str(raw.get("status") or "RECEIVED"),
                    "reason_code": str(raw.get("reason_code") or "").strip() or None,
                    "stage": str(raw.get("stage") or "").strip() or None,
                    "detail": str(raw.get("detail") or "").strip() or None,
                    "payload": raw.get("payload") or {},
                    "ts_ms": int(ts_val),
                })
            accepted_count += 1

            bridge_candidate = _extract_edge_asr_bridge_candidate(
                raw=raw,
                normalized_event_type=normalized_event_type,
            )
            if bridge_candidate is not None:
                bridge_candidates.append(bridge_candidate)

        except Exception as exc:
            logger.warning("Failed to write edge event %d: %s", idx, exc)
            rejected_indices.append(idx)

    for candidate in bridge_candidates:
        if inline_waiters is None:
            _schedule_edge_asr_bridge(
                candidate=candidate,
                logger=logger,
                input_dispatcher=input_dispatcher,
            )
        else:
            _schedule_edge_asr_bridge(
                candidate=candidate,
                logger=logger,
                input_dispatcher=input_dispatcher,
                inline_waiters=inline_waiters,
                main_loop=main_loop,
            )

    rejected_count = len(rejected_indices)
    total = len(raw_events)
    if rejected_count == 0:
        reason_code = None
        detail = f"all {total} events accepted"
    elif accepted_count == 0:
        reason_code = RPC_EVENT_SCHEMA_INVALID
        detail = f"all {total} events rejected"
    else:
        reason_code = RPC_EVENT_BATCH_PARTIAL_FAIL
        detail = f"accepted {accepted_count}, rejected {rejected_count} of {total}"
    return EdgeEventsResponse(
        success=(rejected_count == 0),
        total=total,
        accepted=accepted_count,
        rejected=rejected_count,
        reason_code=reason_code,
        detail=detail,
        rejected_indices=rejected_indices,
        timestamp=ts_ms,
    )
