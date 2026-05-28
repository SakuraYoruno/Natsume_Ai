"""T-SCHED-01~07：NatsumeScheduler 单元测试。

覆盖：
  T-SCHED-01  注册任务后 tick() 在正确时间触发，生成 system_initiated_intent
  T-SCHED-02  sys_mode=GAMING 时 NORMAL 优先级任务被跳过（SYSMODE_FORBIDDEN）
  T-SCHED-03  sys_mode=SLEEP 时 HIGH 优先级白名单任务正常触发
  T-SCHED-04  连续失败 MAX_ERROR_COUNT 次后 status=SUSPENDED，写 scheduler_config_log
  T-SCHED-05  /scheduler/health 返回所有任务健康快照，字段完整
  T-SCHED-06  注销任务后 tick() 不再触发
  T-SCHED-07  system_initiated_intent 中 proposer_node_id="scheduler" 且 source_task_id 正确
"""

from __future__ import annotations

import datetime
import threading
import time
import pytest
from typing import Any

from host_brain.scheduler.task_registry import (
    SchedulerTaskConfig,
    SchedulerTaskRegistry,
    TriggerMode,
    TaskPriority,
)
from host_brain.scheduler.scheduler import NatsumeScheduler
from host_brain.scheduler.health import (
    SchedulerHealthReporter,
    STATUS_DEFERRED,
    STATUS_OK,
    STATUS_QUEUED,
    STATUS_RETRYING,
    STATUS_SUSPENDED,
)
from host_brain.core.errors import LLM_POOL_EXHAUSTED, SYSMODE_FORBIDDEN, SCHED_TASK_SUSPENDED


# ── 辅助工厂 ──

def _make_registry_and_health() -> tuple[SchedulerTaskRegistry, SchedulerHealthReporter]:
    registry = SchedulerTaskRegistry()
    health = SchedulerHealthReporter(registry)
    return registry, health


def _make_interval_task(
    task_id: str = "test_task",
    priority: TaskPriority = TaskPriority.MEDIUM,
    interval_s: int = 900,
    max_failures: int = 3,
) -> SchedulerTaskConfig:
    return SchedulerTaskConfig(
        task_id=task_id,
        trigger_mode=TriggerMode.INTERVAL,
        trigger_config={"seconds": interval_s},
        intent_template={"skill_name": task_id, "args": {}},
        priority=priority,
        enabled=True,
        max_consecutive_failures=max_failures,
    )


def _make_scheduler(
    registry: SchedulerTaskRegistry,
    health: SchedulerHealthReporter,
    *,
    sys_mode: str = "normal",
    emotion_state: dict[str, Any] | None = None,
    dispatcher_result: dict[str, Any] | None = None,
    dispatcher_raises: Exception | None = None,
    ledger: Any = None,
) -> NatsumeScheduler:
    """构造 NatsumeScheduler，不启动后台线程。"""
    dispatched: list[dict[str, Any]] = []

    def _dispatcher(intent: dict[str, Any]) -> dict[str, Any]:
        dispatched.append(intent)
        if dispatcher_raises:
            raise dispatcher_raises
        return dispatcher_result or {"status": "OK", "reason_code": None}

    sched = NatsumeScheduler(
        registry=registry,
        health_reporter=health,
        intent_dispatcher=_dispatcher,
        get_sys_mode=lambda: sys_mode,
        get_emotion_state=(lambda: emotion_state) if emotion_state is not None else None,
        ledger=ledger,
    )
    sched._dispatched = dispatched  # 暴露给测试断言
    return sched


class _CaptureLedger:
    def __init__(self) -> None:
        self.scheduler_events: list[dict[str, Any]] = []

    def log_scheduler_event(self, row: dict[str, Any]) -> None:
        self.scheduler_events.append(dict(row))


