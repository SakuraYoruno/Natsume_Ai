from __future__ import annotations

from typing import Any

def execute_body_sense_tick(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

    current_hour_raw = (args or {}).get("current_hour")
    try:
        current_hour = int(current_hour_raw) if current_hour_raw is not None else None
    except (TypeError, ValueError):
        current_hour = None
    return dict(get_limbic_agent().tick_body_sense(current_hour=current_hour) or {})


def execute_body_sense_conversation_start(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

    emotion_context = dict((context or {}).get("emotion_context") or {})
    result = dict(
        get_limbic_agent().capture_conversation_body_sense(
            request_id=(context or {}).get("request_id"),
            session_id=(context or {}).get("session_id"),
            person_id=(context or {}).get("user_id"),
            emotion_tag=emotion_context.get("emotion_tag"),
            mood_state=emotion_context.get("mood_state"),
            offline_loneliness=(args or {}).get("offline_loneliness"),
        )
        or {}
    )
    return {
        "status": "OK",
        "body_sense": dict(result.get("body_sense") or {}),
    }


def execute_body_sense_conversation_end(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

    duration_minutes_raw = (args or {}).get("duration_minutes")
    try:
        duration_minutes = float(duration_minutes_raw) if duration_minutes_raw is not None else None
    except (TypeError, ValueError):
        duration_minutes = None

    result = dict(
        get_limbic_agent().on_conversation_ended(
            request_id=(context or {}).get("request_id"),
            session_id=(context or {}).get("session_id"),
            person_id=(context or {}).get("user_id"),
            duration_minutes=duration_minutes,
        )
        or {}
    )
    return {
        "status": "OK",
        "duration_minutes": duration_minutes,
        "body_sense": dict(result.get("body_sense") or {}),
    }


def execute_body_sense_update_loneliness(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

    loneliness_raw = (args or {}).get("loneliness_value")
    if loneliness_raw is None:
        loneliness_raw = (args or {}).get("loneliness")
    try:
        loneliness_value = float(loneliness_raw)
    except (TypeError, ValueError):
        return {
            "status": "REJECTED",
            "reason_code": "BODY_SENSE_LONELINESS_INVALID",
        }

    result = dict(
        get_limbic_agent().update_body_sense_loneliness(
            loneliness_value=loneliness_value,
        )
        or {}
    )
    result.setdefault("status", "OK")
    result["body_sense"] = dict(result.get("body_sense") or {})
    return result
