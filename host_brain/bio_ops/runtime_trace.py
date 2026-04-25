"""Runtime trace helpers for low-risk Bio-Ops policies."""

from __future__ import annotations

from typing import Any, Mapping

from host_brain.bio_ops.capabilities import (
    classify_attention_budget,
    classify_reflex_stimulus,
    evaluate_active_silence,
    resolve_internal_weather,
)


def _float_value(value: object, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _planned_path_for_route(*, route_type: str, target_id: str) -> list[str]:
    route = str(route_type or "").strip().lower()
    target = str(target_id or "").strip().lower()
    if route == "skill":
        return [target or "skill_execution"]
    if route == "delegate":
        return [target or "delegate_agent"]
    if route == "plan":
        return ["planner", "skill_dag"]
    if route == "recent_turn":
        return ["recent_turn_lightweight_recall"]
    if route == "task_progress":
        return ["task_progress_lookup"]
    return ["chat_llm"]


def build_response_breathing_policy(
    *,
    internal_weather: Mapping[str, Any],
    emotion_context: Mapping[str, Any] | None = None,
    route_type: str = "chat",
) -> dict[str, Any]:
    emotion = dict(emotion_context or {})
    tag = str(emotion.get("emotion_tag") or "NEUTRAL").strip().upper()
    arousal = _float_value(emotion.get("arousal"), default=0.0)
    weather = str(internal_weather.get("internal_weather") or "clear").strip()
    route = str(route_type or "chat").strip().lower()

    if route in {"skill", "delegate", "plan"}:
        return {
            "reply_density": "compact_status",
            "reason_code": "EXECUTION_ROUTE_STATUS_BREATHING",
            "max_sentences": 3,
        }
    if weather in {"static", "storm"} or tag in {"SAD", "HURT", "ANGRY", "FEAR", "EXHAUSTED", "NUMB"}:
        return {
            "reply_density": "soft_brief",
            "reason_code": "LOW_ENERGY_OR_STORM_BREATHING",
            "max_sentences": 4,
        }
    if arousal > 0.75:
        return {
            "reply_density": "grounded_brief",
            "reason_code": "HIGH_AROUSAL_BREATHING",
            "max_sentences": 4,
        }
    return {
        "reply_density": "normal",
        "reason_code": "DEFAULT_BREATHING",
        "max_sentences": 0,
    }


def build_bio_ops_runtime_trace(
    *,
    message: str,
    route_type: str = "chat",
    target_id: str = "",
    intent_draft: Mapping[str, Any] | None = None,
    emotion_context: Mapping[str, Any] | None = None,
    runtime_signals: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    draft = dict(intent_draft or {})
    signals = dict(runtime_signals or {})
    planned_path = list(draft.get("planned_path") or _planned_path_for_route(route_type=route_type, target_id=target_id))
    reflex = draft.get("bio_ops_reflex")
    if not isinstance(reflex, Mapping):
        reflex = classify_reflex_stimulus(message, events=signals.get("events"))
    else:
        reflex = dict(reflex)

    attention = classify_attention_budget(message, planned_path=planned_path)
    weather = resolve_internal_weather(
        {
            "codex_queue_size": signals.get("codex_queue_size", 0),
            "tts_status": signals.get("tts_status", "ok"),
            "user_stress": signals.get("user_stress") or dict(emotion_context or {}).get("stress_level"),
            "current_hour": signals.get("current_hour", 12),
        }
    )
    silence = evaluate_active_silence(
        {
            "current_hour": signals.get("current_hour", 12),
            "user_stress": signals.get("user_stress") or dict(emotion_context or {}).get("stress_level"),
            "explicit_help_request": bool(signals.get("explicit_help_request") or route_type in {"skill", "delegate", "plan"}),
        }
    )
    breathing = build_response_breathing_policy(
        internal_weather=weather,
        emotion_context=emotion_context,
        route_type=route_type,
    )

    return {
        "source": "bio_ops.runtime_trace",
        "proposal_only": True,
        "reflex": dict(reflex),
        "attention_budget": attention,
        "internal_weather": weather,
        "active_silence": silence,
        "response_breathing": breathing,
        "forbidden_actions": ["codex_worker", "long_reasoning", "skill_execution"]
        if bool(dict(reflex).get("matched"))
        else [],
    }


__all__ = [
    "build_bio_ops_runtime_trace",
    "build_response_breathing_policy",
]
