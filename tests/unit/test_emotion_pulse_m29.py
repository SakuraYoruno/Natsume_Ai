"""M32-I锛歟motion_pulse 妲戒綅娉ㄥ叆涓撻」娴嬭瘯锛圱-EMO-PULSE-01~03锛?""

from __future__ import annotations

import pytest
from host_brain.core.prompt_context import build_emotion_pulse_slot
from host_brain.core.metabolism import EmotionVector


class TestEmotionPulseSlot:
    """T-EMO-PULSE-01~03锛歟motion_pulse 鍔ㄦ€佹Ы浣嶆敞鍏ユ祴璇?""

    def test_returns_required_fields(self):
        """T-EMO-PULSE-01锛歜uild_emotion_pulse_slot() 杩斿洖鍚?emotion_tag/mood_state/style_hint 鐨勫瓧鍏?""
        vector = EmotionVector(valence=0.5, arousal=0.6, dominance=0.0, ts_ms=0)
        result = build_emotion_pulse_slot(vector, emotion_tag="HAPPY", mood_state="engaged")
        assert "emotion_tag" in result
        assert "mood_state" in result
        assert "style_hint" in result
        assert "valence" in result
        assert "arousal" in result
        assert "intensity" in result

    def test_style_hint_is_chinese_string(self):
        """T-EMO-PULSE-02锛歴tyle_hint 涓洪潪绌轰腑鏂囧瓧绗︿覆"""
        vector = EmotionVector(valence=0.3, arousal=0.4, dominance=0.0, ts_ms=0)
        result = build_emotion_pulse_slot(vector, emotion_tag="CALM", mood_state="calm")
        hint = result["style_hint"]
        assert isinstance(hint, str)
        assert len(hint) > 0
        # 搴斿寘鍚腑鏂囧瓧绗?        assert any('\u4e00' <= c <= '\u9fff' for c in hint)

    def test_none_emotion_vector_uses_defaults(self):
        """T-EMO-PULSE-03锛歟motion_vector=None 鏃讹紝浣跨敤榛樿鍊硷紝涓嶆姤閿?""
        result = build_emotion_pulse_slot(None, emotion_tag="NEUTRAL", mood_state="steady")
        assert result is not None
        assert result["emotion_tag"] == "NEUTRAL"
        assert result["mood_state"] == "steady"
        assert result["valence"] == 0.0
        assert result["arousal"] == 0.3

    def test_emotion_tag_normalized_to_uppercase(self):
        """emotion_tag 琚鑼冨寲涓哄ぇ鍐?""
        vector = EmotionVector(valence=0.0, arousal=0.0, dominance=0.0, ts_ms=0)
        result = build_emotion_pulse_slot(vector, emotion_tag="happy")
        assert result["emotion_tag"] == "HAPPY"

    def test_high_arousal_positive_valence_style(self):
        """楂樺敜閱掑害 + 姝ｆ晥浠锋椂锛宻tyle_hint 鍚椿娉兼弿杩?""
        vector = EmotionVector(valence=0.8, arousal=0.9, dominance=0.0, ts_ms=0)
        result = build_emotion_pulse_slot(vector, emotion_tag="EXCITED", mood_state="engaged")
        assert "娲绘臣" in result["style_hint"] or "楂樻定" in result["style_hint"]

    def test_high_arousal_negative_valence_style(self):
        """楂樺敜閱掑害 + 璐熸晥浠锋椂锛宻tyle_hint 鍚畨鎶氭弿杩?""
        vector = EmotionVector(valence=-0.8, arousal=0.9, dominance=0.0, ts_ms=0)
        result = build_emotion_pulse_slot(vector, emotion_tag="ANGRY", mood_state="critical")
        assert "瀹夋姎" in result["style_hint"] or "婵€鍔? in result["style_hint"]

    def test_low_arousal_style(self):
        """浣庡敜閱掑害鏃讹紝style_hint 鍚钩闈欐弿杩?""
        vector = EmotionVector(valence=0.0, arousal=0.1, dominance=0.0, ts_ms=0)
        result = build_emotion_pulse_slot(vector, emotion_tag="CALM", mood_state="calm")
        assert "骞抽潤" in result["style_hint"] or "娓╁拰" in result["style_hint"]

    def test_intensity_in_valid_range(self):
        """intensity 鍦?[0.0, 1.0] 鑼冨洿鍐?""
        vector = EmotionVector(valence=0.5, arousal=0.7, dominance=0.0, ts_ms=0)
        result = build_emotion_pulse_slot(vector, emotion_tag="HAPPY", mood_state="engaged")
        assert 0.0 <= result["intensity"] <= 1.0

