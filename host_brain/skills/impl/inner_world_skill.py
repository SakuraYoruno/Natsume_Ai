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


def _normalize_topics(raw_topics: Any) -> list[str]:
    if isinstance(raw_topics, str):
        text = raw_topics.strip()
        return [text] if text else []
    if isinstance(raw_topics, (list, tuple, set)):
        topics: list[str] = []
        for item in raw_topics:
            text = str(item or "").strip()
            if text:
                topics.append(text)
        return topics
    return []


def execute_inner_world_tick(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.persona.inner_world import get_inner_world  # noqa: PLC0415

    user_id = _resolve_user_id(args, context)
    result = dict(get_inner_world().tick(user_id=user_id) or {})
    result.setdefault("status", "OK")
    result.setdefault("user_id", user_id)
    return result


def execute_inner_world_awaken(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.persona.inner_world import get_inner_world  # noqa: PLC0415

    user_id = _resolve_user_id(args, context)
    snapshot = get_inner_world().on_user_awaken(user_id=user_id)
    return {
        "status": "OK",
        "user_id": user_id,
        "offline_state": snapshot.as_dict(),
    }


def execute_inner_world_session_end(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.persona.inner_world import get_inner_world  # noqa: PLC0415

    user_id = _resolve_user_id(args, context)
    try:
        valence = float((args or {}).get("valence") or 0.0)
    except (TypeError, ValueError):
        valence = 0.0
    try:
        arousal = float((args or {}).get("arousal") or 0.3)
    except (TypeError, ValueError):
        arousal = 0.3
    mood_baseline_raw = (args or {}).get("mood_baseline")
    try:
        mood_baseline = float(mood_baseline_raw) if mood_baseline_raw is not None else None
    except (TypeError, ValueError):
        mood_baseline = None
    pending_topics = _normalize_topics((args or {}).get("pending_topics"))

    inner_world = get_inner_world()
    inner_world.on_session_end(
        user_id=user_id,
        valence=valence,
        arousal=arousal,
        pending_topics=pending_topics,
        mood_baseline=mood_baseline,
    )
    return {
        "status": "OK",
        "user_id": user_id,
        "pending_topics": pending_topics,
        "inner_world_state": inner_world.get_state(user_id),
    }
