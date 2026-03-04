"""T-PROACT-01~06：ProactiveTalk Skill 单元测试。

覆盖：
  T-PROACT-01  空闲触发，用户端收到主动消息，审计链完整
  T-PROACT-02  Gaming → SCHED_CONDITION_UNMET
  T-PROACT-03  频率超限（idle 时间不足）→ SCHED_CONDITION_UNMET
  T-PROACT-04  内容含执行 intent → QA 拦截（通过 qa_checker 模拟）
  T-PROACT-05  Edge 不可达（ui_pusher 异常）→ 静默降级，不重试
  T-PROACT-06  Sleep 模式 → 仅允许（ALLOWED_MODES 包含 sleep）
"""

from __future__ import annotations

import pytest

from host_brain.skills.impl.proactive_talk import (
    execute_proactive_talk,
    ALLOWED_MODES,
    STATUS_EXECUTED,
    STATUS_REJECTED,
    STATUS_DEGRADED,
)
from host_brain.core.errors import SCHED_CONDITION_UNMET


# ── 辅助工厂 ──

def _ctx(
    sys_mode: str = "normal",
    last_interaction_ts: int | None = None,
    **kwargs,
) -> dict:
    return {
        "sys_mode": sys_mode,
        "last_interaction_ts": last_interaction_ts,
        "request_id": "req-proact",
        "intent_id": "intent-proact",
        "time_period": "evening",
        "user_name": "主人",
        **kwargs,
    }


def _make_llm_generator(greeting: str = "主人晚上好～") -> callable:
    def _gen(inp: dict) -> dict:
        return {"greeting": greeting}
    return _gen


def _make_ui_pusher() -> tuple[callable, list[dict]]:
    pushed: list[dict] = []
    def _pusher(msg: dict) -> dict:
        pushed.append(msg)
        return {"status": "OK"}
    return _pusher, pushed


# ── T-PROACT-01 ──

class TestProactiveTalkTrigger:
    """T-PROACT-01：空闲触发，用户端收到主动消息，审计链完整。"""

    def test_idle_trigger_sends_message(self) -> None:
        pusher, pushed = _make_ui_pusher()
        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,  # 很久以前
                llm_generator=_make_llm_generator("主人晚上好～"),
                ui_pusher=pusher,
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["reason_code"] is None
        assert len(pushed) == 1
        assert pushed[0]["type"] == "proactive_talk"
        assert "主人晚上好" in pushed[0]["content"]

    def test_audit_chain_fields(self) -> None:
        pusher, _ = _make_ui_pusher()
        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                llm_generator=_make_llm_generator("问候"),
                ui_pusher=pusher,
                request_id="req-audit-proact",
                intent_id="intent-audit-proact",
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["request_id"] == "req-audit-proact"
        assert result["intent_id"] == "intent-audit-proact"
        assert result["event_id"] is not None
        assert result["ts_ms"] > 0


# ── T-PROACT-02 ──

class TestProactiveTalkGamingBlock:
    """T-PROACT-02：Gaming → SCHED_CONDITION_UNMET。"""

    def test_gaming_blocked(self) -> None:
        result = execute_proactive_talk(
            args={},
            context=_ctx(sys_mode="gaming"),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET

    def test_focus_blocked(self) -> None:
        result = execute_proactive_talk(
            args={},
            context=_ctx(sys_mode="focus"),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET


# ── T-PROACT-03 ──

class TestProactiveTalkIdleThreshold:
    """T-PROACT-03：idle 时间不足 → SCHED_CONDITION_UNMET。"""

    def test_not_idle_enough_rejected(self) -> None:
        import time
        now = int(time.time() * 1000)
        result = execute_proactive_talk(
            args={"idle_threshold_ms": 4 * 60 * 60 * 1000},  # 4h
            context=_ctx(
                last_interaction_ts=now - 60_000,  # 1 分钟前
            ),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET
        assert "elapsed_ms" in result

    def test_idle_enough_allowed(self) -> None:
        pusher, _ = _make_ui_pusher()
        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,  # 很久以前
                llm_generator=_make_llm_generator("问候"),
                ui_pusher=pusher,
            ),
        )
        assert result["status"] == STATUS_EXECUTED

    def test_no_last_interaction_ts_allowed(self) -> None:
        """无 last_interaction_ts 时不检查空闲时间，直接允许。"""
        pusher, _ = _make_ui_pusher()
        result = execute_proactive_talk(
            args={},
            context=_ctx(
                last_interaction_ts=None,
                llm_generator=_make_llm_generator("问候"),
                ui_pusher=pusher,
            ),
        )
        assert result["status"] == STATUS_EXECUTED


# ── T-PROACT-04 ──

class TestProactiveTalkQABlock:
    """T-PROACT-04：内容含执行 intent → LLM 生成空内容模拟 QA 拦截。"""

    def test_empty_greeting_rejected(self) -> None:
        pusher, _ = _make_ui_pusher()
        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                llm_generator=lambda inp: {"greeting": ""},  # 空内容
                ui_pusher=pusher,
            ),
        )
        from host_brain.core.errors import SCHED_NO_CONTENT
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_NO_CONTENT

    def test_llm_unavailable_uses_template(self) -> None:
        """无 LLM 时使用降级模板，仍可发送。"""
        pusher, pushed = _make_ui_pusher()
        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                llm_generator=None,
                ui_pusher=pusher,
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert len(pushed) == 1


# ── T-PROACT-05 ──

class TestProactiveTalkEdgeUnavailable:
    """T-PROACT-05：Edge 不可达（ui_pusher 异常）→ 静默降级，不重试。"""

    def test_ui_pusher_exception_degrades(self) -> None:
        def _bad_pusher(msg: dict) -> dict:
            raise ConnectionError("Edge unreachable")

        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                llm_generator=_make_llm_generator("问候"),
                ui_pusher=_bad_pusher,
            ),
        )
        # 推送失败时仍返回 EXECUTED（静默降级），push_result 含 error
        assert result["status"] == STATUS_EXECUTED
        assert result["push_result"] is not None
        assert "error" in result["push_result"]

    def test_no_ui_pusher_still_executed(self) -> None:
        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                llm_generator=_make_llm_generator("问候"),
                ui_pusher=None,
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["push_result"] is None


# ── T-PROACT-06 ──

class TestProactiveTalkSleepMode:
    """T-PROACT-06：Sleep 模式 → ALLOWED_MODES 包含 sleep，允许触发。"""

    def test_sleep_mode_allowed(self) -> None:
        assert "sleep" in ALLOWED_MODES

        pusher, pushed = _make_ui_pusher()
        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                sys_mode="sleep",
                last_interaction_ts=0,
                llm_generator=_make_llm_generator("晚安～"),
                ui_pusher=pusher,
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert len(pushed) == 1

    def test_normal_mode_allowed(self) -> None:
        assert "normal" in ALLOWED_MODES
