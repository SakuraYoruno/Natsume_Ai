"""M32-D：主动情感触发器专项测试（T-PROACTIVE-EMO-01~04）"""

from __future__ import annotations

import pytest
from host_brain.core.metabolism import (
    register_emotion_trigger,
    evaluate_emotion_triggers,
    _PROACTIVE_TRIGGER_RULES,
)


def _clear_triggers() -> None:
    """清空触发规则列表（测试隔离）"""
    _PROACTIVE_TRIGGER_RULES.clear()


class TestProactiveEmotionTrigger:
    """T-PROACTIVE-EMO-01~04：主动情感触发器测试"""

    def setup_method(self) -> None:
        """每个测试前清空触发规则"""
        _clear_triggers()

    def teardown_method(self) -> None:
        """每个测试后清空触发规则"""
        _clear_triggers()

    def test_trigger_fires_when_condition_met(self):
        """T-PROACTIVE-EMO-01：注册触发规则后，满足条件时返回触发事件"""
        register_emotion_trigger(
            condition_fn=lambda ctx: ctx.get("silence_seconds", 0) > 600,
            emotion_tag="CARING",
            intensity=0.6,
        )
        context = {"silence_seconds": 700}
        triggered = evaluate_emotion_triggers(context)
        assert len(triggered) >= 1
        assert triggered[0]["emotion_tag"] == "CARING"
        assert triggered[0]["intensity"] == 0.6
        assert triggered[0]["source"] == "proactive_trigger"

    def test_trigger_not_fires_when_condition_not_met(self):
        """T-PROACTIVE-EMO-02：不满足条件时，返回空列表"""
        register_emotion_trigger(
            condition_fn=lambda ctx: ctx.get("silence_seconds", 0) > 600,
            emotion_tag="CARING",
            intensity=0.6,
        )
        context = {"silence_seconds": 100}
        triggered = evaluate_emotion_triggers(context)
        assert triggered == []

    def test_triggered_event_has_required_fields(self):
        """T-PROACTIVE-EMO-03：触发事件含 emotion_tag/intensity/source 字段"""
        register_emotion_trigger(
            condition_fn=lambda ctx: True,
            emotion_tag="HAPPY",
            intensity=0.8,
        )
        triggered = evaluate_emotion_triggers({})
        assert len(triggered) >= 1
        event = triggered[0]
        assert "emotion_tag" in event
        assert "intensity" in event
        assert "source" in event
        assert event["emotion_tag"] == "HAPPY"
        assert event["intensity"] == 0.8

    def test_exception_in_trigger_skips_rule(self):
        """T-PROACTIVE-EMO-04：触发规则抛出异常时，跳过该规则，不影响其他规则"""
        # 注册一个会抛出异常的规则
        register_emotion_trigger(
            condition_fn=lambda ctx: 1 / 0,  # 会抛出 ZeroDivisionError
            emotion_tag="ERROR_TAG",
            intensity=0.5,
        )
        # 注册一个正常的规则
        register_emotion_trigger(
            condition_fn=lambda ctx: True,
            emotion_tag="NORMAL_TAG",
            intensity=0.7,
        )
        triggered = evaluate_emotion_triggers({})
        # 异常规则被跳过，正常规则仍然触发
        assert len(triggered) == 1
        assert triggered[0]["emotion_tag"] == "NORMAL_TAG"

    def test_emotion_tag_normalized_to_uppercase(self):
        """emotion_tag 被规范化为大写"""
        register_emotion_trigger(
            condition_fn=lambda ctx: True,
            emotion_tag="happy",  # 小写
            intensity=0.5,
        )
        triggered = evaluate_emotion_triggers({})
        assert triggered[0]["emotion_tag"] == "HAPPY"

    def test_multiple_triggers_all_fire(self):
        """多个触发规则同时满足时，全部触发"""
        register_emotion_trigger(condition_fn=lambda ctx: True, emotion_tag="HAPPY", intensity=0.5)
        register_emotion_trigger(condition_fn=lambda ctx: True, emotion_tag="EXCITED", intensity=0.7)
        triggered = evaluate_emotion_triggers({})
        assert len(triggered) == 2
        tags = {e["emotion_tag"] for e in triggered}
        assert "HAPPY" in tags
        assert "EXCITED" in tags
