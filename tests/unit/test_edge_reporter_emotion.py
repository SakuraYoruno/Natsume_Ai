"""M29-R：EdgeEventReporter 情感字段测试（T-EDGE-REPORTER-EMOTION-01~02）"""

from __future__ import annotations

import pytest
from edge_client.rpc.event_reporter import EventReportItem


class TestEdgeEventReporterEmotion:
    """T-EDGE-REPORTER-EMOTION-01~02：EdgeEventReporter 情感字段测试"""

    def test_emotion_fields_default_none(self):
        """T-EDGE-REPORTER-EMOTION-01：默认情感字段为 None"""
        item = EventReportItem(
            event_type="ASR_TRANSCRIPT",
            ts_ms=1000,
            request_id="req-001",
            session_id="sess-001",
        )
        assert item.emotion_tag is None
        assert item.emotion_intensity is None

    def test_emotion_fields_set_correctly(self):
        """T-EDGE-REPORTER-EMOTION-02：情感字段被正确设置"""
        item = EventReportItem(
            event_type="ASR_TRANSCRIPT",
            ts_ms=1000,
            request_id="req-001",
            session_id="sess-001",
            emotion_tag="SAD",
            emotion_intensity=0.75,
        )
        assert item.emotion_tag == "SAD"
        assert item.emotion_intensity == 0.75

    def test_to_dict_includes_emotion_when_set(self):
        """to_dict() 在有情感字段时包含情感信息"""
        item = EventReportItem(
            event_type="ASR_TRANSCRIPT",
            ts_ms=1000,
            request_id="req-001",
            session_id="sess-001",
            emotion_tag="HAPPY",
            emotion_intensity=0.9,
        )
        d = item.to_dict()
        assert d["emotion_tag"] == "HAPPY"
        assert d["emotion_intensity"] == 0.9

    def test_to_dict_excludes_emotion_when_none(self):
        """to_dict() 在无情感字段时不包含情感信息"""
        item = EventReportItem(
            event_type="ASR_TRANSCRIPT",
            ts_ms=1000,
            request_id="req-001",
            session_id="sess-001",
        )
        d = item.to_dict()
        assert "emotion_tag" not in d
        assert "emotion_intensity" not in d

    def test_standard_fields_still_present(self):
        """标准字段在添加情感字段后仍然存在"""
        item = EventReportItem(
            event_type="ASR_TRANSCRIPT",
            ts_ms=1000,
            request_id="req-001",
            session_id="sess-001",
            emotion_tag="NEUTRAL",
        )
        d = item.to_dict()
        assert d["event_type"] == "ASR_TRANSCRIPT"
        assert d["ts_ms"] == 1000
        assert d["request_id"] == "req-001"
        assert d["session_id"] == "sess-001"
