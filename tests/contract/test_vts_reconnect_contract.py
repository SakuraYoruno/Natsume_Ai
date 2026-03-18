"""T-VTS-Reconnect-01: VTS WebSocket 自动重连契约测试。

验收断言：
- 断连后自动触发重连循环
- 指数退避延迟正确
- 重连成功后恢复 neutral 表情并上报 VTS_RECONNECTED
- 超过最大重试次数后进入 DISCONNECTED 并上报 VTS_RECONNECT_EXHAUSTED
- auto_reconnect=False 时不触发重连
- 已在重连中时 trigger_reconnect 幂等
- shutdown 可中断重连循环
- health 快照包含 reconnect 信息
- 现有 VTS 功能无回归
"""

from __future__ import annotations

import json
import pathlib
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from edge_client.actuators.vts_driver import (
    EMOTION_TO_EXPRESSION_ID,
    TALK_END,
    TALK_START,
    ReconnectConfig,
    VtsConfig,
    VtsDriverResult,
    VtsState,
    VTubeStudioDriver,
)
from host_brain.core.errors import (
    AVATAR_TAG_INVALID,
    VTS_API_ERROR,
    VTS_CONNECTION_FAILED,
    VTS_EXPRESSION_TIMEOUT,
    VTS_RECONNECTED,
    VTS_RECONNECT_EXHAUSTED,
)

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "channels"


def _load_fixture(name: str) -> dict[str, Any]:
    path = FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8-sig"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_driver(
    *,
    max_attempts: int = 3,
    base_delay_s: float = 0.01,
    max_delay_s: float = 0.05,
    auto_reconnect: bool = True,
) -> VTubeStudioDriver:
    """Create a VTS driver with fast reconnect config for testing."""
    rc = ReconnectConfig(
        max_attempts=max_attempts,
        base_delay_s=base_delay_s,
        max_delay_s=max_delay_s,
        auto_reconnect=auto_reconnect,
    )
    config = VtsConfig(reconnect=rc)
    return VTubeStudioDriver(config=config)


def _simulate_connected(driver: VTubeStudioDriver) -> None:
    """Simulate a VTS driver that is already connected and authenticated."""
    driver._state = VtsState.AUTHENTICATED
    driver._vts = MagicMock()
    driver._current_expression = "exp_happy_01"
    driver._talking = True


# ---------------------------------------------------------------------------
# 重连成功
# ---------------------------------------------------------------------------

class TestReconnectSuccess:

    def test_reconnect_after_failures(self) -> None:
        """断连后经过 N 次失败后重连成功。"""
        driver = _make_driver(max_attempts=5, base_delay_s=0.01)
        _simulate_connected(driver)

        call_count = 0
        original_connect = driver.connect

        def mock_connect():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                # 前 2 次失败
                driver._state = VtsState.ERROR
                return VtsDriverResult(
                    success=False,
                    reason_code=VTS_CONNECTION_FAILED,
                    detail="simulated failure",
                    action="connect",
                )
            # 第 3 次成功
            driver._state = VtsState.AUTHENTICATED
            driver._vts = MagicMock()
            return VtsDriverResult(
                success=True,
                reason_code=None,
                detail="connected",
                action="connect",
            )

        driver.connect = mock_connect
        driver._reconnect_loop()

        assert driver.state == VtsState.AUTHENTICATED
        assert len(driver.reconnect_events) >= 1
        last_event = driver.reconnect_events[-1]
        assert last_event["event"] == VTS_RECONNECTED
        assert last_event["attempt"] == 3
        assert driver._total_reconnects == 1

    def test_reconnect_restores_neutral(self) -> None:
        """重连成功后恢复 neutral 表情。"""
        driver = _make_driver(max_attempts=3, base_delay_s=0.01)
        _simulate_connected(driver)

        def mock_connect():
            driver._state = VtsState.AUTHENTICATED
            driver._vts = MagicMock()
            return VtsDriverResult(
                success=True, reason_code=None, detail="ok", action="connect",
            )

        driver.connect = mock_connect
        with patch("edge_client.actuators.vts_driver._trigger_vts_hotkey"):
            driver._reconnect_loop()

        assert driver.state == VtsState.AUTHENTICATED
        last_event = driver.reconnect_events[-1]
        assert last_event["neutral_restored"] is True

    def test_reconnect_first_attempt_success(self) -> None:
        """第一次就重连成功的情况。"""
        driver = _make_driver(max_attempts=10, base_delay_s=0.01)
        _simulate_connected(driver)

        def mock_connect():
            driver._state = VtsState.AUTHENTICATED
            driver._vts = MagicMock()
            return VtsDriverResult(
                success=True, reason_code=None, detail="ok", action="connect",
            )

        driver.connect = mock_connect
        driver._reconnect_loop()

        assert driver._reconnect_attempts == 1
        assert driver._total_reconnects == 1
        assert driver.reconnect_events[-1]["attempt"] == 1


