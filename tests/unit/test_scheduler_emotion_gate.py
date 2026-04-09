"""M29-N：NatsumeScheduler 心跳感知情感状态测试（T-SCHED-EMOTION-01~02）"""

from __future__ import annotations

import time
import pytest
from unittest.mock import MagicMock
from host_brain.scheduler.scheduler import NatsumeScheduler
from host_brain.scheduler.task_registry import (
    SchedulerTaskConfig,
    SchedulerTaskRegistry,
    TaskPriority,
    TriggerMode,
)


def _make_task(priority: TaskPriority, *, task_id: str = "task-001") -> SchedulerTaskConfig:
    return SchedulerTaskConfig(
        task_id=task_id,
        trigger_mode=TriggerMode.INTERVAL,
        trigger_config={"seconds": 60},
        intent_template={"skill_name": "heartbeat"},
        priority=priority,
        enabled=True,
    )


def _make_scheduler(
    emotion_state: dict | None = None,
    immune_state: dict | None = None,
) -> NatsumeScheduler:
    registry = MagicMock(spec=SchedulerTaskRegistry)
    health = MagicMock()
    health.update_task_state = MagicMock()
    get_emotion_state = (lambda: emotion_state) if emotion_state else None
    get_immune_state = (lambda: immune_state) if immune_state else None
    return NatsumeScheduler(
        registry=registry,
        health_reporter=health,
        get_emotion_state=get_emotion_state,
        get_immune_gate_state=get_immune_state,
    )


