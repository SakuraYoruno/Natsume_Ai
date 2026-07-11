"""Expiring scene decisions adapted from existing runtime observations."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


SCENE_DECISION_SCHEMA_VERSION = "Natsume.scene.decision.v1"
SCENE_DECISION_DEFAULT_TTL_MS = 2 * 60 * 1000


def life_projection_enabled() -> bool:
    """Resolve the frozen rollout flag; absent/malformed is disabled."""
    raw = str(os.environ.get("HOST_LIFE_PROJECTION_ENABLED") or "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}

SCENE_PROACTIVE_ALLOW = "ALLOW"
SCENE_PROACTIVE_CONTEXTUAL = "CONTEXTUAL"
SCENE_PROACTIVE_BLOCK = "BLOCK"

_BLOCKING_TAGS = frozenset({"focus_work", "fullscreen_video", "sleeping_screen", "meeting"})
_GAMING_TAGS = frozenset({"gaming", "gameplay", "battle", "lobby"})
_GENERIC_PROACTIVE_SCENES = frozenset({"late_night", "morning_greet", "holiday_greet"})
_TECHNICAL_TASKS = frozenset({"technical_help", "coding", "work_code", "debug", "analysis", "execution"})
_BOUNDARY_TASKS = frozenset({"boundary_repair", "relationship_boundary", "boundary_hurt"})


@dataclass(frozen=True, slots=True)
class SceneDecision:
    schema_version: str
    decision_id: str
    scene_type: str
    scene_tags: tuple[str, ...]
    observed_at_ms: int
    expires_at_ms: int
    confidence: float
    epistemic_type: str
    conflict_state: str
    source_refs: tuple[str, ...]
    task_mode: str
    sys_mode: str
    proactive_policy: str
    expression_mode: str
    reason_codes: tuple[str, ...]
    constraints: dict[str, Any]
    user_goal_ref: str
    user_goal_preserved: bool = True
    read_only: bool = True
    permission_effect: str = "RESTRICT_ONLY"

    def as_dict(self, *, now_ms: int | None = None) -> dict[str, Any]:
        payload = asdict(self)
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        payload["expired"] = now >= self.expires_at_ms
        if payload["expired"] and payload["conflict_state"] == "NONE":
            payload["conflict_state"] = "EXPIRED"
        return payload


def _normalize_tag(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_").strip("_")
    aliases = {
        "focus": "focus_work",
        "working": "focus_work",
        "coding": "focus_work",
        "editing": "focus_work",
        "study": "focus_work",
        "video": "fullscreen_video",
        "fullscreen": "fullscreen_video",
        "movie": "fullscreen_video",
        "sleep": "sleeping_screen",
        "lock_screen": "sleeping_screen",
        "screensaver": "sleeping_screen",
        "conference": "meeting",
        "video_call": "meeting",
        "game": "gaming",
        "combat": "battle",
    }
    return aliases.get(normalized, normalized)


def _scene_tags(scene_awareness: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[Any] = []
    raw = scene_awareness.get("scene_tags")
    if isinstance(raw, str):
        values.extend(raw.replace(";", ",").replace("|", ",").split(","))
    elif isinstance(raw, (list, tuple, set)):
        values.extend(raw)
    values.append(scene_awareness.get("current_activity"))
    tags: list[str] = []
    for value in values:
        tag = _normalize_tag(value)
        if tag and tag not in tags:
            tags.append(tag)
        if tag in {"gameplay", "battle", "lobby"} and "gaming" not in tags:
            tags.insert(0, "gaming")
    return tuple(tags)


def _source_refs(scene: Mapping[str, Any], *, digest: str) -> tuple[str, ...]:
    raw = scene.get("source_refs") or scene.get("source_ref") or []
    if isinstance(raw, str):
        raw = [raw]
    refs = [str(item).strip() for item in list(raw or []) if str(item).strip()]
    if not refs:
        refs = [f"scene-awareness://snapshot/{digest[:24]}"]
    return tuple(dict.fromkeys(refs))


def _source_timestamp(value: Any, *, fallback: int, max_value: int | None = None) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return fallback
    if resolved <= 0 or (max_value is not None and resolved > max_value):
        return fallback
    return resolved


def _source_confidence(value: Any, *, fallback: float) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(0.0, min(1.0, resolved))


def build_scene_decision(
    *,
    scene_awareness: Mapping[str, Any] | None = None,
    task_mode: str = "",
    sys_mode: str = "normal",
    activity_monitor: Mapping[str, Any] | None = None,
    user_goal_ref: str = "",
    now_ms: int | None = None,
    ttl_ms: int = SCENE_DECISION_DEFAULT_TTL_MS,
    enabled: bool = True,
) -> SceneDecision:
    """Build a soft, expiring decision; it never grants a capability."""
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    ttl = max(1, int(ttl_ms))
    scene = dict(scene_awareness or {})
    activity = dict(activity_monitor or {})
    task = _normalize_tag(task_mode)
    mode = _normalize_tag(sys_mode) or "normal"
    tags = list(_scene_tags(scene))
    if bool(activity.get("is_gaming")) and "gaming" not in tags:
        tags.append("gaming")
    tags_tuple = tuple(tags)
    tag_set = set(tags_tuple)

    observed = _source_timestamp(
        scene.get("observed_at_ms", scene.get("source_observed_at_ms")),
        fallback=now,
        max_value=now + 60_000,
    )
    raw_expires = scene.get("expires_at_ms", scene.get("source_expires_at_ms"))
    expires = (
        _source_timestamp(raw_expires, fallback=observed + ttl)
        if raw_expires is not None
        else observed + ttl
    )

    reasons: list[str] = []
    source_conflict = str(scene.get("conflict_state") or "NONE").strip().upper() or "NONE"
    conflict_state = source_conflict
    if tag_set & _BLOCKING_TAGS and tag_set & _GAMING_TAGS:
        conflict_state = "CONFLICT"
        reasons.append("SCENE_SIGNAL_CONFLICT_RESTRICTIVE_WIN")

    companion_suitable = scene.get("companion_suitable")
    if not enabled:
        proactive_policy = SCENE_PROACTIVE_ALLOW
        scene_type = "disabled"
        expression_mode = "legacy"
        reasons.append("SCENE_POLICY_DISABLED")
    elif companion_suitable is False:
        proactive_policy = SCENE_PROACTIVE_BLOCK
        scene_type = "companion_unsuitable"
        expression_mode = "quiet"
        reasons.append("SCENE_COMPANION_UNSUITABLE")
    elif mode in {"sleep", "locked", "maintenance"} or tag_set & _BLOCKING_TAGS:
        proactive_policy = SCENE_PROACTIVE_BLOCK
        scene_type = next(iter(sorted(tag_set & _BLOCKING_TAGS)), mode)
        expression_mode = "task_restrained"
        reasons.append("SCENE_PROACTIVE_SUPPRESSED")
    elif task in _BOUNDARY_TASKS:
        proactive_policy = SCENE_PROACTIVE_BLOCK
        scene_type = "relationship_boundary"
        expression_mode = "withdrawn_respectful"
        reasons.append("USER_BOUNDARY_RESTRICTS_PROACTIVE")
    elif tag_set & _GAMING_TAGS or mode == "gaming":
        proactive_policy = SCENE_PROACTIVE_CONTEXTUAL
        scene_type = "gaming"
        expression_mode = "companion_brief"
        reasons.append("SCENE_GAMING_CONTEXTUAL")
    elif task in _TECHNICAL_TASKS or mode == "focus":
        proactive_policy = SCENE_PROACTIVE_CONTEXTUAL
        scene_type = "task_focus"
        expression_mode = "task_first"
        reasons.append("EXPLICIT_TASK_FIRST")
    else:
        proactive_policy = SCENE_PROACTIVE_ALLOW
        scene_type = _normalize_tag(scene.get("current_activity")) or "general"
        expression_mode = "natural"
        reasons.append("SCENE_NO_ADDITIONAL_RESTRICTION")

    canonical = json.dumps(
        {
            "activity": activity,
            "mode": mode,
            "scene": scene,
            "tags": tags_tuple,
            "task": task,
            "observed_at_ms": observed,
            "expires_at_ms": expires,
            "user_goal_ref": user_goal_ref,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    inferred_confidence = 0.9 if tags_tuple or task or mode != "normal" else 0.55
    confidence = _source_confidence(scene.get("confidence"), fallback=inferred_confidence)
    constraints = {
        "proactive_policy": proactive_policy,
        "allow_generic_greeting": proactive_policy == SCENE_PROACTIVE_ALLOW,
        "expression_mode": expression_mode,
        "max_followup_questions": 0 if proactive_policy == SCENE_PROACTIVE_BLOCK else 1,
        "can_change_user_goal": False,
        "can_expand_scope": False,
        "can_reduce_risk": False,
        "can_bypass_token": False,
        "permission_effect": "RESTRICT_ONLY",
    }
    return SceneDecision(
        schema_version=SCENE_DECISION_SCHEMA_VERSION,
        decision_id=f"scene_{digest[:24]}",
        scene_type=scene_type,
        scene_tags=tags_tuple,
        observed_at_ms=observed,
        expires_at_ms=expires,
        confidence=round(confidence, 3),
        epistemic_type=str(scene.get("epistemic_type") or "observed_and_derived").strip()
        or "observed_and_derived",
        conflict_state=conflict_state,
        source_refs=_source_refs(scene, digest=digest),
        task_mode=task,
        sys_mode=mode,
        proactive_policy=proactive_policy,
        expression_mode=expression_mode,
        reason_codes=tuple(reasons),
        constraints=constraints,
        user_goal_ref=str(user_goal_ref or "").strip(),
    )


def evaluate_proactive_scene(
    decision: SceneDecision | Mapping[str, Any],
    *,
    proactive_scene_type: str,
    now_ms: int | None = None,
) -> tuple[bool, str]:
    data = decision.as_dict(now_ms=now_ms) if isinstance(decision, SceneDecision) else dict(decision)
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    try:
        expires_at_ms = int(data.get("expires_at_ms") or 0)
    except (TypeError, ValueError):
        expires_at_ms = 0
    if bool(data.get("expired")) or bool(expires_at_ms and now >= expires_at_ms):
        return True, "scene decision expired; no additional restriction"
    policy = str(data.get("proactive_policy") or SCENE_PROACTIVE_ALLOW).upper()
    scene_type = str(data.get("scene_type") or "general")
    if policy == SCENE_PROACTIVE_BLOCK:
        return False, f"scene={scene_type} suppresses proactive"
    if policy == SCENE_PROACTIVE_CONTEXTUAL and scene_type == "gaming":
        if str(proactive_scene_type or "").strip().lower() in _GENERIC_PROACTIVE_SCENES:
            return False, "scene=gaming prefers companion follow-up over generic greeting"
        return True, "scene=gaming"
    return True, ""


class ScenePolicy:
    def resolve(self, **kwargs: Any) -> SceneDecision:
        return build_scene_decision(**kwargs)


__all__ = [
    "SCENE_DECISION_DEFAULT_TTL_MS",
    "SCENE_DECISION_SCHEMA_VERSION",
    "SCENE_PROACTIVE_ALLOW",
    "SCENE_PROACTIVE_BLOCK",
    "SCENE_PROACTIVE_CONTEXTUAL",
    "SceneDecision",
    "ScenePolicy",
    "build_scene_decision",
    "evaluate_proactive_scene",
    "life_projection_enabled",
]
