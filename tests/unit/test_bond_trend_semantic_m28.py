"""M28-F：BondScoreState.trend 语义化测试（T-BOND-TREND-01~03）"""

from __future__ import annotations

import pytest
from host_brain.bond.bond_stage_engine import BondScoreState


class TestBondTrendSemantic:
    """T-BOND-TREND-01~03：BondScoreState.trend 中文语义化测试"""

    def test_rising_scores_return_chinese_rising(self):
        """T-BOND-TREND-01：积分连续上升时，trend() 返回 "上升" 或 "快速上升" """
        state = BondScoreState(user_id="user1", recent_scores=[1, 1, 1, 1, 1])
        result = state.trend()
        assert result in ("上升", "快速上升")

    def test_stable_scores_return_chinese_stable(self):
        """T-BOND-TREND-02：积分平稳时，trend() 返回 "稳定" """
        # 使用均衡的正负分数，确保不触发任何趋势
        state = BondScoreState(user_id="user1", recent_scores=[1, -1, 1, -1])
        result = state.trend()
        assert result == "稳定"

    def test_trend_returns_chinese_string(self):
        """T-BOND-TREND-03：trend() 返回值为中文字符串，不含英文枚举"""
        for scores in [[], [1], [1, 1, 1], [-1, -1, -1], [1, -1, 1, -1]]:
            state = BondScoreState(user_id="user1", recent_scores=scores)
            result = state.trend()
            assert result not in ("RISING", "STABLE", "DECLINING")
            # 验证包含中文字符
            assert any('\u4e00' <= c <= '\u9fff' for c in result)

    def test_declining_scores_return_chinese_declining(self):
        """积分连续下降时，trend() 返回 "下降" 或 "快速下降" """
        state = BondScoreState(user_id="user1", recent_scores=[-1, -1, -1, -1, -1])
        result = state.trend()
        assert result in ("下降", "快速下降")

    def test_trend_intensity_returns_strength(self):
        """trend_intensity() 返回强度描述"""
        state = BondScoreState(user_id="user1", recent_scores=[5, 5, 5, 5, 5])
        intensity = state.trend_intensity()
        assert intensity in ("强", "中", "弱")
