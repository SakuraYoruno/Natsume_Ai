"""调度器 Skill 包（P1 优先级）。

提供对 Scheduler 的任务管理能力（添加/取消/列出定时任务），
支撑 PRD S3 场景（主动提醒 + 定时任务 + 周期性行为）。
与 host_brain/scheduler/ 模块深度集成。

风险分级说明：
  risk_level=1  只读操作（scheduler_list）
  risk_level=2  写操作（scheduler_add）
  risk_level=3  破坏性操作（scheduler_cancel），require_confirm=True

所有 handler 均为轻量归一化层，返回标准化 payload，
由 impl/scheduler_executor.py 路由到 scheduler/ 模块执行。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.registry import SkillRegistry, skill

# 支持的触发器类型
_VALID_TRIGGER_TYPES = frozenset({
    "cron", "interval", "once", "idle", "event",
})

# 支持的任务类型（对应 skill_name）
_VALID_TASK_TYPES = frozenset({
    "diary_writer", "dream_generator", "proactive_talk",
    "notify_desktop", "notify_tts_speak", "notify_schedule_reminder",
    "focus_mode", "memory_store", "emotion_update",
    "custom",
})


class SchedulerSkillProvider:
    """调度器 Skill 集合。"""

    @skill(
        skill_name="scheduler_add",
        clearance="MEDIUM",
        risk_level=2,
        scope="system",
        reversible=True,
        description="向调度器添加定时任务（cron / interval / once / idle 触发），支持所有 skill 类型。",
    )
    def scheduler_add(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """添加调度任务。

        Args:
            task_name (str): 任务名称（必填，唯一标识）
            task_type (str): 任务类型，对应 skill_name（必填）
            trigger_type (str): 触发器类型，cron / interval / once / idle / event（必填）
            trigger_config (dict): 触发器配置（必填）
                - cron: {"cron_expr": "0 22 * * *"}（每天 22:00）
                - interval: {"interval_ms": 3600000}（每小时）
                - once: {"trigger_at": "2026-03-14T09:00:00"}（指定时间）
                - idle: {"idle_threshold_ms": 14400000}（空闲 4 小时）
                - event: {"event_type": "sleep_start"}（事件触发）
            task_args (dict): 传递给 skill 的参数（可选）
            enabled (bool): 是否立即启用（默认 True）
            max_retries (int): 失败最大重试次数（默认 0）
            priority (int): 任务优先级 1-5（默认 3）
        """
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
            "status": "OK",
            "tool": "scheduler",
            "action": "add",
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

    @skill(
        skill_name="scheduler_cancel",
        clearance="MEDIUM",
        risk_level=3,
        scope="system",
        reversible=False,
        require_confirm=True,
        description="取消/删除调度任务（不可逆，需用户确认；支持按名称或类型批量取消）。",
    )
    def scheduler_cancel(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """取消调度任务（不可逆操作，需确认）。

        Args:
            task_name (str): 要取消的任务名称（与 task_type 二选一）
            task_type (str): 按类型批量取消（与 task_name 二选一）
            dry_run (bool): 仅模拟执行，不实际取消（默认 False）
        """
        task_name = str((args or {}).get("task_name") or "").strip() or None
        task_type = str((args or {}).get("task_type") or "").strip().lower() or None
        dry_run_raw = (args or {}).get("dry_run")
        dry_run = bool(dry_run_raw) if dry_run_raw is not None else False

        return {
            "status": "OK",
            "tool": "scheduler",
            "action": "cancel",
            "task_name": task_name,
            "task_type": task_type,
            "dry_run": dry_run,
            "manifest_ref": f"scheduler_cancel::{task_name or task_type or 'unknown'}",
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
            "_context": ctx or {},
        }

    @skill(
        skill_name="scheduler_list",
        clearance="LOW",
        risk_level=1,
        scope="system",
        reversible=True,
        description="列出当前所有调度任务（支持按状态/类型过滤）。",
    )
    def scheduler_list(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """列出调度任务。

        Args:
            task_type (str): 按任务类型过滤（可选）
            enabled_only (bool): 是否只返回已启用的任务（默认 False）
            limit (int): 最多返回条数（默认 50）
        """
        task_type = str((args or {}).get("task_type") or "").strip().lower() or None
        enabled_only_raw = (args or {}).get("enabled_only")
        enabled_only = bool(enabled_only_raw) if enabled_only_raw is not None else False
        try:
            limit = max(1, min(200, int((args or {}).get("limit") or 50)))
        except (TypeError, ValueError):
            limit = 50

        return {
            "status": "OK",
            "tool": "scheduler",
            "action": "list",
            "task_type": task_type,
            "enabled_only": enabled_only,
            "limit": limit,
            "manifest_ref": "scheduler_list::all",
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "_context": ctx or {},
        }


def build_scheduler_skill_registry(provider: SchedulerSkillProvider | None = None) -> SkillRegistry:
    """构建包含调度器 Skills 的 SkillRegistry。

    Scheduler Skill 列表：
    - scheduler_add：添加调度任务（risk=2，写操作）
    - scheduler_cancel：取消调度任务（risk=3，不可逆，需确认）
    - scheduler_list：列出调度任务（risk=1，只读）

    Returns:
        已注册 3 个调度器 Skills 的 SkillRegistry 实例
    """
    registry = SkillRegistry()
    registry.register_decorated(provider or SchedulerSkillProvider())
    return registry
