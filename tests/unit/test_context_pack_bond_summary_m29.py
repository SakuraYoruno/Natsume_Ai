"""M29-L：validate_context_pack bond_summary 语义校验测试（T-CP-BOND-STAGE-01~02）"""

from __future__ import annotations

import pytest
from host_brain.core.context_pack import (
    ContextPackValidationResult,
    _validate_bond_summary_slot,
)


def _run_validation(bond_summary: dict) -> ContextPackValidationResult:
    """直接调用 bond_summary 槽位校验函数"""
    result = ContextPackValidationResult()
    _validate_bond_summary_slot(bond_summary, result)
    return result


class TestBondSummarySemanticValidation:
    """T-CP-BOND-STAGE-01~02：bond_summary stage/trend 语义校验测试"""

    def test_valid_chinese_stage_passes(self):
        """T-CP-BOND-STAGE-01：合法中文 stage 通过校验"""
        result = _run_validation({"stage": "朋友", "trend": "上升"})
        assert result.valid, f"期望通过，但失败：{result.violations}"

    def test_valid_english_stage_passes(self):
        """合法英文 stage 通过校验"""
        result = _run_validation({"stage": "FRIEND", "trend": "RISING"})
        assert result.valid, f"期望通过，但失败：{result.violations}"

    def test_invalid_stage_rejected(self):
        """T-CP-BOND-STAGE-02：非法 stage 被拒绝"""
        result = _run_validation({"stage": "INVALID_STAGE", "trend": "上升"})
        assert not result.valid
        assert any("stage" in str(v.detail).lower() for v in result.violations)

    def test_invalid_trend_rejected(self):
        """非法 trend 被拒绝"""
        result = _run_validation({"stage": "朋友", "trend": "INVALID_TREND"})
        assert not result.valid
        assert any("trend" in str(v.detail).lower() for v in result.violations)

    def test_all_valid_chinese_stages(self):
        """所有合法中文 stage 值均通过校验"""
        valid_stages = ["陌生人", "相识", "朋友", "好友", "挚友", "灵魂伴侣"]
        for stage in valid_stages:
            result = _run_validation({"stage": stage, "trend": "稳定"})
            assert result.valid, f"stage='{stage}' 应通过校验，但失败：{result.violations}"

    def test_all_valid_chinese_trends(self):
        """所有合法中文 trend 值均通过校验"""
        valid_trends = ["上升", "稳定", "下降"]
        for trend in valid_trends:
            result = _run_validation({"stage": "朋友", "trend": trend})
            assert result.valid, f"trend='{trend}' 应通过校验，但失败：{result.violations}"

    def test_no_stage_no_error(self):
        """没有 stage/trend 字段时不报错"""
        result = _run_validation({"capability_hint": "CHAT"})
        assert result.valid
