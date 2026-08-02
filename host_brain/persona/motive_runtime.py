"""运行时动机拼装：把背景状态机快照转成统一 motive_state。"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from host_brain.persona.offline_continuity import classify_absence_band

_logger = logging.getLogger("Natsume.motive_runtime")


def _coerce_mapping(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    return {}



def _now_ms() -> int:
    return int(time.time() * 1000)


def _classify_absence_band(offline_hours: float) -> str:
    return classify_absence_band(offline_hours)



def build_offline_state_from_inner_state(
    inner_state: Mapping[str, Any] | None,
    *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    current = _coerce_mapping(inner_state)
    now = int(now_ms if now_ms is not None else _now_ms())

    last_interaction_ts_ms = int(current.get("last_interaction_ts_ms") or 0)
    offline_hours = float(current.get("total_offline_hours") or 0.0)
    if last_interaction_ts_ms > 0:
        elapsed_hours = max(0.0, (now - last_interaction_ts_ms) / (3600.0 * 1000.0))
        offline_hours = max(offline_hours, elapsed_hours)

    resolved_hours = round(max(0.0, offline_hours), 4)
    resolved_duration_ms = max(0, int(round(resolved_hours * 3600.0 * 1000.0)))
    absence_band = _classify_absence_band(resolved_hours)
    continuity_score = float(current.get("continuity_score") or 0.0)
    spontaneous_event_count = int(current.get("spontaneous_event_count") or 0)
    last_spontaneous_ts_ms = int(current.get("last_spontaneous_ts_ms") or 0)
    last_spontaneous_note = str(current.get("last_spontaneous_note") or "").strip()
    last_spontaneous_source = str(current.get("last_spontaneous_source") or "").strip()

    raw_inner_stream = list(current.get("inner_stream") or [])
    inner_stream_tail: list[dict[str, Any]] = []
    for item in raw_inner_stream[-8:]:
        if isinstance(item, Mapping):
            inner_stream_tail.append(dict(item))

    return {
        "offline_hours": resolved_hours,
        "offline_duration_ms": resolved_duration_ms,
        "absence_band": absence_band,
        "loneliness_level": float(current.get("loneliness") or 0.0),
        "anticipation_level": float(current.get("anticipation") or 0.0),
        "anticipation_topics": list(current.get("anticipation_topics") or []),
        "continuity_score": continuity_score,
        "spontaneous_event_count": spontaneous_event_count,
        "last_spontaneous_ts_ms": last_spontaneous_ts_ms,
        "last_spontaneous_note": last_spontaneous_note,
        "last_spontaneous_source": last_spontaneous_source,
        "inner_stream": inner_stream_tail,
    }



def compute_runtime_motive_state(
    *,
    persona_user_id: str,
    current_topic: str = "",
    bond_summary: Mapping[str, Any] | None = None,
    emotion_context: Mapping[str, Any] | None = None,
    pending_topics: list[str] | None = None,
    silence_state: Mapping[str, Any] | None = None,
    body_sense: Mapping[str, Any] | None = None,
    inner_state: Mapping[str, Any] | None = None,
    runtime_flags: Mapping[str, Any] | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    from host_brain.persona.motive_engine import MotiveInput, compute_motive_state  # noqa: PLC0415
    from host_brain.persona.identity import resolve_persona_self_id  # noqa: PLC0415

    resolved_user_id = resolve_persona_self_id(persona_user_id)
    current_topic_text = str(current_topic or "").strip()
    silence_state_dict = _coerce_mapping(silence_state)
    body_sense_dict = _coerce_mapping(body_sense)
    inner_state_dict = _coerce_mapping(inner_state)

    _limbic_agent = None
    if not silence_state_dict or not body_sense_dict:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        _limbic_agent = get_limbic_agent()

    if not silence_state_dict:
        silence_state_dict = _coerce_mapping(
            _limbic_agent.resolve_silence_state(
                current_topic=current_topic_text,
                person_id=resolved_user_id,
            )
            if _limbic_agent is not None
            else {}
        )
    if not body_sense_dict:
        body_sense_dict = _coerce_mapping(
            _limbic_agent.resolve_body_sense_state(person_id=resolved_user_id)
            if _limbic_agent is not None
            else {}
        )
    if not inner_state_dict:
        if _limbic_agent is None:
            from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

            _limbic_agent = get_limbic_agent()
        inner_state_dict = _coerce_mapping(
            _limbic_agent.resolve_inner_world_state(person_id=resolved_user_id)
            if _limbic_agent is not None
            else {}
        )

    offline_state = build_offline_state_from_inner_state(inner_state_dict, now_ms=now_ms)
    pending_followup_topics = list(pending_topics or offline_state.get("anticipation_topics") or [])
    motive_state = compute_motive_state(
        MotiveInput(
            silence_state=silence_state_dict,
            body_sense=body_sense_dict,
            offline_state=offline_state,
            bond_summary=_coerce_mapping(bond_summary),
            emotion_context=_coerce_mapping(emotion_context),
            pending_followup_topics=pending_followup_topics,
            user_message="",
            runtime_flags=_coerce_mapping(runtime_flags),
        )
    ).as_dict()
    _logger.debug("runtime motive_state user=%s motive=%s", resolved_user_id, motive_state)
    return motive_state
