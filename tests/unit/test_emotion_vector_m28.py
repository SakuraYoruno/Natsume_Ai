"""M28-A：EmotionVector 情绪连续体测试（T-EMO-VEC-01~06）"""

from __future__ import annotations

import pytest
from host_brain.core.metabolism import EmotionVector


class TestEmotionVector:
    """T-EMO-VEC-01~06：EmotionVector VAD 三维模型测试"""

    def test_emotion_vector_default_values(self):
        """T-EMO-VEC-01：EmotionVector 默认值为 (0.0, 0.0, 0.0)"""
        ev = EmotionVector()
        assert ev.valence == 0.0
        assert ev.arousal == 0.0
        assert ev.dominance == 0.0

    def test_emotion_vector_clamps_values(self):
        """T-EMO-VEC-02：超出 [-1,1] 范围的值被截断"""
        ev = EmotionVector(valence=2.0, arousal=-3.0, dominance=1.5)
        assert ev.valence == 1.0
        assert ev.arousal == -1.0
        assert ev.dominance == 1.0

    def test_decay_moves_toward_neutral(self):
        """T-EMO-VEC-03：decay() 后情绪向中性靠近"""
        ev = EmotionVector(valence=0.8, arousal=0.6, dominance=0.4)
        decayed = ev.decay(rate=0.1)
        assert abs(decayed.valence) < abs(ev.valence)
        assert abs(decayed.arousal) < abs(ev.arousal)

    def test_blend_mixes_emotions(self):
        """T-EMO-VEC-04：blend() 将 other 情绪混入当前情绪"""
        ev1 = EmotionVector(valence=0.8, arousal=0.0)
        ev2 = EmotionVector(valence=-0.8, arousal=0.0)
        blended = ev1.blend(ev2, weight=0.5)
        assert abs(blended.valence) < abs(ev1.valence)

    def test_to_mood_state_engaged(self):
        """T-EMO-VEC-05：高 valence + 高 arousal → mood_state="engaged" """
        ev = EmotionVector(valence=0.7, arousal=0.7)
        assert ev.to_mood_state() == "engaged"

    def test_to_mood_state_critical(self):
        """T-EMO-VEC-06：低 valence + 低 arousal → mood_state="critical" """
        ev = EmotionVector(valence=-0.8, arousal=-0.6)
        assert ev.to_mood_state() == "critical"

    def test_to_emotion_tag_happy(self):
        """高 valence + 正 arousal → emotion_tag="HAPPY" """
        ev = EmotionVector(valence=0.5, arousal=0.2)
        assert ev.to_emotion_tag() == "HAPPY"

    def test_to_emotion_tag_sad(self):
        """低 valence + 负 arousal → emotion_tag="SAD" """
        ev = EmotionVector(valence=-0.5, arousal=-0.2)
        assert ev.to_emotion_tag() == "SAD"

    def test_as_dict_contains_all_fields(self):
        """as_dict() 包含 valence/arousal/dominance/mood_state/emotion_tag"""
        ev = EmotionVector(valence=0.3, arousal=0.2, dominance=0.1)
        d = ev.as_dict()
        assert "valence" in d
        assert "arousal" in d
        assert "dominance" in d
        assert "mood_state" in d
        assert "emotion_tag" in d

    def test_backward_compat_to_mood_state(self):
        """to_mood_state() 返回值在 ALLOWED_MOOD_STATES 中"""
        from host_brain.core.metabolism import ALLOWED_MOOD_STATES
        for valence in [-0.9, -0.3, 0.0, 0.5, 0.9]:
            for arousal in [-0.9, -0.3, 0.0, 0.5, 0.9]:
                ev = EmotionVector(valence=valence, arousal=arousal)
                assert ev.to_mood_state() in ALLOWED_MOOD_STATES
