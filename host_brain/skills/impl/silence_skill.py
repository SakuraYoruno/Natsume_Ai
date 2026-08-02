from __future__ import annotations

from typing import Any


def _canonical_life_retirement(
    limbic: Any,
    *,
    operation: str,
    relationship_subject_id: str | None = None,
) -> dict[str, Any] | None:
    """Stop a legacy silence write before caller inputs reach its engine.

    Isolated rollback fixtures do not expose the canonical probe and retain
    their documented compatibility behavior.  A composed production Limbic
    instead returns a visible no-write receipt; probe failures fail closed so
    a broken authority check cannot silently restore the global engine.
    """

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
            "silence_state": {},
        }
    if not canonical:
        return None
    result: dict[str, Any] = {
        "status": "RETIRED_CANONICAL_LIFE",
        "authority": "CANONICAL_LIFE_JOURNAL",
        "reason_code": "LEGACY_SILENCE_MUTATION_RETIRED",
        "operation": operation,
        "silence_state": {},
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


def _resolve_user_id(args: dict[str, Any], context: dict[str, Any] | None) -> str:
    raw_user_id = str((args or {}).get("user_id") or (context or {}).get("user_id") or "").strip()
    if raw_user_id:
        from host_brain.persona.identity import resolve_persona_self_id  # noqa: PLC0415

        return resolve_persona_self_id(raw_user_id)
    try:
        from host_brain.persona.identity import get_canonical_persona_self_id  # noqa: PLC0415

        resolved = str(get_canonical_persona_self_id() or "").strip()
        if resolved:
            return resolved
    except Exception:
        pass
    return "natsume_ai"


def _resolve_current_topic(args: dict[str, Any], context: dict[str, Any] | None) -> str:
    return str((args or {}).get("current_topic") or (context or {}).get("current_topic") or "").strip()


def _resolve_bond_summary(args: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any] | None:
    raw = (args or {}).get("bond_summary")
    if isinstance(raw, dict):
        return dict(raw)
    raw_context = (context or {}).get("bond_summary")
    if isinstance(raw_context, dict):
        return dict(raw_context)
    return None


def _resolve_emotion_context(args: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    raw_context = (context or {}).get("emotion_context")
    if isinstance(raw_context, dict):
        resolved.update(raw_context)

    for source, target in (
        ("emotion_tag", "emotion_tag"),
        ("mood_state", "mood_state"),
        ("emotion_valence", "emotion_intensity"),
    ):
        value = (args or {}).get(source)
        if value is not None and target not in resolved:
            resolved[target] = value
    return resolved


def execute_silence_tick(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

    user_id = _resolve_user_id(args, context)
    foreground_guard = _resolve_background_tick_guard()
    if bool(foreground_guard.get("active")) and not bool((args or {}).get("force_foreground_tick")):
        return {
            "status": "SKIPPED",
            "user_id": user_id,
            "reason_code": "BACKGROUND_TICK_FOREGROUND_CHAT_COOLDOWN",
            "cooldown_remaining_ms": int(foreground_guard.get("cooldown_remaining_ms") or 0),
            "foreground_speed_profile": str(foreground_guard.get("speed_profile") or ""),
            "silence_state": {},
        }
    limbic = get_limbic_agent()
    retirement = _canonical_life_retirement(
        limbic,
        operation="silence_tick",
        relationship_subject_id=str((context or {}).get("user_id") or "").strip() or None,
    )
    if retirement is not None:
        return retirement
    current_topic = _resolve_current_topic(args, context)
    bond_summary = _resolve_bond_summary(args, context)
    emotion_context = _resolve_emotion_context(args, context)

    return dict(
        limbic.tick_silence(
            user_id=user_id,
            current_topic=current_topic,
            bond_summary=bond_summary,
            emotion_context=emotion_context,
        )
        or {}
    )


def execute_silence_update_emotion(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

    limbic = get_limbic_agent()
    retirement = _canonical_life_retirement(
        limbic,
        operation="silence_emotion_update",
    )
    if retirement is not None:
        return retirement
    emotion_tag = str((args or {}).get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
    mood_state = str((args or {}).get("mood_state") or "steady").strip().lower() or "steady"
    try:
        emotion_valence = float((args or {}).get("emotion_valence") or 0.0)
    except (TypeError, ValueError):
        emotion_valence = 0.0
    try:
        emotion_arousal = float((args or {}).get("emotion_arousal") or 0.3)
    except (TypeError, ValueError):
        emotion_arousal = 0.3

    return dict(
        limbic.update_silence_emotion(
            emotion_tag=emotion_tag,
            mood_state=mood_state,
            emotion_valence=emotion_valence,
            emotion_arousal=emotion_arousal,
            current_topic=_resolve_current_topic(args, context),
        )
        or {
            "status": "OK",
            "willingness": 0.0,
            "silence_state": {},
        }
    )


def execute_silence_add_resistance(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

    limbic = get_limbic_agent()
    retirement = _canonical_life_retirement(
        limbic,
        operation="silence_topic_resistance",
    )
    if retirement is not None:
        return retirement
    topic_label = str((args or {}).get("topic_label") or (args or {}).get("current_topic") or "").strip()
    try:
        strength = float((args or {}).get("strength") or 0.7)
    except (TypeError, ValueError):
        strength = 0.7

    return dict(
        limbic.add_silence_topic_resistance(
            topic_label=topic_label,
            strength=strength,
        )
        or {}
    )
