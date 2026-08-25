"""Pure runtime policy helpers for the PrefrontalAgent."""

from __future__ import annotations

import os
from typing import Any, Mapping

from host_brain.core.runtime_helpers import parse_bool_env as _env_bool


def background_tasks_enabled() -> bool:
    return _env_bool("HOST_BACKGROUND_TASKS_ENABLED", default=False)


def evolution_event_cooldown_s(reason: str) -> float:
    reason_key = str(reason or "").strip().lower()
    if reason_key == "loneliness_signal":
        return _env_float(
            "HOST_PREFRONTAL_LONELINESS_EVOLUTION_COOLDOWN_S",
            default=1800.0,
            min_value=60.0,
            max_value=86400.0,
        )
    if reason_key == "inner_world_spontaneous_activity":
        return _env_float(
            "HOST_PREFRONTAL_INNER_WORLD_EVOLUTION_COOLDOWN_S",
            default=900.0,
            min_value=60.0,
            max_value=86400.0,
        )
    if reason_key.startswith("immune_block"):
        return _env_float(
            "HOST_PREFRONTAL_IMMUNE_EVOLUTION_COOLDOWN_S",
            default=300.0,
            min_value=30.0,
            max_value=86400.0,
        )
    return _env_float(
        "HOST_PREFRONTAL_EVOLUTION_EVENT_COOLDOWN_S",
        default=900.0,
        min_value=60.0,
        max_value=86400.0,
    )


def evolution_user_cooldown_s() -> float:
    return _env_float(
        "HOST_PREFRONTAL_EVENT_EVOLUTION_USER_COOLDOWN_S",
        default=900.0,
        min_value=60.0,
        max_value=86400.0,
    )


def evolution_startup_grace_s() -> float:
    return _env_float(
        "HOST_PREFRONTAL_EVENT_EVOLUTION_STARTUP_GRACE_S",
        default=300.0,
        min_value=0.0,
        max_value=3600.0,
    )


def loneliness_event_emit_cooldown_s() -> float:
    return _env_float(
        "HOST_PREFRONTAL_LONELINESS_EVENT_EMIT_COOLDOWN_S",
        default=900.0,
        min_value=60.0,
        max_value=86400.0,
    )


def loneliness_signal_log_cooldown_s() -> float:
    return _env_float(
        "HOST_PREFRONTAL_LONELINESS_SIGNAL_LOG_COOLDOWN_S",
        default=300.0,
        min_value=30.0,
        max_value=86400.0,
    )


def loneliness_milestone_cooldown_s() -> float:
    return _env_float(
        "HOST_PREFRONTAL_LONELINESS_MILESTONE_COOLDOWN_S",
        default=3600.0,
        min_value=300.0,
        max_value=86400.0,
    )


def resolve_prefrontal_persona_id(value: Any = None) -> str:
    try:
        from host_brain.persona.identity import (  # noqa: PLC0415
            resolve_persona_self_id,
        )

        return resolve_persona_self_id(value)
    except Exception:
        return str(value or "").strip() or "natsume_ai"


def resolve_prefrontal_persona_workspace_id() -> str:
    value = str(
        os.environ.get("HOST_KNOWLEDGE_WORKSPACE_ID")
        or "host-default"
    ).strip()
    if (
        not value
        or len(value) > 128
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise RuntimeError("PREFRONTAL_PERSONA_WORKSPACE_ID_INVALID")
    return value


def expression_preference_hints(
    policy: Mapping[str, Any] | None,
) -> list[str]:
    if not isinstance(policy, Mapping):
        return []
    raw_scores = policy.get("tag_scores")
    if not isinstance(raw_scores, Mapping):
        return []
    instructions = {
        "ai_tone": "避免机械 AI 腔",
        "template_like": "避免模板化分段",
        "out_of_persona": "保持既定人格语气",
        "logic_break": "先核对意图和因果",
        "too_helpful": "不要过度提供建议",
        "in_persona": "保持当前自然人格表达",
    }
    ranked: list[tuple[int, str]] = []
    for tag, instruction in instructions.items():
        try:
            score = int(raw_scores.get(tag, 0))
        except (TypeError, ValueError):
            continue
        if (
            (tag == "in_persona" and score > 0)
            or (tag != "in_persona" and score < 0)
        ):
            ranked.append((abs(score), instruction))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [instruction for _score, instruction in ranked[:3]]


def expression_preference_directive(
    policy: Mapping[str, Any] | None,
) -> str:
    hints = expression_preference_hints(policy)
    if not hints:
        return ""
    return "已激活的长期表达偏好（本轮执行）：" + "；".join(hints)


def resolve_prefrontal_bond_user_id(
    value: Any = None,
    *,
    session_id: str = "default",
) -> str:
    try:
        from host_brain.bond.user_identity import (  # noqa: PLC0415
            resolve_bond_user_id,
        )

        return resolve_bond_user_id(
            str(value or "").strip() or None,
            session_id=session_id,
        )
    except Exception:
        return str(value or "").strip() or "default"


def _env_float(
    name: str,
    *,
    default: float,
    min_value: float,
    max_value: float,
) -> float:
    try:
        value = float(str(os.environ.get(name) or "").strip())
    except (TypeError, ValueError):
        value = float(default)
    return max(float(min_value), min(float(max_value), value))


__all__ = [
    "background_tasks_enabled",
    "evolution_event_cooldown_s",
    "evolution_startup_grace_s",
    "evolution_user_cooldown_s",
    "expression_preference_hints",
    "expression_preference_directive",
    "loneliness_event_emit_cooldown_s",
    "loneliness_milestone_cooldown_s",
    "loneliness_signal_log_cooldown_s",
    "resolve_prefrontal_bond_user_id",
    "resolve_prefrontal_persona_id",
    "resolve_prefrontal_persona_workspace_id",
]
