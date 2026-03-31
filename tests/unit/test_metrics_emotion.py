"""M29-Q：MetricsCollector 情感维度指标测试（T-METRICS-EMOTION-01~02）"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from host_brain.obs.metrics import MetricsCollector


def _make_collector_with_emotion_log(records: list[dict]) -> MetricsCollector:
    ledger = MagicMock()
    ledger.governor_decision_log = records
    collector = MetricsCollector.__new__(MetricsCollector)
    collector.ledger = ledger
    return collector


class TestMetricsEmotionDimension:
    """T-METRICS-EMOTION-01~02：MetricsCollector 情感维度指标测试"""

    def test_emotion_metrics_in_collect_output(self):
        """T-METRICS-EMOTION-01：collect() 输出包含 emotion_metrics 字段"""
        ledger = MagicMock()
        ledger.governor_decision_log = []
        collector = MagicMock(spec=MetricsCollector)
        collector.ledger = ledger
        collector._query_emotion_metrics = MetricsCollector._query_emotion_metrics.__get__(collector)
        result = collector._query_emotion_metrics()
        assert "emotion_tag_distribution" in result
        assert "avg_emotion_intensity" in result
        assert "emotion_gate_blocked_count" in result
        assert "latest_emotion_tag" in result
        assert "latest_mood_state" in result

    def test_emotion_tag_distribution_counted(self):
        """T-METRICS-EMOTION-02：emotion_tag 分布被正确统计"""
        records = [
            {"payload": {"emotion_tag": "HAPPY", "emotion_intensity": 0.8, "mood_state": "engaged"}},
            {"payload": {"emotion_tag": "HAPPY", "emotion_intensity": 0.7}},
            {"payload": {"emotion_tag": "SAD", "emotion_intensity": 0.5}},
        ]
        collector = _make_collector_with_emotion_log(records)
        result = collector._query_emotion_metrics()
        assert result["emotion_tag_distribution"]["HAPPY"] == 2
        assert result["emotion_tag_distribution"]["SAD"] == 1

    def test_avg_emotion_intensity_calculated(self):
        """平均情感强度被正确计算"""
        records = [
            {"payload": {"emotion_tag": "HAPPY", "emotion_intensity": 0.8}},
            {"payload": {"emotion_tag": "SAD", "emotion_intensity": 0.4}},
        ]
        collector = _make_collector_with_emotion_log(records)
        result = collector._query_emotion_metrics()
        assert abs(result["avg_emotion_intensity"] - 0.6) < 0.01

    def test_emotion_gate_blocked_counted(self):
        """情感门控拒绝次数被正确统计"""
        records = [
            {"payload": {}, "reason_code": "EMOTION_GATE_BLOCKED"},
            {"payload": {}, "reason_code": "EMOTION_GATE_BLOCKED"},
            {"payload": {}, "reason_code": "SYSMODE_FORBIDDEN"},
        ]
        collector = _make_collector_with_emotion_log(records)
        result = collector._query_emotion_metrics()
        assert result["emotion_gate_blocked_count"] == 2

    def test_empty_log_returns_defaults(self):
        """空日志时返回默认值"""
        collector = _make_collector_with_emotion_log([])
        result = collector._query_emotion_metrics()
        assert result["emotion_tag_distribution"] == {}
        assert result["avg_emotion_intensity"] is None
        assert result["emotion_gate_blocked_count"] == 0
        assert result["latest_emotion_tag"] is None