def test_scheduler_event_log_promotes_background_memory(monkeypatch) -> None:
    registry, health = _make_registry_and_health()
    ledger = _CaptureLedger()
    memory_calls: list[dict[str, Any]] = []
    sched = _make_scheduler(registry, health, ledger=ledger)
    monkeypatch.setattr(
        "host_brain.app.observability_persist.persist_background_event_memory",
        lambda **kwargs: memory_calls.append(dict(kwargs)) or {"status": "OK", "memory_id": "mem_sched_1"},
    )

    sched._log_scheduler_event(  # noqa: SLF001
        task_id="daily_recommendation",
        trigger_ts=1_800_000_000_000,
        intent_id="intent_daily_reco",
        status="ERROR",
        reason_code="LLM_POOL_EXHAUSTED",
        mode_at_trigger="normal",
        error_detail="model pool unavailable",
    )

    assert ledger.scheduler_events
    assert ledger.scheduler_events[0]["event_id"].startswith("sched_evt_")
    assert memory_calls
    assert memory_calls[0]["event_source"] == "scheduler"
    assert memory_calls[0]["task_id"] == "daily_recommendation"


# ── T-SCHED-01 ──

class TestSchedTrigger:
    """T-SCHED-01：注册任务后 trigger_task_by_id() 触发，生成 system_initiated_intent。"""

    def test_trigger_generates_intent(self) -> None:
        registry, health = _make_registry_and_health()
        task = _make_interval_task("diary_generate", priority=TaskPriority.HIGH)
        registry.register(task)
        sched = _make_scheduler(registry, health)

        result = sched.trigger_task_by_id("diary_generate")

        assert result["status"] == "OK"
        assert result["task_id"] == "diary_generate"
        assert "intent_id" in result
        assert "request_id" in result
        # dispatcher 收到了 intent
        assert len(sched._dispatched) == 1
        intent = sched._dispatched[0]
        assert intent["skill_name"] == "diary_generate"

    def test_async_manual_trigger_returns_before_dispatch_finishes(self) -> None:
        registry, health = _make_registry_and_health()
        task = _make_interval_task("slow_manual", priority=TaskPriority.HIGH)
        registry.register(task)
        started = threading.Event()
        release = threading.Event()
        dispatched: list[dict[str, Any]] = []

        def _slow_dispatcher(intent: dict[str, Any]) -> dict[str, Any]:
            dispatched.append(intent)
            started.set()
            release.wait(timeout=2.0)
            return {"status": "OK", "reason_code": None}

        sched = NatsumeScheduler(
            registry=registry,
            health_reporter=health,
            intent_dispatcher=_slow_dispatcher,
        )

        begin = time.perf_counter()
        result = sched.trigger_task_by_id_async("slow_manual")
        elapsed = time.perf_counter() - begin

        assert elapsed < 0.2
        assert result["status"] == STATUS_QUEUED
        assert result["task_id"] == "slow_manual"
        assert health.get_task_state("slow_manual")["status"] == STATUS_QUEUED
        assert started.wait(timeout=1.0)
        assert len(dispatched) == 1

        release.set()
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if health.get_task_state("slow_manual").get("status") == STATUS_OK:
                break
            time.sleep(0.02)

        assert health.get_task_state("slow_manual")["status"] == STATUS_OK


# ── T-SCHED-02 ──

class TestSchedGamingBlock:
    """T-SCHED-02：sys_mode=GAMING 时 NORMAL 优先级任务被跳过（SYSMODE_FORBIDDEN）。"""

    def test_gaming_blocks_normal_priority(self) -> None:
        registry, health = _make_registry_and_health()
        task = _make_interval_task("low_task", priority=TaskPriority.MEDIUM)
        registry.register(task)
        sched = _make_scheduler(registry, health, sys_mode="gaming")

        result = sched.trigger_task_by_id("low_task")

        assert result["status"] == "REJECTED"
        assert result["reason_code"] == SYSMODE_FORBIDDEN
        # dispatcher 未被调用
        assert len(sched._dispatched) == 0

    def test_gaming_allows_high_priority(self) -> None:
        registry, health = _make_registry_and_health()
        task = _make_interval_task("urgent_task", priority=TaskPriority.HIGH)
        registry.register(task)
        sched = _make_scheduler(registry, health, sys_mode="gaming")

        result = sched.trigger_task_by_id("urgent_task")

        assert result["status"] == "OK"
        assert len(sched._dispatched) == 1


# ── T-SCHED-03 ──