# ---------------------------------------------------------------------------
# 重连耗尽
# ---------------------------------------------------------------------------

class TestReconnectExhausted:

    def test_all_attempts_fail(self) -> None:
        """所有重连尝试均失败后进入 DISCONNECTED。"""
        driver = _make_driver(max_attempts=3, base_delay_s=0.01, max_delay_s=0.02)
        _simulate_connected(driver)

        def mock_connect():
            driver._state = VtsState.ERROR
            return VtsDriverResult(
                success=False,
                reason_code=VTS_CONNECTION_FAILED,
                detail="always fail",
                action="connect",
            )

        driver.connect = mock_connect
        driver._reconnect_loop()

        assert driver.state == VtsState.DISCONNECTED
        assert driver._total_reconnect_failures == 1
        last_event = driver.reconnect_events[-1]
        assert last_event["event"] == VTS_RECONNECT_EXHAUSTED
        assert last_event["attempts"] == 3

    def test_exhausted_attempts_count(self) -> None:
        """确认所有尝试次数被正确记录。"""
        driver = _make_driver(max_attempts=5, base_delay_s=0.01, max_delay_s=0.02)
        _simulate_connected(driver)

        def mock_connect():
            driver._state = VtsState.ERROR
            return VtsDriverResult(
                success=False,
                reason_code=VTS_CONNECTION_FAILED,
                detail="fail",
                action="connect",
            )

        driver.connect = mock_connect
        driver._reconnect_loop()

        assert driver._reconnect_attempts == 5


# ---------------------------------------------------------------------------
# trigger_reconnect 非阻塞
# ---------------------------------------------------------------------------

class TestTriggerReconnect:

    def test_trigger_reconnect_nonblocking(self) -> None:
        """trigger_reconnect 立即返回，重连在后台执行。"""
        driver = _make_driver(max_attempts=1, base_delay_s=0.01)
        _simulate_connected(driver)

        # mock connect 使其快速成功
        def mock_connect():
            driver._state = VtsState.AUTHENTICATED
            driver._vts = MagicMock()
            return VtsDriverResult(
                success=True, reason_code=None, detail="ok", action="connect",
            )

        driver.connect = mock_connect
        result = driver.trigger_reconnect()

        assert result.success
        assert result.detail == "reconnect started"
        # 等待后台线程完成
        time.sleep(0.5)
        assert driver._total_reconnects >= 1

    def test_trigger_reconnect_idempotent(self) -> None:
        """重连进行中时再次触发返回 no-op。"""
        driver = _make_driver(max_attempts=100, base_delay_s=1.0)
        _simulate_connected(driver)

        # mock connect 永远失败（使重连持续）
        def mock_connect():
            driver._state = VtsState.ERROR
            return VtsDriverResult(
                success=False,
                reason_code=VTS_CONNECTION_FAILED,
                detail="fail",
                action="connect",
            )

        driver.connect = mock_connect
        result1 = driver.trigger_reconnect()
        assert result1.success

        time.sleep(0.1)
        result2 = driver.trigger_reconnect()
        assert result2.success
        assert result2.detail == "reconnect already in progress"

        # 清理
        driver._shutdown_event.set()
        time.sleep(0.5)

    def test_trigger_reconnect_disabled(self) -> None:
        """auto_reconnect=False 时拒绝触发。"""
        driver = _make_driver(auto_reconnect=False)
        result = driver.trigger_reconnect()
        assert not result.success
        assert result.reason_code == VTS_CONNECTION_FAILED
        assert "disabled" in result.detail

    def test_trigger_reconnect_after_shutdown(self) -> None:
        """shutdown 后拒绝触发重连。"""
        driver = _make_driver()
        driver._state = VtsState.SHUTDOWN
        result = driver.trigger_reconnect()
        assert not result.success
        assert "shutdown" in result.detail


