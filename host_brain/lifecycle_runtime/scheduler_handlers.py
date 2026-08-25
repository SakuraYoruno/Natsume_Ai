"""Lifecycle runtime responsibility extracted from the HTTP adapter."""

from typing import Any, Callable

from .ports import LifecycleRuntimeApplicationPorts

def _run_codex_production_scheduler_skill(
    *,
    system_intent: dict[str, Any],
    logger: Any,
) -> dict[str, Any] | None:
    skill_name = str(system_intent.get("skill_name") or "").strip()
    if not skill_name:
        return None

    try:
        from host_brain.agent_runtime.production_studio import AGENT_PRODUCTION_SKILL_IDS  # noqa: PLC0415
    except Exception as exc:
        logger.debug("Codex production skill id set unavailable: %s", exc)
        return None

    if skill_name not in AGENT_PRODUCTION_SKILL_IDS:
        return None

    # The accepted Scheduler effect may invoke its registered worker, but it
    # may not reinterpret a SkillMeta handler as a second execution authority.
    # A Codex production Skill must be admitted as its own immutable Skill node.
    return {
        "status": "REJECTED",
        "reason_code": "GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED",
        "skill_name": skill_name,
        "scheduler_driver": "natsume_scheduler",
    }


def _run_personality_scheduler_skill(
    *,
    ports: LifecycleRuntimeApplicationPorts,
    system_intent: dict[str, Any],
    logger: Any,
    get_ledger: Callable[[], Any],
    ui_pusher: Callable[..., dict[str, Any]],
    llm_generator_override: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    try:
        from host_brain.skills.personality_registry import build_personality_skill_registry  # noqa: PLC0415
    except Exception as exc:
        logger.warning("Personality skill registry unavailable: %s", exc)
        return None

    skill_name = str(system_intent.get("skill_name") or "").strip()
    if not skill_name:
        return None

    try:
        registry = build_personality_skill_registry()
        meta = registry.get(skill_name)
    except Exception as exc:
        logger.warning("Personality skill registry build failed: %s", exc)
        return {
            "status": "FAILED",
            "reason_code": "PERSONALITY_REGISTRY_FAILED",
            "skill_name": skill_name,
            "detail": str(exc),
        }

    if meta is None:
        return None

    if not bool(getattr(meta, "scheduler_allowed", False)):
        return {
            "status": "REJECTED",
            "reason_code": "SKILL_NOT_SCHEDULER_ALLOWED",
            "skill_name": skill_name,
        }

    del ports, get_ledger, ui_pusher, llm_generator_override
    logger.debug(
        "Scheduler refused legacy personality Skill dispatch: %s",
        skill_name,
    )
    return {
        "status": "REJECTED",
        "reason_code": "GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED",
        "skill_name": skill_name,
    }
