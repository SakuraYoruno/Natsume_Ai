"""NatsumeScheduler——定时调度器主体。

调度器触发的 Intent 必须走完整单向链（Governor→Token→Skill→Audit），
与用户请求享有相同治理。proposer_node_id = "scheduler"。

支持 cron / interval / once 三种触发模式。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Callable

from host_brain.core.errors import (
    SCHED_CONDITION_UNMET,
    SCHED_NO_CONTENT,
    SCHED_TASK_SUSPENDED,
    SYSMODE_FORBIDDEN,
)
from host_brain.core.ids import now_ms, new_intent_id, new_request_id
from host_brain.scheduler.task_registry import (
    SchedulerTaskConfig,
    SchedulerTaskRegistry,
    TaskPriority,
    TriggerMode,
)
from host_brain.scheduler.health import (
    SchedulerHealthReporter,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_SUSPENDED,
)

logger = logging.getLogger(__name__)

# 调度器生成的 Intent proposer 标识
PROPOSER_NODE_ID = "scheduler"


# ── 类型别名 ──
# IntentDispatcher 接收 system_initiated_intent 字典，返回执行结果字典
IntentDispatcher = Callable[[dict[str, Any]], dict[str, Any]]

# ConditionChecker 接收 task_config 和当前 sys_mode，返回 (allowed: bool, reason_code: str | None)
ConditionChecker = Callable[[SchedulerTaskConfig, str], tuple[bool, str | None]]


class NatsumeScheduler:
    """轻量级定时调度器。

    基于 threading.Timer 实现 interval 和 once 触发；cron 通过定期检查实现。
    生产部署可替换为 APScheduler 后端，但接口不变。
    """

    def __init__(
        self,
        registry: SchedulerTaskRegistry,
        health_reporter: SchedulerHealthReporter,
        *,
        intent_dispatcher: IntentDispatcher | None = None,
        get_sys_mode: Callable[[], str] | None = None,
        ledger: Any | None = None,
        persist_tasks: bool = True,
    ) -> None:
        self._registry = registry
        self._health = health_reporter
        self._dispatcher = intent_dispatcher
        self._get_sys_mode = get_sys_mode or (lambda: "normal")
        self._ledger = ledger
        self._persist_tasks = persist_tasks  # F-40：是否启用任务持久化

        # 运行时状态
        self._running = False
        self._shutdown_event = threading.Event()
        self._timers: dict[str, threading.Timer] = {}
        self._error_counts: dict[str, int] = {}
        self._last_run_ts: dict[str, int] = {}
        self._check_thread: threading.Thread | None = None

        # cron 检查间隔（秒）
        self._cron_check_interval_s: float = 60.0

    # ── 生命周期 ──

    def start(self) -> None:
        """启动调度器。"""
        if self._running:
            logger.warning("Scheduler already running, skip start")
            return
        self._running = True
        self._shutdown_event.clear()

        # F-40 修复：重启后从 DB 恢复持久化任务
        if self._persist_tasks and self._ledger is not None:
            self._load_persisted_tasks()

        logger.info("Scheduler starting, %d enabled tasks", len(self._registry.enabled_tasks()))

        # 启动 interval/once 定时器
        for task in self._registry.enabled_tasks():
            self._schedule_task(task)

        # 启动 cron 检查线程
        self._check_thread = threading.Thread(
            target=self._cron_check_loop,
            name="sched-cron-check",
            daemon=True,
        )
        self._check_thread.start()
        logger.info("Scheduler started")

    def stop(self) -> None:
        """停止调度器。"""
        if not self._running:
            return
        self._running = False
        self._shutdown_event.set()
        # 取消所有定时器
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
        if self._check_thread and self._check_thread.is_alive():
            self._check_thread.join(timeout=5.0)
        logger.info("Scheduler stopped")

    @property
    def running(self) -> bool:
        return self._running

    # ── 任务调度 ──

    def _schedule_task(self, task: SchedulerTaskConfig) -> None:
        """根据 trigger_mode 安排定时器。"""
        if task.trigger_mode == TriggerMode.INTERVAL:
            seconds = task.trigger_config.get("seconds", 900)
            self._start_interval_timer(task.task_id, float(seconds))
        elif task.trigger_mode == TriggerMode.ONCE:
            delay = task.trigger_config.get("delay_seconds", 0)
            self._start_once_timer(task.task_id, float(delay))
        # cron 由 _cron_check_loop 处理

    def _start_interval_timer(self, task_id: str, interval_s: float) -> None:
        """启动 interval 定时器。"""
        if not self._running:
            return

        def _fire() -> None:
            self._trigger_task(task_id)
            if self._running:
                self._start_interval_timer(task_id, interval_s)

        timer = threading.Timer(interval_s, _fire)
        timer.daemon = True
        timer.name = f"sched-interval-{task_id}"
        self._timers[task_id] = timer
        timer.start()

    def _start_once_timer(self, task_id: str, delay_s: float) -> None:
        """启动 once 定时器。"""
        if not self._running:
            return
        timer = threading.Timer(delay_s, lambda: self._trigger_task(task_id))
        timer.daemon = True
        timer.name = f"sched-once-{task_id}"
        self._timers[task_id] = timer
        timer.start()

    def _cron_check_loop(self) -> None:
        """定期检查 cron 任务是否到达触发时间。"""
        import datetime

        while not self._shutdown_event.is_set():
            now = datetime.datetime.now()
            for task in self._registry.enabled_tasks():
                if task.trigger_mode != TriggerMode.CRON:
                    continue
                if self._should_cron_fire(task, now):
                    self._trigger_task(task.task_id)
            self._shutdown_event.wait(timeout=self._cron_check_interval_s)

    def _should_cron_fire(self, task: SchedulerTaskConfig, now: Any) -> bool:
        """简化 cron 匹配：检查 hour/minute 是否匹配当前时间，且本分钟内未触发过。"""
        tc = task.trigger_config
        hour = tc.get("hour")
        minute = tc.get("minute", 0)
        if hour is None:
            return False
        if now.hour != int(hour) or now.minute != int(minute):
            return False
        # 避免同一分钟重复触发
        last_ts = self._last_run_ts.get(task.task_id, 0)
        current_minute_ts = int(now.replace(second=0, microsecond=0).timestamp() * 1000)
        return last_ts < current_minute_ts

    # ── 触发执行 ──

    def trigger_task_by_id(self, task_id: str) -> dict[str, Any]:
        """外部手动触发某个任务（用于测试或 API 调用）。"""
        return self._trigger_task(task_id)

    def _trigger_task(self, task_id: str) -> dict[str, Any]:
        """触发单个调度任务：门控检查 → 生成 Intent → 分发执行 → 审计记录。"""
        trigger_ts = now_ms()
        task = self._registry.get(task_id)
        if task is None:
            logger.warning("Scheduler trigger skipped: task not found %s", task_id)
            return {"status": "SKIPPED", "reason_code": "TASK_NOT_FOUND", "task_id": task_id}

        if not task.enabled:
            return {"status": "SKIPPED", "reason_code": "TASK_DISABLED", "task_id": task_id}

        # 检查是否已暂停（连续失败超限）
        error_count = self._error_counts.get(task_id, 0)
        if error_count >= task.max_consecutive_failures:
            reason = SCHED_TASK_SUSPENDED
            self._health.update_task_state(
                task_id,
                status=STATUS_SUSPENDED,
                error_count=error_count,
                suspended_reason=reason,
                last_reason_code=reason,
            )
            self._log_scheduler_event(
                task_id=task_id,
                trigger_ts=trigger_ts,
                status="SUSPENDED",
                reason_code=reason,
                mode_at_trigger=self._get_sys_mode(),
                error_detail=f"consecutive failures: {error_count}",
            )
            return {"status": "SUSPENDED", "reason_code": reason, "task_id": task_id, "error_count": error_count}

        # sys_mode 门控
        current_mode = self._get_sys_mode()
        if not task.is_allowed_in_mode(current_mode):
            reason = SYSMODE_FORBIDDEN
            self._log_scheduler_event(
                task_id=task_id,
                trigger_ts=trigger_ts,
                status="REJECTED",
                reason_code=reason,
                mode_at_trigger=current_mode,
                error_detail=f"priority={task.priority.value} blocked in sys_mode={current_mode}",
            )
            self._health.update_task_state(
                task_id,
                status=STATUS_OK,
                last_run_ts=trigger_ts,
                last_reason_code=reason,
            )
            return {"status": "REJECTED", "reason_code": reason, "task_id": task_id, "sys_mode": current_mode}

        # 生成 system_initiated_intent
        intent_id = new_intent_id()
        request_id = new_request_id()
        system_intent = self._build_system_intent(task, intent_id=intent_id, request_id=request_id)

        # 分发执行
        result: dict[str, Any]
        if self._dispatcher:
            try:
                result = self._dispatcher(system_intent)
                exec_status = result.get("status", "OK")
                exec_reason = result.get("reason_code")
            except Exception as exc:
                logger.error("Scheduler dispatch failed for %s: %s", task_id, exc, exc_info=True)
                exec_status = "ERROR"
                exec_reason = str(exc)
                result = {"status": "ERROR", "reason_code": str(exc)}
        else:
            exec_status = "OK"
            exec_reason = None
            result = {"status": "OK", "reason_code": None, "intent": system_intent}

        # 更新错误计数
        if exec_status in ("ERROR", "FAILED"):
            self._error_counts[task_id] = error_count + 1
        else:
            self._error_counts[task_id] = 0

        self._last_run_ts[task_id] = trigger_ts

        # 更新健康状态
        new_error_count = self._error_counts.get(task_id, 0)
        task_status = STATUS_OK
        suspended_reason = None
        if new_error_count >= task.max_consecutive_failures:
            task_status = STATUS_SUSPENDED
            suspended_reason = SCHED_TASK_SUSPENDED
        elif exec_status in ("ERROR", "FAILED"):
            task_status = STATUS_ERROR

        self._health.update_task_state(
            task_id,
            status=task_status,
            last_run_ts=trigger_ts,
            error_count=new_error_count,
            suspended_reason=suspended_reason,
            last_reason_code=exec_reason,
        )

        # 审计日志
        self._log_scheduler_event(
            task_id=task_id,
            trigger_ts=trigger_ts,
            intent_id=intent_id,
            status=exec_status,
            reason_code=exec_reason,
            mode_at_trigger=current_mode,
        )

        return {
            "status": exec_status,
            "reason_code": exec_reason,
            "task_id": task_id,
            "intent_id": intent_id,
            "request_id": request_id,
            "result": result,
        }

    # ── Intent 生成 ──

    def _build_system_intent(
        self,
        task: SchedulerTaskConfig,
        *,
        intent_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        """根据任务模板生成 system_initiated_intent。"""
        template = dict(task.intent_template)
        return {
            "intent_id": intent_id,
            "request_id": request_id,
            "proposer_node_id": PROPOSER_NODE_ID,
            "task_id": task.task_id,
            "skill_name": template.get("skill_name", task.task_id),
            "args": template.get("args", {}),
            "priority": task.priority.value,
            "trigger_mode": task.trigger_mode.value,
            "ts_ms": now_ms(),
        }

    # ── 审计写入 ──

    def _log_scheduler_event(
        self,
        *,
        task_id: str,
        trigger_ts: int,
        intent_id: str | None = None,
        status: str,
        reason_code: str | None,
        mode_at_trigger: str,
        error_detail: str | None = None,
    ) -> None:
        """写入 scheduler_event_log 审计表。"""
        if self._ledger is None:
            return
        try:
            self._ledger.log_scheduler_event({
                "event_id": f"sched_evt_{uuid.uuid4().hex}",
                "task_id": task_id,
                "trigger_ts": trigger_ts,
                "intent_id": intent_id,
                "status": status,
                "reason_code": reason_code,
                "mode_at_trigger": mode_at_trigger,
                "error_detail": error_detail,
                "ts_ms": now_ms(),
            })
        except Exception as exc:
            logger.error("Failed to log scheduler event for %s: %s", task_id, exc)

    # ── 管理接口 ──

    def reset_error_count(self, task_id: str) -> None:
        """重置某任务的连续错误计数（用于手动恢复暂停任务）。"""
        self._error_counts[task_id] = 0
        self._health.update_task_state(task_id, status=STATUS_OK, error_count=0, suspended_reason=None)
        logger.info("Scheduler task %s error count reset", task_id)

    def get_error_count(self, task_id: str) -> int:
        return self._error_counts.get(task_id, 0)

    # ── 持久化接口（F-40 修复）──

    def register_task(self, task: SchedulerTaskConfig) -> None:
        """注册任务并持久化到 DB（F-40 修复：重启后可自动恢复）。

        Args:
            task: 要注册的任务配置。
        """
        self._registry.register(task)
        if self._persist_tasks and self._ledger is not None:
            self._persist_task(task)
        if self._running and task.enabled:
            self._schedule_task(task)
        logger.info("Scheduler task registered and persisted: %s", task.task_id)

    def unregister_task(self, task_id: str) -> bool:
        """注销任务并从 DB 删除持久化记录。

        Returns:
            True 如果任务存在并被注销，False 如果任务不存在。
        """
        existed = self._registry.get(task_id) is not None
        if not existed:
            return False
        # 取消定时器
        timer = self._timers.pop(task_id, None)
        if timer:
            timer.cancel()
        self._registry.unregister(task_id)
        if self._persist_tasks and self._ledger is not None:
            try:
                self._ledger.delete_persisted_scheduler_task(task_id)
            except Exception as exc:
                logger.warning("Failed to delete persisted task %s: %s", task_id, exc)
        logger.info("Scheduler task unregistered: %s", task_id)
        return True

    def _persist_task(self, task: SchedulerTaskConfig) -> None:
        """将任务配置写入 scheduler_tasks 表。"""
        try:
            self._ledger.persist_scheduler_task({
                "task_id": task.task_id,
                "task_name": getattr(task, "task_name", task.task_id),
                "trigger_mode": task.trigger_mode.value,
                "trigger_config": task.trigger_config,
                "intent_template": task.intent_template,
                "priority": task.priority.value,
                "enabled": int(task.enabled),
                "max_consecutive_failures": task.max_consecutive_failures,
                "registered_at": now_ms(),
                "registered_by": "scheduler",
            })
        except Exception as exc:
            logger.warning("Failed to persist scheduler task %s: %s", task.task_id, exc)

    def _load_persisted_tasks(self) -> None:
        """从 DB 加载持久化任务并注册到 registry（重启恢复）。"""
        try:
            rows = self._ledger.load_persisted_scheduler_tasks()
        except Exception as exc:
            logger.warning("Failed to load persisted scheduler tasks: %s", exc)
            return

        loaded = 0
        for row in rows:
            task_id = row.get("task_id")
            if not task_id:
                continue
            # 如果 registry 中已有该任务（启动时手动注册的），跳过
            if self._registry.get(task_id) is not None:
                continue
            try:
                task = SchedulerTaskConfig(
                    task_id=task_id,
                    trigger_mode=TriggerMode(row["trigger_mode"]),
                    trigger_config=row.get("trigger_config", {}),
                    intent_template=row.get("intent_template", {}),
                    priority=TaskPriority(row.get("priority", "NORMAL")),
                    enabled=bool(row.get("enabled", 1)),
                    max_consecutive_failures=int(row.get("max_consecutive_failures", 3)),
                )
                self._registry.register(task)
                loaded += 1
            except Exception as exc:
                logger.warning("Failed to restore persisted task %s: %s", task_id, exc)

        if loaded:
            logger.info("Scheduler restored %d persisted tasks from DB", loaded)
