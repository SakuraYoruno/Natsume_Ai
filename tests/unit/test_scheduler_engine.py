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
    STATUS_OK,
    STATUS_SUSPENDED,
)
from host_brain.core.errors import SYSMODE_FORBIDDEN, SCHED_TASK_SUSPENDED


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
