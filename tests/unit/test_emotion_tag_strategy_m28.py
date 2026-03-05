"""M28-B：emotion_tag 生成策略主动注入测试（T-EMO-TAG-STR-01~03）"""

from __future__ import annotations

import pytest
from host_brain.core.metabolism import HardState, build_hard_snapshot


class TestEmotionTagStrategy:
    """T-EMO-TAG-STR-01~03：HardStateSnapshot 情感策略字段测试"""

    def test_tired_mood_excludes_excited_happy(self):
        """T-EMO-TAG-STR-01：mood_state="tired" 时，allowed_emotion_tags 不含 EXCITED/HAPPY"""
        state = HardState(energy_level=30, mood_state="tired", sys_mode="normal",
                          safety_flag=False, last_event_id=None, ts_ms=0)
        snapshot = build_hard_snapshot(state)
        assert "EXCITED" not in snapshot.allowed_emotion_tags
        assert "HAPPY" not in snapshot.allowed_emotion_tags

    def test_engaged_mood_includes_happy_excited(self):
        """T-EMO-TAG-STR-02：mood_state="engaged" 时，allowed_emotion_tags 含 HAPPY/EXCITED"""
        state = HardState(energy_level=80, mood_state="engaged", sys_mode="normal",
                          safety_flag=False, last_event_id=None, ts_ms=0)
        snapshot = build_hard_snapshot(state)
        assert "HAPPY" in snapshot.allowed_emotion_tags
        assert "EXCITED" in snapshot.allowed_emotion_tags

    def test_emotion_range_hint_is_chinese(self):
        """T-EMO-TAG-STR-03：emotion_range_hint 为非空中文描述字符串"""
        for mood in ["critical", "tired", "calm", "steady", "engaged"]:
            state = HardState(energy_level=50, mood_state=mood, sys_mode="normal",
                              safety_flag=False, last_event_id=None, ts_ms=0)
            snapshot = build_hard_snapshot(state)
            assert snapshot.emotion_range_hint
            # 验证包含中文字符
            assert any('\u4e00' <= c <= '\u9fff' for c in snapshot.emotion_range_hint)
