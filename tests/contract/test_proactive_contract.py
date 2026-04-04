"""Contract tests for Proactive Talk Skill (TASK-0095).

覆盖 T-Proactive-01（空闲触发+问候生成）、T-Proactive-02（阈值未达/模式拒绝/降级）。
"""

from __future__ import annotations

import pytest

from host_brain.skills.impl.proactive_talk import (
    execute_proactive_talk,
    STATUS_EXECUTED,
    STATUS_REJECTED,
    STATUS_DEGRADED,
    DEFAULT_IDLE_THRESHOLD_MS,
)
from host_brain.core.errors import SCHED_CONDITION_UNMET, SCHED_NO_CONTENT
from host_brain.core.ids import now_ms


def _mock_llm_gen(input_data):
    period = input_data.get("time_period", "")
    return {"greeting": f"好久不见，{period}了呢～"}


def _mock_ui_pusher(msg):
    return {"status": "OK", "pushed": True}


class TestProactiveTalkTrigger:
    """T-Proactive-01：空闲触发+问候生成。"""

    def test_execute_normal_mode_idle(self) -> None:
        ts = now_ms()
        result = execute_proactive_talk(
            {},
            {
                "sys_mode": "normal",
                "last_interaction_ts": ts - DEFAULT_IDLE_THRESHOLD_MS - 1000,
                "llm_generator": _mock_llm_gen,
                "ui_pusher": _mock_ui_pusher,
                "time_period": "evening",
            },
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["message"]["type"] == "proactive_talk"
        assert "好久不见" in result["message"]["content"]

    def test_execute_sleep_mode(self) -> None:
        ts = now_ms()
        result = execute_proactive_talk(
            {},
            {
                "sys_mode": "sleep",
                "last_interaction_ts": ts - DEFAULT_IDLE_THRESHOLD_MS - 1000,
                "llm_generator": _mock_llm_gen,
                "time_period": "morning",
            },
        )
        assert result["status"] == STATUS_EXECUTED

    def test_no_llm_uses_template(self) -> None:
        ts = now_ms()
        result = execute_proactive_talk(
            {},
            {
                "sys_mode": "normal",
                "last_interaction_ts": ts - DEFAULT_IDLE_THRESHOLD_MS - 1000,
                "time_period": "morning",
                "user_name": "小明",
            },
        )
        assert result["status"] == STATUS_EXECUTED
        assert "小明" in result["message"]["content"]
        assert "早上好" in result["message"]["content"]

    def test_no_last_interaction_always_triggers(self) -> None:
        """无 last_interaction_ts → 视为超长空闲，总是触发。"""
        result = execute_proactive_talk(
            {},
            {
                "sys_mode": "normal",
                "llm_generator": _mock_llm_gen,
                "time_period": "noon",
            },
        )
        assert result["status"] == STATUS_EXECUTED

    def test_ui_push_called(self) -> None:
        pushed = []
        def capturing_pusher(msg):
            pushed.append(msg)
            return {"status": "OK"}

        ts = now_ms()
        execute_proactive_talk(
            {},
            {
                "sys_mode": "normal",
                "last_interaction_ts": ts - DEFAULT_IDLE_THRESHOLD_MS - 1000,
                "llm_generator": _mock_llm_gen,
                "ui_pusher": capturing_pusher,
                "time_period": "evening",
            },
        )
        assert len(pushed) == 1
        assert pushed[0]["type"] == "proactive_talk"

    def test_topic_hint_passed_to_llm(self) -> None:
        received = []
        def capturing_llm(inp):
            received.append(inp)
            return {"greeting": "hi"}

        ts = now_ms()
        execute_proactive_talk(
            {"topic_hint": "聊聊天气"},
            {
                "sys_mode": "normal",
                "last_interaction_ts": ts - DEFAULT_IDLE_THRESHOLD_MS - 1000,
                "llm_generator": capturing_llm,
                "time_period": "afternoon",
            },
        )
        assert received[0]["topic_hint"] == "聊聊天气"


class TestProactiveTalkConditions:
    """T-Proactive-02：阈值未达/模式拒绝/降级。"""

    def test_idle_threshold_not_met(self) -> None:
        ts = now_ms()
        result = execute_proactive_talk(
            {},
            {
                "sys_mode": "normal",
                "last_interaction_ts": ts - 1000,  # 只过了 1 秒
            },
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET

    def test_gaming_mode_rejected(self) -> None:
        result = execute_proactive_talk(
            {},
            {"sys_mode": "gaming"},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET

    def test_focus_mode_rejected(self) -> None:
        result = execute_proactive_talk(
            {},
            {"sys_mode": "focus"},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET

    def test_maintenance_rejected(self) -> None:
        result = execute_proactive_talk(
            {},
            {"sys_mode": "maintenance"},
        )
        assert result["status"] == STATUS_REJECTED

    def test_llm_failure_degraded(self) -> None:
        ts = now_ms()
        def failing_llm(_):
            raise RuntimeError("GPU OOM")

        result = execute_proactive_talk(
            {},
            {
                "sys_mode": "normal",
                "last_interaction_ts": ts - DEFAULT_IDLE_THRESHOLD_MS - 1000,
                "llm_generator": failing_llm,
                "time_period": "afternoon",
            },
        )
        assert result["status"] == STATUS_DEGRADED

    def test_empty_greeting_rejected(self) -> None:
        ts = now_ms()
        def empty_llm(_):
            return {"greeting": "  "}

        result = execute_proactive_talk(
            {},
            {
                "sys_mode": "normal",
                "last_interaction_ts": ts - DEFAULT_IDLE_THRESHOLD_MS - 1000,
                "llm_generator": empty_llm,
                "time_period": "afternoon",
            },
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_NO_CONTENT

    def test_runtime_fallback_marker_uses_template(self) -> None:
        ts = now_ms()

        def empty_llm(_):
            return {"greeting": "", "fallback_to_template": True}

        result = execute_proactive_talk(
            {},
            {
                "sys_mode": "normal",
                "last_interaction_ts": ts - DEFAULT_IDLE_THRESHOLD_MS - 1000,
                "llm_generator": empty_llm,
                "time_period": "afternoon",
                "user_name": "小明",
            },
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["template_fallback_used"] is True
        assert "小明" in result["message"]["content"]

    def test_custom_idle_threshold(self) -> None:
        ts = now_ms()
        # 自定义阈值 1 小时
        custom_threshold = 60 * 60 * 1000
        result = execute_proactive_talk(
            {"idle_threshold_ms": custom_threshold},
            {
                "sys_mode": "normal",
                "last_interaction_ts": ts - custom_threshold + 1000,
                "time_period": "morning",
            },
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET
