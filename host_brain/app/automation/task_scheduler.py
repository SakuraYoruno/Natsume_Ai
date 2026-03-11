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
from typing import Any, Awaitable, Callable

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

    def register(self, task: ScheduledTask) -> None:
        """注册任务（可在启动前或运行中注册）。"""
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
        """主调度循环：每秒检查一次，触发到期任务。"""
        while self._running:
            now = time.monotonic()
            for task in list(self._tasks.values()):
                if not task.enabled:
                    continue
                if task.status == TaskStatus.RUNNING:
                    continue
                # 检查是否到执行时间
                elapsed = now - task.last_run_ts
                if elapsed < task.interval_s:
                    continue
                # 触发任务
                task._task_handle = asyncio.create_task(
                    self._run_task(task),
                    name=f"task-{task.task_id}",
                )
            await asyncio.sleep(1.0)

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
