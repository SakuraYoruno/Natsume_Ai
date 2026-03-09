"""情感上下文处理模块。

从 fastapi_app.py 拆分，负责情感状态的解析、归一化和治理快照构建。
"""

from __future__ import annotations

import os
import time
from typing import Any, Mapping

from host_brain.core.metabolism import (
    build_governance_snapshot,
    build_hard_snapshot,
    normalize_hard_state,
)


def normalize_chat_emotion_context(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(raw or {})
    state = normalize_hard_state(
        {
            "mood_state": source.get("mood_state"),
            "ts_ms": source.get("emotion_ts_ms"),
        }
    )
    hard_snapshot = build_hard_snapshot(state)
    allowed_emotion_tags = list(hard_snapshot.allowed_emotion_tags)

    emotion_tag = str(source.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
    if allowed_emotion_tags and emotion_tag not in allowed_emotion_tags:
        emotion_tag = allowed_emotion_tags[0]

    emotion_range_hint = str(source.get("emotion_range_hint") or "").strip() or hard_snapshot.emotion_range_hint
    emotion_intensity = _coerce_emotion_intensity(
        source.get("emotion_intensity"),
        default=_coerce_emotion_intensity(os.environ.get("HOST_DEFAULT_EMOTION_INTENSITY"), default=0.5),
    )
    try:
        emotion_ts_ms = int(source.get("emotion_ts_ms") or hard_snapshot.emotion_ts_ms)
    except (TypeError, ValueError):
        emotion_ts_ms = int(hard_snapshot.emotion_ts_ms)

    return {
        "mood_state": str(hard_snapshot.mood_state),
        "emotion_tag": str(emotion_tag),
        "emotion_range_hint": str(emotion_range_hint),
        "emotion_intensity": emotion_intensity,
        "allowed_emotion_tags": allowed_emotion_tags,
        "emotion_ts_ms": emotion_ts_ms,
    }


def resolve_chat_emotion_context(*, request_body: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    if isinstance(getattr(request_body, "emotion_context", None), Mapping):
        raw.update(dict(request_body.emotion_context))
    if getattr(request_body, "mood_state", None) is not None:
        raw["mood_state"] = request_body.mood_state
    if getattr(request_body, "emotion_tag", None) is not None:
        raw["emotion_tag"] = request_body.emotion_tag
    if getattr(request_body, "emotion_range_hint", None) is not None:
        raw["emotion_range_hint"] = request_body.emotion_range_hint
    if getattr(request_body, "emotion_intensity", None) is not None:
        raw["emotion_intensity"] = request_body.emotion_intensity
    if "mood_state" not in raw:
        raw["mood_state"] = str(os.environ.get("HOST_DEFAULT_MOOD_STATE") or "steady").strip().lower() or "steady"
    if "emotion_tag" not in raw:
        raw["emotion_tag"] = str(os.environ.get("HOST_DEFAULT_EMOTION_TAG") or "NEUTRAL").strip().upper() or "NEUTRAL"
    return normalize_chat_emotion_context(raw)


def build_chat_emotion_snapshot(
    *,
    emotion_context: Mapping[str, Any] | None,
    source: str = "chat_request",
    ts_ms: int | None = None,
) -> dict[str, Any]:
    normalized = normalize_chat_emotion_context(emotion_context)
    captured_ts_ms = _coerce_non_negative_int(ts_ms, default=int(time.time() * 1000))
    emotion_ts_ms = _coerce_non_negative_int(
        normalized.get("emotion_ts_ms"),
        default=captured_ts_ms,
    )
    return {
        "version": "chat_emotion_snapshot.v1",
        "source": str(source or "chat_request").strip() or "chat_request",
        "captured_ts_ms": captured_ts_ms,
        "emotion_ts_ms": emotion_ts_ms,
        "age_ms": max(0, captured_ts_ms - emotion_ts_ms),
        "emotion_context": {
            "mood_state": str(normalized.get("mood_state") or "steady"),
            "emotion_tag": str(normalized.get("emotion_tag") or "NEUTRAL"),
            "emotion_range_hint": str(normalized.get("emotion_range_hint") or ""),
            "emotion_intensity": _coerce_emotion_intensity(normalized.get("emotion_intensity"), default=0.5),
        },
        "allowed_emotion_tags": list(normalized.get("allowed_emotion_tags") or []),
    }


def build_chat_governance_snapshot(
    *,
    emotion_context: Mapping[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    normalized = normalize_chat_emotion_context(emotion_context)
    hard_state = normalize_hard_state(
        {
            "mood_state": normalized.get("mood_state"),
            "ts_ms": normalized.get("emotion_ts_ms", ts_ms),
        }
    )
    snapshot = build_governance_snapshot(
        hard_state,
        overrides={
            "mood_state": normalized.get("mood_state"),
            "emotion_tag": normalized.get("emotion_tag"),
            "emotion_intensity": normalized.get("emotion_intensity", 0.5),
            "emotion_ts_ms": normalized.get("emotion_ts_ms", ts_ms),
        },
    )
    snapshot["emotion_context"] = {
        "mood_state": normalized.get("mood_state"),
        "emotion_tag": normalized.get("emotion_tag"),
        "emotion_range_hint": normalized.get("emotion_range_hint"),
        "emotion_intensity": normalized.get("emotion_intensity", 0.5),
    }
    snapshot["emotion_snapshot"] = build_chat_emotion_snapshot(
        emotion_context=normalized,
        source="governance_snapshot",
        ts_ms=ts_ms,
    )
    return snapshot


def map_emotion_tag_to_vad(emotion_tag: str) -> tuple[float, float]:
    tag = str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    if tag in {"HAPPY", "EXCITED"}:
        return 0.7, 0.8
    if tag in {"SAD"}:
        return -0.6, 0.35
    if tag in {"ANGRY"}:
        return -0.7, 0.85
    if tag in {"ANXIOUS"}:
        return -0.4, 0.75
    if tag in {"CALM"}:
        return 0.2, 0.2
    return 0.0, 0.3


def _coerce_non_negative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(0, parsed)


def _coerce_emotion_intensity(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return float(parsed)
