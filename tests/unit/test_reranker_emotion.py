"""M28-O：RerankerServiceContract 情感感知重排序测试（T-RERANK-EMO-01~02）"""

from __future__ import annotations

import pytest
from host_brain.retrieval.reranker import _apply_emotion_boost


class TestRerankerEmotion:
    """T-RERANK-EMO-01~02：RerankerServiceContract 情感感知重排序测试"""

    def test_emotion_matching_candidate_gets_boosted(self):
        """T-RERANK-EMO-01：emotion_context="HAPPY" 时，emotion_tag="HAPPY" 的记录分数上升"""
        candidates = [
            {"content": "开心的记忆", "emotion_tag": "HAPPY"},
            {"content": "悲伤的记忆", "emotion_tag": "SAD"},
        ]
        scores = [0.5, 0.5]
        boosted = _apply_emotion_boost(candidates, scores, "HAPPY")
        # HAPPY 候选项分数应高于 SAD 候选项
        assert boosted[0] > boosted[1]

    def test_no_emotion_context_scores_unchanged(self):
        """T-RERANK-EMO-02：emotion_context=None 时，分数不变"""
        candidates = [
            {"content": "记忆1", "emotion_tag": "HAPPY"},
            {"content": "记忆2", "emotion_tag": "SAD"},
        ]
        scores = [0.6, 0.4]
        result = _apply_emotion_boost(candidates, scores, "")
        assert result == scores

    def test_boost_factor_is_1_3x(self):
        """情感匹配的候选项加权 1.3x"""
        candidates = [{"content": "开心", "emotion_tag": "HAPPY"}]
        scores = [0.5]
        boosted = _apply_emotion_boost(candidates, scores, "HAPPY", boost_factor=1.3)
        assert abs(boosted[0] - 0.65) < 0.001

    def test_boost_capped_at_1_0(self):
        """加权后分数不超过 1.0"""
        candidates = [{"content": "开心", "emotion_tag": "HAPPY"}]
        scores = [0.9]
        boosted = _apply_emotion_boost(candidates, scores, "HAPPY", boost_factor=1.3)
        assert boosted[0] <= 1.0

    def test_empty_candidates_returns_empty(self):
        """空候选项列表返回空列表"""
        result = _apply_emotion_boost([], [], "HAPPY")
        assert result == []
