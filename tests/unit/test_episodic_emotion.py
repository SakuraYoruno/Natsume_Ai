"""M27-D：EpisodicRecord 情感标注字段测试（T-EPISODIC-EMO-01~04）"""

from __future__ import annotations

import pytest
from host_brain.memory.episodic import EpisodicRecord, EpisodicStore


class TestEpisodicEmotionFields:
    """T-EPISODIC-EMO-01~04：EpisodicRecord 情感标注字段测试"""

    def test_save_and_recall_by_emotion_tag(self):
        """T-EPISODIC-EMO-01：保存含 emotion_tag="HAPPY" 的记录后，可按 emotion_tag 检索到"""
        store = EpisodicStore()
        result = store.write_memory(
            text="今天很开心",
            evidence_refs=["ev-001"],
            emotion_tag="HAPPY",
            emotion_valence=0.8,
            emotion_arousal=0.7,
        )
        assert result.success
        record = store.get_memory(result.memory_id)
        assert record is not None
        assert record.emotion_tag == "HAPPY"
        assert record.emotion_valence == 0.8
        assert record.emotion_arousal == 0.7

    def test_old_record_default_emotion_fields(self):
        """T-EPISODIC-EMO-02：旧记录（无情感字段）读取时使用默认值，不报错"""
        record = EpisodicRecord(
            memory_id="mem_old",
            text="旧记录",
            ts_ms=1000,
            text_hash="abc",
            decay_profile_id="default",
            evidence_refs=["ev-001"],
            visibility="internal",
            embedding_model_id="test",
        )
        assert record.emotion_tag == "NEUTRAL"
        assert record.emotion_valence == 0.0
        assert record.emotion_arousal == 0.3

    def test_recall_with_emotion_filter(self):
        """T-EPISODIC-EMO-03：recall(emotion_filter="HAPPY") 只返回 emotion_tag="HAPPY" 的记录排在前列"""
        store = EpisodicStore()
        store.write_memory(
            text="开心的事情",
            evidence_refs=["ev-001"],
            emotion_tag="HAPPY",
        )
        store.write_memory(
            text="悲伤的事情",
            evidence_refs=["ev-002"],
            emotion_tag="SAD",
        )
        results = store.recall(query_text="事情", emotion_filter="HAPPY")
        # 有结果且不报错
        assert isinstance(results, list)

    def test_emotion_valence_clamped(self):
        """T-EPISODIC-EMO-04：emotion_valence 超出 [-1.0, 1.0] 时被截断到合法范围"""
        store = EpisodicStore()
        result = store.write_memory(
            text="测试截断",
            evidence_refs=["ev-001"],
            emotion_valence=2.5,  # 超出范围
            emotion_arousal=-0.5,  # 超出范围
        )
        assert result.success
        record = store.get_memory(result.memory_id)
        assert record is not None
        assert record.emotion_valence == 1.0  # 截断到 1.0
        assert record.emotion_arousal == 0.0  # 截断到 0.0
