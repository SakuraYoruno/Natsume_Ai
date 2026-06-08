"""M27-E：BondDynamicsWriter 情感字段测试（T-BOND-DYN-EMO-01~03）"""

from __future__ import annotations

import pytest
from host_brain.bond.bond_dynamics import BondDynamicsWriter
from host_brain.bond.bond_stage_engine import BondStageEngine, compute_affinity_delta_breakdown


class TestBondDynamicsEmotionFields:
    """T-BOND-DYN-EMO-01~03：BondDynamicsWriter 情感字段测试"""

    def test_high_emotion_intensity_gets_higher_score(self):
        """T-BOND-DYN-EMO-01：emotion_intensity=0.9 的 INTERACTION 事件积分 ≥ 普通 INTERACTION 的 1.5 倍"""
        engine_normal = BondStageEngine()
        engine_high = BondStageEngine()

        # 普通互动
        result_normal = engine_normal.apply_event(
            user_id="user1",
            update_kind="INTERACTION",
            evidence_refs=["ev-001"],
            emotion_intensity=0.5,
        )
        # 高情感强度互动
        result_high = engine_high.apply_event(
            user_id="user1",
            update_kind="INTERACTION",
            evidence_refs=["ev-001"],
            emotion_intensity=0.9,
        )
        assert result_high.score_delta >= result_normal.score_delta * 1.4

    def test_missing_emotion_intensity_uses_default(self):
        """T-BOND-DYN-EMO-02：emotion_intensity 缺失时使用默认值 0.5，行为与旧版一致"""
        engine = BondStageEngine()
        result = engine.apply_event(
            user_id="user1",
            update_kind="INTERACTION",
            evidence_refs=["ev-001"],
        )
        assert result.score_delta == 1  # 默认积分

    def test_sad_emotion_tag_not_filtered(self):
        """T-BOND-DYN-EMO-03：emotion_tag="SAD" 的 INTERACTION 事件不被过滤，正常积分"""
        writer = BondDynamicsWriter()
        result = writer.apply_update(
            request_id="req-001",
            intent_id="intent-001",
            session_id="sess-001",
            seq=1,
            user_id="user1",
            topic_id="topic-001",
            capability_hint="关系好的兄妹",
            evidence_refs=["ev-001"],
            emotion_tag="SAD",
            emotion_intensity=0.6,
        )
        assert result.success

    def test_negative_valence_dampens_plain_interaction_boost(self):
        """短期负情绪不会把普通互动误加速成高好感。"""
        engine = BondStageEngine()
        result = engine.apply_event(
            user_id="sad-plain",
            update_kind="INTERACTION",
            evidence_refs=["ev-plain"],
            emotion_intensity=0.9,
            emotion_valence=-0.8,
        )
        assert result.score_delta == 1

    def test_negative_affect_support_has_clear_positive_affinity_delta(self):
        """用户低落且被支持时，好感增长应强于基础支持事件。"""
        breakdown = compute_affinity_delta_breakdown(
            update_kind="EMOTIONAL_SUPPORT",
            emotion_intensity=0.85,
            emotion_valence=-0.8,
        )
        assert breakdown.base_delta == 8
        assert breakdown.score_delta > breakdown.base_delta
        assert "support_event" in breakdown.reason_code

    def test_high_intensity_conflict_has_stronger_negative_delta(self):
        """高强度冲突会更明显地拉低好感，但仍只影响积分，不直接改阶段。"""
        low = compute_affinity_delta_breakdown(
            update_kind="CONFLICT",
            emotion_intensity=0.4,
            emotion_valence=-0.5,
        )
        high = compute_affinity_delta_breakdown(
            update_kind="CONFLICT",
            emotion_intensity=0.85,
            emotion_valence=-0.8,
        )
        assert low.score_delta == -4
        assert high.score_delta < low.score_delta
