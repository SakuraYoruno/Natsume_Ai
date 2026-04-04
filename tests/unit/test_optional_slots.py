"""M28-G：OPTIONAL_SLOTS 扩展测试（T-OPT-SLOTS-01~04）"""

from __future__ import annotations

import pytest
from host_brain.core.prompt_context import (
    register_slot,
    build_dynamic_slots,
    OPTIONAL_SLOTS,
    _DYNAMIC_SLOT_REGISTRY,
)


class TestOptionalSlots:
    """T-OPT-SLOTS-01~04：OPTIONAL_SLOTS 多槽位架构测试"""

    def setup_method(self):
        """每个测试前清空动态槽位注册表"""
        _DYNAMIC_SLOT_REGISTRY.clear()

    def test_register_slot_appears_in_build_dynamic_slots(self):
        """T-OPT-SLOTS-01：register_slot 后，build_dynamic_slots 输出含该槽位"""
        register_slot("emotion_pulse", lambda ctx: {"valence": 0.5, "arousal": 0.3})
        result = build_dynamic_slots({})
        assert "emotion_pulse" in result
        assert result["emotion_pulse"]["valence"] == 0.5

    def test_failing_slot_builder_is_skipped(self):
        """T-OPT-SLOTS-02：槽位 builder 抛出异常时，该槽位被跳过，不影响其他槽位"""
        register_slot("bad_slot", lambda ctx: 1 / 0)  # 会抛出 ZeroDivisionError
        register_slot("good_slot", lambda ctx: {"ok": True})
        result = build_dynamic_slots({})
        assert "bad_slot" not in result
        assert "good_slot" in result

    def test_unregistered_slot_not_in_output(self):
        """T-OPT-SLOTS-03：未注册槽位不出现在输出中"""
        result = build_dynamic_slots({})
        assert "emotion_pulse" not in result
        assert "bond_texture" not in result

    def test_optional_slots_contains_emotion_slots(self):
        """T-OPT-SLOTS-04：OPTIONAL_SLOTS 包含情感槽位定义"""
        assert "emotion_pulse" in OPTIONAL_SLOTS
        assert "bond_texture" in OPTIONAL_SLOTS
        assert "memory_emotion_hint" in OPTIONAL_SLOTS
        assert "memory_persona_evidence" in OPTIONAL_SLOTS
        assert "memory_recall_summary" in OPTIONAL_SLOTS

    def test_none_returning_builder_not_in_output(self):
        """builder 返回 None 时，该槽位不出现在输出中"""
        register_slot("null_slot", lambda ctx: None)
        result = build_dynamic_slots({})
        assert "null_slot" not in result
