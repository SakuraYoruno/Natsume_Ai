"""M29-L锛歷alidate_prompt_context bond_summary 璇箟鏍￠獙娴嬭瘯锛圱-CP-BOND-STAGE-01~02锛?""

from __future__ import annotations

import pytest
from host_brain.core.prompt_context import (
    PromptContextValidationResult,
    _validate_bond_summary_slot,
)


def _run_validation(bond_summary: dict) -> PromptContextValidationResult:
    """鐩存帴璋冪敤 bond_summary 妲戒綅鏍￠獙鍑芥暟"""
    result = PromptContextValidationResult()
    _validate_bond_summary_slot(bond_summary, result)
    return result


class TestBondSummarySemanticValidation:
    """T-CP-BOND-STAGE-01~02锛歜ond_summary stage/trend 璇箟鏍￠獙娴嬭瘯"""

    def test_valid_chinese_stage_passes(self):
        """T-CP-BOND-STAGE-01锛氬悎娉曚腑鏂?stage 閫氳繃鏍￠獙"""
        result = _run_validation({"stage": "鏈嬪弸", "trend": "涓婂崌"})
        assert result.valid, f"鏈熸湜閫氳繃锛屼絾澶辫触锛歿result.violations}"

    def test_valid_english_stage_passes(self):
        """鍚堟硶鑻辨枃 stage 閫氳繃鏍￠獙"""
        result = _run_validation({"stage": "FRIEND", "trend": "RISING"})
        assert result.valid, f"鏈熸湜閫氳繃锛屼絾澶辫触锛歿result.violations}"

    def test_invalid_stage_rejected(self):
        """T-CP-BOND-STAGE-02锛氶潪娉?stage 琚嫆缁?""
        result = _run_validation({"stage": "INVALID_STAGE", "trend": "涓婂崌"})
        assert not result.valid
        assert any("stage" in str(v.detail).lower() for v in result.violations)

    def test_invalid_trend_rejected(self):
        """闈炴硶 trend 琚嫆缁?""
        result = _run_validation({"stage": "鏈嬪弸", "trend": "INVALID_TREND"})
        assert not result.valid
        assert any("trend" in str(v.detail).lower() for v in result.violations)

    def test_all_valid_chinese_stages(self):
        """鎵€鏈夊悎娉曚腑鏂?stage 鍊煎潎閫氳繃鏍￠獙"""
        valid_stages = ["闄岀敓浜?, "鐩歌瘑", "鏈嬪弸", "濂藉弸", "鎸氬弸", "鐏甸瓊浼翠荆"]
        for stage in valid_stages:
            result = _run_validation({"stage": stage, "trend": "绋冲畾"})
            assert result.valid, f"stage='{stage}' 搴旈€氳繃鏍￠獙锛屼絾澶辫触锛歿result.violations}"

    def test_all_valid_chinese_trends(self):
        """鎵€鏈夊悎娉曚腑鏂?trend 鍊煎潎閫氳繃鏍￠獙"""
        valid_trends = ["涓婂崌", "绋冲畾", "涓嬮檷"]
        for trend in valid_trends:
            result = _run_validation({"stage": "鏈嬪弸", "trend": trend})
            assert result.valid, f"trend='{trend}' 搴旈€氳繃鏍￠獙锛屼絾澶辫触锛歿result.violations}"

    def test_no_stage_no_error(self):
        """娌℃湁 stage/trend 瀛楁鏃朵笉鎶ラ敊"""
        result = _run_validation({"capability_hint": "CHAT"})
        assert result.valid

