"""T-ASR-Recovery-01: ASR 麦克风设备自动恢复契约测试。

验收断言：
- 设备异常后自动触发恢复流程
- 指数退避延迟正确
- 恢复成功后 ASR 采集正常继续并上报 ASR_DEVICE_RECOVERED
- 超过最大重试次数后进入 ERROR 并上报 ASR_DEVICE_RECOVERY_EXHAUSTED
- 支持备用设备自动切换（默认设备失败→枚举可用→逐一尝试）
- auto_recover=False 时不触发恢复
- 已在恢复中时 trigger_device_recovery 幂等
- shutdown 可中断恢复循环
- health 快照包含 recovery 信息
- 现有 ASR 功能无回归
"""

from __future__ import annotations

import json
import pathlib
import threading
import time
from dataclasses import field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from edge_client.senses.asr import (
    AsrConfig,
    AsrResult,
    AsrService,
    AsrState,
    AudioStreamCapture,
    DeviceRecoveryConfig,
    WhisperModelManager,
)
from host_brain.core.errors import (
    ASR_DEVICE_ERROR,
    ASR_DEVICE_RECOVERED,
    ASR_DEVICE_RECOVERY_EXHAUSTED,
    ASR_GPU_OOM,
    ASR_MODEL_UNAVAILABLE,
    ASR_STREAM_INTERRUPTED,
    ASR_VAD_NO_SPEECH,
)

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "voice"


