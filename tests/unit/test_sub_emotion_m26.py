"""M26-H：sub_emotion 细粒度表情驱动测试（T-SUB-EMOTION-01~04）。

验收标准：
- T-SUB-EMOTION-01：sub_emotion_to_hotkey("blush") 返回对应热键 ID
- T-SUB-EMOTION-02：sub_emotion_to_hotkey("unknown") 返回 None（不触发，不报错）
- T-SUB-EMOTION-03：speak payload 含 sub_emotion="star_eye" 时，VTS 额外触发对应热键
- T-SUB-EMOTION-04：speak payload 无 sub_emotion 时，仅触发 set_expression，无额外热键
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from host_brain.channels.vts_mapping_store import (
    SUB_EMOTION_HOTKEY_MAP,
    sub_emotion_to_hotkey,
)


# ---------------------------------------------------------------------------
# T-SUB-EMOTION-01：sub_emotion_to_hotkey("blush") 返回对应热键 ID
# ---------------------------------------------------------------------------

class TestSubEmotionToHotkeyBasic:
    """T-SUB-EMOTION-01"""

    def test_blush_returns_hotkey_id(self) -> None:
        result = sub_emotion_to_hotkey("blush")
        assert result == "hotkey_exp_blush"

    def test_star_eye_returns_hotkey_id(self) -> None:
        result = sub_emotion_to_hotkey("star_eye")
        assert result == "hotkey_exp_star_eye"

    def test_heart_eye_returns_hotkey_id(self) -> None:
        result = sub_emotion_to_hotkey("heart_eye")
        assert result == "hotkey_exp_heart_eye"

    def test_cry_returns_hotkey_id(self) -> None:
        result = sub_emotion_to_hotkey("cry")
        assert result == "hotkey_exp_cry"

    def test_scissors_eye_returns_hotkey_id(self) -> None:
        result = sub_emotion_to_hotkey("scissors_eye")
        assert result == "hotkey_exp_scissors_eye"

    def test_dizzy_returns_hotkey_id(self) -> None:
        result = sub_emotion_to_hotkey("dizzy")
        assert result == "hotkey_exp_dizzy"

    def test_disdain_returns_hotkey_id(self) -> None:
        result = sub_emotion_to_hotkey("disdain")
        assert result == "hotkey_exp_disdain_dark"

    def test_peace_sign_returns_hotkey_id(self) -> None:
        result = sub_emotion_to_hotkey("peace_sign")
        assert result == "hotkey_exp_peace_sign"

    @pytest.mark.parametrize("sub_emotion", list(SUB_EMOTION_HOTKEY_MAP.keys()))
    def test_all_sub_emotions_return_non_empty_hotkey(self, sub_emotion: str) -> None:
        result = sub_emotion_to_hotkey(sub_emotion)
        assert result is not None
        assert len(result) > 0


# ---------------------------------------------------------------------------
# T-SUB-EMOTION-02：sub_emotion_to_hotkey("unknown") 返回 None
# ---------------------------------------------------------------------------

class TestSubEmotionToHotkeyUnknown:
    """T-SUB-EMOTION-02"""

    def test_unknown_returns_none(self) -> None:
        result = sub_emotion_to_hotkey("unknown")
        assert result is None

    def test_none_returns_none(self) -> None:
        result = sub_emotion_to_hotkey(None)
        assert result is None

    def test_empty_string_returns_none(self) -> None:
        result = sub_emotion_to_hotkey("")
        assert result is None

    def test_invalid_value_no_exception(self) -> None:
        """未知 sub_emotion 不应抛出异常。"""
        result = sub_emotion_to_hotkey("INVALID_SUB_EMOTION_XYZ")
        assert result is None


# ---------------------------------------------------------------------------
# T-SUB-EMOTION-03：speak payload 含 sub_emotion 时，VTS 额外触发对应热键
# ---------------------------------------------------------------------------

class TestVoicePipelineSubEmotionTrigger:
    """T-SUB-EMOTION-03"""

    def test_sub_emotion_triggers_extra_hotkey(self) -> None:
        from edge_client.actuators.speech import SpeechPlaybackQueue
        from edge_client.actuators.vts_driver import VTubeStudioDriver
        from edge_client.rpc.event_reporter import EdgeEventReporter
        from edge_client.rpc.voice import VoiceRpcContract
        from edge_client.runtime.voice_pipeline import EdgeVoicePipeline

        mock_vts = MagicMock(spec=VTubeStudioDriver)
        mock_vts.set_expression.return_value = MagicMock(success=True)
        mock_vts.trigger_hotkey.return_value = MagicMock(success=True)
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
            request_id="req_sub_emotion_01",
            payload={
                "session_id": "sess_01",
                "utterance_id": "utt_01",
                "text": "わあ、すごい！",
                "emotion_tag": "EXCITED",
                "sub_emotion": "star_eye",
            },
        )

        # set_expression 应以 EXCITED 被调用
        mock_vts.set_expression.assert_called_once_with("EXCITED", intensity=0.5)
        # trigger_hotkey 应以 star_eye 对应的热键 ID 被调用
        mock_vts.trigger_hotkey.assert_called_once_with("hotkey_exp_star_eye")
        mock_vts.talk_start.assert_called_once()


# ---------------------------------------------------------------------------
# T-SUB-EMOTION-04：speak payload 无 sub_emotion 时，仅触发 set_expression
# ---------------------------------------------------------------------------

class TestVoicePipelineNoSubEmotion:
    """T-SUB-EMOTION-04"""

    def test_no_sub_emotion_no_extra_hotkey(self) -> None:
        from edge_client.actuators.speech import SpeechPlaybackQueue
        from edge_client.actuators.vts_driver import VTubeStudioDriver
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
            request_id="req_no_sub_emotion",
            payload={
                "session_id": "sess_01",
                "utterance_id": "utt_02",
                "text": "こんにちは",
                "emotion_tag": "HAPPY",
                # 无 sub_emotion 字段
            },
        )

        mock_vts.set_expression.assert_called_once_with("HAPPY", intensity=0.5)
        # trigger_hotkey 不应被调用
        mock_vts.trigger_hotkey.assert_not_called()
        mock_vts.talk_start.assert_called_once()
