"""调度器 Skill 包。

声明式 Provider：新增调度相关技能时，只需补一个 `DeclarativeSkillSpec`
和对应的参数归一化函数。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec
from host_brain.skills.registry import SkillRegistry

_VALID_TRIGGER_TYPES = frozenset({"cron", "interval", "once", "idle", "event"})

_VALID_TASK_TYPES = frozenset({
    "diary_writer", "dream_generator",
    "notify_desktop", "notify_tts_speak", "notify_schedule_reminder",
    "focus_mode", "memory_store",
    "custom",
})


def _normalize_scheduler_add(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    task_name = str((args or {}).get("task_name") or "").strip()
    task_type = str((args or {}).get("task_type") or "custom").strip().lower()
    if task_type not in _VALID_TASK_TYPES:
        task_type = "custom"
    trigger_type = str((args or {}).get("trigger_type") or "once").strip().lower()
    if trigger_type not in _VALID_TRIGGER_TYPES:
        trigger_type = "once"
    trigger_config = (args or {}).get("trigger_config")
    if not isinstance(trigger_config, dict):
        trigger_config = {}
    task_args = (args or {}).get("task_args")
    if not isinstance(task_args, dict):
        task_args = {}
    enabled_raw = (args or {}).get("enabled")
    enabled = bool(enabled_raw) if enabled_raw is not None else True
    try:
        max_retries = max(0, min(10, int((args or {}).get("max_retries") or 0)))
    except (TypeError, ValueError):
        max_retries = 0
    try:
        priority = max(1, min(5, int((args or {}).get("priority") or 3)))
    except (TypeError, ValueError):
        priority = 3

    return {
        "task_name": task_name,
        "task_type": task_type,
        "trigger_type": trigger_type,
        "trigger_config": trigger_config,
        "task_args": task_args,
        "enabled": enabled,
        "max_retries": max_retries,
        "priority": priority,
        "manifest_ref": f"scheduler_add::{task_name}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_scheduler_cancel(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    task_name = str((args or {}).get("task_name") or "").strip() or None
    task_type = str((args or {}).get("task_type") or "").strip().lower() or None
    dry_run_raw = (args or {}).get("dry_run")
    dry_run = bool(dry_run_raw) if dry_run_raw is not None else False

    return {
        "task_name": task_name,
        "task_type": task_type,
        "dry_run": dry_run,
        "manifest_ref": f"scheduler_cancel::{task_name or task_type or 'unknown'}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_scheduler_list(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    task_type = str((args or {}).get("task_type") or "").strip().lower() or None
    enabled_only_raw = (args or {}).get("enabled_only")
    enabled_only = bool(enabled_only_raw) if enabled_only_raw is not None else False
    try:
        limit = max(1, min(200, int((args or {}).get("limit") or 50)))
    except (TypeError, ValueError):
        limit = 50

    return {
        "task_type": task_type,
        "enabled_only": enabled_only,
        "limit": limit,
        "manifest_ref": "scheduler_list::all",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "_context": ctx or {},
    }


class SchedulerSkillProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="scheduler_add",
            clearance="MEDIUM",
            risk_level=2,
            scope="system",
            reversible=True,
            description="向调度器添加定时任务（cron / interval / once / idle 触发），支持所有 skill 类型。",
            tool="scheduler",
            action="add",
            normalize_args=_normalize_scheduler_add,
        ),
        DeclarativeSkillSpec(
            skill_name="scheduler_cancel",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=False,
            require_confirm=True,
            description="取消/删除调度任务（不可逆，需用户确认；支持按名称或类型批量取消）。",
            tool="scheduler",
            action="cancel",
            normalize_args=_normalize_scheduler_cancel,
        ),
        DeclarativeSkillSpec(
            skill_name="scheduler_list",
            clearance="LOW",
            risk_level=1,
            scope="system",
            reversible=True,
            description="列出当前所有调度任务（支持按状态/类型过滤）。",
            tool="scheduler",
            action="list",
            normalize_args=_normalize_scheduler_list,
        ),
    )


def build_scheduler_skill_registry(provider: SchedulerSkillProvider | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(provider or SchedulerSkillProvider())
    return registry
