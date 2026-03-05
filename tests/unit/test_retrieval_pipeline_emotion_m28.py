"""M28-P：RetrievalPipeline 情感上下文透传测试（T-PIPELINE-EMO-01~02）"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


class TestRetrievalPipelineEmotion:
    """T-PIPELINE-EMO-01~02：RetrievalPipeline 情感上下文透传测试"""

    def _make_pipeline(self, captured_rerank_kwargs: dict):
        """创建带 mock 服务的 RetrievalPipeline"""
        from host_brain.retrieval.pipeline import RetrievalPipelineContract

        mock_embedding = MagicMock()
        mock_embedding.embed.return_value = MagicMock(
            success=True,
            output={"embedding_vector": [0.1, 0.2, 0.3]},
        )

        mock_recall = MagicMock()
        mock_recall.recall.return_value = MagicMock(
            success=True,
            output={"candidates": [{"content": "记忆1", "emotion_tag": "SAD"}]},
        )

        def mock_rerank(**kwargs):
            captured_rerank_kwargs.update(kwargs)
            return MagicMock(
                success=True,
                output={"topk": [{"content": "记忆1", "score": 0.8}]},
            )

        mock_reranker = MagicMock()
        mock_reranker.rerank.side_effect = mock_rerank

        mock_evidence_pack = MagicMock()
        mock_evidence_pack.pack.return_value = MagicMock(
            success=True,
            output={"evidence_pack": []},
        )

        return RetrievalPipelineContract(
            embedding_service=mock_embedding,
            recall_service=mock_recall,
            reranker_service=mock_reranker,
            evidence_pack_service=mock_evidence_pack,
        )

    def test_emotion_context_passed_to_reranker(self):
        """T-PIPELINE-EMO-01：emotion_context="SAD" 时，pipeline 将参数透传给 reranker"""
        captured = {}
        pipeline = self._make_pipeline(captured)
        pipeline.run(
            request_id="req-001",
            query="测试查询",
            emotion_context="SAD",
        )
        assert captured.get("emotion_context") == "SAD"

    def test_no_emotion_context_behavior_unchanged(self):
        """T-PIPELINE-EMO-02：emotion_context=None 时，行为与旧版一致"""
        captured = {}
        pipeline = self._make_pipeline(captured)
        result = pipeline.run(
            request_id="req-001",
            query="测试查询",
        )
        # 不传 emotion_context 时，reranker 收到 None 或不传
        assert captured.get("emotion_context") is None
        assert result is not None
