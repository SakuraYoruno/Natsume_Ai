"""M32-I：emotion_pulse 槽位注入专项测试（T-EMO-PULSE-01~03）"""

from __future__ import annotations

import pytest
from host_brain.core.context_pack import build_emotion_pulse_slot
from host_brain.core.metabolism import EmotionVector


class TestEmotionPulseSlot:
    """T-EMO-PULSE-01~03：emotion_pulse 动态槽位注入测试"""

    def test_returns_required_fields(self):
        """T-EMO-PULSE-01：build_emotion_pulse_slot() 返回含 emotion_tag/mood_state/style_hint 的字典"""
        vector = EmotionVector(valence=0.5, arousal=0.6, dominance=0.0, ts_ms=0)
        result = build_emotion_pulse_slot(vector, emotion_tag="HAPPY", mood_state="engaged")
        assert "emotion_tag" in result
        assert "mood_state" in result
        assert "style_hint" in result
        assert "valence" in result
        assert "arousal" in result
        assert "intensity" in result

    def test_style_hint_is_chinese_string(self):
        """T-EMO-PULSE-02：style_hint 为非空中文字符串"""
        vector = EmotionVector(valence=0.3, arousal=0.4, dominance=0.0, ts_ms=0)
        result = build_emotion_pulse_slot(vector, emotion_tag="CALM", mood_state="calm")
        hint = result["style_hint"]
        assert isinstance(hint, str)
        assert len(hint) > 0
        # 应包含中文字符
        assert any('\u4e00' <= c <= '\u9fff' for c in hint)

    def test_none_emotion_vector_uses_defaults(self):
        """T-EMO-PULSE-03：emotion_vector=None 时，使用默认值，不报错"""
        result = build_emotion_pulse_slot(None, emotion_tag="NEUTRAL", mood_state="steady")
        assert result is not None
        assert result["emotion_tag"] == "NEUTRAL"
        assert result["mood_state"] == "steady"
        assert result["valence"] == 0.0
        assert result["arousal"] == 0.3

    def test_emotion_tag_normalized_to_uppercase(self):
        """emotion_tag 被规范化为大写"""
        vector = EmotionVector(valence=0.0, arousal=0.0, dominance=0.0, ts_ms=0)
        result = build_emotion_pulse_slot(vector, emotion_tag="happy")
        assert result["emotion_tag"] == "HAPPY"

    def test_high_arousal_positive_valence_style(self):
        """高唤醒度 + 正效价时，style_hint 含活泼描述"""
        vector = EmotionVector(valence=0.8, arousal=0.9, dominance=0.0, ts_ms=0)
        result = build_emotion_pulse_slot(vector, emotion_tag="EXCITED", mood_state="engaged")
        assert "活泼" in result["style_hint"] or "高涨" in result["style_hint"]

    def test_high_arousal_negative_valence_style(self):
        """高唤醒度 + 负效价时，style_hint 含安抚描述"""
        vector = EmotionVector(valence=-0.8, arousal=0.9, dominance=0.0, ts_ms=0)
        result = build_emotion_pulse_slot(vector, emotion_tag="ANGRY", mood_state="critical")
        assert "安抚" in result["style_hint"] or "激动" in result["style_hint"]

    def test_low_arousal_style(self):
        """低唤醒度时，style_hint 含平静描述"""
        vector = EmotionVector(valence=0.0, arousal=0.1, dominance=0.0, ts_ms=0)
        result = build_emotion_pulse_slot(vector, emotion_tag="CALM", mood_state="calm")
        assert "平静" in result["style_hint"] or "温和" in result["style_hint"]

    def test_intensity_in_valid_range(self):
        """intensity 在 [0.0, 1.0] 范围内"""
        vector = EmotionVector(valence=0.5, arousal=0.7, dominance=0.0, ts_ms=0)
        result = build_emotion_pulse_slot(vector, emotion_tag="HAPPY", mood_state="engaged")
        assert 0.0 <= result["intensity"] <= 1.0
