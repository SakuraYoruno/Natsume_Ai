"""
test_ui_assertion_guard.py —— UIAssertionGuard 单元测试（需求 8）
"""
from __future__ import annotations

import pytest

from host_brain.app.vision.ui_assertion import (
    UIAction,
    UIAssertionError,
    UIAssertionGuard,
    is_sensitive_ui,
)


def make_action(**kwargs) -> UIAction:
    """创建测试用 UIAction。"""
    defaults = {
        "action_type": "click",
        "target_selector": "#btn-submit",
        "expected_window_title": "测试应用",
        "expected_process_name": "test_app.exe",
        "expected_ui_state_hash": "abc123",
        "expected_change_description": "按钮被点击后页面跳转",
    }
    defaults.update(kwargs)
    return UIAction(**defaults)


class TestSensitiveUIDetection:
    """敏感界面检测测试（需求 8.5）。"""

    def test_password_window_detected(self):
        """验证密码框界面被检测为敏感。"""
        assert is_sensitive_ui("请输入密码", "app.exe") is True

    def test_payment_window_detected(self):
        """验证支付界面被检测为敏感。"""
        assert is_sensitive_ui("支付确认", "payment.exe") is True

    def test_system_settings_detected(self):
        """验证系统设置界面被检测为敏感。"""
        assert is_sensitive_ui("系统设置", "settings.exe") is True

    def test_normal_window_not_sensitive(self):
        """验证普通窗口不被检测为敏感。"""
        assert is_sensitive_ui("记事本", "notepad.exe") is False

    def test_uac_process_detected(self):
        """验证 UAC 进程被检测为敏感。"""
        assert is_sensitive_ui("用户账户控制", "consent.exe") is True


class TestPreCheck:
    """前置断言测试（需求 8.1、8.2）。"""

    @pytest.mark.asyncio
    async def test_sensitive_ui_blocked_regardless_of_confidence(self):
        """验证敏感界面强制拒绝，无论置信度多高（需求 8.5）。"""
        async def mock_window_info():
            return {
                "title": "请输入密码",
                "process_name": "app.exe",
                "is_foreground": True,
                "ui_state_hash": "abc123",
            }

        guard = UIAssertionGuard(window_info_getter=mock_window_info)
        action = make_action(
            expected_window_title="请输入密码",
            expected_process_name="app.exe",
            expected_ui_state_hash="abc123",
        )

        with pytest.raises(UIAssertionError) as exc_info:
            await guard.pre_check(action)
        assert exc_info.value.audit_event == "SENSITIVE_UI_BLOCKED"

    @pytest.mark.asyncio
    async def test_window_title_mismatch_fails(self):
        """验证 window_title 不匹配时前置断言失败（需求 8.1）。"""
        async def mock_window_info():
            return {
                "title": "其他应用",  # 与预期不符
                "process_name": "test_app.exe",
                "is_foreground": True,
                "ui_state_hash": "abc123",
            }

        audit_events = []

        async def mock_audit(event, detail):
            audit_events.append(event)

        guard = UIAssertionGuard(
            window_info_getter=mock_window_info,
            audit_writer=mock_audit,
        )
        action = make_action()

        with pytest.raises(UIAssertionError) as exc_info:
            await guard.pre_check(action)
        assert exc_info.value.audit_event == "UI_PRECONDITION_FAILED"
        assert "UI_PRECONDITION_FAILED" in audit_events

    @pytest.mark.asyncio
    async def test_ui_state_hash_mismatch_fails(self):
        """验证 ui_state_hash 不一致时前置断言失败（需求 8.1）。"""
        async def mock_window_info():
            return {
                "title": "测试应用",
                "process_name": "test_app.exe",
                "is_foreground": True,
                "ui_state_hash": "different_hash",  # 与预期不符
            }

        guard = UIAssertionGuard(window_info_getter=mock_window_info)
        action = make_action()

        with pytest.raises(UIAssertionError) as exc_info:
            await guard.pre_check(action)
        assert "ui_state_hash" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_background_window_fails(self):
        """验证后台窗口操作被拒绝（需求 8.6）。"""
        async def mock_window_info():
            return {
                "title": "测试应用",
                "process_name": "test_app.exe",
                "is_foreground": False,  # 后台窗口
                "ui_state_hash": "abc123",
            }

        guard = UIAssertionGuard(window_info_getter=mock_window_info)
        action = make_action()

        with pytest.raises(UIAssertionError) as exc_info:
            await guard.pre_check(action)
        assert "is_foreground_window" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_all_assertions_pass(self):
        """验证所有断言通过时返回成功结果。"""
        async def mock_window_info():
            return {
                "title": "测试应用",
                "process_name": "test_app.exe",
                "is_foreground": True,
                "ui_state_hash": "abc123",
            }

        guard = UIAssertionGuard(window_info_getter=mock_window_info)
        action = make_action()

        result = await guard.pre_check(action)
        assert result.passed is True
        assert len(result.failed_assertions) == 0


class TestPostCheck:
    """后置状态验证测试（需求 8.3、8.4）。"""

    @pytest.mark.asyncio
    async def test_change_detected_passes(self):
        """验证检测到状态变化时后置验证通过（需求 8.3）。"""
        before = b"screenshot_before_content"
        after = b"screenshot_after_different_content"

        guard = UIAssertionGuard()
        result = await guard.post_check(before, after, "页面跳转")

        assert result.passed is True
        assert result.change_detected is True

    @pytest.mark.asyncio
    async def test_no_change_triggers_replay_guard(self):
        """验证无状态变化时触发 step_replay_guard（需求 8.4）。"""
        same_screenshot = b"identical_screenshot"

        guard = UIAssertionGuard()

        with pytest.raises(UIAssertionError) as exc_info:
            await guard.post_check(same_screenshot, same_screenshot, "预期有变化")
        assert exc_info.value.audit_event == "UI_POSTCONDITION_FAILED"


class TestUIStateHash:
    """UI 状态哈希计算测试。"""

    def test_same_inputs_same_hash(self):
        """验证相同输入产生相同哈希。"""
        h1 = UIAssertionGuard.compute_ui_state_hash("测试应用", "test.exe")
        h2 = UIAssertionGuard.compute_ui_state_hash("测试应用", "test.exe")
        assert h1 == h2

    def test_different_title_different_hash(self):
        """验证不同窗口标题产生不同哈希。"""
        h1 = UIAssertionGuard.compute_ui_state_hash("应用A", "test.exe")
        h2 = UIAssertionGuard.compute_ui_state_hash("应用B", "test.exe")
        assert h1 != h2
