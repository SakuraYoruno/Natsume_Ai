"""Performance Budget Baseline (TASK-0098).

定义 M4 各组件性能基准并进行回归检测：
  Perf-01：Scheduler trigger_task_by_id 单次 < 5ms
  Perf-02：Skill execute 单次 < 10ms（不含 LLM）
  Perf-03：Health snapshot 构建 < 2ms（100 任务）
  Perf-04：TaskRegistry 操作 < 1ms
"""

from __future__ import annotations

import time
import pytest

from host_brain.scheduler.task_registry import (
    SchedulerTaskConfig,
    SchedulerTaskRegistry,
    TaskPriority,
    TriggerMode,
)
from host_brain.scheduler.scheduler import NatsumeScheduler
from host_brain.scheduler.health import SchedulerHealthReporter

from host_brain.skills.impl.desktop_note import execute_desktop_note
from host_brain.skills.impl.dream_generator import execute_dream_generator
from host_brain.skills.impl.diary_writer import execute_diary_writer
from host_brain.skills.impl.proactive_talk import execute_proactive_talk
from host_brain.skills.impl.focus_mode import execute_focus_mode, FOCUS_INACTIVE

from host_brain.core.ids import now_ms


pytestmark = pytest.mark.ci_unstable


def _make_config(task_id: str, **kwargs) -> SchedulerTaskConfig:
    defaults = {
        "trigger_mode": TriggerMode.INTERVAL,
        "trigger_config": {"seconds": 60},
        "intent_template": {"skill_name": task_id, "args": {}},
        "priority": TaskPriority.MEDIUM,
        "enabled": True,
        "max_consecutive_failures": 3,
    }
    defaults.update(kwargs)
    return SchedulerTaskConfig(task_id=task_id, **defaults)


# ╔══════════════════════════════════════════════════════════════╗
# ║  Perf-01：Scheduler trigger 性能                             ║
# ╚══════════════════════════════════════════════════════════════╝

class TestSchedulerPerformance:
    """Scheduler trigger_task_by_id 单次 < 5ms。"""

    BUDGET_MS = 5.0

    def test_trigger_latency(self) -> None:
        reg = SchedulerTaskRegistry()
        reg.register(_make_config("perf_task"))
        health = SchedulerHealthReporter(reg)

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            intent_dispatcher=lambda i: {"status": "OK", "reason_code": None},
            get_sys_mode=lambda: "normal",
        )

        # 预热
        scheduler.trigger_task_by_id("perf_task")

        # 测量 100 次取平均
        start = time.perf_counter()
        iterations = 100
        for _ in range(iterations):
            scheduler.trigger_task_by_id("perf_task")
        elapsed_ms = (time.perf_counter() - start) * 1000 / iterations

        assert elapsed_ms < self.BUDGET_MS, f"trigger avg {elapsed_ms:.2f}ms > budget {self.BUDGET_MS}ms"

    def test_trigger_rejected_latency(self) -> None:
        """被门控拒绝的请求也应很快。"""
        reg = SchedulerTaskRegistry()
        reg.register(_make_config("rejected_task", priority=TaskPriority.LOW))
        health = SchedulerHealthReporter(reg)

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            get_sys_mode=lambda: "gaming",
        )

        start = time.perf_counter()
        iterations = 100
        for _ in range(iterations):
            scheduler.trigger_task_by_id("rejected_task")
        elapsed_ms = (time.perf_counter() - start) * 1000 / iterations

        assert elapsed_ms < self.BUDGET_MS, f"rejected trigger avg {elapsed_ms:.2f}ms > budget {self.BUDGET_MS}ms"


# ╔══════════════════════════════════════════════════════════════╗
# ║  Perf-02：Skill execute 性能（不含 LLM）                      ║
# ╚══════════════════════════════════════════════════════════════╝

