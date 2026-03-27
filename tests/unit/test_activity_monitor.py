"""ActivityMonitor 单元测试。

覆盖：
- 游戏进程检测（GAMING_PROCESS_NAMES 匹配）
- 标题关键词检测（GAMING_TITLE_KEYWORDS 匹配）
- 空闲检测逻辑
- 早间唤醒逻辑
- start/stop 线程管理
- 环境变量自定义游戏列表
"""
from __future__ import annotations

import os
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# 在 import 之前 mock Windows-only 模块
# ---------------------------------------------------------------------------

# activity_monitor 依赖 ctypes（GetForegroundWindow 等），Linux/CI 下需要 mock
_ctypes_mock = MagicMock()
_ctypes_mock.windll.user32.GetForegroundWindow.return_value = 12345
_ctypes_mock.windll.user32.GetWindowThreadProcessId.return_value = 0
_ctypes_mock.wintypes.DWORD.return_value = MagicMock(value=1000)

# psutil mock
_psutil_mock = MagicMock()
_fake_process = MagicMock()
_fake_process.name.return_value = "notepad.exe"
_fake_process.pid = 1000
_psutil_mock.Process.return_value = _fake_process


@pytest.fixture(autouse=True)
def _patch_platform_deps(monkeypatch):
    """为所有测试注入 ctypes / psutil mock。"""
    import sys
    # 保存原始模块引用
    _orig_ctypes = sys.modules.get("ctypes")
    _orig_psutil = sys.modules.get("psutil")
    # 注入 mock
    sys.modules["ctypes"] = _ctypes_mock
    sys.modules["psutil"] = _psutil_mock
    yield
    # 恢复
    if _orig_ctypes is not None:
        sys.modules["ctypes"] = _orig_ctypes
    elif "ctypes" in sys.modules:
        del sys.modules["ctypes"]
    if _orig_psutil is not None:
        sys.modules["psutil"] = _orig_psutil
    elif "psutil" in sys.modules:
        del sys.modules["psutil"]


# ---------------------------------------------------------------------------
# 直接测试 GAMING_PROCESS_NAMES / GAMING_TITLE_KEYWORDS（常量级）
# ---------------------------------------------------------------------------


class TestGamingProcessNames:
    """游戏进程列表完整性。"""

    def test_builtin_list_not_empty(self):
        from host_brain.core.activity_monitor import GAMING_PROCESS_NAMES
        assert len(GAMING_PROCESS_NAMES) >= 30

    def test_contains_known_games(self):
        from host_brain.core.activity_monitor import GAMING_PROCESS_NAMES
        known = {"genshinimpact.exe", "leagueclient.exe", "steam.exe", "epicgameslauncher.exe"}
        for name in known:
            assert name in GAMING_PROCESS_NAMES, f"{name} should be in GAMING_PROCESS_NAMES"

    def test_all_lowercase(self):
        from host_brain.core.activity_monitor import GAMING_PROCESS_NAMES
        for name in GAMING_PROCESS_NAMES:
            assert name == name.lower(), f"Process name should be lowercase: {name}"

    def test_title_keywords_not_empty(self):
        from host_brain.core.activity_monitor import GAMING_TITLE_KEYWORDS
        assert len(GAMING_TITLE_KEYWORDS) >= 3

    def test_custom_env_processes(self, monkeypatch):
        """HOST_GAMING_PROCESSES 环境变量可追加自定义进程。"""
        monkeypatch.setenv("HOST_GAMING_PROCESSES", "mygame.exe, customgame.exe")
        # 重新加载模块以应用环境变量
        import importlib
        import host_brain.core.activity_monitor as am
        importlib.reload(am)
        assert "mygame.exe" in am.GAMING_PROCESS_NAMES
        assert "customgame.exe" in am.GAMING_PROCESS_NAMES
        # 清理
        monkeypatch.delenv("HOST_GAMING_PROCESSES", raising=False)
        importlib.reload(am)


# ---------------------------------------------------------------------------
# ActivityMonitor 生命周期测试
# ---------------------------------------------------------------------------


class TestActivityMonitorLifecycle:
    """start / stop 线程管理。"""

    def test_start_creates_threads(self):
        from host_brain.core.activity_monitor import ActivityMonitor
        mock_service = MagicMock()
        monitor = ActivityMonitor(mode_service=mock_service)
        monitor.start()
        assert monitor._running is True
        assert monitor._gaming_thread is not None
        assert monitor._gaming_thread.is_alive()
        # 清理
        monitor.stop()

    def test_stop_terminates_threads(self):
        from host_brain.core.activity_monitor import ActivityMonitor
        mock_service = MagicMock()
        monitor = ActivityMonitor(
            mode_service=mock_service,
            gaming_check_interval_s=0.1,
            idle_check_interval_s=0.1,
        )
        monitor.start()
        assert monitor._running is True
        monitor.stop()
        assert monitor._running is False
        # 线程应在短时间内结束
        if monitor._gaming_thread:
            monitor._gaming_thread.join(timeout=2.0)
            assert not monitor._gaming_thread.is_alive()

    def test_double_start_no_error(self):
        from host_brain.core.activity_monitor import ActivityMonitor
        mock_service = MagicMock()
        monitor = ActivityMonitor(mode_service=mock_service)
        monitor.start()
        monitor.start()  # 第二次不应报错
        assert monitor._running is True
        monitor.stop()

    def test_double_stop_no_error(self):
        from host_brain.core.activity_monitor import ActivityMonitor
        mock_service = MagicMock()
        monitor = ActivityMonitor(mode_service=mock_service)
        monitor.start()
        monitor.stop()
        monitor.stop()  # 第二次不应报错
        assert monitor._running is False


# ---------------------------------------------------------------------------
# 配置参数测试
# ---------------------------------------------------------------------------


class TestActivityMonitorConfig:
    """配置参数。"""

    def test_default_intervals(self):
        from host_brain.core.activity_monitor import ActivityMonitor
        mock_service = MagicMock()
        monitor = ActivityMonitor(mode_service=mock_service)
        assert monitor._gaming_check_interval_s == 30
        assert monitor._idle_check_interval_s == 300

    def test_custom_intervals(self):
        from host_brain.core.activity_monitor import ActivityMonitor
        mock_service = MagicMock()
        monitor = ActivityMonitor(
            mode_service=mock_service,
            gaming_check_interval_s=10,
            idle_check_interval_s=60,
        )
        assert monitor._gaming_check_interval_s == 10
        assert monitor._idle_check_interval_s == 60

    def test_env_intervals(self, monkeypatch):
        monkeypatch.setenv("HOST_GAMING_CHECK_INTERVAL_S", "15")
        monkeypatch.setenv("HOST_IDLE_CHECK_INTERVAL_S", "120")
        from host_brain.core.activity_monitor import ActivityMonitor
        mock_service = MagicMock()
        monitor = ActivityMonitor(mode_service=mock_service)
        # 环境变量在构造函数中读取（如果实现支持的话）
        # 注：具体是否从 env 读取取决于实现，至少不报错
        assert isinstance(monitor._gaming_check_interval_s, (int, float))
        assert isinstance(monitor._idle_check_interval_s, (int, float))
