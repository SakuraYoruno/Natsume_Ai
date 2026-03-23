"""NatsumeScheduler——定时调度器主体。

调度器触发的 Intent 必须走完整单向链（Governor→Token→Skill→Audit），
与用户请求享有相同治理。proposer_node_id = "scheduler"。

支持 cron / interval / once 三种触发模式。
"""

from __future__ import annotations

import datetime
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
from host_brain.scheduler.health_check_prompt import (
    DEFAULT_HEARTBEAT_PROMPT,
    HEARTBEAT_EVENT_TYPE_ALERT,
    HEARTBEAT_EVENT_TYPE_OK,
    LLM_UNAVAILABLE_RESPONSE,
    MAX_RESPONSE_CHARS,
    parse_heartbeat_response,
    truncate_response,
)

# TASK-0211 M23-B：导入 edge 传输层探针构建函数
try:
    from edge_client.rpc.health import build_edge_transport_probe_statuses as _build_edge_probes
    _EDGE_PROBE_AVAILABLE = True
except ImportError:
    _build_edge_probes = None  # type: ignore[assignment]
    _EDGE_PROBE_AVAILABLE = False

# edge 探针告警事件类型
EDGE_PROBE_EVENT_TYPE_ALERT = "EDGE_PROBE_ALERT"

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
        # M29-N（OPT-?）：心跳感知情感状态
        get_emotion_state: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._registry = registry
        self._health = health_reporter
        self._dispatcher = intent_dispatcher
        self._get_sys_mode = get_sys_mode or (lambda: "normal")
        self._ledger = ledger
        self._persist_tasks = persist_tasks  # F-40：是否启用任务持久化
        # M29-N：情感状态获取函数（返回 {"mood_state": ..., "emotion_tag": ...}）
        self._get_emotion_state = get_emotion_state or (lambda: {"mood_state": "steady", "emotion_tag": "NEUTRAL"})

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

        # M29-N（OPT-?）：情感状态感知门控——critical 状态下跳过非紧急任务
        emotion_state = self._get_emotion_state()
        mood_state = str(emotion_state.get("mood_state") or "steady").lower()
        if mood_state == "critical" and task.priority not in (TaskPriority.HIGH,):
            reason = "EMOTION_GATE_BLOCKED"
            self._log_scheduler_event(
                task_id=task_id,
                trigger_ts=trigger_ts,
                status="REJECTED",
                reason_code=reason,
                mode_at_trigger=current_mode,
                error_detail=f"task skipped: mood_state=critical, priority={task.priority.value}",
            )
            return {"status": "REJECTED", "reason_code": reason, "task_id": task_id, "mood_state": mood_state}

        # 生成 system_initiated_intent
        intent_id = new_intent_id()
        request_id = new_request_id()
        # M27-K：从情感状态获取函数中读取当前情感状态，传入 Intent
        emotion_state = self._get_emotion_state()
        emotion_tag = str(emotion_state.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
        mood_state_val = str(emotion_state.get("mood_state") or "steady").strip().lower() or "steady"
        system_intent = self._build_system_intent(
            task,
            intent_id=intent_id,
            request_id=request_id,
            emotion_tag=emotion_tag,
            mood_state=mood_state_val,
        )

        # 分发执行
        # 修复：agent_health_check 任务直接调用内部 _run_agent_health_check 方法，
        # 而非通过 dispatcher 分发（dispatcher 不知道如何路由到内部心跳检测逻辑）。
        result: dict[str, Any]
        if task_id == "agent_health_check" and hasattr(self, "_heartbeat_llm_caller"):
            try:
                result = self._run_agent_health_check()
                exec_status = result.get("status", "OK")
                exec_reason = result.get("reason_code")
            except Exception as exc:
                logger.error("AgentHealthCheck execution failed: %s", exc, exc_info=True)
                exec_status = "ERROR"
                exec_reason = str(exc)
                result = {"status": "ERROR", "reason_code": str(exc)}
        elif self._dispatcher:
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
        emotion_tag: str = "NEUTRAL",
        mood_state: str = "steady",
    ) -> dict[str, Any]:
        """根据任务模板生成 system_initiated_intent。

        M27-K（OPT-39）：携带情感状态字段 emotion_tag / mood_state。
        方向 B：携带 triggered_emotions 供 proactive_talk 消费。
        """
        template = dict(task.intent_template)

        # 方向 B：评估主动情感触发器
        triggered_emotions: list[dict[str, Any]] = []
        try:
            from host_brain.core.metabolism import evaluate_emotion_triggers  # noqa: PLC0415
            triggered_emotions = evaluate_emotion_triggers({
                "emotion_tag": emotion_tag,
                "mood_state": mood_state,
            })
        except Exception:
            pass

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
            # M27-K：情感状态字段
            "emotion_tag": emotion_tag,
            "mood_state": mood_state,
            # 方向 B：主动情感触发器结果
            "triggered_emotions": triggered_emotions,
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

    # ── 心跳健康检测（TASK-0200）──

    def register_agent_health_check(
        self,
        *,
        interval_seconds: int = 1800,
        heartbeat_prompt: str | None = None,
        max_response_chars: int = MAX_RESPONSE_CHARS,
        llm_caller: Any | None = None,
    ) -> None:
        """注册 AgentHealthCheck 定时任务（默认 30 分钟间隔）。

        Args:
            interval_seconds: 心跳检测间隔（秒），默认 1800（30 分钟）。
            heartbeat_prompt: 自定义 Heartbeat Prompt，None 时使用默认模板。
            max_response_chars: 回复最大字符数，超出部分截断后写入审计。
            llm_caller: 可调用对象 (prompt: str) -> str，用于向 LLM 发送心跳 Prompt。
                        None 时跳过 LLM 调用，直接写入 HEARTBEAT_ALERT（LLM 不可用降级）。
        """
        self._heartbeat_prompt = str(heartbeat_prompt or DEFAULT_HEARTBEAT_PROMPT)
        self._heartbeat_max_chars = int(max_response_chars)
        self._heartbeat_llm_caller = llm_caller

        task = SchedulerTaskConfig(
            task_id="agent_health_check",
            trigger_mode=TriggerMode.INTERVAL,
            trigger_config={"seconds": interval_seconds},
            intent_template={"skill_name": "agent_health_check"},
            priority=TaskPriority.MEDIUM,
            enabled=True,
            max_consecutive_failures=5,
        )
        # 注册任务（若已存在则跳过）
        if self._registry.get("agent_health_check") is None:
            self._registry.register(task)
        if self._running:
            self._schedule_task(task)
        logger.info("AgentHealthCheck task registered (interval=%ds)", interval_seconds)

    def _run_agent_health_check(self) -> dict[str, Any]:
        """执行一次心跳检测：发送 Prompt → 解析回复 → 静默过滤或推送告警。"""
        prompt = getattr(self, "_heartbeat_prompt", DEFAULT_HEARTBEAT_PROMPT)
        max_chars = getattr(self, "_heartbeat_max_chars", MAX_RESPONSE_CHARS)
        llm_caller = getattr(self, "_heartbeat_llm_caller", None)
        ts = now_ms()

        # 调用 LLM
        raw_response: str
        if llm_caller is not None:
            try:
                raw_response = str(llm_caller(prompt))
            except Exception as exc:
                logger.warning("AgentHealthCheck LLM call failed: %s", exc)
                raw_response = LLM_UNAVAILABLE_RESPONSE
        else:
            raw_response = LLM_UNAVAILABLE_RESPONSE

        # 解析状态
        state = parse_heartbeat_response(raw_response)
        truncated = truncate_response(raw_response, max_chars)

        if state == "OK":
            # 静默丢弃，不写入任何审计记录
            logger.debug("AgentHealthCheck: HEARTBEAT_OK — silent discard")
            # TASK-0211 M23-B：即使 LLM 心跳 OK，仍执行 edge 传输层探针检查
            self._check_edge_transport_probes(ts_ms=ts)
            return {"status": HEARTBEAT_EVENT_TYPE_OK, "ts_ms": ts}

        # 推送 HEARTBEAT_ALERT
        logger.warning("AgentHealthCheck: HEARTBEAT_ALERT — raw_response=%r", truncated)
        self._log_heartbeat_alert(raw_response=truncated, ts_ms=ts)
        # TASK-0211 M23-B：同时执行 edge 传输层探针检查
        self._check_edge_transport_probes(ts_ms=ts)
        return {"status": HEARTBEAT_EVENT_TYPE_ALERT, "raw_response": truncated, "ts_ms": ts}

    def _log_heartbeat_alert(self, *, raw_response: str, ts_ms: int) -> None:
        """将 HEARTBEAT_ALERT 写入审计 Ledger（复用 scheduler_event_log 表）。"""
        if self._ledger is None:
            return
        try:
            self._ledger.log_scheduler_event({
                "event_id": f"hb_alert_{uuid.uuid4().hex}",
                "task_id": "agent_health_check",
                "trigger_ts": ts_ms,
                "intent_id": None,
                "status": HEARTBEAT_EVENT_TYPE_ALERT,
                "reason_code": HEARTBEAT_EVENT_TYPE_ALERT,
                "mode_at_trigger": self._get_sys_mode(),
                "error_detail": raw_response,
                "ts_ms": ts_ms,
            })
        except Exception as exc:
            logger.error("Failed to log heartbeat alert: %s", exc)

    def _check_edge_transport_probes(self, *, ts_ms: int) -> dict[str, Any]:
        """TASK-0211 M23-B：执行 edge 传输层探针检查。

        调用 build_edge_transport_probe_statuses 获取 edge 节点运行时状态，
        若任意 edge 传输层探针为 DOWN，写入 EDGE_PROBE_ALERT 审计事件。

        Args:
            ts_ms: 当前时间戳（毫秒）。

        Returns:
            探针检查结果字典，含 edge_available / down_services / ts_ms。
        """
        if not _EDGE_PROBE_AVAILABLE or _build_edge_probes is None:
            logger.debug("_check_edge_transport_probes: edge_client not available, skipping")
            return {"edge_available": None, "down_services": [], "ts_ms": ts_ms}

        try:
            # 默认假设 edge 可用（available=True），探针返回 gateway UP 状态
            # 实际生产中可通过 RPC ping 检测真实可用性
            probe_statuses = _build_edge_probes(available=True)
            down_services = [
                svc_id
                for svc_id, probe in probe_statuses.items()
                if not probe.get("available", True)
            ]
            edge_available = len(down_services) == 0

            if not edge_available:
                logger.warning(
                    "AgentHealthCheck: EDGE_PROBE_ALERT — down_services=%r", down_services
                )
                self._log_edge_probe_alert(down_services=down_services, ts_ms=ts_ms)

            return {
                "edge_available": edge_available,
                "down_services": down_services,
                "ts_ms": ts_ms,
            }
        except Exception as exc:
            # 探针异常时降级处理，不崩溃心跳任务
            logger.warning("Edge transport probe check failed (degraded): %s", exc)
            return {"edge_available": None, "down_services": [], "ts_ms": ts_ms, "error": str(exc)}

    def _log_edge_probe_alert(self, *, down_services: list[str], ts_ms: int) -> None:
        """将 EDGE_PROBE_ALERT 写入审计 Ledger。"""
        if self._ledger is None:
            return
        try:
            self._ledger.log_scheduler_event({
                "event_id": f"edge_probe_alert_{uuid.uuid4().hex}",
                "task_id": "agent_health_check",
                "trigger_ts": ts_ms,
                "intent_id": None,
                "status": EDGE_PROBE_EVENT_TYPE_ALERT,
                "reason_code": EDGE_PROBE_EVENT_TYPE_ALERT,
                "mode_at_trigger": self._get_sys_mode(),
                "error_detail": f"edge transport down: {', '.join(down_services)}",
                "ts_ms": ts_ms,
            })
        except Exception as exc:
            logger.error("Failed to log edge probe alert: %s", exc)

    def list_health(self) -> list[dict[str, Any]]:
        """返回所有已注册任务的健康状态列表（含 agent_health_check）。

        Returns:
            每个任务的健康状态字典，含 task_id / status / error_count / last_run_ts。
        """
        result: list[dict[str, Any]] = []
        for task in self._registry.enabled_tasks():
            state = self._health.get_task_state(task.task_id) if hasattr(self._health, "get_task_state") else {}
            result.append({
                "task_id": task.task_id,
                "status": state.get("status", STATUS_OK) if state else STATUS_OK,
                "error_count": self._error_counts.get(task.task_id, 0),
                "last_run_ts": self._last_run_ts.get(task.task_id),
                "enabled": task.enabled,
                "trigger_mode": task.trigger_mode.value,
            })
        return result

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
