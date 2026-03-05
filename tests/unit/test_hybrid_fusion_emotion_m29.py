"""M29-O：HybridFusionStrategy 情感强度加权测试（T-FUSION-EMOTION-01~02）"""

from __future__ import annotations

import pytest
from host_brain.retrieval.recall_fusion import HybridFusionStrategy


class TestHybridFusionEmotionBoost:
    """T-FUSION-EMOTION-01~02：HybridFusionStrategy 情感强度加权测试"""

    def _make_strategy(self) -> HybridFusionStrategy:
        return HybridFusionStrategy(vector_weight=0.7, fts_weight=0.3)

    def test_emotion_matching_candidate_boosted(self):
        """T-FUSION-EMOTION-01：emotion_context 匹配的候选项分数被加权"""
        strategy = self._make_strategy()
        vector_candidates = [
            {
                "doc_id": "doc-001",
                "score": 0.8,
                "snippet": "test",
                "metadata": {"emotion_tag": "HAPPY"},
                "evidence_ref": "evd://doc-001",
                "source_ref": "memory://doc-001",
                "confidence": 0.8,
            },
            {
                "doc_id": "doc-002",
                "score": 0.8,
                "snippet": "test2",
                "metadata": {"emotion_tag": "SAD"},
                "evidence_ref": "evd://doc-002",
                "source_ref": "memory://doc-002",
                "confidence": 0.8,
            },
        ]
        results = strategy.fuse(
            vector_candidates=vector_candidates,
            fts_candidates=[],
            emotion_context="HAPPY",
            emotion_boost_factor=1.3,
        )
        # doc-001（HAPPY 匹配）的分数应高于 doc-002
        scores = {r["doc_id"]: r["score"] for r in results}
        assert scores["doc-001"] > scores["doc-002"]

    def test_no_emotion_context_no_boost(self):
        """T-FUSION-EMOTION-02：无情感上下文时不加权"""
        strategy = self._make_strategy()
        vector_candidates = [
            {
                "doc_id": "doc-001",
                "score": 0.8,
                "snippet": "test",
                "metadata": {"emotion_tag": "HAPPY"},
                "evidence_ref": "evd://doc-001",
                "source_ref": "memory://doc-001",
                "confidence": 0.8,
            },
        ]
        results_no_boost = strategy.fuse(
            vector_candidates=vector_candidates,
            fts_candidates=[],
        )
        results_with_boost = strategy.fuse(
            vector_candidates=vector_candidates,
            fts_candidates=[],
            emotion_context="HAPPY",
            emotion_boost_factor=1.3,
        )
        # 无情感上下文时分数不变
        assert results_no_boost[0]["score"] < results_with_boost[0]["score"]

    def test_score_capped_at_1(self):
        """加权后分数不超过 1.0"""
        strategy = self._make_strategy()
        vector_candidates = [
            {
                "doc_id": "doc-001",
                "score": 0.95,
                "snippet": "test",
                "metadata": {"emotion_tag": "HAPPY"},
                "evidence_ref": "evd://doc-001",
                "source_ref": "memory://doc-001",
                "confidence": 0.95,
            },
        ]
        results = strategy.fuse(
            vector_candidates=vector_candidates,
            fts_candidates=[],
            emotion_context="HAPPY",
            emotion_boost_factor=1.5,
        )
        assert results[0]["score"] <= 1.0
