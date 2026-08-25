"""M32-E：情感回声专项测试（T-EMO-ECHO-01~03）"""

from __future__ import annotations

import pytest
from host_brain.core.emotion_timeline import compute_emotion_echo
from host_brain.core.metabolism import EmotionVector


class TestEmotionEcho:
    """T-EMO-ECHO-01~03：情感回声机制测试"""

    def test_echo_returns_required_fields(self):
        """T-EMO-ECHO-01：compute_emotion_echo() 返回含 echo_emotion_tag/response_hint 的字典"""
        current_vector = EmotionVector(valence=0.0, arousal=0.0, dominance=0.0, ts_ms=0)
        result = compute_emotion_echo("HAPPY", current_vector=current_vector)
        assert "echo_emotion_tag" in result
        assert "echo_valence" in result
        assert "echo_arousal" in result
        assert "response_hint" in result

    def test_echo_valence_in_valid_range(self):
        """T-EMO-ECHO-02：echo_valence 在 [-1.0, 1.0] 范围内"""
        current_vector = EmotionVector(valence=0.3, arousal=0.2, dominance=0.0, ts_ms=0)
        result = compute_emotion_echo("HAPPY", current_vector=current_vector)
        assert -1.0 <= result["echo_valence"] <= 1.0
        assert -1.0 <= result["echo_arousal"] <= 1.0

    def test_response_hint_is_chinese_string(self):
        """T-EMO-ECHO-03：response_hint 为非空中文字符串"""
        current_vector = EmotionVector(valence=0.0, arousal=0.0, dominance=0.0, ts_ms=0)
        result = compute_emotion_echo("SAD", current_vector=current_vector)
        hint = result["response_hint"]
        assert isinstance(hint, str)
        assert len(hint) > 0
        # 应包含中文字符
        assert any('\u4e00' <= c <= '\u9fff' for c in hint)

    def test_happy_user_emotion_shifts_echo_positive(self):
        """用户情感为 HAPPY 时，echo_valence 向正方向漂移"""
        current_vector = EmotionVector(valence=0.0, arousal=0.0, dominance=0.0, ts_ms=0)
        result = compute_emotion_echo("HAPPY", current_vector=current_vector, echo_weight=0.4)
        # HAPPY 的 valence=0.6，混合后应 > 0
        assert result["echo_valence"] > 0.0

    def test_sad_user_emotion_shifts_echo_negative(self):
        """用户情感为 SAD 时，echo_valence 向负方向漂移"""
        current_vector = EmotionVector(valence=0.0, arousal=0.0, dominance=0.0, ts_ms=0)
        result = compute_emotion_echo("SAD", current_vector=current_vector, echo_weight=0.4)
        # SAD 的 valence=-0.5，混合后应 < 0
        assert result["echo_valence"] < 0.0

    def test_unknown_emotion_returns_neutral_echo(self):
        """未知情感标签时，返回中性回声"""
        current_vector = EmotionVector(valence=0.0, arousal=0.0, dominance=0.0, ts_ms=0)
        result = compute_emotion_echo("UNKNOWN_EMOTION", current_vector=current_vector)
        # 未知情感不改变当前向量
        assert result["echo_valence"] == 0.0
        assert result["echo_arousal"] == 0.0

    def test_echo_weight_controls_influence(self):
        """echo_weight 控制情感影响程度"""
        current_vector = EmotionVector(valence=0.0, arousal=0.0, dominance=0.0, ts_ms=0)
        result_low = compute_emotion_echo("HAPPY", current_vector=current_vector, echo_weight=0.1)
        result_high = compute_emotion_echo("HAPPY", current_vector=current_vector, echo_weight=0.8)
        # 高权重时，echo_valence 更接近 HAPPY 的 valence（0.6）
        assert result_high["echo_valence"] > result_low["echo_valence"]
