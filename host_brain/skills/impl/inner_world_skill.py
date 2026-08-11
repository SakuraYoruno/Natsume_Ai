from __future__ import annotations

import hashlib
import time
from typing import Any

_INNER_WORLD_MEMORY_WRITTEN_EVENT_IDS: set[str] = set()


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


def _env_bool(name: str, *, default: bool) -> bool:
    import os  # noqa: PLC0415

    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _should_run_inline_default_mode_tick(*, emit_inner_stream: bool) -> bool:
    if not emit_inner_stream:
        return False
    if not _env_bool("HOST_CONTINUOUS_ORGAN_TICKS_ENABLED", default=True):
        return False
    if _env_bool("HOST_BACKGROUND_TASKS_ENABLED", default=False):
        return False
    return _env_bool("HOST_CORE_INLINE_DEFAULT_MODE_TICK_ENABLED", default=True)


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


def _persist_inner_world_stream_memory(
    *,
    user_id: str,
    skill_name: str,
    result: dict[str, Any],
    now_ms: int,
    persist_background_event_memory: Any = None,
) -> None:
    note = str(result.get("last_spontaneous_note") or "").strip()
    event_ts = int(result.get("last_spontaneous_ts_ms") or 0)
    if not note or event_ts <= 0 or (now_ms - event_ts) > 120_000:
        return
    event_key = _inner_world_memory_event_key(user_id=user_id, event_ts=event_ts, note=note)
    if event_key in _INNER_WORLD_MEMORY_WRITTEN_EVENT_IDS:
        return
    try:
        if not callable(persist_background_event_memory):
            return
        event_meta = dict(result.get("last_spontaneous_event") or {})
        persist_result = persist_background_event_memory(
            event_source="inner_world",
            event_id=event_key,
            request_id=f"inner_world::{event_ts}",
            intent_id=skill_name,
            person_id=user_id,
            task_id=skill_name,
            status=str(result.get("status") or "OK"),
            reason_code=None,
            detail=note,
            ts_ms=event_ts,
            metadata={
                "user_id": user_id,
                "source": result.get("last_spontaneous_source"),
                "absence_band": result.get("absence_band"),
                "offline_hours": result.get("offline_hours"),
                "focus": event_meta.get("focus") or result.get("last_spontaneous_focus"),
                "emotion_tag": event_meta.get("emotion_tag") or "NEUTRAL",
                "salience": event_meta.get("salience"),
                "core_need": event_meta.get("core_need"),
                "tension": event_meta.get("tension"),
                "next_tendency": event_meta.get("next_tendency"),
                "thought_form": event_meta.get("thought_form"),
                "novelty_score": event_meta.get("novelty_score"),
                "liveliness_score": event_meta.get("liveliness_score"),
                "quality_flags": list(event_meta.get("quality_flags") or []),
                "continuity_refs": list(event_meta.get("continuity_refs") or []),
                "surface_hint": event_meta.get("surface_hint"),
                "thought_id": event_meta.get("thought_id"),
                "should_distill_to_memory": bool(event_meta.get("should_distill_to_memory", True)),
            },
        )
        if str(persist_result.get("status") or "").upper() in {"OK", "SUCCESS"}:
            _INNER_WORLD_MEMORY_WRITTEN_EVENT_IDS.add(event_key)
            if len(_INNER_WORLD_MEMORY_WRITTEN_EVENT_IDS) > 512:
                _INNER_WORLD_MEMORY_WRITTEN_EVENT_IDS.clear()
    except Exception:
        pass


def _inner_world_memory_event_key(*, user_id: str, event_ts: int, note: str) -> str:
    digest = hashlib.sha1(
        f"{user_id}|{int(event_ts)}|{str(note or '').strip()}".encode("utf-8", errors="ignore")
    ).hexdigest()[:12]
    return f"{user_id}:inner_world:{int(event_ts)}:{digest}"


def _uses_canonical_affective_life(limbic: Any) -> bool:
    """Return the app-local authority mode without reviving a legacy fallback."""

    probe = getattr(limbic, "uses_canonical_affective_life", None)
    if not callable(probe):
        return False
    return bool(probe())


