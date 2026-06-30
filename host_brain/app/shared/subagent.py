"""
Agent-runtime shared runtime probes, sse helpers, and session tracking.

Extracted from agent_runtime_shared.py — these modules monitor
sub-agent execution: lifecycle hooks, progress probes, streaming
formatters, and session-end normalization.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from typing import Any, Awaitable, Callable, TypedDict


def _normalize_text(value: Any = "", lower: bool = False) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if lower:
        text = text.lower()
    return text


def _parse_int_env(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _parse_bool_env(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", ""}


class _ProbeOutcome(TypedDict, total=False):
    alive: bool
    latency_ms: float
    ready: bool
    detail: str
    error: str


def _default_probe_timeout_ms() -> int:
    return _parse_int_env(
        "HOST_AGENT_RUNTIME_PROBE_TIMEOUT_MS",
        default=3_000,
        minimum=500,
        maximum=30_000,
    )


def _default_probe_max_retries() -> int:
    return _parse_int_env(
        "HOST_AGENT_RUNTIME_PROBE_MAX_RETRIES",
        default=1,
        minimum=0,
        maximum=5,
    )


def _lifecycle_probe_impl(
    probe_fn: Callable[..., Awaitable[Any]],
    *,
    timeout_ms: int | None = None,
    max_retries: int | None = None,
    label: str = "agent",
) -> _ProbeOutcome:
    """Run a probe with two-stage check: readiness then liveness."""
    timeout_ms = timeout_ms or _default_probe_timeout_ms()
    max_retries = max_retries or _default_probe_max_retries()
    last_error: str = ""
    for attempt in range(max(1, max_retries + 1)):
        try:
            result = asyncio.wait_for(
                probe_fn(),
                timeout=timeout_ms / 1000.0,
            )
            if hasattr(result, "ready") and not result.ready:
                last_error = _normalize_text(getattr(result, "error", "not_ready"))
                if attempt < max_retries:
                    time.sleep(0.05 * (attempt + 1))
                continue
            alive = bool(
                getattr(result, "alive", False)
                or (hasattr(result, "ready") and result.ready)
            )
            latency_ms = max(0, int(getattr(result, "latency_ms", 0) or 0))
            return _ProbeOutcome(
                alive=alive,
                latency_ms=float(latency_ms),
                ready=bool(getattr(result, "ready", alive)),
                detail=_normalize_text(getattr(result, "detail", "")),
            )
        except asyncio.TimeoutError:
            last_error = f"{label}:timeout_after_{timeout_ms}ms"
        except Exception as exc:
            last_error = f"{label}:{_normalize_text(str(exc))}"
        if attempt < max_retries:
            time.sleep(0.05 * (attempt + 1))
    return _ProbeOutcome(
        alive=False,
        latency_ms=timeout_ms * max(1, max_retries + 1),
        ready=False,
        error=last_error or f"{label}:probe_failed",
    )


def _lifecycle_probe_async(
    probe_fn: Callable[..., Awaitable[Any]],
    **kwargs: Any,
) -> asyncio.Future:
    """Wrap _lifecycle_probe_impl to return a coroutine for await."""
    return asyncio.ensure_future(
        asyncio.to_thread(_lifecycle_probe_impl, probe_fn, **kwargs)
    )


class _SessionEndPayload(TypedDict, total=False):
    request_id: str
    session_id: str
    person_id: str
    status: str
    ended_at_ms: int
    emotion_tag: str
    emotion_valence: float
    emotion_arousal: float
    pending_topics: list[str]
    inner_world: dict[str, Any]


def _build_session_end_payload(
    *,
    request_id: str = "",
    session_id: str = "",
    person_id: str = "",
    emotion_context: dict[str, Any] | None = None,
    ts_ms: int = 0,
) -> _SessionEndPayload:
    """Build an emotion-aware session-end payload."""
    emotion_context = emotion_context or {}
    valence, arousal = _resolve_persona_session_emotion(emotion_context)
    topic_preview = _build_reply_preview(
        str(emotion_context.get("user_message") or ""), limit=64
    )
    payload = _SessionEndPayload(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        status="ended",
        ended_at_ms=max(0, int(ts_ms or time.time() * 1000)),
        emotion_tag=str(
            emotion_context.get("surface_user_emotion_tag")
            or emotion_context.get("user_emotion_tag")
            or emotion_context.get("emotion_tag")
            or "NEUTRAL"
        ).strip().upper()
        or "NEUTRAL",
        emotion_valence=round(valence, 4),
        emotion_arousal=round(arousal, 4),
        pending_topics=[topic_preview] if topic_preview else [],
    )
    return payload


def _build_reply_preview(text: str, *, limit: int = 64) -> str:
    if not text:
        return ""
    preview = text.strip().split("\n")[0]
    if len(preview) > limit:
        preview = preview[: limit - 1].strip().rstrip(".,;:!?，。；：！？") + "…"
    return preview


# ---------------------------------------------------------------------------
# Emotion resolution helpers (mirror executive_runtime_core behavior)
# ---------------------------------------------------------------------------

_FALLBACK_VAD: dict[str, tuple[float, float]] = {
    "HAPPY": (0.7, 0.65),
    "LOVING": (0.8, 0.45),
    "EXCITED": (0.75, 0.85),
    "SURPRISED": (0.2, 0.8),
    "SAD": (-0.6, 0.25),
    "ANGRY": (-0.7, 0.8),
    "NEUTRAL": (0.0, 0.3),
}


def _resolve_persona_session_emotion(
    emotion_context: Mapping[str, Any],
) -> tuple[float, float]:
    """Extract valence / arousal from an emotion context payload."""
    payload = dict(emotion_context or {})
    vad_payload = payload.get("vad") if isinstance(payload.get("vad"), Mapping) else {}

    raw_valence = payload.get("valence")
    if raw_valence is None:
        raw_valence = vad_payload.get("valence")

    raw_arousal = payload.get("arousal")
    if raw_arousal is None:
        raw_arousal = vad_payload.get("arousal")

    def _coerce(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    if raw_valence is not None or raw_arousal is not None:
        valence = max(-1.0, min(1.0, _coerce(raw_valence, 0.0)))
        arousal = max(0.0, min(1.0, _coerce(raw_arousal, 0.3)))
        return valence, arousal

    emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper()
    intensity = max(0.0, min(1.0, _coerce(payload.get("emotion_intensity"), 0.5)))

    base_valence, base_arousal = _FALLBACK_VAD.get(emotion_tag, (0.0, 0.3))
    valence = max(-1.0, min(1.0, float(base_valence) * max(0.4, intensity)))
    arousal = max(0.0, min(1.0, max(float(base_arousal), intensity)))
    return valence, arousal


def _sse_heartbeat_interval_ms() -> int:
    return _parse_int_env(
        "HOST_AGENT_RUNTIME_SSE_HEARTBEAT_INTERVAL_MS",
        default=15_000,
        minimum=5_000,
        maximum=60_000,
    )


def _sse_max_event_size_bytes() -> int:
    return _parse_int_env(
        "HOST_AGENT_RUNTIME_SSE_MAX_EVENT_SIZE_BYTES",
        default=32_768,
        minimum=4_096,
        maximum=262_144,
    )


def _sse_format_event(
    event_id: str | None,
    event_type: str,
    payload: Any,
    *,
    retry_ms: int | None = None,
) -> str:
    """Format a single SSE event wire frame."""
    lines: list[str] = []
    if event_id:
        lines.append(f"id:{event_id}")
    lines.append(f"event:{event_type}")
    if retry_ms is not None:
        lines.append(f"retry:{int(retry_ms)}")
    raw = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(raw.encode("utf-8")) > _sse_max_event_size_bytes():
        raw = raw[: _sse_max_event_size_bytes() - 4] + "…\"}"
    lines.extend(f"data:{chunk}" for chunk in raw.split("\n"))
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def _sse_format_heartbeat(event_id: str) -> str:
    return _sse_format_event(
        event_id,
        "heartbeat",
        {"ts_ms": int(time.time() * 1000)},
        retry_ms=_sse_heartbeat_interval_ms(),
    )


def _sse_format_error(
    event_id: str,
    error_text: str,
    status_code: int = 500,
) -> str:
    return _sse_format_event(
        event_id,
        "error",
        {
            "status": status_code,
            "error": _normalize_text(error_text),
            "ts_ms": int(time.time() * 1000),
        },
    )


def _sse_format_complete(
    event_id: str,
    result: Any,
    *,
    elapsed_ms: int = 0,
) -> str:
    return _sse_format_event(
        event_id,
        "complete",
        {
            "result": result,
            "elapsed_ms": max(0, int(elapsed_ms)),
            "ts_ms": int(time.time() * 1000),
        },
    )