class TestSchedulerEmotionGate:
    """T-SCHED-EMOTION-01~02：NatsumeScheduler 心跳感知情感状态测试"""

    def test_critical_mood_blocks_low_priority_task(self):
        """T-SCHED-EMOTION-01：critical 情感状态下低优先级任务被阻断"""
        scheduler = _make_scheduler({"mood_state": "critical", "emotion_tag": "ANXIOUS"})
        task = _make_task(TaskPriority.LOW)
        scheduler._registry.get = MagicMock(return_value=task)

        result = scheduler._trigger_task(task_id="task-001")
        assert result["status"] == "REJECTED"
        assert result["reason_code"] == "EMOTION_GATE_BLOCKED"

    def test_normal_mood_allows_low_priority_task(self):
        """T-SCHED-EMOTION-02：正常情感状态下低优先级任务不被情感门控拒绝"""
        scheduler = _make_scheduler({"mood_state": "steady", "emotion_tag": "NEUTRAL"})
        task = _make_task(TaskPriority.LOW)
        scheduler._registry.get = MagicMock(return_value=task)

        result = scheduler._trigger_task(task_id="task-001")
        # 正常情感状态下不应被情感门控拒绝
        assert result.get("reason_code") != "EMOTION_GATE_BLOCKED"

    def test_critical_mood_allows_high_priority_task(self):
        """critical 情感状态下 HIGH 优先级任务不被阻断"""
        scheduler = _make_scheduler({"mood_state": "critical", "emotion_tag": "ANXIOUS"})
        task = _make_task(TaskPriority.HIGH)
        scheduler._registry.get = MagicMock(return_value=task)

        result = scheduler._trigger_task(task_id="task-001")
        assert result.get("reason_code") != "EMOTION_GATE_BLOCKED"

    def test_default_emotion_state_is_steady(self):
        """未提供 get_emotion_state 时默认情感状态为 steady"""
        scheduler = _make_scheduler()
        emotion = scheduler._get_emotion_state()
        assert emotion["mood_state"] == "steady"

    def test_endocrine_protective_blocks_low_priority_task(self):
        """cortisol 过高时触发内分泌门控，低优先级任务被阻断"""
        scheduler = _make_scheduler(
            {
                "mood_state": "steady",
                "emotion_tag": "NEUTRAL",
                "endocrine_state": {
                    "cortisol_level": 0.92,
                    "energy_reserve": 0.35,
                    "gate_state": "protective",
                },
            }
        )
        task = _make_task(TaskPriority.LOW)
        scheduler._registry.get = MagicMock(return_value=task)

        result = scheduler._trigger_task(task_id="task-001")
        assert result["status"] == "REJECTED"
        assert result["reason_code"] == "ENDOCRINE_GATE_BLOCKED"
        assert result["gate_state"] == "protective"

    def test_endocrine_open_allows_low_priority_task(self):
        """内分泌 gate=open 时，LOW 任务不被额外阻断"""
        scheduler = _make_scheduler(
            {
                "mood_state": "steady",
                "emotion_tag": "NEUTRAL",
                "endocrine_state": {
                    "cortisol_level": 0.25,
                    "energy_reserve": 0.62,
                    "gate_state": "open",
                },
            }
        )
        task = _make_task(TaskPriority.LOW)
        scheduler._registry.get = MagicMock(return_value=task)

        result = scheduler._trigger_task(task_id="task-001")
        assert result.get("reason_code") not in {"EMOTION_GATE_BLOCKED", "ENDOCRINE_GATE_BLOCKED"}

    def test_low_risk_allowlist_task_bypasses_protective_gate(self):
        """低风险白名单任务在 protective 下保持运行（内在连续性）。"""
        scheduler = _make_scheduler(
            {
                "mood_state": "critical",
                "emotion_tag": "ANXIOUS",
                "endocrine_state": {
                    "cortisol_level": 0.95,
                    "energy_reserve": 0.08,
                    "gate_state": "protective",
                },
            }
        )
        task = _make_task(TaskPriority.LOW, task_id="inner_world_tick")
        scheduler._registry.get = MagicMock(return_value=task)

        result = scheduler._trigger_task(task_id="inner_world_tick")
        assert result.get("reason_code") not in {
            "EMOTION_GATE_BLOCKED",
            "ENDOCRINE_GATE_BLOCKED",
            "ENDOCRINE_PROACTIVE_BLOCKED",
        }

    def test_proactive_task_blocked_by_cautious_endocrine_gate(self):
        """主动任务在 cautious 内分泌态下被抑制。"""
        scheduler = _make_scheduler(
            {
                "mood_state": "steady",
                "emotion_tag": "NEUTRAL",
                "endocrine_state": {
                    "cortisol_level": 0.66,
                    "energy_reserve": 0.28,
                    "gate_state": "cautious",
                },
            }
        )
        task = _make_task(TaskPriority.LOW, task_id="proactive_talk_periodic")
        scheduler._registry.get = MagicMock(return_value=task)

        result = scheduler._trigger_task(task_id="proactive_talk_periodic")
        assert result["status"] == "REJECTED"
        assert result["reason_code"] == "ENDOCRINE_PROACTIVE_BLOCKED"

    def test_proactive_task_uses_endocrine_gate_not_mood_only(self):
        """主动任务改为看 endocrine：mood=critical 但 gate=open 时允许执行。"""
        scheduler = _make_scheduler(
            {
                "mood_state": "critical",
                "emotion_tag": "ANXIOUS",
                "endocrine_state": {
                    "cortisol_level": 0.25,
                    "energy_reserve": 0.72,
                    "gate_state": "open",
                },
            }
        )
        task = _make_task(TaskPriority.LOW, task_id="proactive_talk_periodic")
        scheduler._registry.get = MagicMock(return_value=task)

        result = scheduler._trigger_task(task_id="proactive_talk_periodic")
        assert result.get("reason_code") not in {
            "EMOTION_GATE_BLOCKED",
            "ENDOCRINE_GATE_BLOCKED",
            "ENDOCRINE_PROACTIVE_BLOCKED",
        }

    def test_proactive_task_blocked_by_recent_immune_block_signal(self):
        """主动任务在近期 immune=BLOCK 时被抑制。"""
        now_ms = int(time.time() * 1000)
        scheduler = _make_scheduler(
            {
                "mood_state": "steady",
                "emotion_tag": "NEUTRAL",
                "endocrine_state": {
                    "cortisol_level": 0.25,
                    "energy_reserve": 0.72,
                    "gate_state": "open",
                },
            },
            immune_state={
                "last_inspection": {
                    "decision": "BLOCK",
                    "risk_level": "high",
                    "reason_codes": ["PROMPT_EXFILTRATION_ATTEMPT"],
                    "ts_ms": now_ms,
                }
            },
        )
        task = _make_task(TaskPriority.LOW, task_id="proactive_talk_periodic")
        scheduler._registry.get = MagicMock(return_value=task)

        result = scheduler._trigger_task(task_id="proactive_talk_periodic")
        assert result["status"] == "REJECTED"
        assert result["reason_code"] == "IMMUNE_PROACTIVE_SUPPRESSED"
        assert result["gate_state"] == "immune:block"

    def test_non_proactive_task_not_blocked_by_immune_signal(self):
        """非主动任务不受 immune 主动抑制门禁影响。"""
        now_ms = int(time.time() * 1000)
        scheduler = _make_scheduler(
            {
                "mood_state": "steady",
                "emotion_tag": "NEUTRAL",
                "endocrine_state": {
                    "cortisol_level": 0.25,
                    "energy_reserve": 0.72,
                    "gate_state": "open",
                },
            },
            immune_state={
                "last_inspection": {
                    "decision": "BLOCK",
                    "risk_level": "high",
                    "reason_codes": ["PROMPT_EXFILTRATION_ATTEMPT"],
                    "ts_ms": now_ms,
                }
            },
        )
        task = _make_task(TaskPriority.LOW, task_id="inner_world_tick")
        scheduler._registry.get = MagicMock(return_value=task)

        result = scheduler._trigger_task(task_id="inner_world_tick")
        assert result.get("reason_code") != "IMMUNE_PROACTIVE_SUPPRESSED"

    def test_stale_immune_block_signal_does_not_suppress_proactive_task(self, monkeypatch: pytest.MonkeyPatch):
        """超过 cooldown 的 immune 信号不再抑制主动任务。"""
        monkeypatch.setenv("HOST_SCHEDULER_IMMUNE_PROACTIVE_COOLDOWN_MS", "300")
        now_ms = int(time.time() * 1000)
        scheduler = _make_scheduler(
            {
                "mood_state": "steady",
                "emotion_tag": "NEUTRAL",
                "endocrine_state": {
                    "cortisol_level": 0.25,
                    "energy_reserve": 0.72,
                    "gate_state": "open",
                },
            },
            immune_state={
                "last_inspection": {
                    "decision": "BLOCK",
                    "risk_level": "high",
                    "reason_codes": ["PROMPT_EXFILTRATION_ATTEMPT"],
                    "ts_ms": now_ms - 5000,
                }
            },
        )
        task = _make_task(TaskPriority.LOW, task_id="proactive_talk_periodic")
        scheduler._registry.get = MagicMock(return_value=task)

        result = scheduler._trigger_task(task_id="proactive_talk_periodic")
        assert result.get("reason_code") != "IMMUNE_PROACTIVE_SUPPRESSED"
