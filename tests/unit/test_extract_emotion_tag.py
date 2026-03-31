"""M27-B：extract_emotion_tag 传入 mood_state 测试（T-EXTRACT-TAG-01~03）"""

from __future__ import annotations

import pytest
from host_brain.core.qa import extract_emotion_tag


class TestExtractEmotionTagWithMoodState:
    """T-EXTRACT-TAG-01~03：extract_emotion_tag 传入 mood_state 约束校验"""

    def test_tired_mood_constrains_happy(self):
        """T-EXTRACT-TAG-01：mood_state="tired" 时，HAPPY 情感标签被约束为 calm 系列"""
        payload = {"narrative": {"emotion_tag": "HAPPY"}, "intent_draft": {}, "meta": {}}
        result = extract_emotion_tag(payload, mood_state="tired")
        # tired 状态下允许: NEUTRAL, CALM, SAD；HAPPY 不在其中，应被降级
        assert result in ("NEUTRAL", "CALM", "SAD")
        assert result != "HAPPY"

    def test_engaged_mood_allows_happy(self):
        """T-EXTRACT-TAG-02：mood_state="engaged" 时，HAPPY 情感标签正常通过"""
        payload = {"narrative": {"emotion_tag": "HAPPY"}, "intent_draft": {}, "meta": {}}
        result = extract_emotion_tag(payload, mood_state="engaged")
        assert result == "HAPPY"

    def test_none_mood_state_no_constraint(self):
        """T-EXTRACT-TAG-03：mood_state=None 时，不触发约束，行为与旧版一致"""
        payload = {"narrative": {"emotion_tag": "EXCITED"}, "intent_draft": {}, "meta": {}}
        result = extract_emotion_tag(payload, mood_state=None)
        assert result == "EXCITED"

    def test_missing_emotion_tag_returns_neutral(self):
        """缺少 emotion_tag 时返回 NEUTRAL"""
        payload = {"narrative": {}, "intent_draft": {}, "meta": {}}
        result = extract_emotion_tag(payload)
        assert result == "NEUTRAL"

    def test_invalid_emotion_tag_returns_neutral(self):
        """非法 emotion_tag 返回 NEUTRAL"""
        payload = {"narrative": {"emotion_tag": "INVALID_TAG"}, "intent_draft": {}, "meta": {}}
        result = extract_emotion_tag(payload)
        assert result == "NEUTRAL"
