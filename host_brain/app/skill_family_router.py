"""Skill-First routing helpers used by executive router / dispatch layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from host_brain.codex_runtime.skill_ids import CODEX_RUNTIME_SKILL_IDS


_DEFAULT_SKILL_FAMILY_BY_SKILL_ID: dict[str, str] = {
    **{skill_id: "code" for skill_id in CODEX_RUNTIME_SKILL_IDS},
    "web_search": "browser",
    "browser_navigate": "browser",
    "browser_screenshot": "browser",
    "file_read": "system",
    "file_write": "system",
    "file_list": "system",
    "system_info": "system",
    "system_resource_snapshot": "system",
    "process_resource_top": "system",
    "resource_pressure_analysis": "system",
    "resource_manager_status": "system",
    "resource_release": "system",
    "process_list": "system",
    "network_status": "system",
    "open_app": "system",
    "memory_store": "memory",
    "memory_preference_extract": "memory",
    "memory_consolidate": "memory",
    "memory_recall": "memory",
    "repo_health_scan": "code",
    "test_failure_triage": "code",
    "frontend_route_audit": "code",
    "frontend_component_value_audit": "code",
    "session_summary": "memory",
    "emotion_query": "emotion",
    "bond_query": "social",
    "hub_skill_install": "system",
    "hub_skill_search": "system",
    "hub_skill_list": "system",
    "hub_skill_remove": "system",
}

_DEFAULT_FAMILY_BY_AGENT_ID: dict[str, str] = {
    "translate_agent": "translation",
    "vision_agent": "perception",
    "intent_agent": "reasoning",
    "repair_agent": "system",
    "chat_agent": "social",
}

_DEFAULT_CAPABILITY_TAGS_BY_FAMILY: dict[str, tuple[str, ...]] = {
    "browser": ("web", "http", "navigation"),
    "system": ("io", "os", "runtime"),
    "memory": ("episodic", "recall", "context"),
    "social": ("dialogue", "feedback"),
    "code": ("coding", "tooling"),
    "translation": ("translation", "language"),
    "reasoning": ("planning", "analysis"),
    "perception": ("vision", "multimodal"),
    "emotion": ("affect", "persona"),
}

_RISK_LEVEL_BY_AGENT_RISK: dict[str, int] = {
    "low": 1,
    "medium": 3,
    "high": 4,
}


@dataclass(frozen=True)
class SkillRouteDecision:
    route_type: str
    target_id: str
    reason_code: str
    skill_family: str | None
    skill_id: str | None
    executor_target: str | None
    risk_level: int
    capability_tags: tuple[str, ...]
    requires_model_profile: bool


def _normalize(value: Any) -> str:
    return str(value or "").strip()


def _infer_skill_family_from_name(skill_id: str) -> str:
    normalized = _normalize(skill_id).lower()
    if not normalized:
        return "general"
    if normalized in _DEFAULT_SKILL_FAMILY_BY_SKILL_ID:
        return _DEFAULT_SKILL_FAMILY_BY_SKILL_ID[normalized]
    if normalized.startswith("browser_"):
        return "browser"
    if normalized.startswith("memory_"):
        return "memory"
    if normalized.startswith("file_") or normalized.startswith("system_"):
        return "system"
    if normalized.startswith("emotion_"):
        return "emotion"
    return "general"


def _infer_delegate_family(agent_id: str) -> str:
    normalized = _normalize(agent_id).lower()
    if not normalized:
        return "general"
    if normalized in _DEFAULT_FAMILY_BY_AGENT_ID:
        return _DEFAULT_FAMILY_BY_AGENT_ID[normalized]
    if normalized.endswith("_agent"):
        return normalized.replace("_agent", "") or "general"
    return "general"


def _safe_skill_meta(skill_registry: Any, skill_id: str) -> Any:
    if skill_registry is None:
        return None
    try:
        return skill_registry.get(skill_id)
    except Exception:
        return None


def _safe_agent_meta(agent_id: str) -> dict[str, Any]:
    try:
        from host_brain.app.agents.agent_registry import get_agent_meta  # noqa: PLC0415

        raw = get_agent_meta(agent_id)
        return dict(raw or {}) if isinstance(raw, dict) else {}
    except Exception:
        return {}


def build_skill_route_decision(
    *,
    route_type: str,
    target_id: str,
    reason_code: str,
    skill_registry: Any | None = None,
) -> SkillRouteDecision:
    normalized_route = _normalize(route_type).lower() or "chat"
    normalized_target = _normalize(target_id)
    normalized_reason = _normalize(reason_code) or f"default:{normalized_route}"

    if normalized_route == "skill":
        skill_meta = _safe_skill_meta(skill_registry, normalized_target)
        family = str(getattr(skill_meta, "family", "") or "").strip().lower() or _infer_skill_family_from_name(normalized_target)
        risk_level = int(getattr(skill_meta, "risk_level", 2) or 2)
        tags = tuple(getattr(skill_meta, "capability_tags", ()) or ()) or _DEFAULT_CAPABILITY_TAGS_BY_FAMILY.get(family, ())
        return SkillRouteDecision(
            route_type="skill",
            target_id=normalized_target,
            reason_code=normalized_reason,
            skill_family=family,
            skill_id=normalized_target or None,
            executor_target=normalized_target or None,
            risk_level=max(1, min(5, risk_level)),
            capability_tags=tuple(str(tag).strip().lower() for tag in tags if str(tag).strip()),
            requires_model_profile=True,
        )

    if normalized_route == "delegate":
        agent_meta = _safe_agent_meta(normalized_target)
        family = str(agent_meta.get("family") or "").strip().lower() or _infer_delegate_family(normalized_target)
        risk_text = str(agent_meta.get("risk_level") or "medium").strip().lower()
        risk_level = _RISK_LEVEL_BY_AGENT_RISK.get(risk_text, 3)
        tags = _DEFAULT_CAPABILITY_TAGS_BY_FAMILY.get(family, ())
        return SkillRouteDecision(
            route_type="delegate",
            target_id=normalized_target,
            reason_code=normalized_reason,
            skill_family=family,
            skill_id=None,
            executor_target=normalized_target or None,
            risk_level=risk_level,
            capability_tags=tags,
            requires_model_profile=True,
        )

    if normalized_route in {"plan", "task_progress"}:
        return SkillRouteDecision(
            route_type=normalized_route,
            target_id=normalized_target,
            reason_code=normalized_reason,
            skill_family="executive",
            skill_id=None,
            executor_target=normalized_target or None,
            risk_level=2,
            capability_tags=("planning", "orchestration"),
            requires_model_profile=True,
        )

    if normalized_route == "gap":
        return SkillRouteDecision(
            route_type="gap",
            target_id=normalized_target,
            reason_code=normalized_reason,
            skill_family="executive",
            skill_id=None,
            executor_target=normalized_target or None,
            risk_level=3,
            capability_tags=("gap", "synthesis"),
            requires_model_profile=True,
        )

    if normalized_route == "recent_turn":
        return SkillRouteDecision(
            route_type="recent_turn",
            target_id="",
            reason_code=normalized_reason,
            skill_family="social",
            skill_id=None,
            executor_target=None,
            risk_level=1,
            capability_tags=("dialogue", "memory"),
            requires_model_profile=False,
        )

    return SkillRouteDecision(
        route_type="chat",
        target_id=normalized_target,
        reason_code=normalized_reason,
        skill_family="social",
        skill_id=None,
        executor_target=None,
        risk_level=1,
        capability_tags=("dialogue",),
        requires_model_profile=False,
    )


__all__ = ["SkillRouteDecision", "build_skill_route_decision"]
