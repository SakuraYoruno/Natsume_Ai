"""M28-K：CrossTimelineAggregator 情感事件测试（T-TIMELINE-EMO-01~02）"""

from __future__ import annotations

import pytest
from host_brain.observability.cross_timeline import (
    CrossTimelineAggregator,
    EDGE_EVENT_EMOTION_PEAK,
    EDGE_EVENT_EMOTION_SHIFT,
    VALID_EDGE_EVENT_TYPES,
)


class TestTimelineEmotion:
    """T-TIMELINE-EMO-01~02：CrossTimelineAggregator 情感事件测试"""

    def test_emotion_peak_event_type_in_valid_types(self):
        """T-TIMELINE-EMO-01a：EMOTION_PEAK 事件类型在 VALID_EDGE_EVENT_TYPES 中"""
        assert EDGE_EVENT_EMOTION_PEAK in VALID_EDGE_EVENT_TYPES

    def test_emotion_shift_event_type_in_valid_types(self):
        """T-TIMELINE-EMO-01b：EMOTION_SHIFT 事件类型在 VALID_EDGE_EVENT_TYPES 中"""
        assert EDGE_EVENT_EMOTION_SHIFT in VALID_EDGE_EVENT_TYPES

    def test_high_intensity_generates_emotion_peak(self):
        """T-TIMELINE-EMO-01：情感强度 > 0.8 时，自动生成 EMOTION_PEAK 事件"""
        events = [
            {
                "event_type": "VTS_EXPRESSION",
                "ts_ms": 1000,
                "emotion_tag": "EXCITED",
                "emotion_intensity": 0.9,
            }
        ]
        result = CrossTimelineAggregator.detect_emotion_events(events)
        peak_events = [e for e in result if e["event_type"] == EDGE_EVENT_EMOTION_PEAK]
        assert len(peak_events) >= 1
        assert peak_events[0]["payload"]["emotion_tag"] == "EXCITED"

    def test_emotion_tag_change_generates_emotion_shift(self):
        """T-TIMELINE-EMO-02：情感标签发生变化时，自动生成 EMOTION_SHIFT 事件"""
        events = [
            {"event_type": "VTS_EXPRESSION", "ts_ms": 1000, "emotion_tag": "HAPPY", "emotion_intensity": 0.5},
            {"event_type": "VTS_EXPRESSION", "ts_ms": 2000, "emotion_tag": "SAD", "emotion_intensity": 0.5},
        ]
        result = CrossTimelineAggregator.detect_emotion_events(events)
        shift_events = [e for e in result if e["event_type"] == EDGE_EVENT_EMOTION_SHIFT]
        assert len(shift_events) >= 1
        assert shift_events[0]["payload"]["from_emotion"] == "HAPPY"
        assert shift_events[0]["payload"]["to_emotion"] == "SAD"

    def test_low_intensity_no_peak_event(self):
        """情感强度 <= 0.8 时，不生成 EMOTION_PEAK 事件"""
        events = [
            {"event_type": "VTS_EXPRESSION", "ts_ms": 1000, "emotion_tag": "HAPPY", "emotion_intensity": 0.5},
        ]
        result = CrossTimelineAggregator.detect_emotion_events(events)
        peak_events = [e for e in result if e["event_type"] == EDGE_EVENT_EMOTION_PEAK]
        assert len(peak_events) == 0