class TestSchedSleepAllowsHigh:
    """T-SCHED-03：sys_mode=SLEEP 时 HIGH 优先级任务正常触发。"""

    def test_sleep_allows_high_priority(self) -> None:
        registry, health = _make_registry_and_health()
        task = _make_interval_task("dream_generate", priority=TaskPriority.HIGH)
        registry.register(task)
        sched = _make_scheduler(registry, health, sys_mode="sleep")

        result = sched.trigger_task_by_id("dream_generate")

        assert result["status"] == "OK"
        assert len(sched._dispatched) == 1

    def test_sleep_blocks_low_priority(self) -> None:
        registry, health = _make_registry_and_health()
        task = _make_interval_task("bg_task", priority=TaskPriority.LOW)
        registry.register(task)
        sched = _make_scheduler(registry, health, sys_mode="sleep")

        result = sched.trigger_task_by_id("bg_task")

        assert result["status"] == "REJECTED"
        assert result["reason_code"] == SYSMODE_FORBIDDEN


class TestBackgroundLlmModeGate:
    def test_sleep_blocks_background_llm_task_even_if_priority_matrix_allows(self) -> None:
        registry, health = _make_registry_and_health()
        task = _make_interval_task("inner_world_monologue_tick", priority=TaskPriority.MEDIUM)
        registry.register(task)
        sched = _make_scheduler(registry, health, sys_mode="sleep")

        result = sched.trigger_task_by_id("inner_world_monologue_tick")

        assert result["status"] == "SKIPPED"
        assert result["reason_code"] == "BACKGROUND_LLM_MODE_BLOCKED"
        assert len(sched._dispatched) == 0

    def test_sleep_allows_dream_generator_background_llm_task(self) -> None:
        registry, health = _make_registry_and_health()
        task = SchedulerTaskConfig(
            task_id="dream_generator_sleep",
            trigger_mode=TriggerMode.INTERVAL,
            trigger_config={"seconds": 60},
            intent_template={"skill_name": "dream_generator", "args": {"max_episodic_entries": 20}},
            priority=TaskPriority.LOW,
            mode_allowlist=["sleep"],
            enabled=True,
        )
        registry.register(task)
        sched = _make_scheduler(registry, health, sys_mode="sleep")

        result = sched.trigger_task_by_id("dream_generator_sleep")

        assert result["status"] == "OK"
        assert len(sched._dispatched) == 1
        assert sched._dispatched[0]["sys_mode"] == "sleep"

    def test_gaming_keeps_gaming_companion_background_llm_task(self) -> None:
        registry, health = _make_registry_and_health()
        task = _make_interval_task("gaming_companion_tick", priority=TaskPriority.HIGH)
        registry.register(task)
        sched = _make_scheduler(registry, health, sys_mode="gaming")

        result = sched.trigger_task_by_id("gaming_companion_tick")

        assert result["status"] == "OK"
        assert len(sched._dispatched) == 1

    def test_background_llm_pool_exhausted_is_recorded_as_skip(self) -> None:
        registry, health = _make_registry_and_health()
        task = _make_interval_task("inner_world_monologue_tick", priority=TaskPriority.HIGH)
        registry.register(task)
        sched = _make_scheduler(
            registry,
            health,
            dispatcher_result={"status": "DEGRADED", "reason_code": LLM_POOL_EXHAUSTED},
        )

        result = sched.trigger_task_by_id("inner_world_monologue_tick")

        assert result["status"] == "SKIPPED"
        assert result["reason_code"] == LLM_POOL_EXHAUSTED

    def test_resource_pressure_defers_background_llm_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict[str, Any]] = []

        class FakeResourceManager:
            def assess_background_task(self, **kwargs: Any) -> dict[str, Any]:
                calls.append(kwargs)
                return {
                    "allowed": False,
                    "reason_code": "RESOURCE_PRESSURE_ELEVATED",
                    "pressure_status": "ELEVATED",
                    "retry_after_s": 180,
                }

        monkeypatch.setattr(
            "host_brain.app.dependencies.get_resource_manager",
            lambda create_if_missing=False: FakeResourceManager(),
        )

        registry, health = _make_registry_and_health()
        task = _make_interval_task("inner_world_monologue_tick", priority=TaskPriority.HIGH)
        registry.register(task)
        sched = _make_scheduler(registry, health)

        result = sched.trigger_task_by_id("inner_world_monologue_tick")

        assert result["status"] == STATUS_DEFERRED
        assert result["scheduler_decision"] == "defer"
        assert result["reason_code"] == "RESOURCE_PRESSURE_ELEVATED"
        assert len(sched._dispatched) == 0
        assert calls[0]["uses_llm"] is True
        snapshot = health.build_health_snapshot()
        task_state = next(t for t in snapshot["tasks"] if t["task_id"] == "inner_world_monologue_tick")
        assert task_state["status"] == STATUS_DEFERRED
        assert task_state["last_run_ts"] is None
        assert task_state["last_gate_state"] == "resource:elevated"

    def test_resource_gate_uses_explicit_resource_profile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict[str, Any]] = []

        class FakeResourceManager:
            def assess_background_task(self, **kwargs: Any) -> dict[str, Any]:
                calls.append(kwargs)
                return {
                    "allowed": False,
                    "reason_code": "RESOURCE_PRESSURE_HIGH",
                    "pressure_status": "HIGH",
                    "retry_after_s": 600,
                }

        monkeypatch.setattr(
            "host_brain.app.dependencies.get_resource_manager",
            lambda create_if_missing=False: FakeResourceManager(),
        )

        registry, health = _make_registry_and_health()
        task = _make_interval_task("lightnovel_recommender_daily", priority=TaskPriority.LOW)
        task.resource_profile = {
            "resource_class": "browser_heavy",
            "uses_llm": True,
            "uses_gpu": False,
        }
        registry.register(task)
        sched = _make_scheduler(registry, health)

        result = sched.trigger_task_by_id("lightnovel_recommender_daily")

        assert result["status"] == STATUS_DEFERRED
        assert calls[0]["task_type"] == "browser_heavy"
        assert calls[0]["uses_llm"] is True
        assert calls[0]["uses_gpu"] is False


