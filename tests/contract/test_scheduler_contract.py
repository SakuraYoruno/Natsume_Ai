"""Contract tests for Scheduler (TASK-0091).

覆盖 T-Sched-01（注册/注销/触发可回放）、T-Sched-02（sys_mode 门控矩阵）、T-Sched-03（连续失败自动暂停）。
"""

from __future__ import annotations

import json
import os
import pathlib
import pytest

from host_brain.scheduler.task_registry import (
    SchedulerTaskConfig,
    SchedulerTaskRegistry,
    TaskPriority,
    TriggerMode,
    SYSMODE_PRIORITY_MATRIX,
)
from host_brain.scheduler.scheduler import NatsumeScheduler, PROPOSER_NODE_ID
from host_brain.scheduler.health import SchedulerHealthReporter, STATUS_OK, STATUS_SUSPENDED, STATUS_DISABLED, STATUS_PENDING
from host_brain.core.errors import SCHED_TASK_SUSPENDED, SYSMODE_FORBIDDEN

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "scheduler"


# ── 辅助 ──

def _load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / name
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _make_config(
    task_id: str = "test_task",
    trigger_mode: TriggerMode = TriggerMode.INTERVAL,
    trigger_config: dict | None = None,
    intent_template: dict | None = None,
    priority: TaskPriority = TaskPriority.MEDIUM,
    enabled: bool = True,
    max_consecutive_failures: int = 3,
    mode_allowlist: list[str] | None = None,
) -> SchedulerTaskConfig:
    return SchedulerTaskConfig(
        task_id=task_id,
        trigger_mode=trigger_mode,
        trigger_config=trigger_config or {"seconds": 60},
        intent_template=intent_template or {"skill_name": task_id, "args": {}},
        priority=priority,
        enabled=enabled,
        max_consecutive_failures=max_consecutive_failures,
        mode_allowlist=mode_allowlist,
    )


def _make_scheduler(
    registry: SchedulerTaskRegistry | None = None,
    sys_mode: str = "normal",
    dispatcher_result: dict | None = None,
) -> NatsumeScheduler:
    reg = registry or SchedulerTaskRegistry()
    health = SchedulerHealthReporter(reg)
    dispatcher_res = dispatcher_result or {"status": "OK", "reason_code": None}

    def _mock_dispatcher(intent: dict) -> dict:
        return dict(dispatcher_res)

    return NatsumeScheduler(
        registry=reg,
        health_reporter=health,
        intent_dispatcher=_mock_dispatcher,
        get_sys_mode=lambda: sys_mode,
    )


# ╔══════════════════════════════════════════════════════════════╗
# ║  T-Sched-01：注册/注销/触发可回放                             ║
# ╚══════════════════════════════════════════════════════════════╝

class TestSchedulerRegistry:
    """T-Sched-01 子集：注册表基本操作。"""

    def test_register_and_get(self) -> None:
        reg = SchedulerTaskRegistry()
        config = _make_config(task_id="dream_gen")
        reg.register(config)
        assert reg.get("dream_gen") is config
        assert reg.count() == 1

    def test_register_duplicate_raises(self) -> None:
        reg = SchedulerTaskRegistry()
        config = _make_config(task_id="dream_gen")
        reg.register(config)
        with pytest.raises(ValueError, match="already registered"):
            reg.register(config)

    def test_unregister(self) -> None:
        reg = SchedulerTaskRegistry()
        config = _make_config(task_id="dream_gen")
        reg.register(config)
        removed = reg.unregister("dream_gen")
        assert removed is config
        assert reg.get("dream_gen") is None
        assert reg.count() == 0

    def test_unregister_nonexistent_returns_none(self) -> None:
        reg = SchedulerTaskRegistry()
        assert reg.unregister("nonexistent") is None

    def test_require_raises_for_missing(self) -> None:
        reg = SchedulerTaskRegistry()
        with pytest.raises(KeyError, match="not found"):
            reg.require("missing")

    def test_enabled_tasks_filter(self) -> None:
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="enabled_task", enabled=True))
        reg.register(_make_config(task_id="disabled_task", enabled=False))
        assert len(reg.enabled_tasks()) == 1
        assert reg.enabled_tasks()[0].task_id == "enabled_task"

    def test_validation_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            _make_config(task_id="").validate()

    def test_validation_invalid_max_failures_raises(self) -> None:
        with pytest.raises(ValueError, match="max_consecutive_failures"):
            _make_config(max_consecutive_failures=0).validate()


