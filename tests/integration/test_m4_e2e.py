"""M4 E2E Integration Test (TASK-0097).

验证 Scheduler → Skill → Audit 完整链路：
  E2E-01：调度器触发 Dream/Diary/ProactiveTalk → Skill 执行 → 审计写入
  E2E-02：跨 Skill 联动（Focus 切换 → 调度器门控生效）
  E2E-03：Health 快照反映真实运行状态
"""

from __future__ import annotations

import pathlib
import json
import pytest

from host_brain.scheduler.task_registry import (
    SchedulerTaskConfig,
    SchedulerTaskRegistry,
    TaskPriority,
    TriggerMode,
)
from host_brain.scheduler.scheduler import NatsumeScheduler, PROPOSER_NODE_ID
from host_brain.scheduler.health import SchedulerHealthReporter, STATUS_OK, STATUS_SUSPENDED

from host_brain.skills.impl.desktop_note import execute_desktop_note, STATUS_EXECUTED
from host_brain.skills.impl.dream_generator import execute_dream_generator
from host_brain.skills.impl.diary_writer import execute_diary_writer
from host_brain.skills.impl.proactive_talk import execute_proactive_talk
from host_brain.skills.impl.focus_mode import execute_focus_mode, FOCUS_ACTIVE, FOCUS_INACTIVE

from host_brain.core.errors import SYSMODE_FORBIDDEN, SCHED_TASK_SUSPENDED
from host_brain.core.ids import now_ms


# ── 辅助 ──

def _make_config(
    task_id: str,
    skill_name: str = "",
    priority: TaskPriority = TaskPriority.MEDIUM,
    trigger_mode: TriggerMode = TriggerMode.INTERVAL,
    trigger_config: dict | None = None,
    enabled: bool = True,
    max_consecutive_failures: int = 3,
) -> SchedulerTaskConfig:
    return SchedulerTaskConfig(
        task_id=task_id,
        trigger_mode=trigger_mode,
        trigger_config=trigger_config or {"seconds": 60},
        intent_template={"skill_name": skill_name or task_id, "args": {}},
        priority=priority,
        enabled=enabled,
        max_consecutive_failures=max_consecutive_failures,
    )


