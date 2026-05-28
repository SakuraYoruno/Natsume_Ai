"""NatsumeScheduler——定时调度器主体。

调度器触发的 Intent 必须走完整单向链（Governor→Token→Skill→Audit），
与用户请求享有相同治理。proposer_node_id = "scheduler"。

支持 cron / interval / once 三种触发模式。
"""

from __future__ import annotations

import datetime
import logging
import os
import threading
import time
import uuid
from typing import Any, Callable

from host_brain.core.errors import (
    LLM_POOL_EXHAUSTED,
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
    STATUS_DEFERRED,
    SchedulerHealthReporter,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_QUEUED,
    STATUS_RETRYING,
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

_DEFAULT_ENDOCRINE_LOW_RISK_ALLOWLIST = {
    "inner_world_numeric_tick",
    "inner_world_monologue_tick",
    "silence_tick",
    "body_sense_tick",
}
_DEFAULT_BACKGROUND_LLM_TASK_IDS = {
    "proactive_talk_periodic",
    "evolution_cycle_daily",
    "diary_writer_daily",
    "dream_generator_sleep",
    "inner_world_monologue_tick",
    "gaming_companion_tick",
    "emergent_growth_weekly",
    "long_term_memory_distillation",
    "lightnovel_recommender_daily",
}
_DEFAULT_FOCUS_BACKGROUND_LLM_ALLOWLIST = {
    "agent_health_check",
}
_DEFAULT_GAMING_BACKGROUND_LLM_ALLOWLIST = {
    "agent_health_check",
    "gaming_companion_tick",
}
_DEFAULT_SLEEP_BACKGROUND_LLM_ALLOWLIST = {
    "agent_health_check",
    "diary_writer_daily",
    "dream_generator_sleep",
    "long_term_memory_distillation",
    "evolution_cycle_daily",
    "emergent_growth_weekly",
}
_DEFAULT_ENDOCRINE_PROACTIVE_TASKS = {
    "proactive_talk_periodic",
}
_DEFAULT_IMMUNE_PROACTIVE_TASKS = {
    "proactive_talk_periodic",
    "gaming_companion_tick",
}
_NON_RETRYABLE_REASON_CODES = {
    "TASK_NOT_FOUND",
    "TASK_DISABLED",
    SYSMODE_FORBIDDEN,
    SCHED_CONDITION_UNMET,
    SCHED_NO_CONTENT,
    "BACKGROUND_LLM_MODE_BLOCKED",
    "ENDOCRINE_PROACTIVE_BLOCKED",
    "ENDOCRINE_GATE_BLOCKED",
    "EMOTION_GATE_BLOCKED",
    "IMMUNE_PROACTIVE_SUPPRESSED",
}


def _parse_task_id_set(raw: str | None, *, default_values: set[str]) -> set[str]:
    text = str(raw or "").strip()
    if not text:
        return set(default_values)
    parsed: set[str] = set()
    for part in text.split(","):
        task_id = str(part or "").strip().lower()
        if task_id:
            parsed.add(task_id)
    return parsed or set(default_values)


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int_env(name: str, *, default: int, low: int, high: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return max(int(low), min(int(high), value))


def _resolve_endocrine_gate_state(emotion_state: dict[str, Any]) -> str:
    raw = emotion_state.get("endocrine_state")
    endocrine_state = dict(raw) if isinstance(raw, dict) else {}
    gate_state = str(endocrine_state.get("gate_state") or "").strip().lower()
    if gate_state in {"protective", "cautious", "open"}:
        return gate_state

    cortisol_level = _coerce_float(endocrine_state.get("cortisol_level"), default=0.0)
    energy_reserve = _coerce_float(endocrine_state.get("energy_reserve"), default=1.0)
    if cortisol_level >= 0.78 or energy_reserve <= 0.12:
        return "protective"
    if cortisol_level >= 0.62 or energy_reserve <= 0.22:
        return "cautious"
    return "open"


def _task_id_key(task_id: str | None) -> str:
    return str(task_id or "").strip().lower()


def _resolve_background_llm_mode_allowlist(*, mode: str, env_name: str, default_values: set[str]) -> set[str]:
    if str(mode or "").strip().lower() == "normal":
        return set()
    return _parse_task_id_set(os.environ.get(env_name), default_values=default_values)


def _load_runtime_immune_gate_state() -> dict[str, Any]:
    try:
        from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415

        return dict(get_immune_agent().export_runtime_state() or {})
    except Exception:
        return {}


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
        # C6：免疫门禁快照（仅用于主动任务抑制，不影响被动聊天链路）
        get_immune_gate_state: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._registry = registry
        self._health = health_reporter
        self._dispatcher = intent_dispatcher
        self._get_sys_mode = get_sys_mode or (lambda: "normal")
        self._ledger = ledger
        self._persist_tasks = persist_tasks  # F-40：是否启用任务持久化
        # M29-N：情感状态获取函数（返回 {"mood_state": ..., "emotion_tag": ...}）
        self._get_emotion_state = get_emotion_state or (lambda: {"mood_state": "steady", "emotion_tag": "NEUTRAL"})
        self._get_immune_gate_state = get_immune_gate_state or _load_runtime_immune_gate_state
        self._endocrine_low_risk_allowlist = _parse_task_id_set(
            os.environ.get("HOST_ENDOCRINE_LOW_RISK_TASK_ALLOWLIST"),
            default_values=_DEFAULT_ENDOCRINE_LOW_RISK_ALLOWLIST,
        )
        self._background_llm_task_ids = _parse_task_id_set(
            os.environ.get("HOST_SCHEDULER_BACKGROUND_LLM_TASK_IDS"),
            default_values=_DEFAULT_BACKGROUND_LLM_TASK_IDS,
        )
        self._background_llm_mode_allowlists = {
            "focus": _resolve_background_llm_mode_allowlist(
                mode="focus",
                env_name="HOST_SCHEDULER_FOCUS_LLM_TASK_ALLOWLIST",
                default_values=_DEFAULT_FOCUS_BACKGROUND_LLM_ALLOWLIST,
            ),
            "gaming": _resolve_background_llm_mode_allowlist(
                mode="gaming",
                env_name="HOST_SCHEDULER_GAMING_LLM_TASK_ALLOWLIST",
                default_values=_DEFAULT_GAMING_BACKGROUND_LLM_ALLOWLIST,
            ),
            "sleep": _resolve_background_llm_mode_allowlist(
                mode="sleep",
                env_name="HOST_SCHEDULER_SLEEP_LLM_TASK_ALLOWLIST",
                default_values=_DEFAULT_SLEEP_BACKGROUND_LLM_ALLOWLIST,
            ),
        }
        self._endocrine_proactive_tasks = _parse_task_id_set(
            os.environ.get("HOST_ENDOCRINE_PROACTIVE_TASK_IDS"),
            default_values=_DEFAULT_ENDOCRINE_PROACTIVE_TASKS,
        )
        self._immune_proactive_tasks = _parse_task_id_set(
            os.environ.get("HOST_SCHEDULER_IMMUNE_PROACTIVE_TASK_IDS"),
            default_values=_DEFAULT_IMMUNE_PROACTIVE_TASKS,
        )
        self._immune_proactive_gate_enabled = _parse_bool_env(
            "HOST_SCHEDULER_IMMUNE_PROACTIVE_GATE_ENABLED",
            default=True,
        )
        self._immune_proactive_block_on_report = _parse_bool_env(
            "HOST_SCHEDULER_IMMUNE_PROACTIVE_BLOCK_ON_REPORT",
            default=False,
        )
        self._immune_proactive_cooldown_ms = _parse_int_env(
            "HOST_SCHEDULER_IMMUNE_PROACTIVE_COOLDOWN_MS",
            default=900000,
            low=0,
            high=86_400_000,
        )
        self._resource_gate_enabled = _parse_bool_env(
            "HOST_SCHEDULER_RESOURCE_GATE_ENABLED",
            default=True,
        )

        # 运行时状态
        self._running = False
        self._shutdown_event = threading.Event()
        self._timers: dict[str, threading.Timer] = {}
        self._manual_trigger_lock = threading.Lock()
        self._manual_trigger_threads: dict[str, threading.Thread] = {}
        self._error_counts: dict[str, int] = {}
        self._last_run_ts: dict[str, int] = {}
        self._last_cron_attempt_ts: dict[str, int] = {}
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
        self._hydrate_last_run_ts_from_ledger()

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
                    scheduled_ts = self._cron_scheduled_ts(task, now)
                    if scheduled_ts is not None:
                        self._last_cron_attempt_ts[task.task_id] = scheduled_ts
                    self._trigger_task(task.task_id)
            self._shutdown_event.wait(timeout=self._cron_check_interval_s)

    def _cron_scheduled_ts(self, task: SchedulerTaskConfig, now: Any) -> int | None:
        """返回当前时间命中的计划时间戳；未命中则返回 None。

        Cron 任务不只关心精确分钟。带 catch_up_window_minutes 的任务需要在
        Host 重启后补跑最近一次错过的计划点，否则凌晨生命维护任务只要
        进程当时不在线，就会整天保持 PENDING。
        """
        tc = task.trigger_config
        hour = tc.get("hour")
        minute = tc.get("minute", 0)
        if hour is None:
            return None
        try:
            expected_hour = int(hour)
            expected_minute = int(minute)
        except (TypeError, ValueError):
            return None
        allowed_weekdays: set[int] | None = None
        day_of_week = tc.get("day_of_week")
        try:
            current_weekday = int(now.weekday())
        except Exception:
            return None
        if day_of_week is not None:
            try:
                if isinstance(day_of_week, (list, tuple, set)):
                    allowed_weekdays = {int(item) for item in day_of_week}
                else:
                    allowed_weekdays = {int(day_of_week)}
            except (TypeError, ValueError):
                return None

        matches_weekday = allowed_weekdays is None or current_weekday in allowed_weekdays
        matches_exact_minute = (
            matches_weekday
            and now.hour == expected_hour
            and now.minute == expected_minute
        )
        if matches_exact_minute:
            scheduled_at = now.replace(hour=expected_hour, minute=expected_minute, second=0, microsecond=0)
            return int(scheduled_at.timestamp() * 1000)

        catch_up_window_raw = tc.get("catch_up_window_minutes", 0)
        try:
            catch_up_window_minutes = max(0, int(catch_up_window_raw))
        except (TypeError, ValueError):
            catch_up_window_minutes = 0
        if catch_up_window_minutes <= 0:
            return None

        scheduled_at = None
        lookback_days = 7 if allowed_weekdays is not None else 1
        for days_back in range(lookback_days + 1):
            candidate_base = now - datetime.timedelta(days=days_back)
            if allowed_weekdays is not None and int(candidate_base.weekday()) not in allowed_weekdays:
                continue
            candidate = candidate_base.replace(
                hour=expected_hour,
                minute=expected_minute,
                second=0,
                microsecond=0,
            )
            if candidate <= now:
                scheduled_at = candidate
                break

        if scheduled_at is None:
            return None

        catch_up_until = scheduled_at + datetime.timedelta(minutes=catch_up_window_minutes)
        if not (scheduled_at <= now <= catch_up_until):
            return None

        return int(scheduled_at.timestamp() * 1000)

    def _should_cron_fire(self, task: SchedulerTaskConfig, now: Any) -> bool:
        """简化 cron 匹配：检查 weekday/hour/minute，支持声明式补跑窗口。"""
        scheduled_ts = self._cron_scheduled_ts(task, now)
        if scheduled_ts is None:
            return False

        # 避免同一次计划触发重复执行；补跑窗口内也只补一次。
        last_ts = max(
            self._last_run_ts.get(task.task_id, 0),
            self._last_cron_attempt_ts.get(task.task_id, 0),
        )
        return last_ts < scheduled_ts

    def _hydrate_last_run_ts_from_ledger(self) -> None:
        """进程重启后从审计账本恢复最近触发时间，避免补跑窗口重复触发。"""
        if self._ledger is None:
            return
        loader = getattr(self._ledger, "load_latest_scheduler_event_trigger_ts", None)
        if not callable(loader):
            return
        task_ids = [task.task_id for task in self._registry.enabled_tasks()]
        try:
            latest = loader(task_ids)
        except Exception as exc:
            logger.warning("Scheduler last-run hydration skipped: %s", exc)
            return
        if not isinstance(latest, dict):
            return
        restored = 0
        for task_id, raw_ts in latest.items():
            try:
                trigger_ts = int(raw_ts)
            except (TypeError, ValueError):
                continue
            if trigger_ts <= 0:
                continue
            current = int(self._last_run_ts.get(str(task_id), 0) or 0)
            if trigger_ts > current:
                task_id_text = str(task_id)
                self._last_run_ts[task_id_text] = trigger_ts
                self._health.update_task_state(
                    task_id_text,
                    status=STATUS_OK,
                    last_run_ts=trigger_ts,
                    error_count=self._error_counts.get(task_id_text, 0),
                    last_gate_state="ledger_hydrated",
                )
                restored += 1
        if restored:
            logger.info("Scheduler restored last_run_ts for %d tasks from ledger", restored)

    # ── 触发执行 ──

    def trigger_task_by_id(self, task_id: str) -> dict[str, Any]:
        """外部手动触发某个任务（用于测试或 API 调用）。"""
        return self._trigger_task(task_id)

    def trigger_task_by_id_async(self, task_id: str) -> dict[str, Any]:
        """异步手动触发某个任务，立即返回入队状态。

        REST API 使用这个入口，避免把 LLM、浏览器、技能执行等长任务跑在
        FastAPI 请求线程里，导致 /health 和 /scheduler/health 一起阻塞。
        """
        trigger_ts = now_ms()
        task = self._registry.get(task_id)
        if task is None:
            logger.warning("Scheduler async trigger skipped: task not found %s", task_id)
            return {"status": "SKIPPED", "reason_code": "TASK_NOT_FOUND", "task_id": task_id}
        if not task.enabled:
            return {"status": "SKIPPED", "reason_code": "TASK_DISABLED", "task_id": task_id}

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
            return {"status": "SUSPENDED", "reason_code": reason, "task_id": task_id, "error_count": error_count}

        request_id = new_request_id()

        with self._manual_trigger_lock:
            existing = self._manual_trigger_threads.get(task_id)
            if existing is not None and existing.is_alive():
                return {
                    "status": STATUS_QUEUED,
                    "reason_code": "SCHED_TASK_ALREADY_QUEUED",
                    "task_id": task_id,
                    "request_id": request_id,
                    "queued_ts": trigger_ts,
                }

            def _run_manual_trigger() -> None:
                try:
                    result = self._trigger_task(task_id)
                    logger.info(
                        "Scheduler async manual trigger completed: task_id=%s status=%s reason=%s",
                        task_id,
                        result.get("status"),
                        result.get("reason_code"),
                    )
                except Exception as exc:  # pragma: no cover - defensive background guard
                    logger.error("Scheduler async manual trigger crashed for %s: %s", task_id, exc, exc_info=True)
                    self._error_counts[task_id] = self._error_counts.get(task_id, 0) + 1
                    self._health.update_task_state(
                        task_id,
                        status=STATUS_ERROR,
                        error_count=self._error_counts.get(task_id, 0),
                        last_reason_code=str(exc),
                    )
                finally:
                    with self._manual_trigger_lock:
                        current = self._manual_trigger_threads.get(task_id)
                        if current is threading.current_thread():
                            self._manual_trigger_threads.pop(task_id, None)

            thread = threading.Thread(
                target=_run_manual_trigger,
                name=f"sched-manual-trigger-{task_id}",
                daemon=True,
            )
            self._manual_trigger_threads[task_id] = thread
            self._health.update_task_state(
                task_id,
                status=STATUS_QUEUED,
                last_run_ts=None,
                next_run_ts=trigger_ts,
                error_count=error_count,
            )
            thread.start()

        return {
            "status": STATUS_QUEUED,
            "reason_code": None,
            "task_id": task_id,
            "request_id": request_id,
            "queued_ts": trigger_ts,
        }

    def _is_background_llm_task(self, task_id: str) -> bool:
        return _task_id_key(task_id) in self._background_llm_task_ids

    def _task_resource_profile(self, task: SchedulerTaskConfig) -> dict[str, Any]:
        profile = getattr(task, "resource_profile", None)
        return dict(profile or {}) if isinstance(profile, dict) else {}

    def _task_resource_class(self, task: SchedulerTaskConfig) -> str:
        profile = self._task_resource_profile(task)
        return str(profile.get("resource_class") or "").strip().lower()

    def _task_uses_llm(self, task: SchedulerTaskConfig) -> bool:
        profile = self._task_resource_profile(task)
        raw = profile.get("uses_llm")
        if isinstance(raw, bool):
            return raw
        resource_class = self._task_resource_class(task)
        if resource_class in {"background_llm", "social_light_llm", "proactive_llm", "memory_heavy", "browser_heavy", "gpu_heavy", "heartbeat_llm"}:
            return True
        return self._is_background_llm_task(task.task_id)

    def _task_uses_gpu(self, task: SchedulerTaskConfig) -> bool:
        profile = self._task_resource_profile(task)
        raw = profile.get("uses_gpu")
        if isinstance(raw, bool):
            return raw
        resource_class = self._task_resource_class(task)
        if resource_class in {"gpu_heavy"}:
            return True
        return _task_id_key(task.task_id) in {"gaming_companion_tick"}

    def _is_background_llm_allowed_in_mode(self, *, task_id: str, sys_mode: str) -> bool:
        mode = str(sys_mode or "").strip().lower() or "normal"
        if mode == "normal":
            return True
        allowlist = self._background_llm_mode_allowlists.get(mode, set())
        if not allowlist:
            return False
        return _task_id_key(task_id) in allowlist

    def _resource_gate_task_type(self, *, task: SchedulerTaskConfig, uses_llm: bool) -> str:
        resource_class = self._task_resource_class(task)
        if resource_class:
            return resource_class
        task_id = task.task_id
        task_key = _task_id_key(task_id)
        if uses_llm:
            return "background_llm"
        if task_key in {"inner_world_numeric_tick", "silence_tick", "body_sense_tick", "emotion_decay_tick"}:
            return "light_tick"
        if task_key in {"long_term_memory_distillation", "distillation_engine_periodic"}:
            return "memory_heavy"
        return "scheduler"

    def _retry_timer_key(self, task_id: str) -> str:
        return f"retry:{task_id}"

    def _task_retry_policy(self, task: SchedulerTaskConfig) -> dict[str, Any]:
        policy: dict[str, Any] = {}
        trigger_policy = task.trigger_config.get("retry_policy") if isinstance(task.trigger_config, dict) else None
        resource_policy = self._task_resource_profile(task).get("retry_policy")
        if isinstance(trigger_policy, dict):
            policy.update(trigger_policy)
        if isinstance(resource_policy, dict):
            policy.update(resource_policy)

        enabled = _parse_bool_env("HOST_SCHEDULER_RETRY_ENABLED", default=True)
        if "enabled" in policy:
            enabled = bool(policy.get("enabled"))
        max_retries = _parse_int_env("HOST_SCHEDULER_RETRY_MAX_RETRIES", default=2, low=0, high=10)
        initial_delay_s = _parse_int_env("HOST_SCHEDULER_RETRY_INITIAL_DELAY_S", default=60, low=1, high=3600)
        max_delay_s = _parse_int_env("HOST_SCHEDULER_RETRY_MAX_DELAY_S", default=600, low=1, high=7200)
        backoff = _coerce_float(os.environ.get("HOST_SCHEDULER_RETRY_BACKOFF"), default=2.0)
        try:
            max_retries = max(0, min(10, int(policy.get("max_retries", max_retries))))
        except (TypeError, ValueError):
            max_retries = max(0, min(10, max_retries))
        try:
            initial_delay_s = max(1, min(3600, int(policy.get("initial_delay_s", policy.get("retry_delay_s", initial_delay_s)))))
        except (TypeError, ValueError):
            initial_delay_s = max(1, min(3600, initial_delay_s))
        try:
            max_delay_s = max(1, min(7200, int(policy.get("max_delay_s", max_delay_s))))
        except (TypeError, ValueError):
            max_delay_s = max(1, min(7200, max_delay_s))
        try:
            backoff = max(1.0, min(8.0, float(policy.get("backoff", backoff))))
        except (TypeError, ValueError):
            backoff = max(1.0, min(8.0, backoff))
        return {
            "enabled": enabled,
            "max_retries": max_retries,
            "initial_delay_s": initial_delay_s,
            "max_delay_s": max_delay_s,
            "backoff": backoff,
        }

    def _has_pending_retry(self, task_id: str) -> bool:
        timer = self._timers.get(self._retry_timer_key(task_id))
        return bool(timer is not None and timer.is_alive())

    def _is_retryable_execution_result(self, *, status: str, reason_code: str | None) -> bool:
        status_token = str(status or "").strip().upper()
        reason_token = str(reason_code or "").strip()
        if reason_token in _NON_RETRYABLE_REASON_CODES:
            return False
        if reason_token == LLM_POOL_EXHAUSTED:
            return True
        return status_token in {"ERROR", "FAILED"}

    def _schedule_task_retry(
        self,
        *,
        task: SchedulerTaskConfig,
        trigger_ts: int,
        current_mode: str,
        retry_attempt: int,
        reason_code: str | None,
        source_status: str,
        retry_after_s: int | None = None,
        error_detail: str | None = None,
    ) -> dict[str, Any] | None:
        policy = self._task_retry_policy(task)
        max_retries = int(policy.get("max_retries") or 0)
        if not bool(policy.get("enabled")) or max_retries <= 0 or retry_attempt >= max_retries or not self._running:
            return None

        next_attempt = retry_attempt + 1
        if retry_after_s is not None and retry_after_s > 0:
            delay_s = int(retry_after_s)
        else:
            delay_s = int(float(policy["initial_delay_s"]) * (float(policy["backoff"]) ** max(0, retry_attempt)))
        delay_s = max(1, min(int(policy["max_delay_s"]), delay_s))
        next_run_ts = now_ms() + delay_s * 1000
        timer_key = self._retry_timer_key(task.task_id)
        existing = self._timers.get(timer_key)
        if existing is not None:
            existing.cancel()

        def _retry_fire() -> None:
            try:
                self._trigger_task(task.task_id, retry_attempt=next_attempt)
            finally:
                timer = self._timers.get(timer_key)
                if timer is threading.current_thread():
                    self._timers.pop(timer_key, None)

        timer = threading.Timer(delay_s, _retry_fire)
        timer.daemon = True
        timer.name = f"sched-retry-{task.task_id}"
        self._timers[timer_key] = timer
        self._log_scheduler_event(
            task_id=task.task_id,
            trigger_ts=trigger_ts,
            status=STATUS_RETRYING,
            reason_code=reason_code,
            mode_at_trigger=current_mode,
            error_detail=(
                error_detail
                or f"retry scheduled: attempt={next_attempt}/{max_retries}, delay_s={delay_s}, source_status={source_status}"
            ),
        )
        current_state = self._health.get_task_state(task.task_id)
        self._health.update_task_state(
            task.task_id,
            status=STATUS_RETRYING,
            last_run_ts=current_state.get("last_run_ts") or self._last_run_ts.get(task.task_id),
            next_run_ts=next_run_ts,
            error_count=self._error_counts.get(task.task_id, 0),
            retry_count=next_attempt,
            last_reason_code=reason_code,
            last_retry_reason_code=reason_code,
            last_gate_state=current_state.get("last_gate_state"),
            last_mood_state=current_state.get("last_mood_state"),
            last_endocrine_cortisol=current_state.get("last_endocrine_cortisol"),
            last_endocrine_energy=current_state.get("last_endocrine_energy"),
            last_immune_decision=current_state.get("last_immune_decision"),
            last_immune_risk_level=current_state.get("last_immune_risk_level"),
            last_immune_reason_codes=list(current_state.get("last_immune_reason_codes") or []),
        )
        timer.start()
        return {
            "status": STATUS_RETRYING,
            "reason_code": reason_code,
            "task_id": task.task_id,
            "scheduler_decision": "retry",
            "retry_attempt": next_attempt,
            "max_retries": max_retries,
            "retry_after_s": delay_s,
            "next_run_ts": next_run_ts,
        }

    def _schedule_resource_gate_retry(self, *, task: SchedulerTaskConfig, retry_after_s: int) -> int | None:
        if retry_after_s <= 0 or task.trigger_mode == TriggerMode.INTERVAL or not self._running:
            return None
        delay_s = max(30, min(3600, int(retry_after_s)))
        timer_key = f"resource-retry:{task.task_id}"
        existing = self._timers.get(timer_key)
        if existing is not None:
            existing.cancel()
        timer = threading.Timer(delay_s, lambda: self._trigger_task(task.task_id))
        timer.daemon = True
        timer.name = f"sched-resource-retry-{task.task_id}"
        self._timers[timer_key] = timer
        timer.start()
        return now_ms() + delay_s * 1000

    def _resource_gate_task(
        self,
        *,
        task: SchedulerTaskConfig,
        trigger_ts: int,
        current_mode: str,
        error_count: int,
        retry_attempt: int = 0,
    ) -> dict[str, Any] | None:
        if not self._resource_gate_enabled:
            return None
        try:
            from host_brain.app.dependencies import get_resource_manager  # noqa: PLC0415

            manager = get_resource_manager(create_if_missing=False)
        except Exception as exc:
            logger.debug("scheduler resource gate unavailable: %s", exc)
            return None
        if manager is None or not hasattr(manager, "assess_background_task"):
            return None

        uses_llm = self._task_uses_llm(task)
        uses_gpu = self._task_uses_gpu(task)
        task_type = self._resource_gate_task_type(task=task, uses_llm=uses_llm)
        decision = dict(
            manager.assess_background_task(
                task_id=task.task_id,
                priority=task.priority.value,
                task_type=task_type,
                uses_llm=uses_llm,
                uses_gpu=uses_gpu,
                reason="scheduler",
            )
            or {}
        )
        if bool(decision.get("allowed", True)):
            return None

        reason = str(decision.get("reason_code") or "RESOURCE_PRESSURE_DEFERRED")
        retry_after_s = int(decision.get("retry_after_s") or 0)
        next_run_ts = trigger_ts + retry_after_s * 1000 if retry_after_s > 0 else None
        pressure_status = str(decision.get("pressure_status") or "UNKNOWN").lower()
        detail = (
            f"task deferred by resource gate: pressure={pressure_status}, "
            f"retry_after_s={retry_after_s}, task_type={task_type}, priority={task.priority.value}"
        )
        self._log_scheduler_event(
            task_id=task.task_id,
            trigger_ts=trigger_ts,
            status=STATUS_DEFERRED,
            reason_code=reason,
            mode_at_trigger=current_mode,
            error_detail=detail,
        )
        current_state = self._health.get_task_state(task.task_id)
        self._health.update_task_state(
            task.task_id,
            status=STATUS_DEFERRED,
            last_run_ts=current_state.get("last_run_ts") or self._last_run_ts.get(task.task_id),
            next_run_ts=next_run_ts,
            error_count=error_count,
            last_reason_code=reason,
            last_gate_state=f"resource:{pressure_status}",
        )
        retry_result = self._schedule_task_retry(
            task=task,
            trigger_ts=trigger_ts,
            current_mode=current_mode,
            retry_attempt=retry_attempt,
            reason_code=reason,
            source_status=STATUS_DEFERRED,
            retry_after_s=retry_after_s if retry_after_s > 0 else None,
            error_detail=f"resource gate retry scheduled after defer: pressure={pressure_status}, retry_after_s={retry_after_s}",
        )
        if retry_result is not None:
            retry_result["resource_gate"] = decision
            return retry_result
        return {
            "status": STATUS_DEFERRED,
            "reason_code": reason,
            "task_id": task.task_id,
            "scheduler_decision": "defer",
            "retry_after_s": retry_after_s,
            "resource_gate": decision,
            "next_run_ts": next_run_ts,
        }

    def _trigger_task(self, task_id: str, *, retry_attempt: int = 0) -> dict[str, Any]:
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
                last_gate_state="sys_mode",
            )
            return {"status": "REJECTED", "reason_code": reason, "task_id": task_id, "sys_mode": current_mode}

        if self._task_uses_llm(task) and not self._is_background_llm_allowed_in_mode(
            task_id=task.task_id,
            sys_mode=current_mode,
        ):
            reason = "BACKGROUND_LLM_MODE_BLOCKED"
            detail = (
                f"background llm task skipped in sys_mode={current_mode}; "
                f"allowlist={sorted(self._background_llm_mode_allowlists.get(current_mode, set()))}"
            )
            self._log_scheduler_event(
                task_id=task_id,
                trigger_ts=trigger_ts,
                status="SKIPPED",
                reason_code=reason,
                mode_at_trigger=current_mode,
                error_detail=detail,
            )
            self._health.update_task_state(
                task_id,
                status=STATUS_OK,
                last_run_ts=trigger_ts,
                error_count=error_count,
                last_reason_code=reason,
                last_gate_state=f"llm_mode:{current_mode}",
            )
            return {
                "status": "SKIPPED",
                "reason_code": reason,
                "task_id": task_id,
                "sys_mode": current_mode,
                "scheduler_decision": "skip",
            }

        resource_gate_result = self._resource_gate_task(
            task=task,
            trigger_ts=trigger_ts,
            current_mode=current_mode,
            error_count=error_count,
            retry_attempt=retry_attempt,
        )
        if resource_gate_result is not None:
            return resource_gate_result

        # M29-N（OPT-?）：情感状态感知门控
        # - 主动任务：优先按 endocrine gate_state 判定（cautious/protective 均收敛）
        # - 低风险白名单：允许在 protective 态继续运行（维持内在连续性）
        # - 其他任务：保持原有 critical/protective 非紧急阻断策略
        emotion_state = dict(self._get_emotion_state() or {})
        mood_state = str(emotion_state.get("mood_state") or "steady").lower()
        gate_state = _resolve_endocrine_gate_state(dict(emotion_state or {}))
        endocrine_state_for_gate = (
            dict(emotion_state.get("endocrine_state") or {})
            if isinstance(emotion_state.get("endocrine_state"), dict)
            else {}
        )
        endocrine_cortisol = _coerce_float(endocrine_state_for_gate.get("cortisol_level"), default=0.0)
        endocrine_energy = _coerce_float(endocrine_state_for_gate.get("energy_reserve"), default=0.0)
        task_key = _task_id_key(task.task_id)
        is_non_urgent = task.priority not in (TaskPriority.HIGH,)
        is_low_risk_allowlisted = task_key in self._endocrine_low_risk_allowlist
        is_proactive_task = task_key in self._endocrine_proactive_tasks
        is_immune_proactive_task = task_key in self._immune_proactive_tasks
        immune_decision: str | None = None
        immune_risk_level: str | None = None
        immune_reason_codes: list[str] = []

        if (
            self._immune_proactive_gate_enabled
            and is_non_urgent
            and is_immune_proactive_task
        ):
            immune_runtime_state: dict[str, Any] = {}
            try:
                immune_runtime_state = dict(self._get_immune_gate_state() or {})
            except Exception as exc:
                logger.debug("scheduler immune gate degraded: %s", exc)
                immune_runtime_state = {}

            last_inspection = (
                dict(immune_runtime_state.get("last_inspection") or {})
                if isinstance(immune_runtime_state.get("last_inspection"), dict)
                else {}
            )
            immune_decision = str(last_inspection.get("decision") or "").strip().upper() or None
            immune_risk_level = str(last_inspection.get("risk_level") or "").strip().lower() or None
            immune_reason_codes = [
                str(code).strip()
                for code in list(last_inspection.get("reason_codes") or [])
                if str(code).strip()
            ]
            immune_ts_ms = _coerce_int(last_inspection.get("ts_ms"), default=0)
            immune_age_ms = (trigger_ts - immune_ts_ms) if immune_ts_ms > 0 else None
            report_as_block = (
                immune_decision == "REPORT"
                and self._immune_proactive_block_on_report
                and immune_risk_level in {"high", "critical"}
            )
            should_block_by_immune = immune_decision == "BLOCK" or report_as_block
            is_recent_immune_signal = (
                immune_age_ms is None
                or self._immune_proactive_cooldown_ms <= 0
                or immune_age_ms <= self._immune_proactive_cooldown_ms
            )
            if should_block_by_immune and is_recent_immune_signal:
                reason = "IMMUNE_PROACTIVE_SUPPRESSED"
                immune_gate_state = f"immune:{str(immune_decision or 'BLOCK').lower()}"
                self._log_scheduler_event(
                    task_id=task_id,
                    trigger_ts=trigger_ts,
                    status="REJECTED",
                    reason_code=reason,
                    mode_at_trigger=current_mode,
                    error_detail=(
                        f"task skipped by immune proactive gate: task_id={task.task_id}, "
                        f"immune_decision={immune_decision}, immune_risk={immune_risk_level}, "
                        f"immune_age_ms={immune_age_ms}, cooldown_ms={self._immune_proactive_cooldown_ms}"
                    ),
                )
                self._health.update_task_state(
                    task_id,
                    status=STATUS_OK,
                    last_run_ts=trigger_ts,
                    error_count=error_count,
                    last_reason_code=reason,
                    last_gate_state=immune_gate_state,
                    last_mood_state=mood_state,
                    last_endocrine_cortisol=endocrine_cortisol,
                    last_endocrine_energy=endocrine_energy,
                    last_immune_decision=immune_decision,
                    last_immune_risk_level=immune_risk_level,
                    last_immune_reason_codes=immune_reason_codes,
                )
                return {
                    "status": "REJECTED",
                    "reason_code": reason,
                    "task_id": task_id,
                    "mood_state": mood_state,
                    "gate_state": immune_gate_state,
                    "immune_decision": immune_decision,
                    "immune_risk_level": immune_risk_level,
                    "immune_reason_codes": immune_reason_codes,
                }

        if is_non_urgent and is_proactive_task and gate_state in {"cautious", "protective"}:
            reason = "ENDOCRINE_PROACTIVE_BLOCKED"
            self._log_scheduler_event(
                task_id=task_id,
                trigger_ts=trigger_ts,
                status="REJECTED",
                reason_code=reason,
                mode_at_trigger=current_mode,
                error_detail=(
                    f"task skipped by endocrine proactive gate: task_id={task.task_id}, "
                    f"gate_state={gate_state}, mood_state={mood_state}, priority={task.priority.value}"
                ),
            )
            self._health.update_task_state(
                task_id,
                status=STATUS_OK,
                last_run_ts=trigger_ts,
                error_count=error_count,
                last_reason_code=reason,
                last_gate_state=gate_state,
                last_mood_state=mood_state,
                last_endocrine_cortisol=endocrine_cortisol,
                last_endocrine_energy=endocrine_energy,
                last_immune_decision=immune_decision,
                last_immune_risk_level=immune_risk_level,
                last_immune_reason_codes=immune_reason_codes,
            )
            return {
                "status": "REJECTED",
                "reason_code": reason,
                "task_id": task_id,
                "mood_state": mood_state,
                "gate_state": gate_state,
            }
        if (
            is_non_urgent
            and not is_low_risk_allowlisted
            and not is_proactive_task
            and (mood_state == "critical" or gate_state == "protective")
        ):
            reason = "EMOTION_GATE_BLOCKED" if mood_state == "critical" else "ENDOCRINE_GATE_BLOCKED"
            self._log_scheduler_event(
                task_id=task_id,
                trigger_ts=trigger_ts,
                status="REJECTED",
                reason_code=reason,
                mode_at_trigger=current_mode,
                error_detail=(
                    f"task skipped: mood_state={mood_state}, gate_state={gate_state}, "
                    f"priority={task.priority.value}"
                ),
            )
            self._health.update_task_state(
                task_id,
                status=STATUS_OK,
                last_run_ts=trigger_ts,
                error_count=error_count,
                last_reason_code=reason,
                last_gate_state=gate_state,
                last_mood_state=mood_state,
                last_endocrine_cortisol=endocrine_cortisol,
                last_endocrine_energy=endocrine_energy,
                last_immune_decision=immune_decision,
                last_immune_risk_level=immune_risk_level,
                last_immune_reason_codes=immune_reason_codes,
            )
            return {
                "status": "REJECTED",
                "reason_code": reason,
                "task_id": task_id,
                "mood_state": mood_state,
                "gate_state": gate_state,
            }

        # 生成 system_initiated_intent
        intent_id = new_intent_id()
        request_id = new_request_id()
        # M27-K：从情感状态获取函数中读取当前情感状态，传入 Intent
        emotion_state = dict(emotion_state or self._get_emotion_state() or {})
        emotion_tag = str(emotion_state.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
        mood_state_val = str(emotion_state.get("mood_state") or "steady").strip().lower() or "steady"
        endocrine_state = (
            dict(emotion_state.get("endocrine_state") or {})
            if isinstance(emotion_state.get("endocrine_state"), dict)
            else {}
        )
        system_intent = self._build_system_intent(
            task,
            intent_id=intent_id,
            request_id=request_id,
            sys_mode=current_mode,
            emotion_tag=emotion_tag,
            mood_state=mood_state_val,
            endocrine_state=endocrine_state,
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

        if self._task_uses_llm(task):
            nested_error = str(result.get("error") or "").strip()
            if str(exec_reason or "").strip() == LLM_POOL_EXHAUSTED or LLM_POOL_EXHAUSTED in nested_error:
                exec_status = "SKIPPED"
                exec_reason = LLM_POOL_EXHAUSTED
                result["status"] = "SKIPPED"
                result["reason_code"] = LLM_POOL_EXHAUSTED
                result.setdefault("scheduler_decision", "skip")

        if self._is_retryable_execution_result(status=str(exec_status), reason_code=exec_reason):
            retry_result = self._schedule_task_retry(
                task=task,
                trigger_ts=trigger_ts,
                current_mode=current_mode,
                retry_attempt=retry_attempt,
                reason_code=str(exec_reason or "SCHED_EXECUTION_FAILED"),
                source_status=str(exec_status or "ERROR"),
                error_detail=(
                    f"execution retry scheduled: attempt={retry_attempt + 1}, "
                    f"source_status={exec_status}, reason={exec_reason}"
                ),
            )
            if retry_result is not None:
                retry_result["intent_id"] = intent_id
                retry_result["request_id"] = request_id
                retry_result["result"] = result
                return retry_result

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
            retry_count=0,
            suspended_reason=suspended_reason,
            last_reason_code=exec_reason,
            last_retry_reason_code=None,
            last_gate_state=gate_state,
            last_mood_state=mood_state_val,
            last_endocrine_cortisol=_coerce_float(endocrine_state.get("cortisol_level"), default=0.0),
            last_endocrine_energy=_coerce_float(endocrine_state.get("energy_reserve"), default=0.0),
            last_immune_decision=immune_decision,
            last_immune_risk_level=immune_risk_level,
            last_immune_reason_codes=immune_reason_codes,
        )

        # 审计日志
        event_detail = None
        if exec_reason == LLM_POOL_EXHAUSTED:
            event_detail = "decision=skip reason=LLM_POOL_EXHAUSTED"
        elif exec_reason or str(exec_status or "").upper() in {"REJECTED", "SKIPPED", "ERROR", "FAILED", "DEGRADED"}:
            detail_text = str(result.get("detail") or result.get("error") or "").strip()
            if detail_text:
                event_detail = detail_text[:500]
        self._log_scheduler_event(
            task_id=task_id,
            trigger_ts=trigger_ts,
            intent_id=intent_id,
            status=exec_status,
            reason_code=exec_reason,
            mode_at_trigger=current_mode,
            error_detail=event_detail,
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
        sys_mode: str = "normal",
        emotion_tag: str = "NEUTRAL",
        mood_state: str = "steady",
        endocrine_state: dict[str, Any] | None = None,
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
            "sys_mode": str(sys_mode or "normal").strip().lower() or "normal",
            # M27-K：情感状态字段
            "emotion_tag": emotion_tag,
            "mood_state": mood_state,
            "endocrine_state": dict(endocrine_state or {}),
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
        event_id = f"sched_evt_{uuid.uuid4().hex}"
        ts_value = now_ms()
        try:
            self._ledger.log_scheduler_event({
                "event_id": event_id,
                "task_id": task_id,
                "trigger_ts": trigger_ts,
                "intent_id": intent_id,
                "status": status,
                "reason_code": reason_code,
                "mode_at_trigger": mode_at_trigger,
                "error_detail": error_detail,
                "ts_ms": ts_value,
            })
        except Exception as exc:
            logger.error("Failed to log scheduler event for %s: %s", task_id, exc)
            return
        try:
            from host_brain.app.observability_persist import persist_background_event_memory  # noqa: PLC0415

            persist_background_event_memory(
                event_source="scheduler",
                event_id=event_id,
                request_id=event_id,
                intent_id=intent_id,
                task_id=task_id,
                status=status,
                reason_code=reason_code,
                detail=error_detail,
                ts_ms=ts_value,
                metadata={
                    "mode_at_trigger": mode_at_trigger,
                    "trigger_ts": trigger_ts,
                },
            )
        except Exception as exc:
            logger.debug("scheduler event memory write skipped for %s: %s", task_id, exc)

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
        event_id = f"hb_alert_{uuid.uuid4().hex}"
        try:
            self._ledger.log_scheduler_event({
                "event_id": event_id,
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
            return
        try:
            from host_brain.app.observability_persist import persist_background_event_memory  # noqa: PLC0415

            persist_background_event_memory(
                event_source="scheduler",
                event_id=event_id,
                request_id=event_id,
                task_id="agent_health_check",
                status=HEARTBEAT_EVENT_TYPE_ALERT,
                reason_code=HEARTBEAT_EVENT_TYPE_ALERT,
                detail=raw_response,
                ts_ms=ts_ms,
            )
        except Exception as exc:
            logger.debug("heartbeat alert memory write skipped: %s", exc)

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
                    priority=TaskPriority(row.get("priority", "MEDIUM")),
                    enabled=bool(row.get("enabled", 1)),
                    max_consecutive_failures=int(row.get("max_consecutive_failures", 3)),
                    resource_profile=dict(row.get("resource_profile") or {}),
                )
                self._registry.register(task)
                loaded += 1
            except Exception as exc:
                logger.warning("Failed to restore persisted task %s: %s", task_id, exc)

        if loaded:
            logger.info("Scheduler restored %d persisted tasks from DB", loaded)