def _execute_inner_world_tick(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
    *,
    emit_inner_stream: bool,
    skill_name: str,
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
    try:
        uses_canonical_affective_life = _uses_canonical_affective_life(limbic)
    except Exception:
        return {
            "status": "SKIPPED",
            "user_id": user_id,
            "reason_code": "CANONICAL_AFFECTIVE_LIFE_SCOPE_UNAVAILABLE",
        }
    try:
        result = dict(limbic.tick_inner_world(person_id=user_id, emit_inner_stream=emit_inner_stream) or {})
    except TypeError:
        result = dict(limbic.tick_inner_world(person_id=user_id) or {})
    result.setdefault("status", "OK")
    result.setdefault("user_id", user_id)
    result.setdefault("tick_kind", ("monologue" if emit_inner_stream else "numeric"))
    result.setdefault("skill_name", skill_name)
    if uses_canonical_affective_life:
        # The canonical tick is a bounded Life projection.  The legacy
        # continuation below records volatile EventBus state, can route an
        # unscoped evolution event, and can persist an inner monologue as raw
        # episodic text.  Do not turn this read-only projection into another
        # affect, relationship, or Persona authority.
        result["authority"] = "CANONICAL_LIFE_JOURNAL"
        result["legacy_side_effects_suppressed"] = True
        result["suppressed_effects"] = [
            "tribe_signal",
            "default_mode_tick",
            "legacy_evolution_event",
            "background_inner_world_memory",
            "ans_event_bus",
        ]
        return result
    payload = {
        "user_id": user_id,
        "status": str(result.get("status") or "OK"),
        "loneliness": result.get("loneliness"),
        "anticipation": result.get("anticipation"),
        "continuity_score": result.get("continuity_score"),
        "inner_numeric_state": dict(result.get("inner_numeric_state") or {}),
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
    if _should_run_inline_default_mode_tick(emit_inner_stream=emit_inner_stream):
        try:
            from host_brain.tribe.default_mode_agent import get_default_mode_agent  # noqa: PLC0415

            inner_state = dict(limbic.resolve_inner_world_state(person_id=user_id) or {})
            topic_phrase = ""
            for topic in list(inner_state.get("anticipation_topics") or []):
                topic_phrase = str(topic or "").strip()
                if topic_phrase:
                    break
            default_tick = get_default_mode_agent().tick_idle_cycle(
                person_id=user_id,
                topic_phrase=topic_phrase,
                source=skill_name,
                now_ts_ms=now_ms,
                inner_state=inner_state,
            )
            result["default_mode_tick"] = {
                "status": str(dict(default_tick or {}).get("status") or "OK"),
                "focus": str(dict(default_tick or {}).get("focus") or ""),
                "self_continuity_score": dict(dict(default_tick or {}).get("self_directive") or {}).get(
                    "self_continuity_score"
                ),
                "background_side_effects_suppressed": bool(
                    dict(default_tick or {}).get("background_side_effects_suppressed")
                ),
            }
        except Exception as exc:
            result["default_mode_tick"] = {
                "status": "DEGRADED",
                "reason_code": "DEFAULT_MODE_INLINE_TICK_FAILED",
                "detail": str(exc)[:120],
            }
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
        _persist_inner_world_stream_memory(
            user_id=user_id,
            skill_name=skill_name,
            result=result,
            now_ms=now_ms,
            persist_background_event_memory=(context or {}).get(
                "persist_background_event_memory"
            ),
        )
    # Phase 1：向 TribeEventBus 发布 INNER_WORLD_TICKED 事件，供订阅方（LimbicAgent 等）消费
    try:
        from host_brain.tribe.ans_agent import get_ans_agent  # noqa: PLC0415

        get_ans_agent().emit_inner_world_ticked(tick_result={
            "user_id": user_id,
            "status": str(result.get("status") or "OK"),
            "loneliness": result.get("loneliness"),
            "anticipation": result.get("anticipation"),
            "continuity_score": result.get("continuity_score"),
            "inner_numeric_state": dict(result.get("inner_numeric_state") or {}),
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


def execute_inner_world_tick(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _execute_inner_world_tick(
        args,
        context,
        emit_inner_stream=True,
        skill_name="inner_world_tick",
    )


def execute_inner_world_numeric_tick(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _execute_inner_world_tick(
        args,
        context,
        emit_inner_stream=False,
        skill_name="inner_world_numeric_tick",
    )


def execute_inner_world_monologue_tick(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _execute_inner_world_tick(
        args,
        context,
        emit_inner_stream=True,
        skill_name="inner_world_monologue_tick",
    )


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
    limbic = get_limbic_agent()
    try:
        uses_canonical_affective_life = _uses_canonical_affective_life(limbic)
    except Exception:
        return {
            "status": "SKIPPED",
            "user_id": user_id,
            "reason_code": "CANONICAL_AFFECTIVE_LIFE_SCOPE_UNAVAILABLE",
        }
    if uses_canonical_affective_life:
        # The former session-end path accepted caller-provided VAD and topics
        # then mutated a legacy inner-world/body model.  Canonical relation
        # aftereffects are written by accepted-chat flow instead, so this
        # compatibility endpoint must not create a second session writer.
        return {
            "status": "RETIRED",
            "user_id": user_id,
            "reason_code": "LEGACY_INNER_WORLD_SESSION_END_RETIRED",
            "inner_world_state": {},
        }
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
