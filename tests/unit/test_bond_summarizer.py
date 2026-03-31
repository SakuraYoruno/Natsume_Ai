"""M27-F：derive_bond_summary 修复测试（T-BOND-SUM-01~03）"""

from __future__ import annotations

import pytest
from host_brain.bond.summarizer import derive_bond_summary
from host_brain.bond.bond_stage_engine import BondScoreState, STAGE_FRIEND


class TestDeriveBondSummaryFix:
    """T-BOND-SUM-01~03：derive_bond_summary 从 BondScoreState 读取 stage/trend"""

    def test_stage_from_bond_score_state(self):
        """T-BOND-SUM-01：derive_bond_summary() 返回的 stage 为 "FRIEND" 而非 "S_BOND_UPDATE" """
        state = BondScoreState(user_id="user1", current_stage=STAGE_FRIEND, total_score=30)
        event = {"stage": "S_BOND_UPDATE", "event_id": "evt-001"}
        result = derive_bond_summary(event, bond_score_state=state)
        assert result["stage"] == "FRIEND"
        assert result["stage"] != "S_BOND_UPDATE"

    def test_trend_from_bond_score_state(self):
        """T-BOND-SUM-02：derive_bond_summary() 返回的 trend 为语义描述"""
        state = BondScoreState(user_id="user1", recent_scores=[1, 1, 1, 1, 1])
        event = {}
        result = derive_bond_summary(event, bond_score_state=state)
        # M28-F/H：trend 已中文化
        VALID_TRENDS = ("RISING", "STABLE", "DECLINING", "上升", "稳定", "下降",
                        "快速上升", "缓慢上升", "快速下降", "缓慢下降")
        assert result["trend"] in VALID_TRENDS

    def test_empty_bond_score_state_returns_defaults(self):
        """T-BOND-SUM-03：BondScoreState 为空时，derive_bond_summary() 返回默认值，不报错"""
        result = derive_bond_summary(None, bond_score_state=None)
        assert result["stage"] == "UNKNOWN"
        assert result["trend"] == "STABLE"

    def test_backward_compat_without_bond_score_state(self):
        """向后兼容：不传 bond_score_state 时，从 event 读取"""
        event = {"stage": "FRIEND", "trend": "RISING", "event_id": "evt-001"}
        result = derive_bond_summary(event)
        assert result["stage"] == "FRIEND"
        assert result["trend"] == "RISING"
