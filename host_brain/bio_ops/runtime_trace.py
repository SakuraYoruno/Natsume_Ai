"""Runtime trace helpers for low-risk Bio-Ops policies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from host_brain.bio_ops.capabilities import (
    classify_attention_budget,
    classify_reflex_stimulus,
    evaluate_active_silence,
    resolve_internal_weather,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OWNER_CANDIDATE_STATE_ROOT = REPO_ROOT / "artifacts" / "organ_ops_owner_candidate_state"


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


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        return _as_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return {}


def _load_owner_behavior_overlay(runtime_signals: Mapping[str, Any] | None = None) -> dict[str, Any]:
    signals = dict(runtime_signals or {})
    root = Path(str(signals.get("owner_candidate_state_root") or DEFAULT_OWNER_CANDIDATE_STATE_ROOT)).expanduser()
    hints: list[dict[str, Any]] = []
    if root.exists():
        for path in sorted(root.rglob("owner_behavior_hint.json")):
            hint = _read_json(path)
            if str(hint.get("status") or "").strip().upper() != "ACTIVE":
                continue
            if bool(hint.get("direct_long_term_write")) or bool(hint.get("durable_write_performed")):
                continue
            if str(hint.get("write_policy") or "").strip() != "owner_runtime_hint_only":
                continue
            hints.append(
                {
                    "hint_id": hint.get("hint_id"),
                    "candidate_id": hint.get("candidate_id"),
                    "owner_organ": hint.get("owner_organ"),
                    "capability": hint.get("capability"),
                    "output_kind": hint.get("output_kind"),
                    "behavior_scope": hint.get("behavior_scope"),
                    "guidance_type": hint.get("guidance_type"),
                    "guidance": hint.get("guidance"),
                    "path": str(path),
                }
            )
    return {
        "applied": bool(hints),
        "hint_count": len(hints),
        "hints": hints[:8],
        "source_state_root": str(root),
        "direct_long_term_write": False,
        "durable_write_performed": False,
        "durable_memory_write_performed": False,
        "write_policy": "owner_runtime_hint_only",
    }


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
    owner_behavior_overlay = _load_owner_behavior_overlay(signals)
    if owner_behavior_overlay["applied"]:
        breathing = {
            **breathing,
            "owner_behavior_overlay_applied": True,
            "owner_hint_count": owner_behavior_overlay["hint_count"],
        }
        if str(breathing.get("reason_code") or "") == "DEFAULT_BREATHING":
            breathing["reason_code"] = "OWNER_HINT_AWARE_BREATHING"

    return {
        "source": "bio_ops.runtime_trace",
        "proposal_only": True,
        "reflex": dict(reflex),
        "attention_budget": attention,
        "internal_weather": weather,
        "active_silence": silence,
        "response_breathing": breathing,
        "owner_behavior_overlay": owner_behavior_overlay,
        "forbidden_actions": ["codex_worker", "long_reasoning", "skill_execution"]
        if bool(dict(reflex).get("matched"))
        else [],
    }


__all__ = [
    "build_bio_ops_runtime_trace",
    "build_response_breathing_policy",
]