class TestCronMatching:
    def test_cron_respects_day_of_week(self) -> None:
        registry, health = _make_registry_and_health()
        task = SchedulerTaskConfig(
            task_id="weekly_growth",
            trigger_mode=TriggerMode.CRON,
            trigger_config={"day_of_week": 6, "hour": 3, "minute": 50},
            intent_template={"skill_name": "weekly_growth", "args": {}},
            priority=TaskPriority.LOW,
            enabled=True,
        )
        registry.register(task)
        sched = _make_scheduler(registry, health)

        assert sched._should_cron_fire(task, datetime.datetime(2026, 5, 24, 3, 50)) is True
        assert sched._should_cron_fire(task, datetime.datetime(2026, 5, 25, 3, 50)) is False

    def test_cron_does_not_repeat_within_same_minute(self) -> None:
        registry, health = _make_registry_and_health()
        task = SchedulerTaskConfig(
            task_id="daily_memory",
            trigger_mode=TriggerMode.CRON,
            trigger_config={"hour": 3, "minute": 0},
            intent_template={"skill_name": "daily_memory", "args": {}},
            priority=TaskPriority.LOW,
            enabled=True,
        )
        registry.register(task)
        sched = _make_scheduler(registry, health)
        now = datetime.datetime(2026, 5, 24, 3, 0, 30)
        sched._last_run_ts[task.task_id] = int(now.replace(second=0, microsecond=0).timestamp() * 1000)

        assert sched._should_cron_fire(task, now) is False

    def test_cron_catches_up_once_within_configured_window(self) -> None:
        registry, health = _make_registry_and_health()
        task = SchedulerTaskConfig(
            task_id="daily_recommendation",
            trigger_mode=TriggerMode.CRON,
            trigger_config={"hour": 10, "minute": 30, "catch_up_window_minutes": 180},
            intent_template={"skill_name": "daily_recommendation", "args": {}},
            priority=TaskPriority.LOW,
            enabled=True,
        )
        registry.register(task)
        sched = _make_scheduler(registry, health)
        catch_up_now = datetime.datetime(2026, 5, 22, 11, 5, 0)
        scheduled_ts = int(catch_up_now.replace(hour=10, minute=30, second=0, microsecond=0).timestamp() * 1000)

        assert sched._should_cron_fire(task, catch_up_now) is True

        sched._last_run_ts[task.task_id] = scheduled_ts
        assert sched._should_cron_fire(task, catch_up_now) is False
        assert sched._should_cron_fire(task, datetime.datetime(2026, 5, 22, 13, 31, 0)) is False

    def test_cron_catches_up_recent_previous_occurrence(self) -> None:
        registry, health = _make_registry_and_health()
        task = SchedulerTaskConfig(
            task_id="nightly_audit",
            trigger_mode=TriggerMode.CRON,
            trigger_config={"hour": 23, "minute": 50, "catch_up_window_minutes": 720},
            intent_template={"skill_name": "nightly_audit", "args": {}},
            priority=TaskPriority.LOW,
            enabled=True,
        )
        registry.register(task)
        sched = _make_scheduler(registry, health)

        assert sched._should_cron_fire(task, datetime.datetime(2026, 5, 23, 1, 5, 0)) is True
        assert sched._should_cron_fire(task, datetime.datetime(2026, 5, 23, 12, 1, 0)) is False

    def test_weekly_cron_catches_up_across_day_boundary(self) -> None:
        registry, health = _make_registry_and_health()
        task = SchedulerTaskConfig(
            task_id="weekly_growth",
            trigger_mode=TriggerMode.CRON,
            trigger_config={"day_of_week": 6, "hour": 3, "minute": 50, "catch_up_window_minutes": 1440},
            intent_template={"skill_name": "weekly_growth", "args": {}},
            priority=TaskPriority.LOW,
            enabled=True,
        )
        registry.register(task)
        sched = _make_scheduler(registry, health)

        assert sched._should_cron_fire(task, datetime.datetime(2026, 5, 25, 3, 45, 0)) is True
        assert sched._should_cron_fire(task, datetime.datetime(2026, 5, 25, 4, 0, 0)) is False

    def test_cron_last_run_hydrates_from_ledger_to_avoid_restart_duplicate(self) -> None:
        registry, health = _make_registry_and_health()
        task = SchedulerTaskConfig(
            task_id="daily_recommendation",
            trigger_mode=TriggerMode.CRON,
            trigger_config={"hour": 10, "minute": 30, "catch_up_window_minutes": 180},
            intent_template={"skill_name": "daily_recommendation", "args": {}},
            priority=TaskPriority.LOW,
            enabled=True,
        )
        registry.register(task)
        catch_up_now = datetime.datetime(2026, 5, 22, 11, 5, 0)
        manual_trigger_ts = int(datetime.datetime(2026, 5, 22, 11, 0, 0).timestamp() * 1000)

        class _Ledger:
            def load_latest_scheduler_event_trigger_ts(self, task_ids):
                assert task_ids == ["daily_recommendation"]
                return {"daily_recommendation": manual_trigger_ts}

        sched = _make_scheduler(registry, health, ledger=_Ledger())
        sched._hydrate_last_run_ts_from_ledger()

        assert sched._should_cron_fire(task, catch_up_now) is False
        hydrated_state = health.get_task_state("daily_recommendation")
        assert hydrated_state["status"] == STATUS_OK
        assert hydrated_state["last_run_ts"] == manual_trigger_ts
        assert hydrated_state["last_gate_state"] == "ledger_hydrated"


