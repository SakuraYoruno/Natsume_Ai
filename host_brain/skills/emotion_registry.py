"""情绪 / Bond Skill 包。

声明式 Provider：新增情绪与关系类技能时，只需补一个
`DeclarativeSkillSpec` 和对应的参数归一化函数。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec
from host_brain.skills.registry import SkillRegistry

_VALID_EMOTION_TAGS = frozenset({
    "HAPPY", "SAD", "ANGRY", "SURPRISED", "CALM", "NEUTRAL",
    "EXCITED", "EMBARRASSED", "SLEEPY", "THINKING", "ANXIOUS",
    "GRATEFUL", "LONELY", "PROUD", "CONFUSED",
})

_VALID_MOOD_STATES = frozenset({"steady", "rising", "falling", "volatile", "flat"})

_VALID_BOND_EVENTS = frozenset({
    "positive_interaction", "negative_interaction",
    "gift", "shared_activity", "conflict", "apology",
    "milestone", "daily_greeting", "long_absence",
    "deep_conversation", "help_given", "help_received",
})


def _normalize_emotion_update(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    emotion_tag = str((args or {}).get("emotion_tag") or "NEUTRAL").strip().upper()
    if emotion_tag not in _VALID_EMOTION_TAGS:
        emotion_tag = "NEUTRAL"
    mood_state = str((args or {}).get("mood_state") or "steady").strip().lower()
    if mood_state not in _VALID_MOOD_STATES:
        mood_state = "steady"
    try:
        valence = max(-1.0, min(1.0, float((args or {}).get("valence") or 0.0)))
    except (TypeError, ValueError):
        valence = 0.0
    try:
        arousal = max(0.0, min(1.0, float((args or {}).get("arousal") or 0.5)))
    except (TypeError, ValueError):
        arousal = 0.5
    trigger = str((args or {}).get("trigger") or "").strip() or None
    sync_vts_raw = (args or {}).get("sync_vts")
    sync_vts = bool(sync_vts_raw) if sync_vts_raw is not None else True

    return {
        "emotion_tag": emotion_tag,
        "mood_state": mood_state,
        "valence": valence,
        "arousal": arousal,
        "trigger": trigger,
        "sync_vts": sync_vts,
        "manifest_ref": f"emotion_update::{emotion_tag}",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_bond_score_update(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    event_type = str((args or {}).get("event_type") or "positive_interaction").strip().lower()
    if event_type not in _VALID_BOND_EVENTS:
        event_type = "positive_interaction"
    delta_raw = (args or {}).get("delta")
    try:
        delta = float(delta_raw) if delta_raw is not None else None
    except (TypeError, ValueError):
        delta = None
    user_id = str((args or {}).get("user_id") or "").strip() or None
    note = str((args or {}).get("note") or "").strip() or None

    return {
        "event_type": event_type,
        "delta": delta,
        "user_id": user_id,
        "note": note,
        "manifest_ref": f"bond_score_update::{event_type}",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_emotion_get(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    include_history_raw = (args or {}).get("include_history")
    include_history = bool(include_history_raw) if include_history_raw is not None else False
    try:
        history_limit = max(1, min(50, int((args or {}).get("history_limit") or 10)))
    except (TypeError, ValueError):
        history_limit = 10

    return {
        "include_history": include_history,
        "history_limit": history_limit,
        "manifest_ref": "emotion_get::current",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_bond_score_get(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    user_id = str((args or {}).get("user_id") or "").strip() or None
    include_history_raw = (args or {}).get("include_history")
    include_history = bool(include_history_raw) if include_history_raw is not None else False
    try:
        history_limit = max(1, min(100, int((args or {}).get("history_limit") or 20)))
    except (TypeError, ValueError):
        history_limit = 20

    return {
        "user_id": user_id,
        "include_history": include_history,
        "history_limit": history_limit,
        "manifest_ref": "bond_score_get::current",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "_context": ctx or {},
    }


class EmotionSkillProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="emotion_update",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=True,
            description="更新 Natsume 当前情绪状态（emotion_tag + mood_state + valence），触发 VTS 表情联动。",
            tool="emotion",
            action="update",
            normalize_args=_normalize_emotion_update,
        ),
        DeclarativeSkillSpec(
            skill_name="bond_score_update",
            clearance="LOW",
            risk_level=2,
            scope="host",
            reversible=True,
            description="更新与用户的 Bond 关系积分（基于互动事件类型自动计算增减量）。",
            tool="emotion",
            action="bond_update",
            normalize_args=_normalize_bond_score_update,
        ),
        DeclarativeSkillSpec(
            skill_name="emotion_get",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=True,
            description="查询 Natsume 当前情绪状态快照（emotion_tag / mood_state / valence / arousal）。",
            tool="emotion",
            action="get",
            normalize_args=_normalize_emotion_get,
        ),
        DeclarativeSkillSpec(
            skill_name="bond_score_get",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=True,
            description="查询与用户的 Bond 关系积分和当前阶段（STRANGER / ACQUAINTANCE / FRIEND / ...）。",
            tool="emotion",
            action="bond_get",
            normalize_args=_normalize_bond_score_get,
        ),
    )


def build_emotion_skill_registry(provider: EmotionSkillProvider | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(provider or EmotionSkillProvider())
    return registry