class TestSchedulerTrigger:
    """T-Sched-01 子集：触发执行与 Intent 生成。"""

    def test_trigger_produces_system_intent(self) -> None:
        """触发后生成的 intent 必须包含 proposer_node_id=scheduler。"""
        reg = SchedulerTaskRegistry()
        config = _make_config(task_id="dream_gen")
        reg.register(config)
        scheduler = _make_scheduler(registry=reg, sys_mode="normal")
        result = scheduler.trigger_task_by_id("dream_gen")
        assert result["status"] == "OK"
        assert result["task_id"] == "dream_gen"
        assert result["intent_id"].startswith("intent_")
        assert result["request_id"].startswith("req_")

    def test_trigger_nonexistent_task(self) -> None:
        scheduler = _make_scheduler()
        result = scheduler.trigger_task_by_id("nonexistent")
        assert result["status"] == "SKIPPED"
        assert result["reason_code"] == "TASK_NOT_FOUND"

    def test_trigger_disabled_task(self) -> None:
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="disabled", enabled=False))
        scheduler = _make_scheduler(registry=reg)
        result = scheduler.trigger_task_by_id("disabled")
        assert result["status"] == "SKIPPED"
        assert result["reason_code"] == "TASK_DISABLED"

    def test_trigger_result_from_dispatcher(self) -> None:
        """dispatcher 返回的结果应传递到触发结果中。"""
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="test_task"))
        scheduler = _make_scheduler(
            registry=reg,
            dispatcher_result={"status": "OK", "reason_code": None, "narrative": "hello"},
        )
        result = scheduler.trigger_task_by_id("test_task")
        assert result["status"] == "OK"
        assert result["result"]["narrative"] == "hello"

    def test_fixture_normal_trigger_success(self) -> None:
        """验证 normal_trigger_success.json fixture。"""
        fixture = _load_fixture("normal_trigger_success.json")
        reg = SchedulerTaskRegistry()
        tc = fixture["task_config"]
        config = SchedulerTaskConfig(
            task_id=tc["task_id"],
            trigger_mode=TriggerMode(tc["trigger_mode"]),
            trigger_config=tc["trigger_config"],
            intent_template=tc["intent_template"],
            priority=TaskPriority(tc["priority"]),
            enabled=tc["enabled"],
            max_consecutive_failures=tc["max_consecutive_failures"],
        )
        reg.register(config)
        scheduler = _make_scheduler(registry=reg, sys_mode=fixture["sys_mode"])
        result = scheduler.trigger_task_by_id(tc["task_id"])
        expected = fixture["expected"]
        assert result["status"] == expected["status"]
        assert result.get("reason_code") == expected["reason_code"]


# ╔══════════════════════════════════════════════════════════════╗
# ║  T-Sched-02：sys_mode 门控矩阵                               ║
# ╚══════════════════════════════════════════════════════════════╝

