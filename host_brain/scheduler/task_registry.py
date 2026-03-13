"""调度任务注册表——定义任务元数据、触发模式、优先级与 sys_mode 门控矩阵。"""

from __future__ import annotations

import enum
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── 触发模式 ──
class TriggerMode(str, enum.Enum):
    CRON = "cron"
    INTERVAL = "interval"
    ONCE = "once"


# ── 任务优先级 ──
class TaskPriority(str, enum.Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ── sys_mode 门控矩阵 ──
# key = sys_mode, value = 允许通过的最低优先级集合
# 规则：Gaming 仅允许 HIGH；Focus 仅允许 HIGH；Sleep 允许 HIGH+MEDIUM；Normal 允许全部
SYSMODE_PRIORITY_MATRIX: dict[str, set[str]] = {
    "normal": {TaskPriority.HIGH, TaskPriority.MEDIUM, TaskPriority.LOW},
    "sleep": {TaskPriority.HIGH, TaskPriority.MEDIUM},
    "gaming": {TaskPriority.HIGH},
    "focus": {TaskPriority.HIGH},
    "maintenance": set(),  # 维护模式禁止一切调度
    "locked": set(),  # 锁定模式禁止一切调度
}


@dataclass
class SchedulerTaskConfig:
    """调度任务配置——对应 task_registry 中的一条任务。"""

    task_id: str
    trigger_mode: TriggerMode
    trigger_config: dict[str, Any]  # cron: {"hour": 23, "minute": 0} / interval: {"seconds": 900} / once: {"run_date": "..."}
    intent_template: dict[str, Any]  # Intent 模板，触发时用于生成 system_initiated_intent
    priority: TaskPriority = TaskPriority.MEDIUM
    mode_allowlist: list[str] | None = None  # 显式允许的 sys_mode 列表（None 表示使用矩阵默认）
    enabled: bool = True
    max_consecutive_failures: int = 3
    description: str = ""

    def validate(self) -> None:
        """校验必填字段。"""
        if not self.task_id or not isinstance(self.task_id, str):
            raise ValueError("task_id must be non-empty string")
        if not isinstance(self.trigger_mode, TriggerMode):
            raise ValueError(f"trigger_mode must be TriggerMode, got {type(self.trigger_mode)}")
        if not isinstance(self.trigger_config, dict):
            raise ValueError("trigger_config must be dict")
        if not isinstance(self.intent_template, dict):
            raise ValueError("intent_template must be dict")
        if not isinstance(self.priority, TaskPriority):
            raise ValueError(f"priority must be TaskPriority, got {type(self.priority)}")
        if not isinstance(self.max_consecutive_failures, int) or self.max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be int >= 1")

    def is_allowed_in_mode(self, sys_mode: str) -> bool:
        """判断当前 sys_mode 是否允许此任务执行。"""
        mode = str(sys_mode).strip().lower()
        # 如果有显式 mode_allowlist，以 allowlist 为准
        if self.mode_allowlist is not None:
            return mode in [m.strip().lower() for m in self.mode_allowlist]
        # 否则查矩阵
        allowed_priorities = SYSMODE_PRIORITY_MATRIX.get(mode, set())
        return self.priority in allowed_priorities


class SchedulerTaskRegistry:
    """调度任务注册表——单例管理所有调度任务的元数据。"""

    _instance: "SchedulerTaskRegistry | None" = None
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "SchedulerTaskRegistry":
        """获取全局单例（双重检查锁，线程安全）。"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._tasks: dict[str, SchedulerTaskConfig] = {}

    def register(self, config: SchedulerTaskConfig) -> None:
        """注册一个调度任务。"""
        config.validate()
        if config.task_id in self._tasks:
            raise ValueError(f"scheduler task already registered: {config.task_id}")
        self._tasks[config.task_id] = config
        logger.info("Scheduler task registered: %s (trigger=%s, priority=%s)",
                     config.task_id, config.trigger_mode.value, config.priority.value)

    def unregister(self, task_id: str) -> SchedulerTaskConfig | None:
        """注销一个调度任务，返回被注销的配置。"""
        removed = self._tasks.pop(task_id, None)
        if removed:
            logger.info("Scheduler task unregistered: %s", task_id)
        return removed

    def get(self, task_id: str) -> SchedulerTaskConfig | None:
        return self._tasks.get(task_id)

    def require(self, task_id: str) -> SchedulerTaskConfig:
        config = self.get(task_id)
        if config is None:
            raise KeyError(f"scheduler task not found: {task_id}")
        return config

    def all_tasks(self) -> list[SchedulerTaskConfig]:
        return list(self._tasks.values())

    def enabled_tasks(self) -> list[SchedulerTaskConfig]:
        return [t for t in self._tasks.values() if t.enabled]

    def count(self) -> int:
        return len(self._tasks)