class FakeAuditLedger:
    """仿真审计 Ledger，记录所有写入。"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def log_scheduler_event(self, row: dict) -> None:
        self.events.append(row)


def _build_skill_dispatcher(
    skill_results: dict[str, dict] | None = None,
    default_result: dict | None = None,
) -> callable:
    """构造一个模拟 Skill 分发器。"""
    results = skill_results or {}
    default = default_result or {"status": "OK", "reason_code": None}

    def dispatcher(intent: dict) -> dict:
        skill = intent.get("skill_name", "")
        return dict(results.get(skill, default))

    return dispatcher


# ╔══════════════════════════════════════════════════════════════╗
# ║  E2E-01：调度器 → Skill → 审计完整链路                        ║
# ╚══════════════════════════════════════════════════════════════╝

class TestE2ESchedulerToSkillToAudit:
    """验证调度器触发 Skill 后审计日志完整。"""

    def test_scheduler_triggers_dream_and_logs(self) -> None:
        """调度器触发 dream_generator，验证 intent 生成 + 审计写入。"""
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(
            task_id="e2e_dream",
            skill_name="dream_generator",
            priority=TaskPriority.MEDIUM,
        ))
        health = SchedulerHealthReporter(reg)
        ledger = FakeAuditLedger()

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            intent_dispatcher=_build_skill_dispatcher({
                "dream_generator": {"status": "OK", "reason_code": None, "narrative": "梦境叙事"},
            }),
            get_sys_mode=lambda: "normal",
            ledger=ledger,
        )

        result = scheduler.trigger_task_by_id("e2e_dream")
        assert result["status"] == "OK"
        assert result["intent_id"].startswith("intent_")
        assert result["request_id"].startswith("req_")
        # 审计日志写入
        assert len(ledger.events) == 1
        evt = ledger.events[0]
        assert evt["task_id"] == "e2e_dream"
        assert evt["status"] == "OK"
        assert evt["intent_id"] == result["intent_id"]
        assert evt["mode_at_trigger"] == "normal"

    def test_scheduler_triggers_diary_writer(self) -> None:
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(
            task_id="e2e_diary",
            skill_name="diary_writer",
            priority=TaskPriority.HIGH,
            trigger_mode=TriggerMode.CRON,
            trigger_config={"hour": 23, "minute": 0},
        ))
        health = SchedulerHealthReporter(reg)
        ledger = FakeAuditLedger()

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            intent_dispatcher=_build_skill_dispatcher({
                "diary_writer": {"status": "OK", "reason_code": None},
            }),
            get_sys_mode=lambda: "sleep",
            ledger=ledger,
        )

        result = scheduler.trigger_task_by_id("e2e_diary")
        assert result["status"] == "OK"
        assert len(ledger.events) == 1

    def test_scheduler_triggers_proactive_talk(self) -> None:
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(
            task_id="e2e_proactive",
            skill_name="proactive_talk",
            priority=TaskPriority.LOW,
        ))
        health = SchedulerHealthReporter(reg)
        ledger = FakeAuditLedger()

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            intent_dispatcher=_build_skill_dispatcher({
                "proactive_talk": {"status": "OK", "reason_code": None, "greeting": "你好"},
            }),
            get_sys_mode=lambda: "normal",
            ledger=ledger,
        )

        result = scheduler.trigger_task_by_id("e2e_proactive")
        assert result["status"] == "OK"
        assert len(ledger.events) == 1

    def test_scheduler_multiple_tasks_chain(self) -> None:
        """注册多个任务，依次触发，审计日志完整记录。"""
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="chain_a", skill_name="skill_a", priority=TaskPriority.HIGH))
        reg.register(_make_config(task_id="chain_b", skill_name="skill_b", priority=TaskPriority.MEDIUM))
        reg.register(_make_config(task_id="chain_c", skill_name="skill_c", priority=TaskPriority.LOW))
        health = SchedulerHealthReporter(reg)
        ledger = FakeAuditLedger()

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            intent_dispatcher=_build_skill_dispatcher(),
            get_sys_mode=lambda: "normal",
            ledger=ledger,
        )

        for tid in ["chain_a", "chain_b", "chain_c"]:
            result = scheduler.trigger_task_by_id(tid)
            assert result["status"] == "OK"

        assert len(ledger.events) == 3
        assert [e["task_id"] for e in ledger.events] == ["chain_a", "chain_b", "chain_c"]

    def test_intent_structure_complete(self) -> None:
        """验证生成的 system_initiated_intent 结构完整。"""
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="struct_check", skill_name="test_skill"))
        health = SchedulerHealthReporter(reg)

        captured_intents = []

        def capturing_dispatcher(intent: dict) -> dict:
            captured_intents.append(intent)
            return {"status": "OK", "reason_code": None}

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            intent_dispatcher=capturing_dispatcher,
            get_sys_mode=lambda: "normal",
        )

        scheduler.trigger_task_by_id("struct_check")
        assert len(captured_intents) == 1
        intent = captured_intents[0]
        assert intent["proposer_node_id"] == PROPOSER_NODE_ID
        assert intent["skill_name"] == "test_skill"
        assert intent["task_id"] == "struct_check"
        assert "intent_id" in intent
        assert "request_id" in intent
        assert "ts_ms" in intent
        assert intent["priority"] == TaskPriority.MEDIUM.value


# ╔══════════════════════════════════════════════════════════════╗
# ║  E2E-02：跨 Skill 联动——Focus 切换 → 调度门控                ║
# ╚══════════════════════════════════════════════════════════════╝

class TestE2ECrossSkillIntegration:
    """验证 Focus 模式切换影响调度器门控行为。"""

    def test_focus_blocks_low_priority_scheduler_tasks(self) -> None:
        """进入 Focus → LOW 优先级任务被拦截。"""
        # Step 1: 进入 Focus
        focus_result = execute_focus_mode(
            {"action": "enter"},
            {"sys_mode": "normal", "focus_state": FOCUS_INACTIVE, "mode_changer": lambda m: {"status": "OK"}},
        )
        assert focus_result["status"] == "EXECUTED"

        # Step 2: 尝试调度 LOW 优先级任务
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="low_task", priority=TaskPriority.LOW))
        health = SchedulerHealthReporter(reg)
        ledger = FakeAuditLedger()

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            intent_dispatcher=_build_skill_dispatcher(),
            get_sys_mode=lambda: "focus",  # Focus 模式
            ledger=ledger,
        )

        result = scheduler.trigger_task_by_id("low_task")
        assert result["status"] == "REJECTED"
        assert result["reason_code"] == SYSMODE_FORBIDDEN
        # 拒绝事件也要记入审计
        assert len(ledger.events) == 1
        assert ledger.events[0]["status"] == "REJECTED"

    def test_focus_allows_high_priority_tasks(self) -> None:
        """Focus 模式下 HIGH 优先级任务仍可执行。"""
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="high_task", priority=TaskPriority.HIGH))
        health = SchedulerHealthReporter(reg)

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            intent_dispatcher=_build_skill_dispatcher(),
            get_sys_mode=lambda: "focus",
        )

        result = scheduler.trigger_task_by_id("high_task")
        assert result["status"] == "OK"

    def test_exit_focus_restores_all_tasks(self) -> None:
        """退出 Focus → 恢复 Normal → 所有优先级可执行。"""
        # 先退出 Focus
        exit_result = execute_focus_mode(
            {"action": "exit"},
            {"sys_mode": "focus", "focus_state": FOCUS_ACTIVE, "mode_changer": lambda m: {"status": "OK"}},
        )
        assert exit_result["status"] == "EXECUTED"

        # Normal 模式下所有任务可执行
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="any_task", priority=TaskPriority.LOW))
        health = SchedulerHealthReporter(reg)

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            intent_dispatcher=_build_skill_dispatcher(),
            get_sys_mode=lambda: "normal",
        )

        result = scheduler.trigger_task_by_id("any_task")
        assert result["status"] == "OK"

    def test_desktop_note_then_scheduler_flow(self, tmp_path: pathlib.Path) -> None:
        """Desktop Note 创建 + 调度器触发完整流程。"""
        # Step 1: 创建桌面留言
        note_result = execute_desktop_note(
            {"action": "create", "filename": "e2e_test.txt", "content": "E2E 测试留言", "base_dir": str(tmp_path)},
            {"sys_mode": "normal"},
        )
        assert note_result["status"] == "EXECUTED"
        assert (tmp_path / "e2e_test.txt").exists()

        # Step 2: 调度器触发后续任务
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="follow_up", skill_name="follow_up_skill"))
        health = SchedulerHealthReporter(reg)
        ledger = FakeAuditLedger()

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            intent_dispatcher=_build_skill_dispatcher(),
            get_sys_mode=lambda: "normal",
            ledger=ledger,
        )

        result = scheduler.trigger_task_by_id("follow_up")
        assert result["status"] == "OK"
        assert len(ledger.events) == 1


# ╔══════════════════════════════════════════════════════════════╗
# ║  E2E-03：Health 快照反映真实运行                              ║
# ╚══════════════════════════════════════════════════════════════╝

class TestE2EHealthSnapshot:
    """验证 Health 快照反映多任务真实状态。"""

    def test_health_after_mixed_triggers(self) -> None:
        """多任务混合执行后 Health 快照准确。"""
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="ok_task", priority=TaskPriority.HIGH))
        reg.register(_make_config(task_id="fail_task", priority=TaskPriority.MEDIUM, max_consecutive_failures=2))
        reg.register(_make_config(task_id="disabled_task", enabled=False))
        health = SchedulerHealthReporter(reg)

        call_count = [0]

        def mixed_dispatcher(intent: dict) -> dict:
            if intent["skill_name"] == "fail_task":
                return {"status": "ERROR", "reason_code": "SIMULATED_FAIL"}
            return {"status": "OK", "reason_code": None}

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            intent_dispatcher=mixed_dispatcher,
            get_sys_mode=lambda: "normal",
        )

        # ok_task 成功
        scheduler.trigger_task_by_id("ok_task")
        # fail_task 失败 2 次 → SUSPENDED
        scheduler.trigger_task_by_id("fail_task")
        scheduler.trigger_task_by_id("fail_task")
        scheduler.trigger_task_by_id("fail_task")  # 第 3 次检测到已暂停

        snapshot = health.build_health_snapshot()
        assert snapshot["total_tasks"] == 3
        assert snapshot["enabled_tasks"] == 2
        assert snapshot["suspended_tasks"] == 1
        assert snapshot["scheduler_status"] == "DEGRADED"

        # 验证各任务状态
        task_map = {t["task_id"]: t for t in snapshot["tasks"]}
        assert task_map["ok_task"]["status"] == STATUS_OK
        assert task_map["fail_task"]["status"] == STATUS_SUSPENDED
        assert task_map["disabled_task"]["status"] == "DISABLED"

    def test_health_all_healthy(self) -> None:
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="healthy_a"))
        reg.register(_make_config(task_id="healthy_b"))
        health = SchedulerHealthReporter(reg)

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            intent_dispatcher=_build_skill_dispatcher(),
            get_sys_mode=lambda: "normal",
        )

        scheduler.trigger_task_by_id("healthy_a")
        scheduler.trigger_task_by_id("healthy_b")

        snapshot = health.build_health_snapshot()
        assert snapshot["scheduler_status"] == "HEALTHY"
        assert snapshot["suspended_tasks"] == 0
        assert snapshot["error_tasks"] == 0

    def test_health_recovery_after_reset(self) -> None:
        """手动重置后健康状态恢复。"""
        reg = SchedulerTaskRegistry()
        reg.register(_make_config(task_id="recoverable", max_consecutive_failures=1))
        health = SchedulerHealthReporter(reg)

        scheduler = NatsumeScheduler(
            registry=reg,
            health_reporter=health,
            intent_dispatcher=lambda i: {"status": "ERROR", "reason_code": "FAIL"},
            get_sys_mode=lambda: "normal",
        )

        # 1 次失败 → 暂停
        scheduler.trigger_task_by_id("recoverable")
        scheduler.trigger_task_by_id("recoverable")

        snapshot = health.build_health_snapshot()
        assert snapshot["suspended_tasks"] == 1

        # 手动重置
        scheduler.reset_error_count("recoverable")
        snapshot = health.build_health_snapshot()
        assert snapshot["suspended_tasks"] == 0
        assert snapshot["scheduler_status"] == "HEALTHY"