# ── T-SCHED-04 ──

class TestSchedSuspendOnFailure:
    """T-SCHED-04：连续失败 MAX_ERROR_COUNT 次后 status=SUSPENDED。"""

    def test_suspended_after_max_failures(self) -> None:
        registry, health = _make_registry_and_health()
        task = _make_interval_task("flaky_task", max_failures=3)
        registry.register(task)
        sched = _make_scheduler(
            registry, health,
            dispatcher_raises=RuntimeError("simulated failure"),
        )

        # 触发 3 次失败
        for _ in range(3):
            sched.trigger_task_by_id("flaky_task")

        # 第 4 次触发时应已暂停
        result = sched.trigger_task_by_id("flaky_task")
        assert result["status"] == "SUSPENDED"
        assert result["reason_code"] == SCHED_TASK_SUSPENDED

        # 健康状态应为 SUSPENDED
        snapshot = health.build_health_snapshot()
        task_state = next(t for t in snapshot["tasks"] if t["task_id"] == "flaky_task")
        assert task_state["status"] == STATUS_SUSPENDED

    def test_error_count_resets_on_success(self) -> None:
        registry, health = _make_registry_and_health()
        task = _make_interval_task("recover_task", max_failures=3)
        registry.register(task)

        # 先失败 2 次
        sched = _make_scheduler(
            registry, health,
            dispatcher_raises=RuntimeError("fail"),
        )
        for _ in range(2):
            sched.trigger_task_by_id("recover_task")
        assert sched.get_error_count("recover_task") == 2

        # 成功 1 次后重置
        sched2 = _make_scheduler(registry, health, dispatcher_result={"status": "OK"})
        sched2._error_counts = sched._error_counts  # 共享错误计数
        sched2.trigger_task_by_id("recover_task")
        assert sched2.get_error_count("recover_task") == 0


