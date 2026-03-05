"""M28-H：BondScoreState.to_summary 中文化测试（T-BOND-SUM-CN-01~02）"""

from __future__ import annotations

import pytest
from host_brain.bond.bond_stage_engine import BondScoreState, STAGE_FRIEND, STAGE_STRANGER


class TestBondToSummaryCN:
    """T-BOND-SUM-CN-01~02：BondScoreState.to_summary 中文化测试"""

    def test_to_summary_no_english_enum_values(self):
        """T-BOND-SUM-CN-01：to_summary() 返回 dict 中无英文枚举值"""
        state = BondScoreState(user_id="user1", current_stage=STAGE_FRIEND,
                               recent_scores=[1, 1, 1])
        summary = state.to_summary()
        english_enums = {"RISING", "STABLE", "DECLINING", "STRANGER", "ACQUAINTANCE",
                         "FRIEND", "CLOSE_FRIEND", "CONFIDANT", "SOULMATE"}
        for value in summary.values():
            assert str(value) not in english_enums, f"发现英文枚举值: {value}"

    def test_to_summary_stage_is_chinese(self):
        """T-BOND-SUM-CN-02：to_summary() 的 stage 字段为中文阶段名"""
        state = BondScoreState(user_id="user1", current_stage=STAGE_FRIEND)
        summary = state.to_summary()
        assert "stage" in summary
        # 验证 stage 包含中文字符
        assert any('\u4e00' <= c <= '\u9fff' for c in str(summary["stage"]))
        assert summary["stage"] == "朋友"

    def test_to_summary_stranger_stage_is_chinese(self):
        """STRANGER 阶段的 stage 字段为 "陌生人" """
        state = BondScoreState(user_id="user1", current_stage=STAGE_STRANGER)
        summary = state.to_summary()
        assert summary["stage"] == "陌生人"

    def test_to_summary_trend_is_chinese(self):
        """to_summary() 的 trend 字段为中文"""
        state = BondScoreState(user_id="user1", recent_scores=[1, 1, 1])
        summary = state.to_summary()
        assert "trend" in summary
        assert any('\u4e00' <= c <= '\u9fff' for c in str(summary["trend"]))
