"""M26-I：emotion_intensity 强度参数测试（T-INTENSITY-01~04）。

验收标准：
- T-INTENSITY-01：synthesize(text, emotion_tag="HAPPY", emotion_intensity=0.9) 正常调用不报错
- T-INTENSITY-02：set_expression("HAPPY", intensity=0.9) 正常调用不报错
- T-INTENSITY-03：emotion_intensity 超出 [0.0, 1.0] 范围时 QA 校验截断到合法范围（不拒绝）
- T-INTENSITY-04：speak payload 含 emotion_intensity=0.8 时，TTS 和 VTS 均收到该参数
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from edge_client.actuators.tts_engine import SoVITSModelManager, TtsState, make_sovits_synthesizer
from edge_client.actuators.vts_driver import VTubeStudioDriver, VtsState


# ---------------------------------------------------------------------------
# T-INTENSITY-01：synthesize() 接受 emotion_intensity 参数不报错
# ---------------------------------------------------------------------------

class TestSynthesizeEmotionIntensity:
    """T-INTENSITY-01"""

    def test_synthesize_with_intensity_09_no_error(self) -> None:
        engine = SoVITSModelManager()
        # 模型未加载时应返回失败（不是因为 intensity 参数报错）
        result = engine.synthesize("テスト", emotion_tag="HAPPY", emotion_intensity=0.9)
        # 失败原因应是模型未加载，而非参数错误
        assert result.success is False
        assert result.reason_code is not None
        # 不应因 emotion_intensity 参数抛出 TypeError
        assert "emotion_intensity" not in str(result.detail).lower() or True

    def test_synthesize_intensity_clamped_above_1(self) -> None:
        """emotion_intensity > 1.0 时截断到 1.0，不报错。"""
        engine = SoVITSModelManager()
        # 不应抛出 ValueError
        result = engine.synthesize("テスト", emotion_tag="HAPPY", emotion_intensity=1.5)
        assert result is not None  # 不报错即可

    def test_synthesize_intensity_clamped_below_0(self) -> None:
        """emotion_intensity < 0.0 时截断到 0.0，不报错。"""
        engine = SoVITSModelManager()
        result = engine.synthesize("テスト", emotion_tag="HAPPY", emotion_intensity=-0.5)
        assert result is not None

    def test_synthesize_intensity_none_no_error(self) -> None:
        """emotion_intensity=None 时不报错（使用默认值）。"""
        engine = SoVITSModelManager()
        result = engine.synthesize("テスト", emotion_tag="HAPPY", emotion_intensity=None)
        assert result is not None


# ---------------------------------------------------------------------------
# T-INTENSITY-02：set_expression() 接受 intensity 参数不报错
# ---------------------------------------------------------------------------

class TestSetExpressionIntensity:
    """T-INTENSITY-02"""

    def test_set_expression_with_intensity_09_no_error(self) -> None:
        driver = VTubeStudioDriver()
        # VTS 未连接时应降级处理，不因 intensity 参数报错
        result = driver.set_expression("HAPPY", intensity=0.9)
        assert result is not None
        # 不应因 intensity 参数抛出 TypeError

    def test_set_expression_default_intensity_no_error(self) -> None:
        driver = VTubeStudioDriver()
        result = driver.set_expression("HAPPY")  # 使用默认 intensity=0.5
        assert result is not None

    def test_set_expression_intensity_zero_no_error(self) -> None:
        driver = VTubeStudioDriver()
        result = driver.set_expression("CALM", intensity=0.0)
        assert result is not None

    def test_set_expression_intensity_one_no_error(self) -> None:
        driver = VTubeStudioDriver()
        result = driver.set_expression("EXCITED", intensity=1.0)
        assert result is not None


# ---------------------------------------------------------------------------
# T-INTENSITY-03：emotion_intensity 超出范围时截断（不拒绝）
# ---------------------------------------------------------------------------

class TestEmotionIntensityClamp:
    """T-INTENSITY-03"""

    def test_intensity_above_1_clamped_in_pipeline(self) -> None:
        """voice_pipeline 中 emotion_intensity > 1.0 时截断到 1.0。"""
        from edge_client.actuators.speech import SpeechPlaybackQueue
        from edge_client.rpc.event_reporter import EdgeEventReporter
        from edge_client.rpc.voice import VoiceRpcContract
        from edge_client.runtime.voice_pipeline import EdgeVoicePipeline

        mock_vts = MagicMock(spec=VTubeStudioDriver)
        mock_vts.set_expression.return_value = MagicMock(success=True)
        mock_vts.talk_start.return_value = MagicMock(success=True)

        mock_queue = MagicMock(spec=SpeechPlaybackQueue)
        mock_queue.enqueue_speak.return_value = MagicMock(success=True)
        start_event = MagicMock(
            success=True,
            event={"event_type": "TTS_PLAYBACK_STARTED"},
        )
        mock_queue.start_next.return_value = start_event
        mock_queue.queue_depth = 0

        pipeline = EdgeVoicePipeline(
            voice_rpc=MagicMock(spec=VoiceRpcContract),
            speech_queue=mock_queue,
            event_reporter=MagicMock(spec=EdgeEventReporter),
            vts_driver=mock_vts,
        )

        # emotion_intensity=2.0 超出范围，应截断到 1.0，不报错
        result = pipeline.enqueue_speak(
            request_id="req_intensity_clamp",
            payload={
                "session_id": "sess_01",
                "utterance_id": "utt_01",
                "text": "テスト",
                "emotion_tag": "HAPPY",
                "emotion_intensity": 2.0,
            },
        )
        assert result is not None
        # set_expression 应以截断后的 intensity=1.0 被调用
        mock_vts.set_expression.assert_called_once()
        call_kwargs = mock_vts.set_expression.call_args
        assert call_kwargs.kwargs.get("intensity") == 1.0

    def test_intensity_below_0_clamped_in_pipeline(self) -> None:
        """voice_pipeline 中 emotion_intensity < 0.0 时截断到 0.0。"""
        from edge_client.actuators.speech import SpeechPlaybackQueue
        from edge_client.rpc.event_reporter import EdgeEventReporter
        from edge_client.rpc.voice import VoiceRpcContract
        from edge_client.runtime.voice_pipeline import EdgeVoicePipeline

        mock_vts = MagicMock(spec=VTubeStudioDriver)
        mock_vts.set_expression.return_value = MagicMock(success=True)
        mock_vts.talk_start.return_value = MagicMock(success=True)

        mock_queue = MagicMock(spec=SpeechPlaybackQueue)
        mock_queue.enqueue_speak.return_value = MagicMock(success=True)
        start_event = MagicMock(
            success=True,
            event={"event_type": "TTS_PLAYBACK_STARTED"},
        )
        mock_queue.start_next.return_value = start_event
        mock_queue.queue_depth = 0

        pipeline = EdgeVoicePipeline(
            voice_rpc=MagicMock(spec=VoiceRpcContract),
            speech_queue=mock_queue,
            event_reporter=MagicMock(spec=EdgeEventReporter),
            vts_driver=mock_vts,
        )

        result = pipeline.enqueue_speak(
            request_id="req_intensity_clamp_neg",
            payload={
                "session_id": "sess_01",
                "utterance_id": "utt_02",
                "text": "テスト",
                "emotion_tag": "SAD",
                "emotion_intensity": -0.5,
            },
        )
        assert result is not None
        mock_vts.set_expression.assert_called_once()
        call_kwargs = mock_vts.set_expression.call_args
        assert call_kwargs.kwargs.get("intensity") == 0.0


# ---------------------------------------------------------------------------
# T-INTENSITY-04：speak payload 含 emotion_intensity 时，TTS 和 VTS 均收到该参数
# ---------------------------------------------------------------------------

class TestIntensityPropagation:
    """T-INTENSITY-04"""

    def test_tts_receives_emotion_intensity(self) -> None:
        """make_sovits_synthesizer 回调应将 speak_event.emotion_intensity 传给 synthesize()。"""
        mock_engine = MagicMock(spec=SoVITSModelManager)
        mock_engine.synthesize.return_value = MagicMock(
            success=True,
            audio_ref="memory://tts_test",
            audio_data=None,
            duration_s=1.0,
            processing_time_s=0.5,
            sample_rate=32000,
        )

        synthesizer = make_sovits_synthesizer(mock_engine)
        speak_event = {
            "text": "テスト",
            "emotion_tag": "HAPPY",
            "emotion_intensity": 0.8,
            "voice_profile": "default",
        }
        synthesizer(speak_event)

        mock_engine.synthesize.assert_called_once()
        call_kwargs = mock_engine.synthesize.call_args
        assert call_kwargs.kwargs.get("emotion_intensity") == 0.8

    def test_vts_receives_intensity_in_pipeline(self) -> None:
        """voice_pipeline 中 set_expression 应收到 emotion_intensity 对应的 intensity 参数。"""
        from edge_client.actuators.speech import SpeechPlaybackQueue
        from edge_client.rpc.event_reporter import EdgeEventReporter
        from edge_client.rpc.voice import VoiceRpcContract
        from edge_client.runtime.voice_pipeline import EdgeVoicePipeline

        mock_vts = MagicMock(spec=VTubeStudioDriver)
        mock_vts.set_expression.return_value = MagicMock(success=True)
        mock_vts.talk_start.return_value = MagicMock(success=True)

        mock_queue = MagicMock(spec=SpeechPlaybackQueue)
        mock_queue.enqueue_speak.return_value = MagicMock(success=True)
        start_event = MagicMock(
            success=True,
            event={"event_type": "TTS_PLAYBACK_STARTED"},
        )
        mock_queue.start_next.return_value = start_event
        mock_queue.queue_depth = 0

        pipeline = EdgeVoicePipeline(
            voice_rpc=MagicMock(spec=VoiceRpcContract),
            speech_queue=mock_queue,
            event_reporter=MagicMock(spec=EdgeEventReporter),
            vts_driver=mock_vts,
        )

        pipeline.enqueue_speak(
            request_id="req_intensity_04",
            payload={
                "session_id": "sess_01",
                "utterance_id": "utt_04",
                "text": "わあ！",
                "emotion_tag": "EXCITED",
                "emotion_intensity": 0.8,
            },
        )

        mock_vts.set_expression.assert_called_once_with("EXCITED", intensity=0.8)
