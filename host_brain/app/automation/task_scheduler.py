"""Host 端常驻任务调度器。

管理 Host 端的后台自动化任务（游戏辅助、定时操作等）。

设计原则：
  - 任务以协程形式注册，由调度器统一管理生命周期
  - 支持 cron 风格的定时触发和事件触发两种模式
  - 任务失败自动重试，超出重试次数后告警
  - 与 Agent Loop 解耦：Agent Loop 是单次目标驱动，调度器是常驻循环
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

_logger = logging.getLogger("Natsume.host.automation.task_scheduler")


# ---------------------------------------------------------------------------
# 任务状态
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    PENDING = "PENDING"       # 等待执行
    RUNNING = "RUNNING"       # 执行中
    SUCCESS = "SUCCESS"       # 上次执行成功
    FAILED = "FAILED"         # 上次执行失败
    DISABLED = "DISABLED"     # 已禁用
    CANCELLED = "CANCELLED"   # 已取消
    BLOCKED = "BLOCKED"       # 依赖未满足，阻塞中


# ---------------------------------------------------------------------------
# Ref #97: 任务优先级
# ---------------------------------------------------------------------------

class TaskPriority(int, Enum):
    """任务优先级（数值越小优先级越高）。"""
    P0 = 0   # 最高优先级：系统关键任务（如 MemoryGuard 清理）
    P1 = 1   # 高优先级：记忆蒸馏、Canon 提案提交
    P2 = 2   # 普通优先级：游戏辅助、定时操作
    P3 = 3   # 低优先级：统计上报、日志清理


# ---------------------------------------------------------------------------
# 任务定义
# ---------------------------------------------------------------------------

TaskFn = Callable[[], Awaitable[Any]]


@dataclass
class ScheduledTask:
    """常驻调度任务定义。"""
    task_id: str
    name: str
    fn: TaskFn                          # 异步任务函数
    interval_s: float = 60.0           # 执行间隔（秒），0=只执行一次
    max_retries: int = 3               # 失败最大重试次数
    retry_delay_s: float = 5.0         # 重试间隔（秒）
    enabled: bool = True               # 是否启用
    # Ref #97: 优先级和依赖链
    priority: TaskPriority = TaskPriority.P2
    """任务优先级（P0 最高，P3 最低）"""
    depends_on: list[str] = field(default_factory=list)
    """依赖的任务 ID 列表（所有依赖任务最近一次执行成功后才触发本任务）"""
    # 运行时状态（不参与初始化）
    status: TaskStatus = field(default=TaskStatus.PENDING, init=False)
    last_run_ts: float = field(default=0.0, init=False)
    last_error: str | None = field(default=None, init=False)
    run_count: int = field(default=0, init=False)
    error_count: int = field(default=0, init=False)
    _task_handle: asyncio.Task | None = field(default=None, init=False, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "interval_s": self.interval_s,
            "max_retries": self.max_retries,
            "enabled": self.enabled,
            "priority": self.priority.value,
            "depends_on": self.depends_on,
            "status": self.status.value,
            "last_run_ts": self.last_run_ts,
            "last_error": self.last_error,
            "run_count": self.run_count,
            "error_count": self.error_count,
        }


# ---------------------------------------------------------------------------
# 调度器
# ---------------------------------------------------------------------------

class HostTaskScheduler:
    """Host 端常驻任务调度器。

    使用方式：
        scheduler = HostTaskScheduler()
        scheduler.register(ScheduledTask(
            task_id="game_helper",
            name="游戏辅助",
            fn=my_game_helper_fn,
            interval_s=5.0,
        ))
        await scheduler.start()
        # ... 运行中 ...
        await scheduler.stop()
    """

    def __init__(self) -> None:
        self._tasks: dict[str, ScheduledTask] = {}
        self._running = False
        self._scheduler_task: asyncio.Task | None = None

    @staticmethod
    def _prime_task_clock(task: ScheduledTask, *, now: float | None = None) -> None:
        if task.interval_s > 0 and task.last_run_ts <= 0:
            task.last_run_ts = float(now if now is not None else time.monotonic())

    def register(self, task: ScheduledTask) -> None:
        """注册任务（可在启动前或运行中注册）。"""
        if self._running:
            self._prime_task_clock(task)
        self._tasks[task.task_id] = task
        _logger.info("TaskScheduler: 注册任务 %s (%s)", task.task_id, task.name)

    def unregister(self, task_id: str) -> bool:
        """注销任务。"""
        if task_id not in self._tasks:
            return False
        task = self._tasks.pop(task_id)
        if task._task_handle and not task._task_handle.done():
            task._task_handle.cancel()
        _logger.info("TaskScheduler: 注销任务 %s", task_id)
        return True

    def enable(self, task_id: str) -> bool:
        """启用任务。"""
        if task_id not in self._tasks:
            return False
        self._tasks[task_id].enabled = True
        return True

    def disable(self, task_id: str) -> bool:
        """禁用任务（不取消正在运行的，下次不再触发）。"""
        if task_id not in self._tasks:
            return False
        task = self._tasks[task_id]
        task.enabled = False
        task.status = TaskStatus.DISABLED
        return True

    async def start(self) -> None:
        """启动调度器。"""
        if self._running:
            return
        bootstrap_ts = time.monotonic()
        for task in self._tasks.values():
            self._prime_task_clock(task, now=bootstrap_ts)
        self._running = True
        self._scheduler_task = asyncio.create_task(
            self._scheduler_loop(),
            name="host-task-scheduler",
        )
        _logger.info("TaskScheduler: 已启动，共 %d 个任务", len(self._tasks))

    async def stop(self) -> None:
        """停止调度器，等待所有任务完成。"""
        self._running = False
        # 取消所有任务
        for task in self._tasks.values():
            if task._task_handle and not task._task_handle.done():
                task._task_handle.cancel()
        # 取消调度循环
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        _logger.info("TaskScheduler: 已停止")

    def trigger_now(self, task_id: str) -> bool:
        """立即触发指定任务（不等待下次调度）。"""
        if task_id not in self._tasks:
            return False
        task = self._tasks[task_id]
        if not task.enabled:
            return False
        task.last_run_ts = 0.0  # 重置时间，让调度器立即触发
        return True

    def register_task(
        self,
        task_id: str,
        name: str,
        fn: TaskFn,
        interval_s: float = 60.0,
        max_retries: int = 3,
        retry_delay_s: float = 5.0,
        enabled: bool = True,
        priority: TaskPriority = TaskPriority.P2,
        depends_on: Optional[list] = None,
    ) -> ScheduledTask:
        """
        便捷注册方法（Ref #102）：直接传入参数构建 ScheduledTask 并注册。

        等价于：
            task = ScheduledTask(task_id=..., name=..., fn=..., ...)
            scheduler.register(task)

        Returns:
            已注册的 ScheduledTask 实例。
        """
        task = ScheduledTask(
            task_id=task_id,
            name=name,
            fn=fn,
            interval_s=interval_s,
            max_retries=max_retries,
            retry_delay_s=retry_delay_s,
            enabled=enabled,
            priority=priority,
            depends_on=depends_on or [],
        )
        self.register(task)
        return task

    def get_all_tasks_status(self) -> list[dict[str, Any]]:
        """
        返回所有任务的状态快照（Ref #102）。

        Returns:
            按优先级排序的任务状态列表，每项包含 task_id/name/status/priority/
            last_run_ts/run_count/error_count/last_error/depends_on 等字段。
        """
        tasks = sorted(self._tasks.values(), key=lambda t: t.priority.value)
        return [t.as_dict() for t in tasks]

    def health(self) -> dict[str, Any]:
        """返回调度器健康快照。"""
        return {
            "running": self._running,
            "task_count": len(self._tasks),
            "tasks": [t.as_dict() for t in self._tasks.values()],
        }

    # ------------------------------------------------------------------
    # 内部调度循环
    # ------------------------------------------------------------------

    async def _scheduler_loop(self) -> None:
        """主调度循环：每秒检查一次，按优先级触发到期任务（Ref #97）。"""
        while self._running:
            now = time.monotonic()
            # Ref #97: 按优先级排序（P0 最先触发）
            ready_tasks = []
            for task in list(self._tasks.values()):
                if not task.enabled:
                    continue
                if task.status == TaskStatus.RUNNING:
                    continue
                # 检查是否到执行时间
                elapsed = now - task.last_run_ts
                if elapsed < task.interval_s:
                    continue
                # Ref #97: 检查依赖链
                if not self._check_dependencies(task):
                    task.status = TaskStatus.BLOCKED
                    continue
                ready_tasks.append(task)

            # 按优先级排序（P0 最高优先级先执行）
            ready_tasks.sort(key=lambda t: t.priority.value)

            for task in ready_tasks:
                task._task_handle = asyncio.create_task(
                    self._run_task(task),
                    name=f"task-{task.task_id}",
                )
            await asyncio.sleep(1.0)

    def _check_dependencies(self, task: ScheduledTask) -> bool:
        """Ref #97: 检查任务依赖链是否满足（所有依赖任务最近一次执行成功）。"""
        if not task.depends_on:
            return True
        for dep_id in task.depends_on:
            dep_task = self._tasks.get(dep_id)
            if dep_task is None:
                _logger.warning(
                    "TaskScheduler: 任务 %s 依赖的任务 %s 不存在，视为未满足",
                    task.task_id, dep_id,
                )
                return False
            if dep_task.status != TaskStatus.SUCCESS:
                _logger.debug(
                    "TaskScheduler: 任务 %s 依赖 %s 未满足（status=%s）",
                    task.task_id, dep_id, dep_task.status,
                )
                return False
        return True

    async def _run_task(self, task: ScheduledTask) -> None:
        """执行单个任务，含重试逻辑。"""
        task.status = TaskStatus.RUNNING
        task.last_run_ts = time.monotonic()
        task.run_count += 1

        for attempt in range(task.max_retries + 1):
            try:
                await task.fn()
                task.status = TaskStatus.SUCCESS
                task.last_error = None
                _logger.debug(
                    "TaskScheduler: 任务 %s 执行成功（第 %d 次）",
                    task.task_id, task.run_count,
                )
                return
            except asyncio.CancelledError:
                task.status = TaskStatus.CANCELLED
                return
            except Exception as exc:
                task.error_count += 1
                task.last_error = str(exc)
                if attempt < task.max_retries:
                    _logger.warning(
                        "TaskScheduler: 任务 %s 失败（attempt=%d/%d），%ss 后重试: %s",
                        task.task_id, attempt + 1, task.max_retries + 1,
                        task.retry_delay_s, exc,
                    )
                    await asyncio.sleep(task.retry_delay_s)
                else:
                    _logger.error(
                        "TaskScheduler: 任务 %s 重试耗尽（%d 次），放弃: %s",
                        task.task_id, task.max_retries + 1, exc,
                    )
                    task.status = TaskStatus.FAILED


# ---------------------------------------------------------------------------
# 全局调度器单例（可选使用）
# ---------------------------------------------------------------------------

_global_scheduler: HostTaskScheduler | None = None


def get_scheduler() -> HostTaskScheduler:
    """获取全局调度器单例。"""
    global _global_scheduler
    if _global_scheduler is None:
        _global_scheduler = HostTaskScheduler()
    return _global_scheduler