class TestSkillPerformance:
    """Skill 单次执行 < 10ms（不含 LLM I/O）。"""

    BUDGET_MS = 10.0

    def test_desktop_note_create_latency(self, tmp_path) -> None:
        start = time.perf_counter()
        iterations = 50
        for i in range(iterations):
            execute_desktop_note(
                {"action": "create", "filename": f"perf_{i}.txt", "content": "test", "base_dir": str(tmp_path)},
                {"sys_mode": "normal"},
            )
        elapsed_ms = (time.perf_counter() - start) * 1000 / iterations

        assert elapsed_ms < self.BUDGET_MS, f"desktop_note avg {elapsed_ms:.2f}ms > budget {self.BUDGET_MS}ms"

    def test_dream_generator_rejected_latency(self) -> None:
        """条件不满足的 dream 请求应极快退出。"""
        start = time.perf_counter()
        iterations = 100
        for _ in range(iterations):
            execute_dream_generator({}, {"sys_mode": "normal", "idle": False})
        elapsed_ms = (time.perf_counter() - start) * 1000 / iterations

        assert elapsed_ms < self.BUDGET_MS

    def test_proactive_talk_rejected_latency(self) -> None:
        ts = now_ms()
        start = time.perf_counter()
        iterations = 100
        for _ in range(iterations):
            execute_proactive_talk(
                {},
                {"sys_mode": "normal", "last_interaction_ts": ts - 1000},
            )
        elapsed_ms = (time.perf_counter() - start) * 1000 / iterations

        assert elapsed_ms < self.BUDGET_MS

    def test_focus_mode_enter_latency(self) -> None:
        start = time.perf_counter()
        iterations = 100
        for _ in range(iterations):
            execute_focus_mode(
                {"action": "enter"},
                {"sys_mode": "normal", "focus_state": FOCUS_INACTIVE},
            )
        elapsed_ms = (time.perf_counter() - start) * 1000 / iterations

        assert elapsed_ms < self.BUDGET_MS


# ╔══════════════════════════════════════════════════════════════╗
# ║  Perf-03：Health snapshot 构建性能                            ║
# ╚══════════════════════════════════════════════════════════════╝

class TestHealthPerformance:
    """Health snapshot 构建 < 2ms（100 任务）。"""

    BUDGET_MS = 2.0

    def test_health_snapshot_100_tasks(self) -> None:
        reg = SchedulerTaskRegistry()
        for i in range(100):
            reg.register(_make_config(f"task_{i:03d}"))
        health = SchedulerHealthReporter(reg)

        # 模拟一些状态更新
        for i in range(100):
            health.update_task_state(f"task_{i:03d}", status="OK", error_count=0)

        # 预热
        health.build_health_snapshot()

        start = time.perf_counter()
        iterations = 100
        for _ in range(iterations):
            health.build_health_snapshot()
        elapsed_ms = (time.perf_counter() - start) * 1000 / iterations

        assert elapsed_ms < self.BUDGET_MS, f"health snapshot avg {elapsed_ms:.2f}ms > budget {self.BUDGET_MS}ms"


# ╔══════════════════════════════════════════════════════════════╗
# ║  Perf-04：TaskRegistry 操作性能                               ║
# ╚══════════════════════════════════════════════════════════════╝

class TestRegistryPerformance:
    """TaskRegistry 基本操作 < 1ms。"""

    BUDGET_MS = 1.0

    def test_register_lookup_latency(self) -> None:
        reg = SchedulerTaskRegistry()
        configs = [_make_config(f"reg_{i}") for i in range(100)]
        for c in configs:
            reg.register(c)

        # 预热
        reg.get("reg_50")

        start = time.perf_counter()
        iterations = 1000
        for i in range(iterations):
            reg.get(f"reg_{i % 100}")
        elapsed_ms = (time.perf_counter() - start) * 1000 / iterations

        assert elapsed_ms < self.BUDGET_MS

    def test_enabled_tasks_filter_latency(self) -> None:
        reg = SchedulerTaskRegistry()
        for i in range(100):
            reg.register(_make_config(f"en_{i}", enabled=(i % 2 == 0)))

        start = time.perf_counter()
        iterations = 100
        for _ in range(iterations):
            reg.enabled_tasks()
        elapsed_ms = (time.perf_counter() - start) * 1000 / iterations

        assert elapsed_ms < self.BUDGET_MS