class TestSchedRetryTolerance:
    def test_running_scheduler_retries_transient_dispatch_failure_before_counting_error(self) -> None:
        registry, health = _make_registry_and_health()
        task = _make_interval_task("retry_task", max_failures=2)
        task.trigger_config["retry_policy"] = {"max_retries": 1, "initial_delay_s": 1, "max_delay_s": 1}
        registry.register(task)
        calls = 0

        def _dispatcher(intent: dict[str, Any]) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"status": "FAILED", "reason_code": "TRANSIENT_NETWORK"}
            return {"status": "OK", "reason_code": None}

        sched = NatsumeScheduler(
            registry=registry,
            health_reporter=health,
            intent_dispatcher=_dispatcher,
            get_sys_mode=lambda: "normal",
        )
        sched._running = True

        result = sched.trigger_task_by_id("retry_task")

        assert result["status"] == STATUS_RETRYING
        assert result["retry_attempt"] == 1
        assert sched.get_error_count("retry_task") == 0
        assert health.get_task_state("retry_task")["status"] == STATUS_RETRYING

        for _ in range(30):
            if health.get_task_state("retry_task").get("status") == STATUS_OK:
                break
            time.sleep(0.1)

        assert calls == 2
        assert sched.get_error_count("retry_task") == 0
        assert health.get_task_state("retry_task")["status"] == STATUS_OK

    def test_running_scheduler_retries_resource_gate_deferral_for_interval_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict[str, Any]] = []

        class FakeResourceManager:
            def assess_background_task(self, **kwargs: Any) -> dict[str, Any]:
                calls.append(kwargs)
                if len(calls) == 1:
                    return {
                        "allowed": False,
                        "reason_code": "RESOURCE_PRESSURE_ELEVATED",
                        "pressure_status": "ELEVATED",
                        "retry_after_s": 1,
                    }
                return {"allowed": True, "pressure_status": "NORMAL"}

        monkeypatch.setattr(
            "host_brain.app.dependencies.get_resource_manager",
            lambda create_if_missing=False: FakeResourceManager(),
        )
        registry, health = _make_registry_and_health()
        task = _make_interval_task("retry_resource_task")
        task.resource_profile = {"resource_class": "background_llm", "uses_llm": True}
        task.trigger_config["retry_policy"] = {"max_retries": 1, "initial_delay_s": 1, "max_delay_s": 1}
        registry.register(task)
        sched = _make_scheduler(registry, health)
        sched._running = True

        result = sched.trigger_task_by_id("retry_resource_task")

        assert result["status"] == STATUS_RETRYING
        assert result["resource_gate"]["reason_code"] == "RESOURCE_PRESSURE_ELEVATED"
        assert health.get_task_state("retry_resource_task")["status"] == STATUS_RETRYING

        for _ in range(30):
            if health.get_task_state("retry_resource_task").get("status") == STATUS_OK:
                break
            time.sleep(0.1)

        assert len(calls) >= 2
        assert health.get_task_state("retry_resource_task")["status"] == STATUS_OK
        assert sched.get_error_count("retry_resource_task") == 0


# ── T-SCHED-05 ──

