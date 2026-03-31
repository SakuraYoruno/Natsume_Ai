"""M28-J：LongTermDistiller 情感关系蒸馏测试（T-DISTILL-EMO-01~02）"""

from __future__ import annotations

import pytest
from host_brain.memory.long_term_distiller import (
    DEFAULT_DISTILL_TOPICS,
    EMOTION_RELATIONSHIP_PROMPT_TEMPLATE,
    LongTermMemoryDistiller,
)


class TestDistillerEmotion:
    """T-DISTILL-EMO-01~02：LongTermDistiller 情感关系蒸馏测试"""

    def test_emotion_relationship_topic_in_default_topics(self):
        """T-DISTILL-EMO-01：DEFAULT_DISTILL_TOPICS 包含 "情感关系史" 主题"""
        assert "情感关系史" in DEFAULT_DISTILL_TOPICS

    def test_emotion_relationship_prompt_template_exists(self):
        """T-DISTILL-EMO-02：EMOTION_RELATIONSHIP_PROMPT_TEMPLATE 为非空字符串"""
        assert EMOTION_RELATIONSHIP_PROMPT_TEMPLATE
        assert isinstance(EMOTION_RELATIONSHIP_PROMPT_TEMPLATE, str)
        assert len(EMOTION_RELATIONSHIP_PROMPT_TEMPLATE.strip()) > 0

    def test_build_proposal_body_emotion_topic_has_summary_field(self):
        """情感关系史主题的提案正文包含 emotional_relationship_summary 字段"""
        from unittest.mock import MagicMock
        distiller = LongTermMemoryDistiller(
            distillation_engine=MagicMock(),
            canon_store=MagicMock(),
        )
        body = distiller._build_proposal_body(
            topic="情感关系史",
            summary="用户与AI从陌生人发展为朋友，经历了多次情感支持事件。",
            semantic_id="sem-001",
            source_count=5,
        )
        assert "emotional_relationship_summary" in body
        assert "情感关系史" in body

    def test_build_proposal_body_other_topic_no_emotion_field(self):
        """非情感关系史主题的提案正文不含 emotional_relationship_summary 字段"""
        from unittest.mock import MagicMock
        distiller = LongTermMemoryDistiller(
            distillation_engine=MagicMock(),
            canon_store=MagicMock(),
        )
        body = distiller._build_proposal_body(
            topic="用户日常习惯与偏好",
            summary="用户喜欢早起，偏好简洁的回答。",
            semantic_id="sem-002",
            source_count=3,
        )
        assert "emotional_relationship_summary" not in body