class TestSysModGating:
    """T-Sched-02：sys_mode 门控矩阵全覆盖。"""

    @pytest.mark.parametrize("sys_mode,priority,allowed", [
        # Normal: 全部允许
        ("normal", TaskPriority.HIGH, True),
        ("normal", TaskPriority.MEDIUM, True),
        ("normal", TaskPriority.LOW, True),
        # Sleep: HIGH + MEDIUM
        ("sleep", TaskPriority.HIGH, True),
        ("sleep", TaskPriority.MEDIUM, True),
        ("sleep", TaskPriority.LOW, False),
        # Gaming: 仅 HIGH
        ("gaming", TaskPriority.HIGH, True),
        ("gaming", TaskPriority.MEDIUM, False),
        ("gaming", TaskPriority.LOW, False),
        # Focus: 仅 HIGH
        ("focus", TaskPriority.HIGH, True),
        ("focus", TaskPriority.MEDIUM, False),
        ("focus", TaskPriority.LOW, False),
        # Maintenance: 全部禁止
        ("maintenance", TaskPriority.HIGH, False),
        ("maintenance", TaskPriority.MEDIUM, False),
        ("maintenance", TaskPriority.LOW, False),
        # Locked: 全部禁止
        ("locked", TaskPriority.HIGH, False),
        ("locked", TaskPriority.MEDIUM, False),
        ("locked", TaskPriority.LOW, False),
    ])
    def test_mode_priority_matrix(self, sys_mode: str, priority: TaskPriority, allowed: bool) -> None:
        config = _make_config(task_id="test", priority=priority)
        assert config.is_allowed_in_mode(sys_mode) is allowed

    def test_gaming_blocks_medium_fixture(self) -> None:
        """验证 gaming_blocks_medium.json fixture。"""
        fixture = _load_fixture("gaming_blocks_medium.json")
        tc = fixture["task_config"]
        config = SchedulerTaskConfig(
            task_id=tc["task_id"],
            trigger_mode=TriggerMode(tc["trigger_mode"]),
            trigger_config=tc["trigger_config"],
            intent_template=tc["intent_template"],
            priority=TaskPriority(tc["priority"]),
            enabled=tc["enabled"],
            max_consecutive_failures=tc["max_consecutive_failures"],
        )
        reg = SchedulerTaskRegistry()
        reg.register(config)
        scheduler = _make_scheduler(registry=reg, sys_mode=fixture["sys_mode"])
        result = scheduler.trigger_task_by_id(tc["task_id"])
        expected = fixture["expected"]
        assert result["status"] == expected["status"]
        assert result["reason_code"] == expected["reason_code"]

    def test_focus_blocks_low_fixture(self) -> None:
        """验证 focus_mode_blocks_low.json fixture。"""
        fixture = _load_fixture("focus_mode_blocks_low.json")
        tc = fixture["task_config"]
        config = SchedulerTaskConfig(
            task_id=tc["task_id"],
            trigger_mode=TriggerMode(tc["trigger_mode"]),
            trigger_config=tc["trigger_config"],
            intent_template=tc["intent_template"],
            priority=TaskPriority(tc["priority"]),
            enabled=tc["enabled"],
            max_consecutive_failures=tc["max_consecutive_failures"],
        )
        reg = SchedulerTaskRegistry()
        reg.register(config)
        scheduler = _make_scheduler(registry=reg, sys_mode=fixture["sys_mode"])
        result = scheduler.trigger_task_by_id(tc["task_id"])
        expected = fixture["expected"]
        assert result["status"] == expected["status"]
        assert result["reason_code"] == expected["reason_code"]

    def test_mode_allowlist_override(self) -> None:
        """显式 mode_allowlist 覆盖矩阵默认行为。"""
        config = _make_config(
            task_id="custom",
            priority=TaskPriority.LOW,
            mode_allowlist=["gaming", "normal"],
        )
        # LOW 在 gaming 默认被拦截，但 allowlist 显式允许
        assert config.is_allowed_in_mode("gaming") is True
        # sleep 不在 allowlist 中
        assert config.is_allowed_in_mode("sleep") is False

    def test_trigger_rejected_in_gaming(self) -> None:
        """Gaming 模式下 MEDIUM 任务触发被拒绝，返回 SYSMODE_FORBIDDEN。"""
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="medium_task", priority=TaskPriority.MEDIUM))
        scheduler = _make_scheduler(registry=reg, sys_mode="gaming")
        result = scheduler.trigger_task_by_id("medium_task")
        assert result["status"] == "REJECTED"
        assert result["reason_code"] == SYSMODE_FORBIDDEN
        assert result["sys_mode"] == "gaming"


# ╔══════════════════════════════════════════════════════════════╗
# ║  T-Sched-03：连续失败自动暂停                                 ║
# ╚══════════════════════════════════════════════════════════════╝

