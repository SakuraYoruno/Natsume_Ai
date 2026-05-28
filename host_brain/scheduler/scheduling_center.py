"""Unified scheduling center for registry-driven background tasks.

The center turns declarative ``SkillMeta.scheduler_profile`` entries into
``SchedulerTaskConfig`` seed tasks. LLM advisory is kept as proposal data; only
profiles that pass deterministic guards are registered automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from typing import Any, Iterable, Mapping

from host_brain.scheduler.task_registry import SchedulerTaskConfig, TaskPriority, TriggerMode
from host_brain.skills.registry import SkillMeta, SkillRegistry, SkillSchedulerProfile


RETIRED_AUTO_SCHEDULE_SKILLS = {
    "inner_world_tick",
}

SCHEDULER_SEED_TASK_ORDER = (
    "proactive_talk_periodic",
    "evolution_cycle_daily",
    "lightnovel_recommender_daily",
    "diary_writer_daily",
    "dream_generator_sleep",
    "life_memory_sleep_replay_daily",
    "inner_world_numeric_tick",
    "inner_world_monologue_tick",
    "silence_tick",
    "body_sense_tick",
    "gaming_companion_tick",
    "emergent_growth_weekly",
    "emotion_decay_tick",
)

CORE_ORGAN_TICK_TASK_IDS = (
    "inner_world_numeric_tick",
    "inner_world_monologue_tick",
    "silence_tick",
    "body_sense_tick",
    "emotion_decay_tick",
)


@dataclass(frozen=True)
class SchedulingAdvisory:
    skill_name: str
    decision: str
    reason: str
    proposed_profile: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "decision": self.decision,
            "reason": self.reason,
            "proposed_profile": dict(self.proposed_profile),
        }


@dataclass(frozen=True)
class SchedulingPlan:
    tasks: tuple[SchedulerTaskConfig, ...]
    skipped: tuple[SchedulingAdvisory, ...] = ()

    @property
    def task_ids(self) -> list[str]:
        return [task.task_id for task in self.tasks]


def _profile_to_dict(profile: Any) -> dict[str, Any]:
    if isinstance(profile, SkillSchedulerProfile):
        return profile.to_dict()
    if isinstance(profile, Mapping):
        return dict(profile)
    return {}


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _clamp_int(value: Any, *, default: int, low: int, high: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = int(default)
    return max(int(low), min(int(high), parsed))


def _env_int(spec: Mapping[str, Any], fallback: Any) -> int:
    default = int(spec.get("default", fallback if fallback is not None else 0))
    low = int(spec.get("low", 0))
    high = int(spec.get("high", 2_147_483_647))
    name = str(spec.get("name") or "").strip()
    raw = os.environ.get(name) if name else fallback
    return _clamp_int(raw, default=default, low=low, high=high)


def _env_value(spec: Mapping[str, Any], fallback: Any) -> Any:
    name = str(spec.get("name") or "").strip()
    raw = os.environ.get(name) if name else None
    value_type = str(spec.get("type") or "str").strip().lower()
    default = spec.get("default", fallback)

    if value_type == "int":
        value = _env_int(spec, fallback)
    elif value_type == "csv":
        if raw is None or not str(raw).strip():
            return list(default or [])
        value = [item.strip() for item in str(raw).split(",") if item.strip()]
    else:
        value = str(raw).strip() if raw is not None and str(raw).strip() else default

    if str(spec.get("unit") or "").strip().lower() == "minutes_to_ms":
        return int(value) * 60 * 1000
    return value


def _resolve_trigger_config(profile: Mapping[str, Any]) -> dict[str, Any]:
    trigger_config = dict(profile.get("trigger_config") or {})
    trigger_env = dict(profile.get("trigger_env") or {})
    for key, spec in trigger_env.items():
        if isinstance(spec, Mapping):
            trigger_config[str(key)] = _env_int(spec, trigger_config.get(str(key)))
    return trigger_config


def _resolve_args(profile: Mapping[str, Any]) -> dict[str, Any]:
    args = dict(profile.get("args") or {})
    arg_env = dict(profile.get("arg_env") or {})
    for key, spec in arg_env.items():
        if isinstance(spec, Mapping):
            args[str(key)] = _env_value(spec, args.get(str(key)))
    return args


def _resolve_trigger_mode(value: Any) -> TriggerMode:
    normalized = str(value or "interval").strip().lower()
    if normalized == TriggerMode.CRON.value:
        return TriggerMode.CRON
    if normalized == TriggerMode.ONCE.value:
        return TriggerMode.ONCE
    return TriggerMode.INTERVAL


def _resolve_priority(value: Any) -> TaskPriority:
    normalized = str(value or "LOW").strip().upper()
    if normalized == TaskPriority.HIGH.value:
        return TaskPriority.HIGH
    if normalized == TaskPriority.MEDIUM.value:
        return TaskPriority.MEDIUM
    return TaskPriority.LOW


def _resolve_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _build_resource_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    resource_class = str(profile.get("resource_class") or "scheduler").strip().lower() or "scheduler"
    if resource_class == "light":
        resource_class = "light_tick"
    inferred_llm = resource_class in {
        "background_llm",
        "social_light_llm",
        "proactive_llm",
        "memory_heavy",
        "browser_heavy",
        "gpu_heavy",
        "heartbeat_llm",
    }
    inferred_gpu = resource_class in {"gpu_heavy"}
    return {
        "resource_class": resource_class,
        "uses_llm": _resolve_bool(profile.get("uses_llm"), default=inferred_llm),
        "uses_gpu": _resolve_bool(profile.get("uses_gpu"), default=inferred_gpu),
        "advisory_policy": str(profile.get("advisory_policy") or "rule").strip().lower() or "rule",
    }


def _iter_registry_metas(registry: SkillRegistry) -> Iterable[SkillMeta]:
    skills = getattr(registry, "_skills", {})
    if isinstance(skills, Mapping):
        return list(skills.values())
    return []


def _should_auto_schedule(meta: SkillMeta, profile: Mapping[str, Any]) -> tuple[bool, str]:
    skill_name = str(meta.skill_name or "").strip()
    if skill_name in RETIRED_AUTO_SCHEDULE_SKILLS:
        return False, "retired auto-schedule skill"
    if not bool(getattr(meta, "scheduler_allowed", False)):
        return False, "skill is not scheduler_allowed"
    if not _as_bool(profile.get("auto_schedule"), default=False):
        return False, "profile auto_schedule is false"
    if int(getattr(meta, "risk_level", 5) or 5) >= 4:
        return False, "high-risk skills require explicit proposal approval"
    trigger_mode = str(profile.get("trigger_mode") or "manual").strip().lower()
    if trigger_mode in {"manual", "event"}:
        return False, f"trigger_mode={trigger_mode} is not periodic"
    if not str(profile.get("task_id") or "").strip():
        return False, "missing task_id"
    return True, "auto_schedule profile accepted"


def build_task_config_from_skill_meta(meta: SkillMeta) -> SchedulerTaskConfig | None:
    profile = _profile_to_dict(getattr(meta, "scheduler_profile", None))
    allowed, _reason = _should_auto_schedule(meta, profile)
    if not allowed:
        return None

    args = _resolve_args(profile)
    task_id = str(profile.get("task_id") or f"{meta.skill_name}_scheduled").strip()
    mode_allowlist = profile.get("mode_allowlist")
    if mode_allowlist is not None:
        mode_allowlist = [str(mode).strip().lower() for mode in list(mode_allowlist) if str(mode).strip()]

    return SchedulerTaskConfig(
        task_id=task_id,
        trigger_mode=_resolve_trigger_mode(profile.get("trigger_mode")),
        trigger_config=_resolve_trigger_config(profile),
        intent_template={
            "skill_name": meta.skill_name,
            "args": args,
            "proposer_node_id": "scheduler",
        },
        priority=_resolve_priority(profile.get("priority")),
        mode_allowlist=mode_allowlist,
        enabled=_as_bool(profile.get("enabled"), default=True),
        max_consecutive_failures=max(1, int(profile.get("max_consecutive_failures") or 3)),
        description=str(profile.get("description") or meta.description or task_id),
        resource_profile=_build_resource_profile(profile),
    )


def build_scheduler_advisory_prompt(*, registries: Mapping[str, SkillRegistry]) -> str:
    manifest: list[dict[str, Any]] = []
    for registry_name, registry in registries.items():
        for meta in _iter_registry_metas(registry):
            profile = _profile_to_dict(getattr(meta, "scheduler_profile", None))
            manifest.append(
                {
                    "registry": registry_name,
                    "skill_name": meta.skill_name,
                    "scheduler_allowed": bool(getattr(meta, "scheduler_allowed", False)),
                    "risk_level": int(getattr(meta, "risk_level", 5) or 5),
                    "description": str(getattr(meta, "description", "") or ""),
                    "scheduler_profile": profile,
                }
            )
    return (
        "You are Natsume SchedulingCenter advisory. Review this skill registry manifest and "
        "propose scheduling changes as JSON. Do not approve high-risk skills automatically. "
        "Output a list of {skill_name, decision, reason, proposed_profile}.\n"
        + json.dumps(manifest, ensure_ascii=False, default=str)
    )


def parse_scheduler_advisory(raw: Any) -> list[SchedulingAdvisory]:
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
    else:
        payload = raw
    if isinstance(payload, Mapping):
        payload = payload.get("advisories") or payload.get("items") or []
    advisories: list[SchedulingAdvisory] = []
    for item in list(payload or []):
        if not isinstance(item, Mapping):
            continue
        skill_name = str(item.get("skill_name") or "").strip()
        if not skill_name:
            continue
        advisories.append(
            SchedulingAdvisory(
                skill_name=skill_name,
                decision=str(item.get("decision") or "proposal").strip().lower() or "proposal",
                reason=str(item.get("reason") or "").strip(),
                proposed_profile=dict(item.get("proposed_profile") or {}),
            )
        )
    return advisories


def build_registry_driven_scheduling_plan(
    *,
    registries: Mapping[str, SkillRegistry],
    include_system_tasks: bool = True,
) -> SchedulingPlan:
    tasks: list[SchedulerTaskConfig] = []
    skipped: list[SchedulingAdvisory] = []
    seen_task_ids: set[str] = set()

    for _registry_name, registry in registries.items():
        for meta in _iter_registry_metas(registry):
            profile = _profile_to_dict(getattr(meta, "scheduler_profile", None))
            allowed, reason = _should_auto_schedule(meta, profile)
            if not allowed:
                if profile:
                    skipped.append(SchedulingAdvisory(meta.skill_name, "skip", reason, profile))
                continue
            config = build_task_config_from_skill_meta(meta)
            if config is None:
                continue
            if config.task_id in seen_task_ids:
                skipped.append(SchedulingAdvisory(meta.skill_name, "skip", "duplicate task_id", profile))
                continue
            seen_task_ids.add(config.task_id)
            tasks.append(config)

    if include_system_tasks:
        for config in build_system_scheduler_task_configs():
            if config.task_id in seen_task_ids:
                continue
            seen_task_ids.add(config.task_id)
            tasks.append(config)

    task_by_id = {task.task_id: task for task in tasks}
    ordered_tasks = [
        task_by_id.pop(task_id)
        for task_id in SCHEDULER_SEED_TASK_ORDER
        if task_id in task_by_id
    ]
    ordered_tasks.extend(task_by_id[task_id] for task_id in sorted(task_by_id))

    return SchedulingPlan(tasks=tuple(ordered_tasks), skipped=tuple(skipped))


def build_default_scheduler_registries() -> dict[str, SkillRegistry]:
    from host_brain.skills.codex_runtime_registry import build_codex_runtime_skill_registry  # noqa: PLC0415
    from host_brain.skills.personality_registry import build_personality_skill_registry  # noqa: PLC0415

    return {
        "personality": build_personality_skill_registry(),
        "codex_runtime": build_codex_runtime_skill_registry(),
    }


def build_personality_scheduler_seed_task_configs() -> list[SchedulerTaskConfig]:
    plan = build_registry_driven_scheduling_plan(
        registries=build_default_scheduler_registries(),
        include_system_tasks=True,
    )
    return list(plan.tasks)


def scheduler_seed_task_ids() -> list[str]:
    return [config.task_id for config in build_personality_scheduler_seed_task_configs()]


def build_system_scheduler_task_configs() -> list[SchedulerTaskConfig]:
    from host_brain.persona.identity import get_canonical_persona_self_id  # noqa: PLC0415

    user_id = get_canonical_persona_self_id()
    return [
        SchedulerTaskConfig(
            task_id="evolution_cycle_daily",
            trigger_mode=TriggerMode.CRON,
            trigger_config={"hour": 3, "minute": 20, "catch_up_window_minutes": 720},
            intent_template={
                "skill_name": "evolution_cycle",
                "args": {"user_id": user_id},
                "proposer_node_id": "scheduler",
            },
            priority=TaskPriority.MEDIUM,
            mode_allowlist=["normal", "sleep"],
            enabled=True,
            description="daily evolution cycle task, staggered after memory distillation",
            resource_profile={
                "resource_class": "background_llm",
                "uses_llm": True,
                "uses_gpu": False,
                "advisory_policy": "rule",
            },
        ),
        SchedulerTaskConfig(
            task_id="emotion_decay_tick",
            trigger_mode=TriggerMode.INTERVAL,
            trigger_config={"seconds": 30},
            intent_template={
                "skill_name": "emotion_decay_tick",
                "args": {},
                "proposer_node_id": "scheduler",
            },
            priority=TaskPriority.LOW,
            mode_allowlist=["normal", "focus", "gaming", "sleep"],
            enabled=True,
            description=(
                "D4/D6/D7/D8: 情绪衰减连续动态系统 tick——每 30s 驱动 LimbicAgent 情绪衰减、"
                "CircadianAgent 基线调制、InteroceptionAgent 身体预算、GlymphaticAgent 恢复窗口"
            ),
            resource_profile={
                "resource_class": "light_tick",
                "uses_llm": False,
                "uses_gpu": False,
                "advisory_policy": "rule",
            },
        ),
        SchedulerTaskConfig(
            task_id="life_memory_sleep_replay_daily",
            trigger_mode=TriggerMode.CRON,
            trigger_config={"hour": 2, "minute": 20, "catch_up_window_minutes": 720},
            intent_template={
                "skill_name": "life_memory_sleep_replay",
                "args": {"replay_limit": 12, "forgetting_batch_limit": 500},
                "proposer_node_id": "scheduler",
            },
            priority=TaskPriority.LOW,
            mode_allowlist=["normal", "sleep"],
            enabled=True,
            description="生命记忆睡眠回放：每日执行遗忘分层、低价值降温和高显著经历强化",
            resource_profile={
                "resource_class": "maintenance",
                "uses_llm": False,
                "uses_gpu": False,
                "advisory_policy": "rule",
            },
        ),
    ]


def register_scheduler_seed_tasks(
    *,
    registry: Any,
    logger: Any,
    task_configs: Iterable[SchedulerTaskConfig],
    success_message: str = "调度中心任务已注册: task_id=%s",
    failure_message: str = "调度中心任务注册失败（降级）: %s",
) -> list[str]:
    task_ids: list[str] = []
    for config in task_configs:
        try:
            if registry.get(config.task_id) is None:
                registry.register(config)
                logger.info(success_message, config.task_id)
            task_ids.append(config.task_id)
        except Exception as exc:
            logger.warning(failure_message, exc)
    return task_ids


def get_scheduled_task_config_by_skill(skill_name: str) -> SchedulerTaskConfig:
    normalized = str(skill_name or "").strip()
    for config in build_personality_scheduler_seed_task_configs():
        if str(config.intent_template.get("skill_name") or "").strip() == normalized:
            return config
    raise KeyError(f"auto-scheduled skill not found: {skill_name}")


def get_scheduled_task_config_by_task_id(task_id: str) -> SchedulerTaskConfig:
    normalized = str(task_id or "").strip()
    for config in build_personality_scheduler_seed_task_configs():
        if config.task_id == normalized:
            return config
    raise KeyError(f"auto-scheduled task not found: {task_id}")


def get_scheduled_task_configs_by_task_ids(task_ids: Iterable[str]) -> list[SchedulerTaskConfig]:
    wanted = {str(task_id or "").strip() for task_id in task_ids if str(task_id or "").strip()}
    configs = []
    for config in build_personality_scheduler_seed_task_configs():
        if config.task_id in wanted:
            configs.append(config)
    return configs
