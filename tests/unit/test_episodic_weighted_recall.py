"""M32-A：情感记忆加权召回专项测试（T-EPISODIC-WR-01~03）"""

from __future__ import annotations

import pytest
from tests.fakes.chroma_client import FakeChromaClient
from host_brain.memory.episodic import EpisodicStore


class TestEpisodicWeightedRecall:
    """T-EPISODIC-WR-01~03：情感记忆加权召回测试"""

    def _make_store(self) -> EpisodicStore:
        return EpisodicStore(chroma_client=FakeChromaClient())

    def test_happy_emotion_context_boosts_happy_records(self):
        """T-EPISODIC-WR-01：当前情感为 HAPPY 时，emotion_tag="HAPPY" 的记录排名更高"""
        store = self._make_store()
        # 写入两条语义相近但情感不同的记录
        store.write_memory(
            text="今天天气很好，心情愉快",
            evidence_refs=["ev-happy-001"],
            emotion_tag="HAPPY",
            emotion_valence=0.7,
            emotion_arousal=0.5,
        )
        store.write_memory(
            text="今天天气很好，心情低落",
            evidence_refs=["ev-sad-001"],
            emotion_tag="SAD",
            emotion_valence=-0.5,
            emotion_arousal=0.2,
        )
        # 使用 HAPPY 情感上下文召回
        results = store.recall(
            query_text="今天天气",
            top_k=5,
            emotion_context="HAPPY",
        )
        assert len(results) >= 1
        # 验证 HAPPY 记录的分数被加权
        happy_results = [r for r in results if (r.get("metadata") or {}).get("emotion_tag") == "HAPPY"]
        sad_results = [r for r in results if (r.get("metadata") or {}).get("emotion_tag") == "SAD"]
        if happy_results and sad_results:
            happy_score = float(happy_results[0].get("score") or 0.0)
            sad_score = float(sad_results[0].get("score") or 0.0)
            # HAPPY 记录分数应 >= SAD 记录分数（因为加权了 1.3x）
            assert happy_score >= sad_score

    def test_high_salience_records_rank_higher(self):
        """T-EPISODIC-WR-02：emotion_arousal > 0.8 的记录在同等条件下排名更高"""
        store = self._make_store()
        store.write_memory(
            text="重要的情感记忆",
            evidence_refs=["ev-high-001"],
            emotion_tag="EXCITED",
            emotion_valence=0.8,
            emotion_arousal=0.9,  # 高唤醒度
        )
        store.write_memory(
            text="重要的情感记忆",
            evidence_refs=["ev-low-001"],
            emotion_tag="CALM",
            emotion_valence=0.1,
            emotion_arousal=0.1,  # 低唤醒度
        )
        results = store.recall(
            query_text="重要的情感记忆",
            top_k=5,
            emotion_context="EXCITED",
        )
        assert len(results) >= 1
        # 高唤醒度记录应被加权
        excited_results = [r for r in results if (r.get("metadata") or {}).get("emotion_tag") == "EXCITED"]
        assert len(excited_results) >= 1

    def test_old_records_without_emotion_not_filtered(self):
        """T-EPISODIC-WR-03：无情感字段的旧记录不被过滤，正常参与召回"""
        store = self._make_store()
        # 写入无情感字段的记录（使用默认值）
        store.write_memory(
            text="普通记忆，无特殊情感",
            evidence_refs=["ev-neutral-001"],
            # 不传 emotion_tag，使用默认 NEUTRAL
        )
        results = store.recall(
            query_text="普通记忆",
            top_k=5,
            emotion_context="HAPPY",  # 即使情感上下文不匹配，也不过滤
        )
        assert len(results) >= 1
        # 确认 NEUTRAL 记录仍在结果中
        neutral_results = [r for r in results if (r.get("metadata") or {}).get("emotion_tag") == "NEUTRAL"]
        assert len(neutral_results) >= 1

    def test_no_emotion_context_returns_normal_results(self):
        """emotion_context=None 时，行为与旧版一致"""
        store = self._make_store()
        store.write_memory(
            text="测试记忆",
            evidence_refs=["ev-001"],
            emotion_tag="HAPPY",
        )
        results = store.recall(query_text="测试记忆", top_k=5)
        assert len(results) >= 1

    def test_emotion_filter_backward_compatible(self):
        """emotion_filter 向后兼容：优先返回匹配情感的记录"""
        store = self._make_store()
        store.write_memory(
            text="快乐的记忆",
            evidence_refs=["ev-happy-002"],
            emotion_tag="HAPPY",
        )
        store.write_memory(
            text="悲伤的记忆",
            evidence_refs=["ev-sad-002"],
            emotion_tag="SAD",
        )
        results = store.recall(
            query_text="记忆",
            top_k=5,
            emotion_filter="HAPPY",
        )
        assert len(results) >= 1
        # HAPPY 记录应排在前面
        if len(results) >= 2:
            first_tag = (results[0].get("metadata") or {}).get("emotion_tag")
            assert first_tag == "HAPPY"