class TestSchedHealthSnapshot:
    """T-SCHED-05：health_snapshot 返回所有任务健康快照，字段完整。"""

    def test_health_snapshot_fields(self) -> None:
        registry, health = _make_registry_and_health()
        registry.register(_make_interval_task("task_a", priority=TaskPriority.HIGH))
        registry.register(_make_interval_task("task_b", priority=TaskPriority.LOW))
        sched = _make_scheduler(registry, health)

        # 触发一次
        sched.trigger_task_by_id("task_a")

        snapshot = health.build_health_snapshot()
        assert "scheduler_status" in snapshot
        assert "total_tasks" in snapshot
        assert "enabled_tasks" in snapshot
        assert "tasks" in snapshot
        assert snapshot["total_tasks"] == 2

        task_a_state = next(t for t in snapshot["tasks"] if t["task_id"] == "task_a")
        assert "status" in task_a_state
        assert "last_run_ts" in task_a_state
        assert "error_count" in task_a_state

    def test_health_snapshot_healthy_when_no_errors(self) -> None:
        registry, health = _make_registry_and_health()
        registry.register(_make_interval_task("clean_task"))
        sched = _make_scheduler(registry, health)
        sched.trigger_task_by_id("clean_task")

        snapshot = health.build_health_snapshot()
        assert snapshot["scheduler_status"] == "HEALTHY"

    def test_health_snapshot_surfaces_endocrine_gate_metadata(self) -> None:
        registry, health = _make_registry_and_health()
        registry.register(_make_interval_task("proactive_talk_periodic", priority=TaskPriority.LOW))
        sched = _make_scheduler(
            registry,
            health,
            emotion_state={
                "mood_state": "steady",
                "emotion_tag": "NEUTRAL",
                "endocrine_state": {
                    "gate_state": "cautious",
                    "cortisol_level": 0.71,
                    "energy_reserve": 0.33,
                },
            },
        )

        result = sched.trigger_task_by_id("proactive_talk_periodic")
        assert result["status"] == "REJECTED"
        assert result["reason_code"] == "ENDOCRINE_PROACTIVE_BLOCKED"

        snapshot = health.build_health_snapshot()
        task_state = next(t for t in snapshot["tasks"] if t["task_id"] == "proactive_talk_periodic")
        assert task_state["last_reason_code"] == "ENDOCRINE_PROACTIVE_BLOCKED"
        assert task_state["last_gate_state"] == "cautious"
        assert task_state["last_mood_state"] == "steady"
        assert float(task_state["last_endocrine_cortisol"]) == 0.71
        assert float(task_state["last_endocrine_energy"]) == 0.33
        assert "last_immune_decision" in task_state
        assert "last_immune_risk_level" in task_state
        assert "last_immune_reason_codes" in task_state


# ── T-SCHED-06 ──

class TestSchedUnregister:
    """T-SCHED-06：注销任务后 trigger_task_by_id() 不再触发。"""

    def test_unregistered_task_skipped(self) -> None:
        registry, health = _make_registry_and_health()
        task = _make_interval_task("temp_task")
        registry.register(task)
        sched = _make_scheduler(registry, health)

        # 注销
        registry.unregister("temp_task")

        result = sched.trigger_task_by_id("temp_task")
        assert result["status"] == "SKIPPED"
        assert result["reason_code"] == "TASK_NOT_FOUND"
        assert len(sched._dispatched) == 0


# ── T-SCHED-07 ──

class TestSchedIntentFields:
    """T-SCHED-07：system_initiated_intent 中 proposer_node_id="scheduler" 且 source_task_id 正确。"""

    def test_intent_proposer_and_source(self) -> None:
        registry, health = _make_registry_and_health()
        task = _make_interval_task("proactive_talk", priority=TaskPriority.MEDIUM)
        registry.register(task)
        sched = _make_scheduler(registry, health)

        sched.trigger_task_by_id("proactive_talk")

        assert len(sched._dispatched) == 1
        intent = sched._dispatched[0]
        assert intent["proposer_node_id"] == "scheduler"
        assert intent["task_id"] == "proactive_talk"
        assert "intent_id" in intent
        assert "request_id" in intent
        assert "ts_ms" in intent

    def test_intent_skill_name_from_template(self) -> None:
        registry, health = _make_registry_and_health()
        task = SchedulerTaskConfig(
            task_id="custom_task",
            trigger_mode=TriggerMode.INTERVAL,
            trigger_config={"seconds": 60},
            intent_template={"skill_name": "diary_writer", "args": {"date": "today"}},
            priority=TaskPriority.HIGH,
            enabled=True,
        )
        registry.register(task)
        sched = _make_scheduler(registry, health)

        sched.trigger_task_by_id("custom_task")

        intent = sched._dispatched[0]
        assert intent["skill_name"] == "diary_writer"
        assert intent["args"] == {"date": "today"}
