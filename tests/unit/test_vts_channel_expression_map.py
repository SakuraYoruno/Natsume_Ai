"""M28-L：EMOTION_TO_EXPRESSION_ID 扩展至16种测试（T-VTS-MAP-01~03）"""

from __future__ import annotations

import pytest
from host_brain.channels.vts import EMOTION_TO_EXPRESSION_ID, EMOTION_FALLBACK_MAP


# 16 种情感标签
ALL_EMOTION_TAGS = [
    "NEUTRAL", "HAPPY", "SAD", "ANGRY", "SURPRISED", "THINKING",
    "EXCITED", "CALM", "FRUSTRATED", "ANXIOUS", "LOVING",
    "EMBARRASSED", "CONFUSED", "BORED", "PROUD", "GRATEFUL",
]


class TestVTSExpressionMap:
    """T-VTS-MAP-01~03：EMOTION_TO_EXPRESSION_ID 扩展测试"""

    def test_all_16_emotions_have_expression_id(self):
        """T-VTS-MAP-01：16 种情感标签均有对应表情 ID，无 None 返回"""
        for tag in ALL_EMOTION_TAGS:
            expression_id = EMOTION_TO_EXPRESSION_ID.get(tag)
            assert expression_id is not None, f"情感标签 {tag} 没有对应表情 ID"
            assert len(expression_id) > 0

    def test_unknown_emotion_has_fallback(self):
        """T-VTS-MAP-02：未知情感标签有 fallback 映射，不返回 None"""
        assert "UNKNOWN" in EMOTION_FALLBACK_MAP
        assert EMOTION_FALLBACK_MAP["UNKNOWN"] is not None

    def test_neutral_maps_to_neutral_expression(self):
        """T-VTS-MAP-03：NEUTRAL 情感有对应的热键映射（中性/移除表情操作）"""
        expression_id = EMOTION_TO_EXPRESSION_ID.get("NEUTRAL")
        assert expression_id is not None
        assert len(expression_id) > 0

    def test_emotion_map_has_16_entries(self):
        """EMOTION_TO_EXPRESSION_ID 包含 16 个条目"""
        assert len(EMOTION_TO_EXPRESSION_ID) >= 16

    def test_fallback_map_has_positive_negative_unknown(self):
        """EMOTION_FALLBACK_MAP 包含 POSITIVE/NEGATIVE/UNKNOWN 三个分组"""
        assert "POSITIVE" in EMOTION_FALLBACK_MAP
        assert "NEGATIVE" in EMOTION_FALLBACK_MAP
        assert "UNKNOWN" in EMOTION_FALLBACK_MAP
