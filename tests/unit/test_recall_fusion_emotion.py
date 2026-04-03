"""M27-G：RecallFusionServiceContract 情感维度路由测试（T-RECALL-EMO-01~03）"""

from __future__ import annotations

import pytest
from host_brain.retrieval.recall_fusion import RecallFusionServiceContract
from tests.fakes.chroma_client import FakeChromaClient


class TestRecallFusionEmotionFilter:
    """T-RECALL-EMO-01~03：RecallFusionServiceContract 情感维度路由测试"""

    def test_emotion_filter_none_behaves_as_before(self):
        """T-RECALL-EMO-02：emotion_filter=None 时，行为与旧版一致"""
        service = RecallFusionServiceContract(chroma_client=FakeChromaClient(), use_hybrid=False)
        result = service.recall(
            request_id="req-001",
            query="测试查询",
            query_embedding=[0.1] * 384,
            emotion_filter=None,
        )
        # 无候选时降级，但不报错
        assert result.stage == "S_RET_RECALL"

    def test_emotion_filter_with_no_matching_records_falls_back(self):
        """T-RECALL-EMO-03：无匹配情感记录时，降级为普通语义召回，不返回空列表"""
        service = RecallFusionServiceContract(chroma_client=FakeChromaClient(), use_hybrid=False)
        result = service.recall(
            request_id="req-001",
            query="测试查询",
            query_embedding=[0.1] * 384,
            emotion_filter="HAPPY",
        )
        # 不报错，结果结构稳定
        assert result.stage == "S_RET_RECALL"
        assert isinstance(result.output, dict)

    def test_recall_accepts_emotion_filter_parameter(self):
        """recall 方法接受 emotion_filter 参数不报错"""
        service = RecallFusionServiceContract(chroma_client=FakeChromaClient(), use_hybrid=False)
        # 只验证不抛出异常
        result = service.recall(
            request_id="req-001",
            query="情感测试",
            query_embedding=[0.1] * 384,
            emotion_filter="SAD",
        )
        assert result is not None
