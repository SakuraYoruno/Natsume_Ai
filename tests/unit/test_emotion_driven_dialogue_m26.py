"""M26-E：情感驱动对话链路测试（T-EMOTION-01~06）。

验收标准：
- T-EMOTION-01：LLM 输出含合法 emotion_tag 时，VTS set_expression 被调用（emotion_tag 正确传递）
- T-EMOTION-02：LLM 输出含合法 emotion_tag 时，TTS synthesize 接收到对应 emotion_tag
- T-EMOTION-03：LLM 输出 emotion_tag 非法时，QA 降级为 NEUTRAL，不中断链路
- T-EMOTION-04：LLM 输出缺少 emotion_tag 时，默认使用 NEUTRAL
- T-EMOTION-05：_validate_emotion_tag() 对所有枚举值返回合法
- T-EMOTION-06：_validate_emotion_tag() 对非枚举值返回 NEUTRAL 并写警告日志
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from host_brain.core.qa import (
    DEFAULT_EMOTION_TAG,
    VALID_EMOTION_TAGS,
    VIOLATION_INVALID_EMOTION_TAG,
    _validate_emotion_tag,
    extract_emotion_tag,
)


# ---------------------------------------------------------------------------
# T-EMOTION-05：_validate_emotion_tag() 对所有枚举值返回合法
# ---------------------------------------------------------------------------

class TestValidateEmotionTagAllValid:
    """T-EMOTION-05"""

    @pytest.mark.parametrize("tag", sorted(VALID_EMOTION_TAGS))
    def test_all_valid_tags_pass(self, tag: str) -> None:
        result = _validate_emotion_tag(tag)
        assert result == tag, f"expected {tag!r}, got {result!r}"

    @pytest.mark.parametrize("tag", sorted(VALID_EMOTION_TAGS))
    def test_lowercase_normalized(self, tag: str) -> None:
        """小写输入应被规范化为大写枚举值。"""
        result = _validate_emotion_tag(tag.lower())
        assert result == tag


# ---------------------------------------------------------------------------
# T-EMOTION-06：_validate_emotion_tag() 对非枚举值返回 NEUTRAL 并写警告日志
# ---------------------------------------------------------------------------

class TestValidateEmotionTagInvalid:
    """T-EMOTION-06"""

    def test_invalid_tag_returns_neutral(self) -> None:
        result = _validate_emotion_tag("UNKNOWN_EMOTION")
        assert result == DEFAULT_EMOTION_TAG

    def test_empty_string_returns_neutral(self) -> None:
        result = _validate_emotion_tag("")
        assert result == DEFAULT_EMOTION_TAG

    def test_none_returns_neutral(self) -> None:
        result = _validate_emotion_tag(None)
        assert result == DEFAULT_EMOTION_TAG

    def test_invalid_tag_writes_qa_violation(self) -> None:
        mock_ledger = MagicMock()
        mock_ledger.log_qa_violation = MagicMock()

        result = _validate_emotion_tag(
            "INVALID_TAG",
            ledger=mock_ledger,
            request_id="req_test_001",
        )

        assert result == DEFAULT_EMOTION_TAG
        mock_ledger.log_qa_violation.assert_called_once()
        call_kwargs = mock_ledger.log_qa_violation.call_args.kwargs
        assert call_kwargs["code"] == VIOLATION_INVALID_EMOTION_TAG
        assert call_kwargs["field"] == "narrative.emotion_tag"
        assert call_kwargs["request_id"] == "req_test_001"

    def test_invalid_tag_no_ledger_no_error(self) -> None:
        """无 ledger 时不报错，仍返回 NEUTRAL。"""
        result = _validate_emotion_tag("BAD_TAG", ledger=None)
        assert result == DEFAULT_EMOTION_TAG


# ---------------------------------------------------------------------------
# T-EMOTION-03：LLM 输出 emotion_tag 非法时，QA 降级为 NEUTRAL，不中断链路
# ---------------------------------------------------------------------------

class TestExtractEmotionTagFromPayload:
    """T-EMOTION-03 & T-EMOTION-04"""

    def test_valid_emotion_tag_extracted(self) -> None:
        """T-EMOTION-01 前置：合法 emotion_tag 正确提取。"""
        payload = {
            "narrative": {"text": "hello", "emotion_tag": "HAPPY"},
            "intent_draft": {},
            "meta": {"draft_only": True},
        }
        result = extract_emotion_tag(payload)
        assert result == "HAPPY"

    def test_invalid_emotion_tag_downgraded(self) -> None:
        """T-EMOTION-03：非法 emotion_tag 降级为 NEUTRAL。"""
        payload = {
            "narrative": {"text": "hello", "emotion_tag": "INVALID"},
            "intent_draft": {},
            "meta": {"draft_only": True},
        }
        result = extract_emotion_tag(payload)
        assert result == DEFAULT_EMOTION_TAG

    def test_missing_emotion_tag_defaults_neutral(self) -> None:
        """T-EMOTION-04：缺少 emotion_tag 时默认 NEUTRAL。"""
        payload = {
            "narrative": {"text": "hello"},
            "intent_draft": {},
            "meta": {"draft_only": True},
        }
        result = extract_emotion_tag(payload)
        assert result == DEFAULT_EMOTION_TAG

    def test_empty_payload_returns_neutral(self) -> None:
        result = extract_emotion_tag({})
        assert result == DEFAULT_EMOTION_TAG


# ---------------------------------------------------------------------------
# T-EMOTION-01：VTS set_expression 被调用（emotion_tag 正确传递）
# ---------------------------------------------------------------------------

class TestVoicePipelineEmotionTagVTS:
    """T-EMOTION-01"""

    def test_set_expression_called_with_emotion_tag(self) -> None:
        """enqueue_speak 时，VTS set_expression 应以正确 emotion_tag 被调用。"""
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

        mock_rpc = MagicMock(spec=VoiceRpcContract)
        mock_reporter = MagicMock(spec=EdgeEventReporter)

        pipeline = EdgeVoicePipeline(
            voice_rpc=mock_rpc,
            speech_queue=mock_queue,
            event_reporter=mock_reporter,
            vts_driver=mock_vts,
        )

        pipeline.enqueue_speak(
            request_id="req_emotion_01",
            payload={
                "session_id": "sess_01",
                "utterance_id": "utt_01",
                "text": "こんにちは",
                "emotion_tag": "HAPPY",
            },
        )

        mock_vts.set_expression.assert_called_once_with("HAPPY", intensity=0.5)
        mock_vts.talk_start.assert_called_once()


# ---------------------------------------------------------------------------
# T-EMOTION-02：TTS synthesize 接收到对应 emotion_tag
# ---------------------------------------------------------------------------

class TestTTSSynthesizeEmotionTag:
    """T-EMOTION-02"""

    def test_synthesize_receives_emotion_tag(self) -> None:
        """make_sovits_synthesizer 回调应将 speak_event.emotion_tag 传给 synthesize()。"""
        from edge_client.actuators.tts_engine import SoVITSModelManager, make_sovits_synthesizer

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
            "emotion_tag": "EXCITED",
            "voice_profile": "default",
        }
        synthesizer(speak_event)

        mock_engine.synthesize.assert_called_once()
        call_kwargs = mock_engine.synthesize.call_args
        assert call_kwargs.kwargs.get("emotion_tag") == "EXCITED"