# ---------------------------------------------------------------------------
# shutdown 中断
# ---------------------------------------------------------------------------

class TestReconnectShutdown:

    def test_shutdown_interrupts_reconnect(self) -> None:
        """shutdown_event 可中断正在等待的重连循环。"""
        driver = _make_driver(max_attempts=10, base_delay_s=5.0, max_delay_s=10.0)
        _simulate_connected(driver)

        def mock_connect():
            driver._state = VtsState.ERROR
            return VtsDriverResult(
                success=False,
                reason_code=VTS_CONNECTION_FAILED,
                detail="fail",
                action="connect",
            )

        driver.connect = mock_connect

        # 启动重连
        result = driver.trigger_reconnect()
        assert result.success

        # 等待第一次尝试
        time.sleep(0.2)

        # 触发 shutdown
        driver._shutdown_event.set()
        time.sleep(0.5)

        # 应提前退出（不是全部 10 次）
        assert driver._reconnect_attempts < 10


# ---------------------------------------------------------------------------
# _maybe_trigger_reconnect
# ---------------------------------------------------------------------------

class TestMaybeTriggerReconnect:

    def test_auto_trigger_on_connection_loss(self) -> None:
        """API 失败时自动触发重连。"""
        driver = _make_driver(max_attempts=1, base_delay_s=0.01)
        driver._state = VtsState.ERROR

        # mock connect 快速成功
        def mock_connect():
            driver._state = VtsState.AUTHENTICATED
            driver._vts = MagicMock()
            return VtsDriverResult(
                success=True, reason_code=None, detail="ok", action="connect",
            )

        driver.connect = mock_connect
        driver._maybe_trigger_reconnect("test loss")

        time.sleep(0.5)
        assert driver._total_reconnects >= 1

    def test_no_trigger_when_disabled(self) -> None:
        """auto_reconnect=False 时不触发。"""
        driver = _make_driver(auto_reconnect=False)
        driver._state = VtsState.ERROR
        driver._maybe_trigger_reconnect("test")
        assert driver._total_reconnects == 0

    def test_no_trigger_when_already_reconnecting(self) -> None:
        """已在重连中时不重复触发。"""
        driver = _make_driver()
        driver._state = VtsState.RECONNECTING
        driver._maybe_trigger_reconnect("test")
        assert driver._reconnect_thread is None  # 没有启动新线程


# ---------------------------------------------------------------------------
# Health 快照
# ---------------------------------------------------------------------------

class TestHealthSnapshot:

    def test_health_includes_reconnect_info(self) -> None:
        """health 快照包含重连统计信息。"""
        driver = _make_driver()
        health = driver.health()

        assert "reconnect" in health
        rc = health["reconnect"]
        assert rc["total_reconnects"] == 0
        assert rc["total_reconnect_failures"] == 0
        assert rc["reconnect_attempts"] == 0
        assert rc["reconnecting"] is False

    def test_health_after_reconnect(self) -> None:
        """重连后 health 快照更新统计。"""
        driver = _make_driver(max_attempts=2, base_delay_s=0.01)
        _simulate_connected(driver)

        def mock_connect():
            driver._state = VtsState.AUTHENTICATED
            driver._vts = MagicMock()
            return VtsDriverResult(
                success=True, reason_code=None, detail="ok", action="connect",
            )

        driver.connect = mock_connect
        driver._reconnect_loop()

        health = driver.health()
        assert health["reconnect"]["total_reconnects"] == 1

    def test_health_config_includes_reconnect(self) -> None:
        """config 快照包含 reconnect 配置。"""
        driver = _make_driver(max_attempts=7, base_delay_s=2.0)
        health = driver.health()
        assert health["config"]["reconnect"]["max_attempts"] == 7
        assert health["config"]["reconnect"]["base_delay_s"] == 2.0


# ---------------------------------------------------------------------------
# 现有功能无回归
# ---------------------------------------------------------------------------

