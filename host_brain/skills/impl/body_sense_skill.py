from __future__ import annotations

from typing import Any


def _resolve_body_sense_state(model: Any) -> dict[str, Any]:
    state = model.get_state()
    if hasattr(state, "as_dict"):
        return dict(state.as_dict())
    if isinstance(state, dict):
        return dict(state)
    return {}


def execute_body_sense_tick(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.persona.body_sense import get_body_sense_model  # noqa: PLC0415

    model = get_body_sense_model()
    current_hour_raw = (args or {}).get("current_hour")
    try:
        current_hour = int(current_hour_raw) if current_hour_raw is not None else None
    except (TypeError, ValueError):
        current_hour = None
    result = dict(model.tick(current_hour=current_hour) or {})
    result.setdefault("status", "OK")
    result["body_sense"] = _resolve_body_sense_state(model)
    return result


def execute_body_sense_conversation_start(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.persona.body_sense import get_body_sense_model  # noqa: PLC0415

    model = get_body_sense_model()
    model.on_conversation_start()
    return {
        "status": "OK",
        "body_sense": _resolve_body_sense_state(model),
    }


def execute_body_sense_conversation_end(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.persona.body_sense import get_body_sense_model  # noqa: PLC0415

    duration_minutes_raw = (args or {}).get("duration_minutes")
    try:
        duration_minutes = float(duration_minutes_raw) if duration_minutes_raw is not None else None
    except (TypeError, ValueError):
        duration_minutes = None

    model = get_body_sense_model()
    model.on_conversation_end(duration_minutes=duration_minutes)
    return {
        "status": "OK",
        "duration_minutes": duration_minutes,
        "body_sense": _resolve_body_sense_state(model),
    }


def execute_body_sense_update_loneliness(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.persona.body_sense import get_body_sense_model  # noqa: PLC0415

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

    model = get_body_sense_model()
    model.update_loneliness(loneliness_value=loneliness_value)
    return {
        "status": "OK",
        "body_sense": _resolve_body_sense_state(model),
    }