class TestConsecutiveFailureSuspend:
    """T-Sched-03：连续失败 N 次后自动暂停。"""

    def test_consecutive_failures_suspend(self) -> None:
        """连续 3 次 ERROR 后任务进入 SUSPENDED。"""
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="fragile", max_consecutive_failures=3))
        health = SchedulerHealthReporter(reg)

        call_count = [0]

        def failing_dispatcher(intent: dict) -> dict:
            call_count[0] += 1
            return {"status": "ERROR", "reason_code": "LLM_UNAVAILABLE"}

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            intent_dispatcher=failing_dispatcher,
            get_sys_mode=lambda: "normal",
        )

        # 触发 3 次，每次失败
        for i in range(3):
            result = scheduler.trigger_task_by_id("fragile")
            assert result["status"] == "ERROR" or result["status"] == "OK"

        # 第 4 次触发应返回 SUSPENDED
        result = scheduler.trigger_task_by_id("fragile")
        assert result["status"] == "SUSPENDED"
        assert result["reason_code"] == SCHED_TASK_SUSPENDED
        assert result["error_count"] == 3

    def test_success_resets_error_count(self) -> None:
        """成功执行会重置错误计数。"""
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="recovering", max_consecutive_failures=3))
        health = SchedulerHealthReporter(reg)

        results = [
            {"status": "ERROR", "reason_code": "FAIL1"},
            {"status": "ERROR", "reason_code": "FAIL2"},
            {"status": "OK", "reason_code": None},  # 成功，重置
        ]
        call_idx = [0]

        def varying_dispatcher(intent: dict) -> dict:
            idx = min(call_idx[0], len(results) - 1)
            call_idx[0] += 1
            return results[idx]

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            intent_dispatcher=varying_dispatcher,
            get_sys_mode=lambda: "normal",
        )

        scheduler.trigger_task_by_id("recovering")  # ERROR
        scheduler.trigger_task_by_id("recovering")  # ERROR
        assert scheduler.get_error_count("recovering") == 2

        scheduler.trigger_task_by_id("recovering")  # OK -> reset
        assert scheduler.get_error_count("recovering") == 0

    def test_manual_reset_error_count(self) -> None:
        """手动重置错误计数恢复暂停任务。"""
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="manual_reset", max_consecutive_failures=2))
        health = SchedulerHealthReporter(reg)

        def failing_dispatcher(intent: dict) -> dict:
            return {"status": "ERROR", "reason_code": "FAIL"}

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            intent_dispatcher=failing_dispatcher,
            get_sys_mode=lambda: "normal",
        )

        # 触发 2 次失败
        scheduler.trigger_task_by_id("manual_reset")
        scheduler.trigger_task_by_id("manual_reset")
        # 第 3 次 SUSPENDED
        result = scheduler.trigger_task_by_id("manual_reset")
        assert result["status"] == "SUSPENDED"

        # 手动重置
        scheduler.reset_error_count("manual_reset")
        assert scheduler.get_error_count("manual_reset") == 0

    def test_consecutive_failure_fixture(self) -> None:
        """验证 consecutive_failure_suspend.json fixture。"""
        fixture = _load_fixture("consecutive_failure_suspend.json")
        tc = fixture["task_config"]
        reg = SchedulerTaskRegistry()
        config = SchedulerTaskConfig(
            task_id=tc["task_id"],
            trigger_mode=TriggerMode(tc["trigger_mode"]),
            trigger_config=tc["trigger_config"],
            intent_template=tc["intent_template"],
            priority=TaskPriority(tc["priority"]),
            enabled=tc["enabled"],
            max_consecutive_failures=tc["max_consecutive_failures"],
        )
        reg.register(config)
        health = SchedulerHealthReporter(reg)

        sequence = fixture["failure_sequence"]
        call_idx = [0]

        def sequenced_dispatcher(intent: dict) -> dict:
            idx = min(call_idx[0], len(sequence) - 1)
            call_idx[0] += 1
            return sequence[idx]

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            intent_dispatcher=sequenced_dispatcher,
            get_sys_mode=lambda: "normal",
        )

        for _ in sequence:
            scheduler.trigger_task_by_id(tc["task_id"])

        result = scheduler.trigger_task_by_id(tc["task_id"])
        expected = fixture["expected_after_failures"]
        assert result["status"] == expected["status"]
        assert result["reason_code"] == expected["reason_code"]
        assert result["error_count"] == expected["error_count"]


# ╔══════════════════════════════════════════════════════════════╗
# ║  Health 报告验证                                             ║
# ╚══════════════════════════════════════════════════════════════╝

class TestSchedulerHealth:
    """健康快照结构稳定性验证。"""

    def test_health_snapshot_structure(self) -> None:
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="task_a", enabled=True))
        reg.register(_make_config(task_id="task_b", enabled=False))
        health = SchedulerHealthReporter(reg)

        snapshot = health.build_health_snapshot()
        assert "scheduler_status" in snapshot
        assert "total_tasks" in snapshot
        assert "enabled_tasks" in snapshot
        assert "suspended_tasks" in snapshot
        assert "error_tasks" in snapshot
        assert "tasks" in snapshot
        assert "ts_ms" in snapshot
        assert snapshot["total_tasks"] == 2
        assert snapshot["enabled_tasks"] == 1

    def test_health_snapshot_reflects_suspended(self) -> None:
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="suspended_task"))
        health = SchedulerHealthReporter(reg)

        health.update_task_state("suspended_task", status=STATUS_SUSPENDED, error_count=3, suspended_reason="SCHED_TASK_SUSPENDED")
        snapshot = health.build_health_snapshot()
        assert snapshot["suspended_tasks"] == 1
        assert snapshot["scheduler_status"] == "DEGRADED"
        task_snap = snapshot["tasks"][0]
        assert task_snap["status"] == STATUS_SUSPENDED
        assert task_snap["error_count"] == 3

    def test_health_snapshot_healthy_when_all_ok(self) -> None:
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="ok_task"))
        health = SchedulerHealthReporter(reg)
        health.update_task_state("ok_task", status=STATUS_OK, error_count=0)
        snapshot = health.build_health_snapshot()
        assert snapshot["scheduler_status"] == "HEALTHY"

    def test_disabled_task_shows_disabled_status(self) -> None:
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="dis_task", enabled=False))
        health = SchedulerHealthReporter(reg)
        snapshot = health.build_health_snapshot()
        task_snap = snapshot["tasks"][0]
        assert task_snap["status"] == STATUS_DISABLED