class TestVtsDriverRegression:

    def test_emotion_map_unchanged(self) -> None:
        """确定性表情映射表未变更。"""
        assert "NEUTRAL" in EMOTION_TO_EXPRESSION_ID
        assert "HAPPY" in EMOTION_TO_EXPRESSION_ID
        assert len(EMOTION_TO_EXPRESSION_ID) == 16

    def test_set_expression_invalid_tag(self) -> None:
        """无效 emotion_tag 仍被拒绝。"""
        driver = _make_driver()
        result = driver.set_expression("INVALID")
        assert not result.success
        assert result.reason_code == AVATAR_TAG_INVALID

    def test_set_expression_empty_tag(self) -> None:
        """空 emotion_tag 仍被拒绝。"""
        driver = _make_driver()
        result = driver.set_expression("")
        assert not result.success
        assert result.reason_code == AVATAR_TAG_INVALID

    def test_disconnect_resets_state(self) -> None:
        """disconnect 仍正确重置状态。"""
        driver = _make_driver()
        _simulate_connected(driver)
        result = driver.disconnect()
        assert result.success
        assert driver.state == VtsState.DISCONNECTED
        assert driver.current_expression is None
        assert driver.talking is False

    def test_talk_start_when_not_connected(self) -> None:
        """未连接时 talk_start 触发连接尝试。"""
        driver = _make_driver()
        # connect 会失败（无真实 VTS）
        result = driver.talk_start()
        # 预期连接失败
        assert not result.success
        assert result.reason_code == VTS_CONNECTION_FAILED

    def test_talk_end_when_not_connected(self) -> None:
        """未连接时 talk_end 为 no-op 成功。"""
        driver = _make_driver()
        result = driver.talk_end()
        assert result.success

    def test_vts_state_enum_values(self) -> None:
        """VtsState 枚举包含 RECONNECTING。"""
        assert VtsState.RECONNECTING == "RECONNECTING"
        assert VtsState.DISCONNECTED == "DISCONNECTED"
        assert VtsState.AUTHENTICATED == "AUTHENTICATED"

    def test_reset_to_neutral_when_not_connected(self) -> None:
        """未连接时 reset_to_neutral 降级但成功。"""
        driver = _make_driver()
        result = driver.reset_to_neutral()
        # 降级时 set_expression 仍返回 success（queued）
        assert result.success


# ---------------------------------------------------------------------------
# Fixture 验证
# ---------------------------------------------------------------------------

class TestFixtureStructure:

    @pytest.mark.parametrize("fixture_name", [
        "vts_reconnect_success.json",
        "vts_reconnect_exhausted.json",
    ])
    def test_fixture_loadable(self, fixture_name: str) -> None:
        """Fixture 文件可加载且结构正确。"""
        data = _load_fixture(fixture_name)
        assert "scenario" in data
        assert "reconnect_config" in data
        assert "expected" in data

    def test_success_fixture_structure(self) -> None:
        data = _load_fixture("vts_reconnect_success.json")
        assert data["expected"]["final_state"] == "AUTHENTICATED"
        assert data["expected"]["reconnect_event"] == VTS_RECONNECTED

    def test_exhausted_fixture_structure(self) -> None:
        data = _load_fixture("vts_reconnect_exhausted.json")
        assert data["expected"]["final_state"] == "DISCONNECTED"
        assert data["expected"]["reconnect_event"] == VTS_RECONNECT_EXHAUSTED


# ---------------------------------------------------------------------------
# ReconnectConfig 序列化
# ---------------------------------------------------------------------------

class TestReconnectConfig:

    def test_default_values(self) -> None:
        rc = ReconnectConfig()
        assert rc.max_attempts == 10
        assert rc.base_delay_s == 1.0
        assert rc.max_delay_s == 16.0
        assert rc.auto_reconnect is True

    def test_as_dict(self) -> None:
        rc = ReconnectConfig(max_attempts=5, base_delay_s=2.0)
        d = rc.as_dict()
        assert d["max_attempts"] == 5
        assert d["base_delay_s"] == 2.0
        assert d["auto_reconnect"] is True

    def test_custom_config(self) -> None:
        rc = ReconnectConfig(
            max_attempts=3,
            base_delay_s=0.5,
            max_delay_s=8.0,
            auto_reconnect=False,
        )
        assert rc.auto_reconnect is False
        assert rc.max_delay_s == 8.0
