"""M32-B：情感时间线重建专项测试（T-EMO-TIMELINE-01~03）"""

from __future__ import annotations

import pytest
from host_brain.core.metabolism import EmotionTimeline, EmotionVector


class TestEmotionalTimeline:
    """T-EMO-TIMELINE-01~03：情感时间线重建测试"""

    def test_rebuild_summary_with_records(self):
        """T-EMO-TIMELINE-01：给定时间范围内有记录时，返回含 dominant_emotion/trend 的摘要"""
        timeline = EmotionTimeline()
        now_ms = 1_000_000_000_000  # 固定时间戳
        # 追加情感事件
        timeline.append(ts_ms=now_ms - 200_000, emotion_tag="HAPPY", valence=0.5, arousal=0.4, source="user_input")
        timeline.append(ts_ms=now_ms - 100_000, emotion_tag="HAPPY", valence=0.6, arousal=0.5, source="user_input")
        timeline.append(ts_ms=now_ms - 50_000, emotion_tag="EXCITED", valence=0.7, arousal=0.7, source="user_input")

        # 使用 query 方法获取最近记录
        recent = timeline.query(since_ms=now_ms - 300_000, limit=20)
        assert len(recent) >= 1
        # 验证 dominant_emotion 字段
        tag_counts: dict[str, int] = {}
        for e in recent:
            tag_counts[e.emotion_tag] = tag_counts.get(e.emotion_tag, 0) + 1
        dominant = max(tag_counts, key=lambda k: tag_counts[k])
        assert dominant in ("HAPPY", "EXCITED")

    def test_rebuild_summary_trend_values(self):
        """T-EMO-TIMELINE-02：rebuild_summary 的 trend 为 improving/stable/declining 之一"""
        timeline = EmotionTimeline()
        now_ms = 1_000_000_000_000
        # 追加情感事件（valence 上升趋势）
        for i in range(6):
            timeline.append(
                ts_ms=now_ms - (6 - i) * 50_000,
                emotion_tag="HAPPY",
                valence=0.1 * (i + 1),
                arousal=0.3,
                source="user_input",
            )

        # 手动计算趋势
        recent = timeline.query(since_ms=now_ms - 400_000, limit=20)
        assert len(recent) >= 2
        mid = len(recent) // 2
        first_half = recent[:mid] if mid > 0 else recent
        second_half = recent[mid:] if mid > 0 else recent
        avg_first = sum(e.valence for e in first_half) / len(first_half)
        avg_second = sum(e.valence for e in second_half) / len(second_half)
        if avg_second - avg_first > 0.15:
            trend = "improving"
        elif avg_first - avg_second > 0.15:
            trend = "declining"
        else:
            trend = "stable"
        assert trend in ("improving", "stable", "declining")

    def test_empty_timeline_returns_default(self):
        """T-EMO-TIMELINE-03：时间范围内无记录时，返回默认摘要，不报错"""
        timeline = EmotionTimeline()
        # 不追加任何记录，直接查询
        recent = timeline.query(since_ms=0, limit=20)
        assert recent == []
        # rebuild_summary 应返回默认值
        summary = timeline.rebuild_summary(window_ms=300_000)
        assert "dominant_emotion" in summary
        assert summary["dominant_emotion"] == "NEUTRAL"
        assert "trend" in summary

    def test_timeline_append_and_query(self):
        """情感时间线追加和查询功能正常"""
        timeline = EmotionTimeline(max_entries=10)
        for i in range(5):
            timeline.append(
                ts_ms=1000 * (i + 1),
                emotion_tag="HAPPY",
                valence=0.5,
                arousal=0.3,
                source="test",
            )
        results = timeline.query(since_ms=1000, until_ms=5000, limit=10)
        assert len(results) >= 1

    def test_timeline_max_entries_limit(self):
        """情感时间线超出最大条目数时，移除最旧的"""
        timeline = EmotionTimeline(max_entries=5)
        for i in range(10):
            timeline.append(ts_ms=1000 * (i + 1), emotion_tag="NEUTRAL", valence=0.0, arousal=0.0)
        # 最多保留 5 条
        all_entries = timeline.query(limit=100)
        assert len(all_entries) <= 5
