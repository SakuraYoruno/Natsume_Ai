"""M29-F：LLM 情感感知生成参数动态调整测试（T-LLM-GEN-PARAM-01~03）"""

from __future__ import annotations

import pytest
from host_brain.integrations.llm_gateway import _inject_emotion_gen_params


class TestLlmGenParamEmotion:
    """T-LLM-GEN-PARAM-01~03：LLM 情感感知生成参数动态调整测试"""

    def test_excited_emotion_raises_temperature(self):
        """T-LLM-GEN-PARAM-01：EXCITED 情感时 temperature 上调（>0.5）"""
        payload = {"task": "chat"}
        result = _inject_emotion_gen_params(payload, {"emotion_tag": "EXCITED", "mood_state": "engaged"})
        assert "gen_params" in result
        assert result["gen_params"]["temperature"] > 0.5
        assert result["gen_params"]["emotion_adjusted"] is True

    def test_sad_emotion_lowers_temperature(self):
        """T-LLM-GEN-PARAM-02：SAD 情感时 temperature 下调（<0.7）"""
        payload = {"task": "chat"}
        result = _inject_emotion_gen_params(payload, {"emotion_tag": "SAD", "mood_state": "tired"})
        assert result["gen_params"]["temperature"] < 0.7

    def test_angry_emotion_lowers_temperature(self):
        """T-LLM-GEN-PARAM-03：ANGRY 情感时 temperature 下调（<0.7）"""
        payload = {"task": "chat"}
        result = _inject_emotion_gen_params(payload, {"emotion_tag": "ANGRY", "mood_state": "steady"})
        assert result["gen_params"]["temperature"] < 0.7

    def test_neutral_emotion_uses_default(self):
        """NEUTRAL 情感时使用基础 temperature（0.3~0.6 范围内）"""
        payload = {"task": "chat"}
        result = _inject_emotion_gen_params(payload, {"emotion_tag": "NEUTRAL", "mood_state": "steady"})
        assert 0.3 <= result["gen_params"]["temperature"] <= 0.6

    def test_original_payload_not_mutated(self):
        """原始 payload 不被修改"""
        payload = {"task": "chat"}
        _inject_emotion_gen_params(payload, {"emotion_tag": "HAPPY"})
        assert "gen_params" not in payload

    def test_gen_params_contains_emotion_tag(self):
        """gen_params 中包含 emotion_tag 字段"""
        payload = {"task": "chat"}
        result = _inject_emotion_gen_params(payload, {"emotion_tag": "HAPPY"})
        assert result["gen_params"]["emotion_tag"] == "HAPPY"
