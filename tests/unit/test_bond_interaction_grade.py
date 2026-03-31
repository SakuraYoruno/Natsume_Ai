"""M28-C：Bond 积分情感事件分类精化测试（T-BOND-GRADE-01~04）"""

from __future__ import annotations

import pytest
from host_brain.bond.bond_dynamics import BondDynamicsWriter
from host_brain.bond.bond_stage_engine import BondStageEngine, DEFAULT_SCORE_MAP


class TestBondInteractionGrade:
    """T-BOND-GRADE-01~04：Bond 互动质量分级测试"""

    def test_short_text_no_question_is_minimal(self):
        """T-BOND-GRADE-01：文本长度 < 10 字且无问号 → INTERACTION_MINIMAL"""
        result = BondDynamicsWriter.classify_interaction(text="hello", emotion_intensity=0.3)
        assert result == "INTERACTION_MINIMAL"

    def test_long_text_is_engaged(self):
        """T-BOND-GRADE-02：文本长度 > 50 字 → INTERACTION_ENGAGED"""
        # 使用 ASCII 字符确保长度计算正确
        long_text = "a" * 60
        result = BondDynamicsWriter.classify_interaction(text=long_text, emotion_intensity=0.3)
        assert result == "INTERACTION_ENGAGED"

    def test_question_mark_is_engaged(self):
        """T-BOND-GRADE-02b：含问号 → INTERACTION_ENGAGED"""
        result = BondDynamicsWriter.classify_interaction(text="how are you?", emotion_intensity=0.3)
        assert result == "INTERACTION_ENGAGED"

    def test_high_intensity_long_text_is_deep(self):
        """T-BOND-GRADE-03：emotion_intensity > 0.8 且文本长度 > 100 字 → INTERACTION_DEEP"""
        long_text = "a" * 110
        result = BondDynamicsWriter.classify_interaction(text=long_text, emotion_intensity=0.9)
        assert result == "INTERACTION_DEEP"

    def test_old_interaction_still_works(self):
        """T-BOND-GRADE-04：旧 INTERACTION 事件仍可处理，不报错（向后兼容）"""
        engine = BondStageEngine()
        result = engine.apply_event(
            user_id="user1",
            update_kind="INTERACTION",
            evidence_refs=["ev-001"],
        )
        assert result.score_delta >= 1

    def test_interaction_deep_score_higher_than_minimal(self):
        """INTERACTION_DEEP 积分 > INTERACTION_MINIMAL 积分"""
        assert DEFAULT_SCORE_MAP["INTERACTION_DEEP"] > DEFAULT_SCORE_MAP["INTERACTION_MINIMAL"]
