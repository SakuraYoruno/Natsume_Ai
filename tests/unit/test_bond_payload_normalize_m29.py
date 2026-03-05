"""M29-J：BondDynamicsWriter payload 语义标准化测试（T-BOND-PAYLOAD-STD-01~02）"""

from __future__ import annotations

import pytest
from host_brain.bond.bond_dynamics import _normalize_bond_payload


class TestBondPayloadNormalize:
    """T-BOND-PAYLOAD-STD-01~02：BondDynamicsWriter payload 语义标准化测试"""

    def test_standard_fields_injected(self):
        """T-BOND-PAYLOAD-STD-01：标准字段被注入到 payload"""
        result = _normalize_bond_payload(
            payload={},
            update_kind="INTERACTION_DEEP",
            emotion_tag="HAPPY",
            emotion_intensity=0.8,
        )
        assert result["update_kind"] == "INTERACTION_DEEP"
        assert result["emotion_tag"] == "HAPPY"
        assert result["emotion_intensity"] == 0.8
        assert "semantic_label" in result

    def test_semantic_label_deep_interaction(self):
        """T-BOND-PAYLOAD-STD-02：INTERACTION_DEEP 的语义标签为深度互动"""
        result = _normalize_bond_payload(
            payload={},
            update_kind="INTERACTION_DEEP",
            emotion_tag="NEUTRAL",
            emotion_intensity=0.5,
        )
        assert result["semantic_label"] == "深度互动"

    def test_existing_fields_not_overwritten(self):
        """已有字段不被覆盖"""
        result = _normalize_bond_payload(
            payload={"update_kind": "CUSTOM", "semantic_label": "自定义"},
            update_kind="INTERACTION_MINIMAL",
            emotion_tag="NEUTRAL",
            emotion_intensity=0.3,
        )
        assert result["update_kind"] == "CUSTOM"
        assert result["semantic_label"] == "自定义"

    def test_intensity_clamped_to_0_1(self):
        """情感强度被截断到 [0, 1]"""
        result = _normalize_bond_payload(
            payload={},
            update_kind="INTERACTION",
            emotion_tag="NEUTRAL",
            emotion_intensity=1.5,
        )
        assert result["emotion_intensity"] <= 1.0

    def test_conflict_semantic_label(self):
        """CONFLICT 事件的语义标签为冲突事件"""
        result = _normalize_bond_payload(
            payload={},
            update_kind="CONFLICT",
            emotion_tag="ANGRY",
            emotion_intensity=0.9,
        )
        assert result["semantic_label"] == "冲突事件"
