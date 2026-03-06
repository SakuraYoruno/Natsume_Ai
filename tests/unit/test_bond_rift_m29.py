"""M32-C：Bond 裂痕修复专项测试（T-BOND-RIFT-01~04）"""

from __future__ import annotations

import pytest
from host_brain.bond.bond_stage_engine import BondRiftState, BondScoreState


class TestBondRift:
    """T-BOND-RIFT-01~04：Bond 裂痕修复机制测试"""

    def test_rift_is_active_when_rift_level_positive(self):
        """T-BOND-RIFT-01：rift_level > 0 且 repair_progress < 1.0 时，is_active() 返回 True"""
        rift = BondRiftState(rift_level=0.5, repair_progress=0.3, rift_cause="争吵")
        assert rift.is_active() is True

    def test_rift_not_active_when_fully_repaired(self):
        """T-BOND-RIFT-02：repair_progress >= 1.0 时，is_active() 返回 False"""
        rift = BondRiftState(rift_level=0.5, repair_progress=1.0, rift_cause="争吵")
        assert rift.is_active() is False

    def test_rift_not_active_when_no_rift(self):
        """rift_level = 0 时，is_active() 返回 False"""
        rift = BondRiftState(rift_level=0.0, repair_progress=0.0)
        assert rift.is_active() is False

    def test_repair_hint_returns_chinese_string_when_active(self):
        """T-BOND-RIFT-03：repair_hint() 在裂痕活跃时返回非空中文字符串"""
        rift = BondRiftState(rift_level=0.5, repair_progress=0.2, rift_cause="误解")
        hint = rift.repair_hint()
        assert isinstance(hint, str)
        assert len(hint) > 0
        # 应包含中文字符
        assert any('\u4e00' <= c <= '\u9fff' for c in hint)

    def test_repair_hint_empty_when_not_active(self):
        """repair_hint() 在裂痕不活跃时返回空字符串"""
        rift = BondRiftState(rift_level=0.0, repair_progress=0.0)
        hint = rift.repair_hint()
        assert hint == ""

    def test_to_summary_contains_repair_hint_when_rift_active(self):
        """T-BOND-RIFT-04：BondScoreState.to_summary() 在裂痕活跃时含 repair_hint 字段"""
        state = BondScoreState(
            user_id="user_rift_test",
            rift=BondRiftState(rift_level=0.6, repair_progress=0.1, rift_cause="冲突"),
        )
        summary = state.to_summary()
        assert "repair_hint" in summary
        assert isinstance(summary["repair_hint"], str)
        assert len(summary["repair_hint"]) > 0

    def test_to_summary_no_repair_hint_when_no_rift(self):
        """BondScoreState.to_summary() 在无裂痕时不含 repair_hint 字段"""
        state = BondScoreState(user_id="user_no_rift")
        summary = state.to_summary()
        assert "repair_hint" not in summary

    def test_repair_hint_stages(self):
        """repair_hint() 根据修复进度返回不同阶段的提示"""
        # 早期修复（< 0.3）
        rift_early = BondRiftState(rift_level=0.8, repair_progress=0.1)
        hint_early = rift_early.repair_hint()
        assert "道歉" in hint_early or "关心" in hint_early or "裂痕" in hint_early

        # 中期修复（0.3~0.7）
        rift_mid = BondRiftState(rift_level=0.5, repair_progress=0.5)
        hint_mid = rift_mid.repair_hint()
        assert len(hint_mid) > 0

        # 后期修复（> 0.7）
        rift_late = BondRiftState(rift_level=0.3, repair_progress=0.8)
        hint_late = rift_late.repair_hint()
        assert len(hint_late) > 0
