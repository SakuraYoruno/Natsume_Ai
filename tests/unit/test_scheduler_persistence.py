"""T-SCHED-PERSIST-01~05：调度器任务持久化测试（M16-C TASK-0180）。

覆盖：
  T-SCHED-PERSIST-01  register_task 将任务写入 scheduler_tasks 表
  T-SCHED-PERSIST-02  重启后 start() 自动从 DB 恢复任务
  T-SCHED-PERSIST-03  unregister_task 从 DB 删除持久化记录
  T-SCHED-PERSIST-04  persist_tasks=False 时不写入 DB（向后兼容）
  T-SCHED-PERSIST-05  DB 加载失败时 start() 不崩溃（降级为空注册表）
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.scheduler.scheduler import NatsumeScheduler
from host_brain.scheduler.task_registry import (
    SchedulerTaskConfig,
    SchedulerTaskRegistry,
    TaskPriority,
    TriggerMode,
)
from host_brain.scheduler.health import SchedulerHealthReporter


# ---------------------------------------------------------------------------
# 辅助：构造最小调度器
# ---------------------------------------------------------------------------

def _make_scheduler(
    ledger: SQLiteLedger | None = None,
    persist_tasks: bool = True,
) -> NatsumeScheduler:
    registry = SchedulerTaskRegistry()
    health = SchedulerHealthReporter(registry)
    return NatsumeScheduler(
        registry=registry,
        health_reporter=health,
        ledger=ledger,
        persist_tasks=persist_tasks,
    )


def _make_task(task_id: str = "test-task-001") -> SchedulerTaskConfig:
    return SchedulerTaskConfig(
        task_id=task_id,
        trigger_mode=TriggerMode.INTERVAL,
        trigger_config={"seconds": 900},
        intent_template={"skill_name": "test_skill", "args": {}},
        priority=TaskPriority.MEDIUM,
        enabled=True,
        max_consecutive_failures=3,
    )


def _make_in_memory_ledger() -> SQLiteLedger:
    return SQLiteLedger(db_path=":memory:")


# ---------------------------------------------------------------------------
# T-SCHED-PERSIST-01  register_task 将任务写入 scheduler_tasks 表
# ---------------------------------------------------------------------------

class TestRegisterTaskPersistence:
    def test_register_task_writes_to_db(self):
        """T-SCHED-PERSIST-01：register_task 将任务配置写入 scheduler_tasks 表。"""
        ledger = _make_in_memory_ledger()
        scheduler = _make_scheduler(ledger=ledger)
        task = _make_task("persist-01")

        scheduler.register_task(task)

        rows = ledger.load_persisted_scheduler_tasks()
        assert len(rows) == 1
        assert rows[0]["task_id"] == "persist-01"
        assert rows[0]["trigger_mode"] == "interval"
        assert rows[0]["trigger_config"]["seconds"] == 900

    def test_register_task_also_adds_to_registry(self):
        """T-SCHED-PERSIST-01b：register_task 同时将任务添加到内存 registry。"""
        ledger = _make_in_memory_ledger()
        scheduler = _make_scheduler(ledger=ledger)
        task = _make_task("persist-01b")

        scheduler.register_task(task)

        assert scheduler._registry.get("persist-01b") is not None

    def test_register_multiple_tasks(self):
        """T-SCHED-PERSIST-01c：注册多个任务，全部写入 DB。"""
        ledger = _make_in_memory_ledger()
        scheduler = _make_scheduler(ledger=ledger)

        for i in range(3):
            scheduler.register_task(_make_task(f"persist-01c-{i}"))

        rows = ledger.load_persisted_scheduler_tasks()
        assert len(rows) == 3


# ---------------------------------------------------------------------------
# T-SCHED-PERSIST-02  重启后 start() 自动从 DB 恢复任务
# ---------------------------------------------------------------------------

class TestRestoreOnStart:
    def test_start_restores_persisted_tasks(self):
        """T-SCHED-PERSIST-02：start() 从 DB 恢复持久化任务到 registry。"""
        ledger = _make_in_memory_ledger()

        # 第一个调度器实例注册任务
        scheduler1 = _make_scheduler(ledger=ledger)
        scheduler1.register_task(_make_task("restore-02"))

        # 模拟重启：创建新调度器实例（空 registry），使用同一 ledger
        scheduler2 = _make_scheduler(ledger=ledger)
        assert scheduler2._registry.get("restore-02") is None  # 重启前为空

        # start() 应从 DB 恢复
        scheduler2.start()
        scheduler2.stop()

        assert scheduler2._registry.get("restore-02") is not None, "重启后应从 DB 恢复任务"

    def test_start_skips_already_registered_tasks(self):
        """T-SCHED-PERSIST-02b：start() 不重复注册已在 registry 中的任务。"""
        ledger = _make_in_memory_ledger()
        task = _make_task("restore-02b")

        # 预先写入 DB
        ledger.persist_scheduler_task({
            "task_id": "restore-02b",
            "task_name": "restore-02b",
            "trigger_mode": "interval",
            "trigger_config": {"seconds": 900},
            "intent_template": {"skill_name": "test"},
            "priority": "MEDIUM",
            "enabled": 1,
            "max_consecutive_failures": 3,
            "registered_at": int(time.time() * 1000),
        })

        # 手动注册同一任务到 registry
        scheduler = _make_scheduler(ledger=ledger)
        scheduler._registry.register(task)

        # start() 不应抛出 ValueError（重复注册）
        scheduler.start()
        scheduler.stop()

        # registry 中只有一个实例
        assert scheduler._registry.count() == 1


# ---------------------------------------------------------------------------
# T-SCHED-PERSIST-03  unregister_task 从 DB 删除持久化记录
# ---------------------------------------------------------------------------

class TestUnregisterTaskPersistence:
    def test_unregister_removes_from_db(self):
        """T-SCHED-PERSIST-03：unregister_task 从 scheduler_tasks 表删除记录。"""
        ledger = _make_in_memory_ledger()
        scheduler = _make_scheduler(ledger=ledger)
        scheduler.register_task(_make_task("unreg-03"))

        # 确认已写入
        assert len(ledger.load_persisted_scheduler_tasks()) == 1

        result = scheduler.unregister_task("unreg-03")
        assert result is True

        # 确认已删除
        rows = ledger.load_persisted_scheduler_tasks()
        assert len(rows) == 0

    def test_unregister_nonexistent_returns_false(self):
        """T-SCHED-PERSIST-03b：注销不存在的任务返回 False。"""
        ledger = _make_in_memory_ledger()
        scheduler = _make_scheduler(ledger=ledger)

        result = scheduler.unregister_task("nonexistent-task")
        assert result is False

    def test_unregister_removes_from_registry(self):
        """T-SCHED-PERSIST-03c：unregister_task 同时从内存 registry 移除。"""
        ledger = _make_in_memory_ledger()
        scheduler = _make_scheduler(ledger=ledger)
        scheduler.register_task(_make_task("unreg-03c"))

        scheduler.unregister_task("unreg-03c")

        assert scheduler._registry.get("unreg-03c") is None


# ---------------------------------------------------------------------------
# T-SCHED-PERSIST-04  persist_tasks=False 时不写入 DB（向后兼容）
# ---------------------------------------------------------------------------

class TestPersistTasksDisabled:
    def test_no_db_write_when_persist_disabled(self):
        """T-SCHED-PERSIST-04：persist_tasks=False 时 register_task 不写入 DB。"""
        ledger = _make_in_memory_ledger()
        scheduler = _make_scheduler(ledger=ledger, persist_tasks=False)
        scheduler.register_task(_make_task("no-persist-04"))

        rows = ledger.load_persisted_scheduler_tasks()
        assert len(rows) == 0, "persist_tasks=False 时不应写入 DB"

    def test_no_db_restore_when_persist_disabled(self):
        """T-SCHED-PERSIST-04b：persist_tasks=False 时 start() 不从 DB 恢复。"""
        ledger = _make_in_memory_ledger()
        # 预先写入 DB
        ledger.persist_scheduler_task({
            "task_id": "no-restore-04b",
            "task_name": "no-restore-04b",
            "trigger_mode": "interval",
            "trigger_config": {"seconds": 900},
            "intent_template": {"skill_name": "test"},
            "priority": "MEDIUM",
            "enabled": 1,
            "max_consecutive_failures": 3,
            "registered_at": int(time.time() * 1000),
        })

        scheduler = _make_scheduler(ledger=ledger, persist_tasks=False)
        scheduler.start()
        scheduler.stop()

        # registry 应为空（未从 DB 恢复）
        assert scheduler._registry.count() == 0


class TestSchedulerEventHydration:
    def test_last_run_hydration_ignores_incomplete_and_skipped_events(self):
        ledger = _make_in_memory_ledger()
        base = {
            "task_id": "daily_memory",
            "intent_id": None,
            "reason_code": None,
            "mode_at_trigger": "normal",
            "error_detail": None,
        }

        ledger.log_scheduler_event({**base, "event_id": "evt-deferred", "trigger_ts": 2000, "status": "DEFERRED"})
        ledger.log_scheduler_event({**base, "event_id": "evt-skipped", "trigger_ts": 3000, "status": "SKIPPED"})
        ledger.log_scheduler_event({**base, "event_id": "evt-rejected", "trigger_ts": 4000, "status": "REJECTED"})
        ledger.log_scheduler_event({**base, "event_id": "evt-retrying", "trigger_ts": 4500, "status": "RETRYING"})
        ledger.log_scheduler_event({**base, "event_id": "evt-error", "trigger_ts": 4600, "status": "ERROR"})
        ledger.log_scheduler_event({**base, "event_id": "evt-failed", "trigger_ts": 4700, "status": "FAILED"})

        assert ledger.load_latest_scheduler_event_trigger_ts(["daily_memory"]) == {}

        ledger.log_scheduler_event({**base, "event_id": "evt-ok", "trigger_ts": 1500, "status": "OK"})
        ledger.log_scheduler_event({**base, "event_id": "evt-later-deferred", "trigger_ts": 5000, "status": "DEFERRED"})

        assert ledger.load_latest_scheduler_event_trigger_ts(["daily_memory"]) == {"daily_memory": 1500}


# ---------------------------------------------------------------------------
# T-SCHED-PERSIST-05  DB 加载失败时 start() 不崩溃（降级为空注册表）
# ---------------------------------------------------------------------------

class TestPersistenceFailureDegradation:
    def test_start_survives_db_load_failure(self):
        """T-SCHED-PERSIST-05：DB 加载失败时 start() 不崩溃，降级为空注册表。"""
        ledger = MagicMock()
        ledger.load_persisted_scheduler_tasks.side_effect = RuntimeError("DB corrupted")

        scheduler = _make_scheduler(ledger=ledger, persist_tasks=True)

        # 不应抛出异常
        scheduler.start()
        scheduler.stop()

        # registry 应为空（降级）
        assert scheduler._registry.count() == 0

    def test_persist_failure_does_not_block_registration(self):
        """T-SCHED-PERSIST-05b：DB 写入失败时 register_task 不崩溃，任务仍注册到内存。"""
        ledger = MagicMock()
        ledger.persist_scheduler_task.side_effect = RuntimeError("DB write failed")

        scheduler = _make_scheduler(ledger=ledger, persist_tasks=True)
        task = _make_task("fail-persist-05b")

        # 不应抛出异常
        scheduler.register_task(task)

        # 任务仍在内存 registry 中
        assert scheduler._registry.get("fail-persist-05b") is not None
