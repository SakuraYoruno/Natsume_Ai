from __future__ import annotations

from typing import Any


def _canonical_life_retirement(
    limbic: Any,
    *,
    operation: str,
    relationship_subject_id: str | None = None,
) -> dict[str, Any] | None:
    """Reject a legacy global BodySense mutation before parsing its inputs."""

    probe = getattr(limbic, "uses_canonical_affective_life", None)
    if not callable(probe):
        return None
    try:
        canonical = bool(probe())
    except Exception:
        return {
            "status": "SKIPPED",
            "authority": "CANONICAL_LIFE_JOURNAL",
            "reason_code": "CANONICAL_AFFECTIVE_LIFE_SCOPE_UNAVAILABLE",
            "operation": operation,
            "body_sense": {},
        }
    if not canonical:
        return None
    result: dict[str, Any] = {
        "status": "RETIRED_CANONICAL_LIFE",
        "authority": "CANONICAL_LIFE_JOURNAL",
        "reason_code": "LEGACY_BODY_SENSE_MUTATION_RETIRED",
        "operation": operation,
        "body_sense": {},
    }
    subject_id = str(relationship_subject_id or "").strip()
    if subject_id:
        result["relationship_subject_id"] = subject_id
    return result


def _resolve_background_tick_guard() -> dict[str, Any]:
    try:
        from host_brain.tribe.default_mode_agent import resolve_background_tick_guard_state  # noqa: PLC0415

        return dict(resolve_background_tick_guard_state())
    except Exception:
        return {
            "active": False,
            "cooldown_ms": 0,
            "cooldown_remaining_ms": 0,
            "last_ts_ms": 0,
            "speed_profile": "",
        }


def execute_body_sense_tick(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

    foreground_guard = _resolve_background_tick_guard()
    if bool(foreground_guard.get("active")) and not bool((args or {}).get("force_foreground_tick")):
        return {
            "status": "SKIPPED",
            "reason_code": "BACKGROUND_TICK_FOREGROUND_CHAT_COOLDOWN",
            "cooldown_remaining_ms": int(foreground_guard.get("cooldown_remaining_ms") or 0),
            "foreground_speed_profile": str(foreground_guard.get("speed_profile") or ""),
            "body_sense": {},
        }

    limbic = get_limbic_agent()
    retirement = _canonical_life_retirement(limbic, operation="body_sense_tick")
    if retirement is not None:
        return retirement
    current_hour_raw = (args or {}).get("current_hour")
    try:
        current_hour = int(current_hour_raw) if current_hour_raw is not None else None
    except (TypeError, ValueError):
        current_hour = None
    result = dict(limbic.tick_body_sense(current_hour=current_hour) or {})
    # Compatibility ticks may remain scheduled during the rollback window, but
    # canonical Life retirement is not an update and must not re-enter the
    # event bus as if the former global singleton had changed.
    if str(result.get("status") or "").strip().upper() != "OK":
        return result
    # Phase 1：向 TribeEventBus 发布 BODY_SENSE_UPDATED 事件，供订阅方（LimbicAgent 等）消费
    try:
        from host_brain.tribe.ans_agent import get_ans_agent  # noqa: PLC0415

        get_ans_agent().emit_body_sense_updated(sense_result={
            "current_hour": current_hour,
            "status": str(result.get("status") or "OK"),
            "fatigue_level": result.get("fatigue_level"),
            "loneliness_level": result.get("loneliness_level"),
            "body_sense": dict(result.get("body_sense") or {}),
        })
    except Exception:
        pass
    return result


def execute_body_sense_conversation_start(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

    limbic = get_limbic_agent()
    person_id = str((context or {}).get("user_id") or "").strip() or None
    retirement = _canonical_life_retirement(
        limbic,
        operation="conversation_body_sense_start",
        relationship_subject_id=person_id,
    )
    if retirement is not None:
        return retirement
    emotion_context = dict((context or {}).get("emotion_context") or {})
    result = dict(
        limbic.capture_conversation_body_sense(
            request_id=(context or {}).get("request_id"),
            session_id=(context or {}).get("session_id"),
            person_id=person_id,
            emotion_tag=emotion_context.get("emotion_tag"),
            mood_state=emotion_context.get("mood_state"),
            offline_loneliness=(args or {}).get("offline_loneliness"),
        )
        or {}
    )
    return {
        "status": str(result.get("status") or "OK"),
        "authority": result.get("authority"),
        "reason_code": result.get("reason_code"),
        "body_sense": dict(result.get("body_sense") or {}),
    }


def execute_body_sense_conversation_end(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

    limbic = get_limbic_agent()
    person_id = str((context or {}).get("user_id") or "").strip() or None
    retirement = _canonical_life_retirement(
        limbic,
        operation="conversation_body_sense_end",
        relationship_subject_id=person_id,
    )
    if retirement is not None:
        return retirement
    duration_minutes_raw = (args or {}).get("duration_minutes")
    try:
        duration_minutes = float(duration_minutes_raw) if duration_minutes_raw is not None else None
    except (TypeError, ValueError):
        duration_minutes = None

    result = dict(
        limbic.on_conversation_ended(
            request_id=(context or {}).get("request_id"),
            session_id=(context or {}).get("session_id"),
            person_id=person_id,
            duration_minutes=duration_minutes,
        )
        or {}
    )
    return {
        "status": str(result.get("status") or "OK"),
        "authority": result.get("authority"),
        "reason_code": result.get("reason_code"),
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

    limbic = get_limbic_agent()
    person_id = str((context or {}).get("user_id") or "").strip() or None
    retirement = _canonical_life_retirement(
        limbic,
        operation="body_sense_loneliness_update",
        relationship_subject_id=person_id,
    )
    if retirement is not None:
        return retirement

    result = dict(
        limbic.update_body_sense_loneliness(
            loneliness_value=loneliness_value,
            person_id=person_id,
        )
        or {}
    )
    result.setdefault("status", "OK")
    result["body_sense"] = dict(result.get("body_sense") or {})
    return result
