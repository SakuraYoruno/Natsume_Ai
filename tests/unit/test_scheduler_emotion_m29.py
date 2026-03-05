"""M29-N：NatsumeScheduler 心跳感知情感状态测试（T-SCHED-EMOTION-01~02）"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from host_brain.scheduler.scheduler import NatsumeScheduler
from host_brain.scheduler.task_registry import (
    SchedulerTaskConfig,
    SchedulerTaskRegistry,
    TaskPriority,
    TriggerMode,
)


def _make_task(priority: TaskPriority) -> SchedulerTaskConfig:
    return SchedulerTaskConfig(
        task_id="task-001",
        trigger_mode=TriggerMode.INTERVAL,
        trigger_config={"seconds": 60},
        intent_template={"skill_name": "heartbeat"},
        priority=priority,
        enabled=True,
    )


def _make_scheduler(emotion_state: dict | None = None) -> NatsumeScheduler:
    registry = MagicMock(spec=SchedulerTaskRegistry)
    health = MagicMock()
    health.update_task_state = MagicMock()
    get_emotion_state = (lambda: emotion_state) if emotion_state else None
    return NatsumeScheduler(
        registry=registry,
        health_reporter=health,
        get_emotion_state=get_emotion_state,
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
