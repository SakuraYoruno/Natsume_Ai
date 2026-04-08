from __future__ import annotations

from typing import Any


def _resolve_user_id(args: dict[str, Any], context: dict[str, Any] | None) -> str:
    raw_user_id = str((args or {}).get("user_id") or (context or {}).get("user_id") or "").strip()
    if raw_user_id:
        return raw_user_id
    try:
        from host_brain.bond.user_identity import get_canonical_bond_user_id  # noqa: PLC0415

        resolved = str(get_canonical_bond_user_id() or "").strip()
        if resolved:
            return resolved
    except Exception:
        pass
    return "user_1"


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
    from host_brain.persona.emotional_silence import get_silence_engine  # noqa: PLC0415

    user_id = _resolve_user_id(args, context)
    current_topic = _resolve_current_topic(args, context)
    bond_summary = _resolve_bond_summary(args, context)
    emotion_context = _resolve_emotion_context(args, context)

    engine = get_silence_engine()
    result = dict(
        engine.tick(
            user_id=user_id,
            current_topic=current_topic,
            bond_summary=bond_summary,
            emotion_context=emotion_context,
        )
        or {}
    )
    result.setdefault("status", "OK")
    result["silence_state"] = engine.get_silence_state(current_topic=current_topic).as_dict()
    return result


def execute_silence_update_emotion(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.persona.emotional_silence import get_silence_engine  # noqa: PLC0415

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

    engine = get_silence_engine()
    willingness = engine.update_emotion(
        emotion_tag=emotion_tag,
        emotion_valence=emotion_valence,
        emotion_arousal=emotion_arousal,
        mood_state=mood_state,
    )
    return {
        "status": "OK",
        "willingness": willingness,
        "silence_state": engine.get_silence_state(current_topic=_resolve_current_topic(args, context)).as_dict(),
    }


def execute_silence_add_resistance(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.persona.emotional_silence import get_silence_engine  # noqa: PLC0415

    topic_label = str((args or {}).get("topic_label") or (args or {}).get("current_topic") or "").strip()
    if not topic_label:
        return {
            "status": "REJECTED",
            "reason_code": "SILENCE_TOPIC_REQUIRED",
        }
    try:
        strength = float((args or {}).get("strength") or 0.7)
    except (TypeError, ValueError):
        strength = 0.7

    engine = get_silence_engine()
    engine.add_topic_resistance(topic_label, strength=strength)
    return {
        "status": "OK",
        "topic_label": topic_label,
        "strength": max(0.0, min(1.0, strength)),
        "silence_state": engine.get_silence_state(current_topic=topic_label).as_dict(),
    }
