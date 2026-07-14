"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any, Callable, Sequence
from collections.abc import Mapping
from host_brain.core.runtime_helpers import (  # noqa: F401
    _format_template_text,
    _load_template_mapping,
    _mapping_section,
    _mapping_text,
    _parse_bool_env,
    _sse,
    _airlock_sanitize_input,
    _airlock_sanitize_output,
    _build_reply_preview,
    _coerce_trace_mapping,
    _coerce_trace_score,
    _coerce_trace_bool,
    _normalize_trace_stage_meta,
    _resolve_envelope_confidence,
    _build_trace_output_digest,
    _detect_trace_fallback_used,
    _summarize_motive_state_for_trace,
    _summarize_conversation_interpretation_for_trace,
    _summarize_reply_plan_for_trace,
    _summarize_tactic_ledger_for_trace,
    _summarize_topic_ledger_for_trace,
    _summarize_affect_state_for_trace,
    _summarize_relationship_state_for_trace,
    _summarize_drive_state_for_trace,
    _summarize_emotion_context_for_trace,
    _summarize_persona_resident_memory_for_trace,
)
import asyncio
from host_brain.core.orchestration_settings import get_orchestration_settings
import re

from host_brain.agent_runtime.runtime_texts import (
    _EMOTION_INFER_KEYWORDS,
    _logger,
)
def _resolve_persona_session_emotion(emotion_context: Mapping[str, Any]) -> tuple[float, float]:
    """为人格 session_end 提取可用的 valence / arousal。"""
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
    fallback_vad = {
        "HAPPY": (0.7, 0.65),
        "LOVING": (0.8, 0.45),
        "EXCITED": (0.75, 0.85),
        "SURPRISED": (0.2, 0.8),
        "SAD": (-0.6, 0.25),
        "ANGRY": (-0.7, 0.8),
        "NEUTRAL": (0.0, 0.3),
    }
    base_valence, base_arousal = fallback_vad.get(emotion_tag, (0.0, 0.3))
    valence = max(-1.0, min(1.0, float(base_valence) * max(0.4, intensity)))
    arousal = max(0.0, min(1.0, max(float(base_arousal), intensity)))
    return valence, arousal


async def _run_persona_session_end_hooks(
    *,
    request_id: str,
    person_id: str,
    session_id: str,
    user_message: str,
    emotion_context: Mapping[str, Any],
) -> None:
    """统一触发人格 session_end/conversation_end 收口。"""
    try:
        from host_brain.bond.user_identity import get_canonical_bond_user_id  # noqa: PLC0415
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        persona_user_id = get_canonical_bond_user_id()
        valence, arousal = _resolve_persona_session_emotion(emotion_context)
        pending_topics = []
        topic_preview = _build_reply_preview(user_message, limit=64)
        if topic_preview:
            pending_topics = [topic_preview]
        await asyncio.to_thread(
            get_limbic_agent().on_session_end,
            person_id=persona_user_id,
            valence=valence,
            arousal=arousal,
            pending_topics=pending_topics,
        )
    except Exception as exc:
        _logger.debug("[%s] persona inner_world session_end failed: %s", request_id, exc)

    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        await asyncio.to_thread(
            get_limbic_agent().on_conversation_ended,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
        )
    except Exception as exc:
        _logger.debug("[%s] persona body_sense conversation_end failed: %s", request_id, exc)


def _run_surface_persona_session_end_sync(
    *,
    request_id: str,
    person_id: str,
    session_id: str,
    user_message: str,
    emotion_context: Mapping[str, Any],
) -> dict[str, Any]:
    from host_brain.bond.user_identity import get_canonical_bond_user_id  # noqa: PLC0415
    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

    payload_emotion = dict(emotion_context or {})
    user_emotion_tag = str(
        payload_emotion.get("surface_user_emotion_tag")
        or payload_emotion.get("user_emotion_tag")
        or payload_emotion.get("emotion_tag")
        or "NEUTRAL"
    ).strip().upper() or "NEUTRAL"
    valence, arousal = _resolve_persona_session_emotion({**payload_emotion, "emotion_tag": user_emotion_tag})
    topic_preview = _build_reply_preview(user_message, limit=64)
    _ = person_id
    persona_user_id = get_canonical_bond_user_id()
    pending_topics = [topic_preview] if topic_preview else []
    state = get_limbic_agent().on_session_end(
        person_id=persona_user_id,
        valence=valence,
        arousal=arousal,
        pending_topics=pending_topics,
    )
    return {
        "status": "OK",
        "request_id": request_id,
        "session_id": session_id,
        "person_id": persona_user_id,
        "emotion_tag": user_emotion_tag,
        "valence": round(float(valence), 4),
        "arousal": round(float(arousal), 4),
        "pending_topics": pending_topics,
        "inner_world": {
            "continuity_score": dict(state or {}).get("continuity_score"),
            "anticipation_topics": list(dict(state or {}).get("anticipation_topics") or [])[:5],
            "inner_stream_size": dict(state or {}).get("inner_stream_size"),
        },
    }


def _infer_emotion_tag_for_tts(
    narrative: str,
    current_tag: str,
    emotion_infer_keywords: list[tuple[str, str]] | None = None,
) -> str:
    """当 current_tag 为 NEUTRAL 时，根据回复文案推断 emotion_tag。"""
    if not narrative or str(current_tag or "").strip().upper() != "NEUTRAL":
        return str(current_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    text = (narrative or "")[:500]
    _emotion_infer_keywords = emotion_infer_keywords or _EMOTION_INFER_KEYWORDS
    for tag, pattern in _emotion_infer_keywords:
        if re.search(pattern, text, re.IGNORECASE):
            return tag
    return "NEUTRAL"


def _resolve_retrieval_timeout_seconds() -> float:
    return get_orchestration_settings().top_agent_retrieval_timeout_ms / 1000.0


def _resolve_selection_followup_retrieval_timeout_seconds() -> float:
    return (
        get_orchestration_settings().selection_followup_retrieval_timeout_ms
        / 1000.0
    )
