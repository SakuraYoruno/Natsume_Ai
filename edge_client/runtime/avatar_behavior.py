"""Avatar behavior presets for the desktop companion.

This layer turns high-level companion states into the existing avatar transport
contract: expression, talk state, and optional body action.  It intentionally
does not know whether the downstream avatar is VTS, VRM, or MateEngine; the
runtime drivers keep owning those details.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import os
import time


@dataclass(frozen=True)
class AvatarBehaviorProfile:
    behavior_id: str
    expression_tag: str = "NEUTRAL"
    body_action_id: str | None = None
    talk_state: str | None = None
    duration_s: float = 1.8
    intensity: float = 0.55
    detail: str = ""


@dataclass(frozen=True)
class AvatarBehaviorBodyHint:
    action_id: str
    duration_s: float
    intensity: float
    detail: str = ""


_BEHAVIOR_ALIASES: dict[str, str] = {
    "attention": "listening",
    "attentive": "listening",
    "listen": "listening",
    "thinking_hard": "thinking",
    "ponder": "thinking",
    "hello": "greet",
    "hi": "greet",
    "joy": "happy",
    "smile": "happy",
    "bashful": "shy",
    "interrupt": "interrupted",
    "barge_in": "interrupted",
    "talking": "speaking",
    "speak": "speaking",
    "neutral": "idle",
    "reset": "idle",
}


_BEHAVIOR_PROFILES: dict[str, AvatarBehaviorProfile] = {
    "idle": AvatarBehaviorProfile(
        behavior_id="idle",
        expression_tag="NEUTRAL",
        talk_state="end",
        duration_s=1.2,
        intensity=0.35,
        detail="calm neutral idle state",
    ),
    "listening": AvatarBehaviorProfile(
        behavior_id="listening",
        expression_tag="NEUTRAL",
        body_action_id=None,
        talk_state="end",
        duration_s=1.6,
        intensity=0.48,
        detail="quiet attentive listening state",
    ),
    "thinking": AvatarBehaviorProfile(
        behavior_id="thinking",
        expression_tag="NEUTRAL",
        body_action_id=None,
        talk_state="end",
        duration_s=1.8,
        intensity=0.42,
        detail="quiet thinking state",
    ),
    "greet": AvatarBehaviorProfile(
        behavior_id="greet",
        expression_tag="HAPPY",
        body_action_id=None,
        talk_state="end",
        duration_s=2.0,
        intensity=0.72,
        detail="friendly greeting expression",
    ),
    "happy": AvatarBehaviorProfile(
        behavior_id="happy",
        expression_tag="HAPPY",
        body_action_id=None,
        duration_s=1.8,
        intensity=0.78,
        detail="bright positive reaction",
    ),
    "shy": AvatarBehaviorProfile(
        behavior_id="shy",
        expression_tag="HAPPY",
        body_action_id=None,
        talk_state="end",
        duration_s=1.4,
        intensity=0.5,
        detail="soft shy attention",
    ),
    "interrupted": AvatarBehaviorProfile(
        behavior_id="interrupted",
        expression_tag="NEUTRAL",
        body_action_id=None,
        talk_state="end",
        duration_s=1.0,
        intensity=0.62,
        detail="stop speaking and hold still for user input",
    ),
    "speaking": AvatarBehaviorProfile(
        behavior_id="speaking",
        expression_tag="NEUTRAL",
        talk_state="start",
        duration_s=0.8,
        intensity=0.5,
        detail="prepare avatar for speech",
    ),
}

_BEHAVIOR_BODY_HINTS: dict[str, AvatarBehaviorBodyHint] = {
    "listening": AvatarBehaviorBodyHint(
        action_id="lean_in",
        duration_s=2.1,
        intensity=0.68,
        detail="one-shot obvious attentive lean",
    ),
    "thinking": AvatarBehaviorBodyHint(
        action_id="nod",
        duration_s=2.0,
        intensity=0.74,
        detail="one-shot obvious thinking nod",
    ),
    "interrupted": AvatarBehaviorBodyHint(
        action_id="emphasis",
        duration_s=1.55,
        intensity=0.7,
        detail="one-shot obvious interruption emphasis",
    ),
}

_AVATAR_BEHAVIOR_STATE: dict[str, Any] = {
    "behavior_id": "idle",
    "state": "idle",
    "source": "startup",
    "detail": "initial idle state",
    "transition_count": 0,
    "last_transition_ts_ms": None,
    "last_success": True,
    "last_reason": None,
    "last_resolved": {},
}


def list_avatar_behaviors() -> list[dict[str, Any]]:
    return [
        {
            "behavior_id": profile.behavior_id,
            "expression_tag": profile.expression_tag,
            "body_action_id": profile.body_action_id,
            "talk_state": profile.talk_state,
            "duration_s": profile.duration_s,
            "intensity": profile.intensity,
            "detail": profile.detail,
            "state_body_hint": _body_hint_event(_BEHAVIOR_BODY_HINTS.get(profile.behavior_id)),
        }
        for profile in _BEHAVIOR_PROFILES.values()
    ]


def build_avatar_behavior_state(runtime: Any, *, ts_ms: int | None = None) -> dict[str, Any]:
    driver = getattr(runtime, "vts_driver", None) or getattr(runtime, "vts", None)
    voice_pipeline = getattr(runtime, "voice_pipeline", None)
    config = getattr(runtime, "config", None)
    pipeline_health = _safe_component_health(voice_pipeline)
    state_machine = get_avatar_behavior_state()

    return {
        "stage": "S_EDGE_AVATAR_STATE",
        "status": "ok" if driver is not None else "unavailable",
        "detail": "avatar behavior state snapshot",
        "avatar_backend": str(getattr(config, "avatar_backend", "") or ""),
        "driver": _driver_fast_state(driver),
        "voice_pipeline": _voice_pipeline_state(voice_pipeline, pipeline_health),
        "behavior": {
            "catalog": list_avatar_behaviors(),
            "aliases": dict(sorted(_BEHAVIOR_ALIASES.items())),
            "current": _current_behavior_hint(pipeline_health) or state_machine,
            "state_machine": state_machine,
            "state_body_hint_enabled": _env_bool("EDGE_AVATAR_BEHAVIOR_STATE_HINT_ENABLED", True),
            "state_body_hints": {
                key: _body_hint_event(value)
                for key, value in sorted(_BEHAVIOR_BODY_HINTS.items())
            },
        },
        "safety": _avatar_safety_state(),
        "ts_ms": ts_ms,
    }


def get_avatar_behavior_state() -> dict[str, Any]:
    return dict(_AVATAR_BEHAVIOR_STATE)


def normalize_avatar_behavior_id(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    normalized = _BEHAVIOR_ALIASES.get(raw, raw)
    return normalized if normalized in _BEHAVIOR_PROFILES else ""


def execute_avatar_behavior(runtime: Any, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(payload or {})
    raw_behavior = (
        payload.get("behavior_id")
        or payload.get("behavior")
        or payload.get("intent")
        or payload.get("state")
        or payload.get("action_id")
    )
    behavior_id = normalize_avatar_behavior_id(raw_behavior)
    if str(raw_behavior or "").strip().lower() in {"list", "catalog", "behaviors"}:
        return {
            "success": True,
            "state": "catalog",
            "detail": "avatar behavior catalog",
            "behaviors": list_avatar_behaviors(),
        }
    if not behavior_id:
        return {
            "success": False,
            "state": "rejected",
            "reason": "unknown_behavior_id",
            "detail": f"unknown avatar behavior: {raw_behavior}",
            "behavior_id": str(raw_behavior or ""),
            "supported_behaviors": sorted(_BEHAVIOR_PROFILES),
        }

    profile = _BEHAVIOR_PROFILES[behavior_id]
    expression_tag = str(payload.get("emotion_tag") or profile.expression_tag).strip().upper() or "NEUTRAL"
    allow_body_motion = _env_bool("EDGE_AVATAR_BEHAVIOR_BODY_MOTION_ENABLED", False)
    explicit_body_action = payload.get("body_action_id")
    state_body_hint = _resolve_state_body_hint(behavior_id, payload)
    body_action_origin = ""
    if explicit_body_action is not None:
        body_action_id = str(explicit_body_action).strip()
        body_action_origin = "explicit"
    elif allow_body_motion and profile.body_action_id:
        body_action_id = str(profile.body_action_id).strip()
        body_action_origin = "profile"
    elif state_body_hint is not None:
        body_action_id = state_body_hint.action_id
        body_action_origin = "state_hint"
    else:
        body_action_id = ""
    talk_state = str(payload.get("talk_state") or profile.talk_state or "").strip().lower()
    default_intensity = (
        state_body_hint.intensity
        if body_action_origin == "state_hint" and state_body_hint is not None
        else profile.intensity
    )
    default_duration_s = (
        state_body_hint.duration_s
        if body_action_origin == "state_hint" and state_body_hint is not None
        else profile.duration_s
    )
    intensity = _float_or_default(payload.get("emotion_intensity"), default_intensity, minimum=0.0, maximum=1.0)
    duration_s = _float_or_default(payload.get("duration_s"), default_duration_s, minimum=0.1, maximum=8.0)

    steps: list[dict[str, Any]] = []
    driver = getattr(runtime, "vts_driver", None)
    voice_pipeline = getattr(runtime, "voice_pipeline", None)

    if driver is None:
        return {
            "success": False,
            "state": "rejected",
            "reason": "avatar_driver_unavailable",
            "detail": "avatar driver unavailable",
            "behavior_id": behavior_id,
            "profile": _profile_event(profile),
        }

    if talk_state == "start" and hasattr(driver, "talk_start"):
        steps.append(_driver_result_event("talk_start", driver.talk_start()))
    elif talk_state == "end" and hasattr(driver, "talk_end"):
        steps.append(_driver_result_event("talk_end", driver.talk_end()))

    neutral_expression_enabled = _env_bool("EDGE_AVATAR_BEHAVIOR_NEUTRAL_EXPRESSION_ENABLED", False)
    should_set_expression = bool(expression_tag and hasattr(driver, "set_expression"))
    if expression_tag == "NEUTRAL" and not neutral_expression_enabled:
        should_set_expression = False
        steps.append({
            "step": "set_expression",
            "success": True,
            "reason": "neutral_expression_disabled",
            "detail": "NEUTRAL expression hotkey skipped",
            "emotion_tag": expression_tag,
        })

    if should_set_expression:
        try:
            expression_result = driver.set_expression(expression_tag, intensity=intensity)
        except TypeError:
            expression_result = driver.set_expression(expression_tag)
        steps.append(_driver_result_event("set_expression", expression_result))

    if body_action_id:
        if voice_pipeline is None or not hasattr(voice_pipeline, "perform_body_action"):
            steps.append({
                "step": "body_action",
                "success": False,
                "reason": "voice_pipeline_unavailable",
                "detail": "voice pipeline unavailable for body action",
                "action_id": body_action_id,
            })
        else:
            body_result = voice_pipeline.perform_body_action(
                action_id=body_action_id,
                emotion_tag=expression_tag,
                intensity=intensity,
                duration_s=duration_s,
            )
            steps.append({
                "step": "body_action",
                **dict(body_result),
            })

    hard_failures = [
        step for step in steps
        if step.get("success") is False
        and step.get("reason") not in {"speech_active", "body_action_busy"}
    ]
    soft_failures = [
        step for step in steps
        if step.get("success") is False
        and step.get("reason") in {"speech_active", "body_action_busy"}
    ]
    successes = [step for step in steps if step.get("success") is True]
    success = bool(successes) and not hard_failures
    state = "applied" if success and not soft_failures else "partial" if success else "rejected"
    summary = {
        "success": success,
        "state": state,
        "reason": str((hard_failures[0] or {}).get("reason") or "") if hard_failures else None,
        "detail": profile.detail or f"avatar behavior {behavior_id}",
        "behavior_id": behavior_id,
        "profile": _profile_event(profile),
        "resolved": {
            "expression_tag": expression_tag,
            "body_action_id": body_action_id or None,
            "body_action_origin": body_action_origin or None,
            "talk_state": talk_state or None,
            "duration_s": duration_s,
            "intensity": intensity,
            "state_body_hint": _body_hint_event(state_body_hint),
        },
        "steps": steps,
        "soft_failures": soft_failures,
    }
    if success:
        _record_avatar_behavior_state(
            behavior_id=behavior_id,
            source=payload.get("source") or payload.get("request_source") or "edge_behavior",
            detail=summary["detail"],
            resolved=summary["resolved"],
            success=success,
            reason=summary.get("reason"),
        )
    summary["state_machine"] = get_avatar_behavior_state()
    return summary


def _profile_event(profile: AvatarBehaviorProfile) -> dict[str, Any]:
    return {
        "behavior_id": profile.behavior_id,
        "expression_tag": profile.expression_tag,
        "body_action_id": profile.body_action_id,
        "talk_state": profile.talk_state,
        "duration_s": profile.duration_s,
        "intensity": profile.intensity,
        "detail": profile.detail,
    }


def _body_hint_event(hint: AvatarBehaviorBodyHint | None) -> dict[str, Any] | None:
    if hint is None:
        return None
    return {
        "action_id": hint.action_id,
        "duration_s": hint.duration_s,
        "intensity": hint.intensity,
        "detail": hint.detail,
    }


def _driver_result_event(step: str, result: Any) -> dict[str, Any]:
    if hasattr(result, "as_dict"):
        event = dict(result.as_dict())
    elif isinstance(result, dict):
        event = dict(result)
    else:
        event = {"detail": str(result)}
    event.setdefault("step", step)
    event["step"] = step
    return event


def _record_avatar_behavior_state(
    *,
    behavior_id: str,
    source: Any,
    detail: str,
    resolved: dict[str, Any],
    success: bool,
    reason: Any,
) -> None:
    previous = int(_AVATAR_BEHAVIOR_STATE.get("transition_count") or 0)
    _AVATAR_BEHAVIOR_STATE.update({
        "behavior_id": str(behavior_id or "idle"),
        "state": str(behavior_id or "idle"),
        "source": str(source or "edge_behavior"),
        "detail": str(detail or ""),
        "transition_count": previous + 1,
        "last_transition_ts_ms": int(time.time() * 1000),
        "last_success": bool(success),
        "last_reason": str(reason or "") or None,
        "last_resolved": dict(resolved or {}),
    })


def _safe_component_health(component: Any) -> dict[str, Any]:
    if component is None or not hasattr(component, "health"):
        return {}
    try:
        health = component.health()
    except Exception as exc:  # pragma: no cover - debug snapshot must be non-fatal
        return {
            "health_error": type(exc).__name__,
            "health_detail": str(exc),
        }
    return dict(health or {}) if isinstance(health, dict) else {"health_detail": str(health)}


def _driver_fast_state(driver: Any) -> dict[str, Any]:
    if driver is None:
        return {
            "available": False,
            "detail": "avatar driver unavailable",
        }
    state = _first_present(driver, "state", "_state")
    return {
        "available": True,
        "driver_type": type(driver).__name__,
        "state": _stringify_state(state),
        "talking": _first_present(driver, "talking", "_talking"),
        "current_expression": _first_present(driver, "current_expression", "_current_expression"),
        "last_command": _first_present(driver, "last_command", "_last_command"),
        "connection_error": _first_present(driver, "connection_error", "_connection_error"),
    }


def _voice_pipeline_state(voice_pipeline: Any, health: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": voice_pipeline is not None,
        "body_action_active": health.get("body_action_active"),
        "last_body_action": health.get("last_body_action"),
        "last_vts_result": health.get("last_vts_result"),
        "mouth_sync": health.get("mouth_sync"),
        "life_motion": health.get("life_motion"),
        "health_error": health.get("health_error"),
        "health_detail": health.get("health_detail"),
    }


def _current_behavior_hint(health: dict[str, Any]) -> dict[str, Any]:
    last_vts_result = health.get("last_vts_result")
    if isinstance(last_vts_result, dict):
        behavior = last_vts_result.get("avatar_behavior")
        if isinstance(behavior, dict):
            return dict(behavior)
    last_body_action = health.get("last_body_action")
    if isinstance(last_body_action, dict):
        return {
            "source": "last_body_action",
            "state": last_body_action.get("state"),
            "action_id": last_body_action.get("action_id"),
            "reason": last_body_action.get("reason"),
        }
    return {}


def _avatar_safety_state() -> dict[str, Any]:
    blocked_hotkeys: dict[str, bool] = {}
    try:
        from edge_client.actuators.vts_driver import is_blocked_avatar_hotkey_id

        for hotkey_id in (
            "hotkey_remove_watermark",
            "remove_watermark",
            "hotkey_reset_all",
            "hotkey_exp_blush",
        ):
            blocked_hotkeys[hotkey_id] = bool(is_blocked_avatar_hotkey_id(hotkey_id))
    except Exception as exc:  # pragma: no cover - safety debug should not break state
        blocked_hotkeys["error"] = False
        blocked_hotkeys["error_detail"] = type(exc).__name__  # type: ignore[assignment]

    return {
        "body_motion_enabled": _env_bool("EDGE_AVATAR_BEHAVIOR_BODY_MOTION_ENABLED", False),
        "state_body_hint_enabled": _env_bool("EDGE_AVATAR_BEHAVIOR_STATE_HINT_ENABLED", True),
        "listening_hint_during_asr_enabled": _env_bool(
            "EDGE_AVATAR_BEHAVIOR_LISTENING_HINT_DURING_ASR_ENABLED",
            False,
        ),
        "neutral_expression_enabled": _env_bool("EDGE_AVATAR_BEHAVIOR_NEUTRAL_EXPRESSION_ENABLED", False),
        "reset_to_neutral_enabled": _env_bool("EDGE_VTS_RESET_TO_NEUTRAL_ENABLED", False),
        "watermark_hotkey_block_enabled": _env_bool("EDGE_VTS_BLOCK_WATERMARK_HOTKEY_ENABLED", True),
        "blocked_hotkeys": blocked_hotkeys,
    }


def _resolve_state_body_hint(behavior_id: str, payload: dict[str, Any]) -> AvatarBehaviorBodyHint | None:
    hint = _BEHAVIOR_BODY_HINTS.get(behavior_id)
    if hint is None:
        return None
    enabled_override = _payload_bool(
        payload,
        "state_body_hint_enabled",
        "body_hint_enabled",
        "enable_body_hint",
    )
    if enabled_override is False:
        return None
    if enabled_override is None and not _env_bool("EDGE_AVATAR_BEHAVIOR_STATE_HINT_ENABLED", True):
        return None
    if behavior_id == "listening" and _looks_like_asr_source(payload):
        if not _env_bool("EDGE_AVATAR_BEHAVIOR_LISTENING_HINT_DURING_ASR_ENABLED", False):
            return None
    return hint


def _looks_like_asr_source(payload: dict[str, Any]) -> bool:
    source = str(payload.get("source") or payload.get("request_source") or "").strip().lower()
    if not source:
        return False
    return any(token in source for token in ("asr", "vad", "mic", "microphone", "speech_recognition"))


def _first_present(obj: Any, *names: str) -> Any:
    for name in names:
        if hasattr(obj, name):
            try:
                value = getattr(obj, name)
            except Exception:
                continue
            if callable(value):
                continue
            return value
    return None


def _stringify_state(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return str(enum_value)
    return str(value)


def _float_or_default(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return round(max(minimum, min(maximum, parsed)), 4)


def _payload_bool(payload: dict[str, Any], *names: str) -> bool | None:
    for name in names:
        if name not in payload:
            continue
        raw = payload.get(name)
        if raw is None:
            return None
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() not in {"0", "false", "no", "off", ""}
    return None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}