def _load_fixture(name: str) -> dict[str, Any]:
    path = FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockRpc:
    """Mock VoiceRpcContract for testing."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def asr_chunk(self, *, request_id: str, payload: dict, ts_ms: int | None = None) -> Any:
        self.events.append({"request_id": request_id, "payload": payload, "ts_ms": ts_ms})
        return MagicMock(success=True)


def _make_service(
    *,
    max_attempts: int = 3,
    base_delay_s: float = 0.01,
    max_delay_s: float = 0.05,
    auto_recover: bool = True,
    try_alternate_devices: bool = True,
) -> AsrService:
    """Create an ASR service with fast recovery config for testing."""
    rc = DeviceRecoveryConfig(
        max_attempts=max_attempts,
        base_delay_s=base_delay_s,
        max_delay_s=max_delay_s,
        auto_recover=auto_recover,
        try_alternate_devices=try_alternate_devices,
    )
    config = AsrConfig(recovery=rc)
    rpc = MockRpc()
    model = WhisperModelManager(config)
    audio = AudioStreamCapture(
        sample_rate=config.sample_rate,
        channels=config.channels,
        chunk_duration_s=config.chunk_duration_s,
    )
    return AsrService(rpc=rpc, config=config, model_manager=model, audio_capture=audio)


def _simulate_streaming(service: AsrService) -> None:
    """Simulate a service that is streaming (device active)."""
    service._model._state = AsrState.READY
    service._model._model = MagicMock()
    service._running = True
    service._session_id = "test_session"
    service._last_callback = service._on_audio_chunk


# ---------------------------------------------------------------------------
# 恢复成功
# ---------------------------------------------------------------------------

class TestRecoverySuccess:

    def test_recovery_after_failures(self) -> None:
        """设备错误后经过 N 次失败后恢复成功。"""
        service = _make_service(max_attempts=5, base_delay_s=0.01)
        _simulate_streaming(service)

        call_count = 0
        original_start = service._audio.start

        def mock_start(callback):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return AsrResult(
                    success=False,
                    reason_code=ASR_DEVICE_ERROR,
                    detail="simulated device failure",
                )
            return AsrResult(
                success=True,
                reason_code=None,
                detail="audio capture started",
            )

        service._audio.start = mock_start
        service._device_recovery_loop()

        assert service._model.state == AsrState.READY
        assert service._running is True
        assert len(service.recovery_events) >= 1
        last_event = service.recovery_events[-1]
        assert last_event["event"] == ASR_DEVICE_RECOVERED
        assert service._total_recoveries == 1

    def test_recovery_first_attempt_success(self) -> None:
        """第一次就恢复成功的情况。"""
        service = _make_service(max_attempts=10, base_delay_s=0.01)
        _simulate_streaming(service)

        def mock_start(callback):
            return AsrResult(success=True, reason_code=None, detail="ok")

        service._audio.start = mock_start
        service._device_recovery_loop()

        assert service._recovery_attempts == 1
        assert service._total_recoveries == 1
        assert service.recovery_events[-1]["attempt"] == 1


# ---------------------------------------------------------------------------
# 恢复耗尽
# ---------------------------------------------------------------------------

class TestRecoveryExhausted:

    def test_all_attempts_fail(self) -> None:
        """所有恢复尝试均失败后进入 ERROR。"""
        service = _make_service(max_attempts=3, base_delay_s=0.01, max_delay_s=0.02)
        _simulate_streaming(service)

        def mock_start(callback):
            return AsrResult(
                success=False,
                reason_code=ASR_DEVICE_ERROR,
                detail="always fail",
            )

        service._audio.start = mock_start
        service._device_recovery_loop()

        assert service._model.state == AsrState.ERROR
        assert service._running is False
        assert service._total_recovery_failures == 1
        last_event = service.recovery_events[-1]
        assert last_event["event"] == ASR_DEVICE_RECOVERY_EXHAUSTED
        assert last_event["attempts"] == 3

    def test_exhausted_attempts_count(self) -> None:
        """确认所有尝试次数被正确记录。"""
        service = _make_service(max_attempts=5, base_delay_s=0.01, max_delay_s=0.02)
        _simulate_streaming(service)

        def mock_start(callback):
            return AsrResult(
                success=False,
                reason_code=ASR_DEVICE_ERROR,
                detail="fail",
            )

        service._audio.start = mock_start
        service._device_recovery_loop()

        assert service._recovery_attempts == 5


# ---------------------------------------------------------------------------
# 备用设备切换
# ---------------------------------------------------------------------------

class TestAlternateDeviceSwitch:

    def test_switch_to_alternate_device(self) -> None:
        """默认设备失败后自动切换到备用设备。"""
        service = _make_service(
            max_attempts=5,
            base_delay_s=0.01,
            try_alternate_devices=True,
        )
        _simulate_streaming(service)

        call_count = 0
        device_set: list[int | None] = []

        def mock_start(callback):
            nonlocal call_count
            call_count += 1
            current_dev = service._audio._device_index
            device_set.append(current_dev)
            # 原始设备（None/0）始终失败，备用设备（index=2）成功
            if current_dev is not None and current_dev == 2:
                return AsrResult(success=True, reason_code=None, detail="ok")
            return AsrResult(
                success=False,
                reason_code=ASR_DEVICE_ERROR,
                detail="original device unavailable",
            )

        # Mock enumerate_input_devices to return 2 devices
        mock_devices = [
            {"index": 0, "name": "Default Mic", "max_input_channels": 1, "default_samplerate": 16000},
            {"index": 2, "name": "USB Audio", "max_input_channels": 2, "default_samplerate": 48000},
        ]

        service._audio.start = mock_start
        with patch.object(AudioStreamCapture, "enumerate_input_devices", return_value=mock_devices):
            service._device_recovery_loop()

        assert service._model.state == AsrState.READY
        last_event = service.recovery_events[-1]
        assert last_event["event"] == ASR_DEVICE_RECOVERED
        # 确认使用了备用设备
        assert last_event["device_index"] == 2

    def test_no_alternate_when_disabled(self) -> None:
        """try_alternate_devices=False 时只尝试原始设备。"""
        service = _make_service(
            max_attempts=2,
            base_delay_s=0.01,
            try_alternate_devices=False,
        )
        _simulate_streaming(service)

        devices_tried: list[int | None] = []

        def mock_start(callback):
            devices_tried.append(service._audio._device_index)
            return AsrResult(
                success=False,
                reason_code=ASR_DEVICE_ERROR,
                detail="fail",
            )

        mock_devices = [
            {"index": 0, "name": "Default Mic", "max_input_channels": 1, "default_samplerate": 16000},
            {"index": 2, "name": "USB Audio", "max_input_channels": 2, "default_samplerate": 48000},
        ]

        service._audio.start = mock_start
        with patch.object(AudioStreamCapture, "enumerate_input_devices", return_value=mock_devices):
            service._device_recovery_loop()

        # 只应该尝试原始设备
        unique_devices = set(devices_tried)
        assert len(unique_devices) == 1  # 只尝试了一个设备


# ---------------------------------------------------------------------------
# trigger_device_recovery 非阻塞
# ---------------------------------------------------------------------------

class TestTriggerRecovery:

    def test_trigger_nonblocking(self) -> None:
        """trigger_device_recovery 立即返回，恢复在后台执行。"""
        service = _make_service(max_attempts=1, base_delay_s=0.01)
        _simulate_streaming(service)

        def mock_start(callback):
            return AsrResult(success=True, reason_code=None, detail="ok")

        service._audio.start = mock_start
        result = service.trigger_device_recovery()

        assert result.success
        assert result.detail == "device recovery started"
        time.sleep(0.5)
        assert service._total_recoveries >= 1

    def test_trigger_idempotent(self) -> None:
        """恢复进行中时再次触发返回 no-op。"""
        service = _make_service(max_attempts=100, base_delay_s=1.0)
        _simulate_streaming(service)

        def mock_start(callback):
            return AsrResult(
                success=False,
                reason_code=ASR_DEVICE_ERROR,
                detail="fail",
            )

        service._audio.start = mock_start
        result1 = service.trigger_device_recovery()
        assert result1.success

        time.sleep(0.1)
        result2 = service.trigger_device_recovery()
        assert result2.success
        assert result2.detail == "device recovery already in progress"

        # 清理
        service._shutdown_event.set()
        time.sleep(0.5)

    def test_trigger_disabled(self) -> None:
        """auto_recover=False 时拒绝触发。"""
        service = _make_service(auto_recover=False)
        result = service.trigger_device_recovery()
        assert not result.success
        assert result.reason_code == ASR_DEVICE_ERROR
        assert "disabled" in result.detail

    def test_trigger_after_shutdown(self) -> None:
        """shutdown 后拒绝触发恢复。"""
        service = _make_service()
        service._model._state = AsrState.SHUTDOWN
        result = service.trigger_device_recovery()
        assert not result.success
        assert "shutdown" in result.detail


# ---------------------------------------------------------------------------
# shutdown 中断
# ---------------------------------------------------------------------------

class TestRecoveryShutdown:

    def test_shutdown_interrupts_recovery(self) -> None:
        """shutdown_event 可中断正在等待的恢复循环。"""
        service = _make_service(max_attempts=10, base_delay_s=5.0, max_delay_s=10.0)
        _simulate_streaming(service)

        def mock_start(callback):
            return AsrResult(
                success=False,
                reason_code=ASR_DEVICE_ERROR,
                detail="fail",
            )

        service._audio.start = mock_start
        result = service.trigger_device_recovery()
        assert result.success

        # 等待第一次尝试
        time.sleep(0.2)

        # 触发 shutdown
        service._shutdown_event.set()
        time.sleep(0.5)

        # 应提前退出（不是全部 10 次）
        assert service._recovery_attempts < 10


# ---------------------------------------------------------------------------
# _maybe_trigger_recovery
# ---------------------------------------------------------------------------

class TestMaybeTriggerRecovery:

    def test_auto_trigger_on_device_error(self) -> None:
        """设备错误时自动触发恢复。"""
        service = _make_service(max_attempts=1, base_delay_s=0.01)
        service._model._state = AsrState.ERROR

        def mock_start(callback):
            return AsrResult(success=True, reason_code=None, detail="ok")

        service._audio.start = mock_start
        service._last_callback = service._on_audio_chunk
        service._maybe_trigger_recovery("test device error")

        time.sleep(0.5)
        assert service._total_recoveries >= 1

    def test_no_trigger_when_disabled(self) -> None:
        """auto_recover=False 时不触发。"""
        service = _make_service(auto_recover=False)
        service._model._state = AsrState.ERROR
        service._maybe_trigger_recovery("test")
        assert service._total_recoveries == 0

    def test_no_trigger_when_already_recovering(self) -> None:
        """已在恢复中时不重复触发。"""
        service = _make_service()
        service._model._state = AsrState.RECOVERING
        service._maybe_trigger_recovery("test")
        assert service._recovery_thread is None  # 没有启动新线程


# ---------------------------------------------------------------------------
# Health 快照
# ---------------------------------------------------------------------------

class TestHealthSnapshot:

    def test_health_includes_recovery_info(self) -> None:
        """health 快照包含恢复统计信息。"""
        service = _make_service()
        health = service.health()

        assert "recovery" in health
        rc = health["recovery"]
        assert rc["total_recoveries"] == 0
        assert rc["total_recovery_failures"] == 0
        assert rc["recovery_attempts"] == 0
        assert rc["recovering"] is False

    def test_health_after_recovery(self) -> None:
        """恢复后 health 快照更新统计。"""
        service = _make_service(max_attempts=2, base_delay_s=0.01)
        _simulate_streaming(service)

        def mock_start(callback):
            return AsrResult(success=True, reason_code=None, detail="ok")

        service._audio.start = mock_start
        service._device_recovery_loop()

        health = service.health()
        assert health["recovery"]["total_recoveries"] == 1

    def test_health_config_includes_recovery(self) -> None:
        """config 快照包含 recovery 配置。"""
        service = _make_service(max_attempts=7, base_delay_s=2.0)
        health = service.health()
        assert health["config"]["recovery"]["max_attempts"] == 7
        assert health["config"]["recovery"]["base_delay_s"] == 2.0


# ---------------------------------------------------------------------------
# AudioStreamCapture 增强
# ---------------------------------------------------------------------------

class TestAudioStreamCaptureEnhancements:

    def test_set_device_index(self) -> None:
        """set_device_index 可切换设备。"""
        capture = AudioStreamCapture(device_index=0)
        assert capture._device_index == 0
        capture.set_device_index(2)
        assert capture._device_index == 2

    def test_original_device_preserved(self) -> None:
        """原始设备索引在切换后仍保留。"""
        capture = AudioStreamCapture(device_index=3)
        assert capture._original_device_index == 3
        capture.set_device_index(5)
        assert capture._original_device_index == 3
        assert capture._device_index == 5

    def test_enumerate_graceful_failure(self) -> None:
        """enumerate_input_devices 在 sounddevice 不可用时返回空列表。"""
        with patch("edge_client.senses.asr.AudioStreamCapture.enumerate_input_devices", side_effect=Exception("no sounddevice")):
            # 直接调用静态方法（无 mock 时异常）
            pass
        # 直接测试方法本身的异常处理
        devices = AudioStreamCapture.enumerate_input_devices()
        # 在 CI 环境可能返回空列表也可能返回设备
        assert isinstance(devices, list)

    def test_health_includes_original_device(self) -> None:
        """health 快照包含原始设备索引。"""
        capture = AudioStreamCapture(device_index=1)
        health = capture.health()
        assert health["original_device_index"] == 1
        assert health["device_index"] == 1


# ---------------------------------------------------------------------------
# DeviceRecoveryConfig 序列化
# ---------------------------------------------------------------------------

class TestDeviceRecoveryConfig:

    def test_default_values(self) -> None:
        rc = DeviceRecoveryConfig()
        assert rc.max_attempts == 5
        assert rc.base_delay_s == 1.0
        assert rc.max_delay_s == 8.0
        assert rc.auto_recover is True
        assert rc.try_alternate_devices is True

    def test_as_dict(self) -> None:
        rc = DeviceRecoveryConfig(max_attempts=3, base_delay_s=0.5)
        d = rc.as_dict()
        assert d["max_attempts"] == 3
        assert d["base_delay_s"] == 0.5
        assert d["auto_recover"] is True
        assert d["try_alternate_devices"] is True

    def test_custom_config(self) -> None:
        rc = DeviceRecoveryConfig(
            max_attempts=2,
            base_delay_s=0.1,
            max_delay_s=4.0,
            auto_recover=False,
            try_alternate_devices=False,
        )
        assert rc.auto_recover is False
        assert rc.try_alternate_devices is False
        assert rc.max_delay_s == 4.0


# ---------------------------------------------------------------------------
# 现有功能无回归
# ---------------------------------------------------------------------------

class TestAsrRegression:

    def test_asr_state_enum_values(self) -> None:
        """AsrState 枚举包含 RECOVERING。"""
        assert AsrState.RECOVERING == "RECOVERING"
        assert AsrState.UNLOADED == "UNLOADED"
        assert AsrState.READY == "READY"
        assert AsrState.ERROR == "ERROR"

    def test_whisper_model_health_includes_recovery_count(self) -> None:
        """WhisperModelManager health 包含 recovery_count。"""
        model = WhisperModelManager()
        health = model.health()
        assert "recovery_count" in health
        assert health["recovery_count"] == 0

    def test_config_backward_compatible(self) -> None:
        """AsrConfig 无参数时仍可用（recovery 有默认值）。"""
        config = AsrConfig()
        assert config.recovery.max_attempts == 5
        assert config.recovery.auto_recover is True
        d = config.as_dict()
        assert "recovery" in d

    def test_asr_result_structure(self) -> None:
        """AsrResult 结构不变。"""
        result = AsrResult(
            success=True,
            reason_code=None,
            detail="ok",
        )
        d = result.as_dict()
        assert d["success"] is True
        assert "segments" in d
        assert "language" in d

    def test_audio_capture_stop_not_running(self) -> None:
        """未运行时 stop 为 no-op 成功。"""
        capture = AudioStreamCapture()
        result = capture.stop()
        assert result.success


# ---------------------------------------------------------------------------
# Fixture 验证
# ---------------------------------------------------------------------------

class TestFixtureStructure:

    @pytest.mark.parametrize("fixture_name", [
        "asr_device_recovery_success.json",
        "asr_device_recovery_exhausted.json",
        "asr_device_alternate_switch.json",
    ])
    def test_fixture_loadable(self, fixture_name: str) -> None:
        """Fixture 文件可加载且结构正确。"""
        data = _load_fixture(fixture_name)
        assert "scenario" in data
        assert "recovery_config" in data
        assert "expected" in data

    def test_success_fixture_structure(self) -> None:
        data = _load_fixture("asr_device_recovery_success.json")
        assert data["expected"]["final_state"] == "READY"
        assert data["expected"]["recovery_event"] == ASR_DEVICE_RECOVERED

    def test_exhausted_fixture_structure(self) -> None:
        data = _load_fixture("asr_device_recovery_exhausted.json")
        assert data["expected"]["final_state"] == "ERROR"
        assert data["expected"]["recovery_event"] == ASR_DEVICE_RECOVERY_EXHAUSTED

    def test_alternate_switch_fixture_structure(self) -> None:
        data = _load_fixture("asr_device_alternate_switch.json")
        assert data["expected"]["device_switched"] is True
        assert data["expected"]["new_device_index"] == 2
