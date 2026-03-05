"""M29-P：ASRTranscriptEvent 用户情感推断字段测试（T-ASR-EMOTION-01~02）"""

from __future__ import annotations

import pytest
from edge_client.senses.asr import ASRTranscriptEvent


def _make_asr_event(**kwargs) -> ASRTranscriptEvent:
    defaults = dict(
        event_id="evt-001",
        session_id="sess-001",
        segment_id="seg-001",
        text="你好",
        confidence=0.95,
        ts_start_ms=1000,
        ts_end_ms=2000,
        is_final=True,
        alternates=(),
        evidence_ref="evd://asr/evt-001",
        low_confidence=False,
        reason_code=None,
    )
    defaults.update(kwargs)
    return ASRTranscriptEvent(**defaults)


class TestASREmotionFields:
    """T-ASR-EMOTION-01~02：ASRTranscriptEvent 用户情感推断字段测试"""

    def test_emotion_fields_default_none(self):
        """T-ASR-EMOTION-01：默认情感字段为 None"""
        event = _make_asr_event()
        assert event.user_emotion_hint is None
        assert event.user_emotion_confidence is None

    def test_emotion_fields_set_correctly(self):
        """T-ASR-EMOTION-02：情感字段被正确设置"""
        event = _make_asr_event(
            user_emotion_hint="SAD",
            user_emotion_confidence=0.85,
        )
        assert event.user_emotion_hint == "SAD"
        assert event.user_emotion_confidence == 0.85

    def test_as_dict_includes_emotion_when_set(self):
        """as_dict() 在有情感字段时包含情感信息"""
        event = _make_asr_event(
            user_emotion_hint="HAPPY",
            user_emotion_confidence=0.9,
        )
        d = event.as_dict()
        assert d["user_emotion_hint"] == "HAPPY"
        assert d["user_emotion_confidence"] == 0.9

    def test_as_dict_excludes_emotion_when_none(self):
        """as_dict() 在无情感字段时不包含情感信息"""
        event = _make_asr_event()
        d = event.as_dict()
        assert "user_emotion_hint" not in d
        assert "user_emotion_confidence" not in d

    def test_standard_fields_still_present(self):
        """标准字段在添加情感字段后仍然存在"""
        event = _make_asr_event(user_emotion_hint="NEUTRAL")
        d = event.as_dict()
        assert "text" in d
        assert "confidence" in d
        assert "session_id" in d
        assert "evidence_ref" in d
