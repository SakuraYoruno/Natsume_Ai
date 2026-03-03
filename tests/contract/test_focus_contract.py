"""Contract tests for Focus Mode Skill (TASK-0096).

覆盖 T-Focus-01（进入/退出/过期）、T-Focus-02（重复进入/非Normal模式拒绝/未激活退出）。
"""

from __future__ import annotations

import pytest

from host_brain.skills.impl.focus_mode import (
    execute_focus_mode,
    STATUS_EXECUTED,
    STATUS_REJECTED,
    FOCUS_ACTIVE,
    FOCUS_INACTIVE,
    DEFAULT_DURATION_MS,
    ACTION_ENTER,
    ACTION_EXIT,
)
from host_brain.core.errors import SYSMODE_FORBIDDEN


def _mock_mode_changer(new_mode):
    return {"status": "OK", "new_mode": new_mode}


class TestFocusModeEnterExit:
    """T-Focus-01：进入/退出专注模式。"""

    def test_enter_from_normal(self) -> None:
        result = execute_focus_mode(
            {"action": "enter"},
            {"sys_mode": "normal", "focus_state": FOCUS_INACTIVE, "mode_changer": _mock_mode_changer},
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["focus_state"] == FOCUS_ACTIVE
        assert result["duration_ms"] == DEFAULT_DURATION_MS
        assert result["expiry_ts"] > result["ts_ms"]

    def test_enter_custom_duration(self) -> None:
        result = execute_focus_mode(
            {"action": "enter", "duration_ms": 60 * 60 * 1000},
            {"sys_mode": "normal", "focus_state": FOCUS_INACTIVE, "mode_changer": _mock_mode_changer},
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["duration_ms"] == 60 * 60 * 1000

    def test_enter_with_reason(self) -> None:
        result = execute_focus_mode(
            {"action": "enter", "reason": "写代码中"},
            {"sys_mode": "normal", "focus_state": FOCUS_INACTIVE, "mode_changer": _mock_mode_changer},
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["reason"] == "写代码中"

    def test_exit_focus(self) -> None:
        result = execute_focus_mode(
            {"action": "exit"},
            {"sys_mode": "focus", "focus_state": FOCUS_ACTIVE, "mode_changer": _mock_mode_changer},
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["focus_state"] == FOCUS_INACTIVE

    def test_mode_changer_called_on_enter(self) -> None:
        changed = []
        def capturing_changer(mode):
            changed.append(mode)
            return {"status": "OK"}

        execute_focus_mode(
            {"action": "enter"},
            {"sys_mode": "normal", "focus_state": FOCUS_INACTIVE, "mode_changer": capturing_changer},
        )
        assert changed == ["focus"]

    def test_mode_changer_called_on_exit(self) -> None:
        changed = []
        def capturing_changer(mode):
            changed.append(mode)
            return {"status": "OK"}

        execute_focus_mode(
            {"action": "exit"},
            {"sys_mode": "focus", "focus_state": FOCUS_ACTIVE, "mode_changer": capturing_changer},
        )
        assert changed == ["normal"]

    def test_enter_without_mode_changer(self) -> None:
        """无 mode_changer 也不报错。"""
        result = execute_focus_mode(
            {"action": "enter"},
            {"sys_mode": "normal", "focus_state": FOCUS_INACTIVE},
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["change_result"] is None


class TestFocusModeRejections:
    """T-Focus-02：各种拒绝场景。"""

    def test_enter_from_gaming_rejected(self) -> None:
        result = execute_focus_mode(
            {"action": "enter"},
            {"sys_mode": "gaming", "focus_state": FOCUS_INACTIVE},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SYSMODE_FORBIDDEN

    def test_enter_from_sleep_rejected(self) -> None:
        result = execute_focus_mode(
            {"action": "enter"},
            {"sys_mode": "sleep", "focus_state": FOCUS_INACTIVE},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SYSMODE_FORBIDDEN

    def test_enter_from_maintenance_rejected(self) -> None:
        result = execute_focus_mode(
            {"action": "enter"},
            {"sys_mode": "maintenance", "focus_state": FOCUS_INACTIVE},
        )
        assert result["status"] == STATUS_REJECTED

    def test_double_enter_rejected(self) -> None:
        result = execute_focus_mode(
            {"action": "enter"},
            {"sys_mode": "normal", "focus_state": FOCUS_ACTIVE},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == "FOCUS_ALREADY_ACTIVE"

    def test_exit_not_active_rejected(self) -> None:
        result = execute_focus_mode(
            {"action": "exit"},
            {"sys_mode": "normal", "focus_state": FOCUS_INACTIVE},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == "FOCUS_NOT_ACTIVE"

    def test_invalid_action_rejected(self) -> None:
        result = execute_focus_mode(
            {"action": "toggle"},
            {"sys_mode": "normal"},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == "FOCUS_INVALID_ACTION"

    def test_mode_changer_failure_rejected(self) -> None:
        def failing_changer(_):
            raise RuntimeError("cannot switch")

        result = execute_focus_mode(
            {"action": "enter"},
            {"sys_mode": "normal", "focus_state": FOCUS_INACTIVE, "mode_changer": failing_changer},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == "FOCUS_MODE_CHANGE_FAILED"
