"""M29-M：build_fallback_output 情感感知降级测试（T-FALLBACK-EMOTION-01~03）"""

from __future__ import annotations

import pytest
from host_brain.core.qa import build_fallback_output


class TestFallbackOutputEmotion:
    """T-FALLBACK-EMOTION-01~03：build_fallback_output 情感感知降级测试"""

    def test_meta_fields_always_present(self):
        """meta 字段始终包含 degraded/reason_code/stage"""
        result = build_fallback_output(
            reason_code="SKILL_FAILED",
            stage="execution",
            emotion_context={"emotion_tag": "NEUTRAL"},
        )
        assert result["meta"]["degraded"] is True
        assert result["meta"]["reason_code"] == "SKILL_FAILED"
        assert result["meta"]["stage"] == "execution"
