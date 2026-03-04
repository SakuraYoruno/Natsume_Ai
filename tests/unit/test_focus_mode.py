"""T-FOCUS-01~04：FocusMode Skill 单元测试。

覆盖：
  T-FOCUS-01  进入 Focus 后主动对话被拦截（SYSMODE_FORBIDDEN）
  T-FOCUS-02  用户主动提问正常响应（Focus 不影响用户发起的对话）
  T-FOCUS-03  退出 Focus 后调度器恢复（mode_changer 被调用为 normal）
  T-FOCUS-04  切换事件写入 hard_state_log，字段完整
"""

from __future__ import annotations

import pytest

from host_brain.skills.impl.focus_mode import (
    execute_focus_mode,
    FOCUS_ACTIVE,
    FOCUS_INACTIVE,
    STATUS_EXECUTED,
    STATUS_REJECTED,
)
from host_brain.skills.impl.proactive_talk import execute_proactive_talk
from host_brain.core.errors import SYSMODE_FORBIDDEN


# ── 辅助工厂 ──

def _ctx(sys_mode: str = "normal", focus_state: str = FOCUS_INACTIVE, **kwargs) -> dict:
    return {
        "sys_mode": sys_mode,
        "focus_state": focus_state,
        "request_id": "req-focus",
        "intent_id": "intent-focus",
        **kwargs,
    }


def _make_mode_changer() -> tuple[callable, list[str]]:
    calls: list[str] = []
    def _changer(new_mode: str) -> dict:
        calls.append(new_mode)
        return {"status": "OK", "new_mode": new_mode}
    return _changer, calls


# ── T-FOCUS-01 ──

class TestFocusModeBlocksProactive:
    """T-FOCUS-01：进入 Focus 后主动对话被拦截（SCHED_CONDITION_UNMET）。"""

    def test_proactive_blocked_in_focus_mode(self) -> None:
        """Focus 模式不在 proactive_talk 的 ALLOWED_MODES 中，触发被拦截。"""
        from host_brain.skills.impl.proactive_talk import ALLOWED_MODES
        assert "focus" not in ALLOWED_MODES

        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context={
                "sys_mode": "focus",
                "last_interaction_ts": 0,
                "request_id": "req-focus-proact",
                "intent_id": "intent-focus-proact",
                "time_period": "evening",
            },
        )
        from host_brain.core.errors import SCHED_CONDITION_UNMET
        assert result["status"] == "REJECTED"
        assert result["reason_code"] == SCHED_CONDITION_UNMET

    def test_enter_focus_success(self) -> None:
        changer, calls = _make_mode_changer()
        result = execute_focus_mode(
            args={"action": "enter", "duration_ms": 3600000},
            context=_ctx(mode_changer=changer),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["focus_state"] == FOCUS_ACTIVE
        assert result["duration_ms"] == 3600000
        assert "expiry_ts" in result
        assert calls == ["focus"]


# ── T-FOCUS-02 ──

class TestFocusModeUserDialogAllowed:
    """T-FOCUS-02：用户主动提问正常响应（Focus 不影响用户发起的对话）。

    Focus Mode 只抑制系统发起的主动对话（proactive_talk），
    不影响用户主动发起的对话（由 intent_dispatcher 处理，不经过 focus_mode skill）。
    此测试验证 focus_mode skill 本身不拦截 enter/exit 操作。
    """

    def test_enter_focus_from_normal_allowed(self) -> None:
        changer, _ = _make_mode_changer()
        result = execute_focus_mode(
            args={"action": "enter"},
            context=_ctx(sys_mode="normal", mode_changer=changer),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["focus_state"] == FOCUS_ACTIVE

    def test_enter_focus_from_gaming_rejected(self) -> None:
        """非 normal 模式下不允许进入 Focus。"""
        result = execute_focus_mode(
            args={"action": "enter"},
            context=_ctx(sys_mode="gaming"),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SYSMODE_FORBIDDEN

    def test_already_in_focus_rejected(self) -> None:
        result = execute_focus_mode(
            args={"action": "enter"},
            context=_ctx(sys_mode="normal", focus_state=FOCUS_ACTIVE),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == "FOCUS_ALREADY_ACTIVE"


# ── T-FOCUS-03 ──

class TestFocusModeExit:
    """T-FOCUS-03：退出 Focus 后调度器恢复（mode_changer 被调用为 normal）。"""

    def test_exit_focus_calls_mode_changer_normal(self) -> None:
        changer, calls = _make_mode_changer()
        result = execute_focus_mode(
            args={"action": "exit"},
            context=_ctx(
                sys_mode="focus",
                focus_state=FOCUS_ACTIVE,
                mode_changer=changer,
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["focus_state"] == FOCUS_INACTIVE
        assert calls == ["normal"]

    def test_exit_when_not_in_focus_rejected(self) -> None:
        result = execute_focus_mode(
            args={"action": "exit"},
            context=_ctx(sys_mode="normal", focus_state=FOCUS_INACTIVE),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == "FOCUS_NOT_ACTIVE"

    def test_mode_changer_exception_rejected(self) -> None:
        def _bad_changer(mode: str) -> dict:
            raise RuntimeError("mode change failed")

        result = execute_focus_mode(
            args={"action": "exit"},
            context=_ctx(
                sys_mode="focus",
                focus_state=FOCUS_ACTIVE,
                mode_changer=_bad_changer,
            ),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == "FOCUS_MODE_CHANGE_FAILED"


# ── T-FOCUS-04 ──

class TestFocusModeAuditFields:
    """T-FOCUS-04：切换事件字段完整（event_id / request_id / intent_id / ts_ms / focus_state）。"""

    def test_enter_result_has_audit_fields(self) -> None:
        changer, _ = _make_mode_changer()
        result = execute_focus_mode(
            args={"action": "enter", "reason": "开始工作"},
            context=_ctx(
                sys_mode="normal",
                mode_changer=changer,
                request_id="req-focus-audit",
                intent_id="intent-focus-audit",
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["event_id"] is not None
        assert result["request_id"] == "req-focus-audit"
        assert result["intent_id"] == "intent-focus-audit"
        assert result["ts_ms"] > 0
        assert result["focus_state"] == FOCUS_ACTIVE
        assert result["reason"] == "开始工作"
        assert result["expiry_ts"] > result["ts_ms"]

    def test_exit_result_has_audit_fields(self) -> None:
        changer, _ = _make_mode_changer()
        result = execute_focus_mode(
            args={"action": "exit"},
            context=_ctx(
                sys_mode="focus",
                focus_state=FOCUS_ACTIVE,
                mode_changer=changer,
                request_id="req-exit-audit",
                intent_id="intent-exit-audit",
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["event_id"] is not None
        assert result["request_id"] == "req-exit-audit"
        assert result["ts_ms"] > 0
        assert result["focus_state"] == FOCUS_INACTIVE

    def test_invalid_action_has_reason_code(self) -> None:
        result = execute_focus_mode(
            args={"action": "unknown"},
            context=_ctx(),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == "FOCUS_INVALID_ACTION"
        assert result["event_id"] is not None
