"""M27-K：NatsumeScheduler._build_system_intent 情感状态测试（T-SCHED-EMO-01~02）"""

from __future__ import annotations

import pytest
from host_brain.scheduler.scheduler import NatsumeScheduler
from host_brain.scheduler.task_registry import SchedulerTaskConfig, SchedulerTaskRegistry, TriggerMode, TaskPriority
from host_brain.scheduler.health import SchedulerHealthReporter


def _make_scheduler() -> NatsumeScheduler:
    registry = SchedulerTaskRegistry()
    health = SchedulerHealthReporter(registry)
    return NatsumeScheduler(registry=registry, health_reporter=health)


def _make_task() -> SchedulerTaskConfig:
    return SchedulerTaskConfig(
        task_id="test_task",
        trigger_mode=TriggerMode.INTERVAL,
        trigger_config={"seconds": 900},
        intent_template={"skill_name": "test_skill"},
        priority=TaskPriority.MEDIUM,
        enabled=True,
        max_consecutive_failures=3,
    )


class TestSchedulerBuildSystemIntent:
    """T-SCHED-EMO-01~02：NatsumeScheduler._build_system_intent 情感状态测试"""

    def test_build_system_intent_contains_emotion_fields(self):
        """T-SCHED-EMO-01：_build_system_intent 返回的 intent 包含 emotion_tag 和 mood_state 字段"""
        scheduler = _make_scheduler()
        task = _make_task()
        intent = scheduler._build_system_intent(
            task,
            intent_id="intent-001",
            request_id="req-001",
            emotion_tag="HAPPY",
            mood_state="engaged",
            endocrine_state={"gate_state": "open", "cortisol_level": 0.3},
        )
        assert intent["emotion_tag"] == "HAPPY"
        assert intent["mood_state"] == "engaged"
        assert intent["endocrine_state"]["gate_state"] == "open"

    def test_build_system_intent_default_emotion_fields(self):
        """T-SCHED-EMO-02：_build_system_intent 默认 emotion_tag="NEUTRAL", mood_state="steady" """
        scheduler = _make_scheduler()
        task = _make_task()
        intent = scheduler._build_system_intent(
            task,
            intent_id="intent-001",
            request_id="req-001",
        )
        assert intent["emotion_tag"] == "NEUTRAL"
        assert intent["mood_state"] == "steady"
        assert intent["endocrine_state"] == {}
