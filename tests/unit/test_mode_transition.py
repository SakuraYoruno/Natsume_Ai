"""ModeTransitionService 单元测试。

覆盖：
- 手动模式切换（合法/非法/相同模式）
- 切换路径校验（VALID_TRANSITIONS）
- 自动规则引擎（on_event）
- 规则开关更新（update_auto_rules）
- 切换历史记录（get_history）
- 状态查询（get_current_status）
- 审计日志写入
"""
from __future__ import annotations

import datetime
import time
from unittest.mock import MagicMock, patch

import pytest

from host_brain.core.metabolism import HardState, ALLOWED_SYS_MODES
from host_brain.core.mode_transition import (
    DEFAULT_AUTO_RULES,
    ModeTransitionService,
    TransitionSource,
    VALID_TRANSITIONS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_hard_state(sys_mode: str = "normal", **kwargs) -> HardState:
    defaults = {
        "energy_level": 60,
        "mood_state": "steady",
        "sys_mode": sys_mode,
        "safety_flag": False,
        "last_event_id": None,
        "ts_ms": int(time.time() * 1000),
    }
    defaults.update(kwargs)
    return HardState(**defaults)


def _make_service(
    initial_mode: str = "normal",
    auto_rules: dict | None = None,
    idle_threshold_ms: int = 7200000,
) -> tuple[ModeTransitionService, MagicMock, MagicMock]:
    """创建测试用 ModeTransitionService 和 mock 依赖。"""
    state_holder = {"state": _make_hard_state(initial_mode)}

    def get_hard_state():
        return state_holder["state"]

    def set_sys_mode(target_mode, **metadata):
        old = state_holder["state"]
        state_holder["state"] = _make_hard_state(target_mode)
        return {
            "from_mode": old.sys_mode,
            "to_mode": target_mode,
            "event_id": metadata["event_id"],
            # The canonical owner commits the caller-bound transition
            # timestamp. Reading the wall clock again makes this identity
            # assertion race across a millisecond boundary under coverage.
            "ts_ms": metadata["ts_ms"],
        }

    mock_get = MagicMock(side_effect=get_hard_state)
    mock_set = MagicMock(side_effect=set_sys_mode)
    mock_ledger = MagicMock()

    service = ModeTransitionService(
        get_hard_state=mock_get,
        set_sys_mode=mock_set,
        ledger=mock_ledger,
        auto_rules=auto_rules,
        idle_threshold_ms=idle_threshold_ms,
    )
    return service, mock_set, mock_ledger


# ---------------------------------------------------------------------------
# 基本切换测试
# ---------------------------------------------------------------------------


class TestManualSwitch:
    """手动模式切换。"""

    def test_normal_to_focus_succeeds(self):
        service, mock_set, _ = _make_service("normal")
        result = service.switch_mode("focus", source=TransitionSource.MANUAL, reason="手动切换")
        assert result["success"] is True
        assert result["from_mode"] == "normal"
        assert result["to_mode"] == "focus"
        assert result["source"] == "manual"
        mock_set.assert_called_once()
        call_args, call_kwargs = mock_set.call_args
        assert call_args == ("focus",)
        assert call_kwargs["source"] == "manual"
        assert call_kwargs["reason"] == "手动切换"
        assert call_kwargs["event_id"] == result["event_id"]
        assert call_kwargs["ts_ms"] == result["ts_ms"]

    def test_normal_to_gaming_succeeds(self):
        service, mock_set, _ = _make_service("normal")
        result = service.switch_mode("gaming")
        assert result["success"] is True
        assert result["to_mode"] == "gaming"

    def test_normal_to_sleep_succeeds(self):
        service, _, _ = _make_service("normal")
        result = service.switch_mode("sleep")
        assert result["success"] is True

    def test_normal_to_maintenance_succeeds(self):
        service, _, _ = _make_service("normal")
        result = service.switch_mode("maintenance")
        assert result["success"] is True

    def test_normal_to_locked_succeeds(self):
        service, _, _ = _make_service("normal")
        result = service.switch_mode("locked")
        assert result["success"] is True

    def test_same_mode_no_op(self):
        """相同模式返回 NO_CHANGE，不能伪造 transition receipt。"""
        service, mock_set, _ = _make_service("normal")
        result = service.switch_mode("normal")
        assert result["success"] is True
        assert result["from_mode"] == "normal"
        assert result["to_mode"] == "normal"
        assert result["event_id"] is None
        assert result["no_change"] is True
        assert result["transition_committed"] is False
        mock_set.assert_not_called()

    def test_same_mode_with_request_identity_can_return_durable_exact_replay(self):
        state = _make_hard_state("focus")
        setter = MagicMock(
            return_value={
                "from_mode": "normal",
                "to_mode": "focus",
                "event_id": "operator-request-1",
                "ts_ms": 123,
                "revision": 2,
                "event_digest": "sha256:" + "a" * 64,
                "exact_replay": True,
                "current_mode": "focus",
                "current_revision": 2,
            }
        )
        service = ModeTransitionService(
            get_hard_state=lambda: state,
            set_sys_mode=setter,
        )
        result = service.switch_mode(
            "focus",
            source=TransitionSource.MANUAL,
            reason="work",
            event_id="operator-request-1",
        )
        assert result["success"] is True
        assert result["exact_replay"] is True
        assert result["event_id"] == "operator-request-1"
        assert result["ts_ms"] == 123
        setter.assert_called_once()

    def test_invalid_target_mode_fails(self):
        service, mock_set, _ = _make_service("normal")
        result = service.switch_mode("nonexistent_mode")
        assert result["success"] is False
        assert "invalid" in (result.get("error") or "").lower()
        mock_set.assert_not_called()


# ---------------------------------------------------------------------------
# 路径校验测试
# ---------------------------------------------------------------------------


class TestTransitionPaths:
    """VALID_TRANSITIONS 切换路径合法性检查。"""

    def test_focus_can_only_go_to_normal(self):
        service, _, _ = _make_service("focus")
        # focus → normal 合法
        result = service.switch_mode("normal")
        assert result["success"] is True

    def test_focus_to_gaming_blocked(self):
        service, _, _ = _make_service("focus")
        result = service.switch_mode("gaming")
        assert result["success"] is False
        assert "not allowed" in (result.get("error") or "")

    def test_gaming_can_only_go_to_normal(self):
        service, _, _ = _make_service("gaming")
        result = service.switch_mode("normal")
        assert result["success"] is True

    def test_gaming_to_sleep_blocked(self):
        service, _, _ = _make_service("gaming")
        result = service.switch_mode("sleep")
        assert result["success"] is False

    def test_sleep_can_only_go_to_normal(self):
        service, _, _ = _make_service("sleep")
        result = service.switch_mode("normal")
        assert result["success"] is True

    def test_maintenance_to_normal_or_locked(self):
        service, _, _ = _make_service("maintenance")
        result_normal = service.switch_mode("normal")
        assert result_normal["success"] is True

    def test_locked_to_normal_or_maintenance(self):
        service, _, _ = _make_service("locked")
        result = service.switch_mode("normal")
        assert result["success"] is True

    def test_all_valid_transitions_coverage(self):
        """确保 VALID_TRANSITIONS 中定义的所有路径都能成功。"""
        for from_mode, targets in VALID_TRANSITIONS.items():
            for to_mode in targets:
                service, _, _ = _make_service(from_mode)
                result = service.switch_mode(to_mode)
                assert result["success"] is True, (
                    f"Expected {from_mode} → {to_mode} to succeed, got error: {result.get('error')}"
                )

    def test_all_six_modes_in_allowed(self):
        """确认 6 种模式都在 ALLOWED_SYS_MODES 中。"""
        expected = {"normal", "focus", "gaming", "sleep", "maintenance", "locked"}
        assert expected == ALLOWED_SYS_MODES


# ---------------------------------------------------------------------------
# 自动规则事件测试
# ---------------------------------------------------------------------------


class TestAutoRuleEvents:
    """on_event 自动模式切换。"""

    def test_gaming_process_detected_triggers_gaming(self):
        service, _, _ = _make_service("normal")
        result = service.on_event("gaming_process_detected", {"process_name": "genshin.exe"})
        assert result is not None
        assert result["success"] is True
        assert result["to_mode"] == "gaming"
        assert result["source"] == "auto_gaming"

    def test_gaming_process_detected_ignored_when_not_normal(self):
        """非 normal 模式下不触发 gaming。"""
        service, _, _ = _make_service("focus")
        result = service.on_event("gaming_process_detected", {"process_name": "game.exe"})
        assert result is None

    def test_gaming_process_detected_ignored_when_rule_disabled(self):
        service, _, _ = _make_service("normal", auto_rules={"gaming_detect": False})
        result = service.on_event("gaming_process_detected", {"process_name": "game.exe"})
        assert result is None

    def test_gaming_exit_returns_to_normal(self):
        service, _, _ = _make_service("gaming")
        result = service.on_event("gaming_process_exited")
        assert result is not None
        assert result["to_mode"] == "normal"

    def test_gaming_exit_ignored_when_not_gaming(self):
        service, _, _ = _make_service("normal")
        result = service.on_event("gaming_process_exited")
        assert result is None

    def test_user_message_wakes_from_sleep(self):
        service, _, _ = _make_service("sleep")
        result = service.on_event("user_message_received", {"channel": "wechat"})
        assert result is not None
        assert result["to_mode"] == "normal"
        assert result["source"] == "auto_wakeup"

    def test_user_message_ignored_when_not_sleep(self):
        service, _, _ = _make_service("normal")
        result = service.on_event("user_message_received")
        assert result is None

    def test_wakeup_on_msg_disabled(self):
        service, _, _ = _make_service("sleep", auto_rules={"wakeup_on_msg": False})
        result = service.on_event("user_message_received")
        assert result is None

    @patch("host_brain.core.mode_transition.datetime")
    def test_morning_check_wakes_from_sleep(self, mock_dt):
        """早间 (06:00-10:00) 唤醒。"""
        mock_dt.datetime.now.return_value = datetime.datetime(2026, 3, 27, 7, 30)
        service, _, _ = _make_service("sleep")
        result = service.on_event("morning_check")
        assert result is not None
        assert result["to_mode"] == "normal"

    @patch("host_brain.core.mode_transition.datetime")
    def test_morning_check_ignored_at_night(self, mock_dt):
        """深夜 (23:00-06:00) 不唤醒。"""
        mock_dt.datetime.now.return_value = datetime.datetime(2026, 3, 27, 2, 0)
        service, _, _ = _make_service("sleep")
        result = service.on_event("morning_check")
        assert result is None

    @patch("host_brain.core.mode_transition.datetime")
    def test_idle_check_triggers_sleep_at_night(self, mock_dt):
        """深夜 + 超过空闲阈值 → sleep。"""
        mock_dt.datetime.now.return_value = datetime.datetime(2026, 3, 27, 1, 0)
        service, _, _ = _make_service("normal", idle_threshold_ms=1000)
        # 模拟空闲超过阈值
        service._last_interaction_ts_ms = int(time.time() * 1000) - 5000
        result = service.on_event("idle_check")
        assert result is not None
        assert result["to_mode"] == "sleep"

    @patch("host_brain.core.mode_transition.datetime")
    def test_idle_check_no_sleep_during_day(self, mock_dt):
        """白天即使空闲也不 sleep。"""
        mock_dt.datetime.now.return_value = datetime.datetime(2026, 3, 27, 14, 0)
        service, _, _ = _make_service("normal", idle_threshold_ms=1000)
        service._last_interaction_ts_ms = int(time.time() * 1000) - 5000
        result = service.on_event("idle_check")
        assert result is None

    def test_unknown_event_returns_none(self):
        service, _, _ = _make_service("normal")
        result = service.on_event("some_random_event")
        assert result is None


# ---------------------------------------------------------------------------
# 规则更新 + 查询测试
# ---------------------------------------------------------------------------


class TestQueryAndConfig:
    """查询和配置接口。"""

    def test_get_current_status_fields(self):
        service, _, _ = _make_service("normal")
        status = service.get_current_status()
        assert status["sys_mode"] == "normal"
        assert isinstance(status["auto_rules"], dict)
        assert isinstance(status["idle_ms"], int)
        assert isinstance(status["idle_minutes"], float)
        assert isinstance(status["valid_transitions"], list)
        assert isinstance(status["ts_ms"], int)

    def test_update_auto_rules(self):
        service, _, _ = _make_service("normal")
        updated = service.update_auto_rules({"gaming_detect": False, "idle_to_sleep": False})
        assert updated["gaming_detect"] is False
        assert updated["idle_to_sleep"] is False
        assert updated["gaming_exit"] is True  # 未改的保持默认

    def test_update_auto_rules_ignores_unknown_keys(self):
        service, _, _ = _make_service("normal")
        updated = service.update_auto_rules({"nonexistent_rule": True})
        assert "nonexistent_rule" not in updated

    def test_get_history_empty_initially(self):
        service, _, _ = _make_service("normal")
        history = service.get_history()
        assert history == []

    def test_get_history_after_switch(self):
        service, _, _ = _make_service("normal")
        service.switch_mode("focus")
        history = service.get_history()
        assert len(history) == 1
        assert history[0]["from_mode"] == "normal"
        assert history[0]["to_mode"] == "focus"

    def test_history_limit(self):
        service, _, _ = _make_service("normal")
        # 做多次来回切换
        for _ in range(5):
            service.switch_mode("focus")
            service.switch_mode("normal")  # focus → normal
        history = service.get_history(limit=3)
        assert len(history) == 3

    def test_update_last_interaction(self):
        service, _, _ = _make_service("normal")
        custom_ts = 1234567890000
        service.update_last_interaction(ts_ms=custom_ts)
        assert service._last_interaction_ts_ms == custom_ts


# ---------------------------------------------------------------------------
# 审计日志测试
# ---------------------------------------------------------------------------


class TestAuditLog:
    """审计日志写入。"""

    def test_successful_switch_uses_canonical_journal_instead_of_error_log(self):
        service, _, mock_ledger = _make_service("normal")
        service.switch_mode("focus")
        mock_ledger.log_error.assert_not_called()

    def test_failed_switch_writes_audit(self):
        service, _, mock_ledger = _make_service("focus")
        service.switch_mode("gaming")  # focus → gaming 不合法
        mock_ledger.log_error.assert_called_once()

    def test_no_ledger_no_error(self):
        """无 ledger 时不应报错。"""
        state = _make_hard_state("normal")
        service = ModeTransitionService(
            get_hard_state=lambda: state,
            set_sys_mode=lambda m, **metadata: {"from_mode": "normal", "to_mode": m, "event_id": metadata["event_id"], "ts_ms": 0},
            ledger=None,
        )
        # 不抛异常即通过
        result = service.switch_mode("focus")
        assert result["success"] is True


# ---------------------------------------------------------------------------
# 边界条件
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """边界条件。"""

    def test_get_hard_state_failure_graceful(self):
        """get_hard_state 抛异常时应返回失败结果。"""
        def exploding_get():
            raise RuntimeError("state unavailable")

        service = ModeTransitionService(
            get_hard_state=exploding_get,
            set_sys_mode=lambda m, **_metadata: {},
        )
        result = service.switch_mode("focus")
        assert result["success"] is False
        assert "get_hard_state failed" in (result.get("error") or "")

    def test_set_sys_mode_failure_graceful(self):
        """set_sys_mode 抛异常时应返回失败结果。"""
        state = _make_hard_state("normal")

        def exploding_set(m, **_metadata):
            raise RuntimeError("write failed")

        service = ModeTransitionService(
            get_hard_state=lambda: state,
            set_sys_mode=exploding_set,
        )
        result = service.switch_mode("focus")
        assert result["success"] is False

    def test_whitespace_target_mode_trimmed(self):
        service, _, _ = _make_service("normal")
        result = service.switch_mode("  focus  ")
        assert result["success"] is True
        assert result["to_mode"] == "focus"

    def test_uppercase_target_mode_normalized(self):
        service, _, _ = _make_service("normal")
        result = service.switch_mode("FOCUS")
        assert result["success"] is True
        assert result["to_mode"] == "focus"

    def test_default_auto_rules_complete(self):
        """DEFAULT_AUTO_RULES 包含所有 5 条规则。"""
        expected_keys = {
            "gaming_detect", "gaming_exit", "idle_to_sleep",
            "wakeup_on_msg", "morning_wakeup",
        }
        assert set(DEFAULT_AUTO_RULES.keys()) == expected_keys
        # 默认全部启用
        for v in DEFAULT_AUTO_RULES.values():
            assert v is True
