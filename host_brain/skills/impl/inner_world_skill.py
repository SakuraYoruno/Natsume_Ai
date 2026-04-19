from __future__ import annotations

import time
from typing import Any


def _resolve_background_tick_guard(*, now_ms: int | None = None) -> dict[str, Any]:
    try:
        from host_brain.tribe.default_mode_agent import resolve_background_tick_guard_state  # noqa: PLC0415

        return dict(resolve_background_tick_guard_state(current_ts_ms=now_ms))
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
    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

    user_id = _resolve_user_id(args, context)
    now_ms = int(time.time() * 1000)
    foreground_guard = _resolve_background_tick_guard(now_ms=now_ms)
    if bool(foreground_guard.get("active")) and not bool((args or {}).get("force_foreground_tick")):
        return {
            "status": "SKIPPED",
            "user_id": user_id,
            "reason_code": "BACKGROUND_TICK_FOREGROUND_CHAT_COOLDOWN",
            "cooldown_remaining_ms": int(foreground_guard.get("cooldown_remaining_ms") or 0),
            "foreground_speed_profile": str(foreground_guard.get("speed_profile") or ""),
        }

    limbic = get_limbic_agent()
    result = dict(limbic.tick_inner_world(person_id=user_id) or {})
    result.setdefault("status", "OK")
    result.setdefault("user_id", user_id)
    payload = {
        "user_id": user_id,
        "status": str(result.get("status") or "OK"),
        "loneliness": result.get("loneliness"),
        "anticipation": result.get("anticipation"),
        "continuity_score": result.get("continuity_score"),
    }
    try:
            limbic.handle_signal(
                event_type="INNER_WORLD_TICKED",
                source_agent="ans_agent",
                target_agent="limbic_agent",
                payload=payload,
            )
    except Exception:
        pass
    last_spontaneous_ts_ms = int(result.get("last_spontaneous_ts_ms") or 0)
    now_ms = int(time.time() * 1000)
    if last_spontaneous_ts_ms > 0 and (now_ms - last_spontaneous_ts_ms) <= 120_000:
        try:
            limbic.handle_signal(
                event_type="EVOLUTION_CYCLE_TRIGGERED",
                source_agent="ans_agent",
                target_agent="prefrontal_agent",
                payload={
                    "user_id": user_id,
                    "reason": "inner_world_spontaneous_activity",
                    "continuity_score": result.get("continuity_score"),
                },
            )
        except Exception:
            pass
    # Phase 1：向 TribeEventBus 发布 INNER_WORLD_TICKED 事件，供订阅方（LimbicAgent 等）消费
    try:
        from host_brain.tribe.ans_agent import get_ans_agent  # noqa: PLC0415

        get_ans_agent().emit_inner_world_ticked(tick_result={
            "user_id": user_id,
            "status": str(result.get("status") or "OK"),
            "loneliness": result.get("loneliness"),
            "anticipation": result.get("anticipation"),
            "continuity_score": result.get("continuity_score"),
        })
    except Exception:
        pass
    # T-93：孤独感超阈值时向 TribeEventBus 发布 LONELINESS_SIGNAL 事件
    try:
        from host_brain.tribe.ans_agent import get_ans_agent  # noqa: PLC0415

        _loneliness_val = result.get("loneliness")
        if _loneliness_val is not None:
            _loneliness_float = float(_loneliness_val)
            # 阈值 0.5：超过此值即认为孤独感显著，触发事件
            if _loneliness_float >= 0.5:
                get_ans_agent().emit_loneliness_signal(
                    loneliness=_loneliness_float,
                    user_id=user_id,
                )
    except Exception:
        pass
    return result


def execute_inner_world_awaken(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

    user_id = _resolve_user_id(args, context)
    snapshot = dict(get_limbic_agent().on_user_awaken(person_id=user_id) or {})
    return {
        "status": "OK",
        "user_id": user_id,
        "offline_state": snapshot,
    }


def execute_inner_world_session_end(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

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

    limbic = get_limbic_agent()
    inner_world_state = dict(
        limbic.on_session_end(
            person_id=user_id,
            valence=valence,
            arousal=arousal,
            pending_topics=pending_topics,
            mood_baseline=mood_baseline,
        )
        or {}
    )
    try:
        limbic.handle_signal(
            event_type="INNER_WORLD_SESSION_ENDED",
            source_agent="cortex_agent",
            target_agent="limbic_agent",
            payload={
                "user_id": user_id,
                "valence": float(valence),
                "arousal": float(arousal),
                "pending_topics": list(pending_topics),
            },
        )
    except Exception:
        pass
    return {
        "status": "OK",
        "user_id": user_id,
        "pending_topics": pending_topics,
        "inner_world_state": inner_world_state,
    }
