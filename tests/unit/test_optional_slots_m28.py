"""M28-G锛歄PTIONAL_SLOTS 鎵╁睍娴嬭瘯锛圱-OPT-SLOTS-01~04锛?""

from __future__ import annotations

import pytest
from host_brain.core.prompt_context import (
    register_slot,
    build_dynamic_slots,
    OPTIONAL_SLOTS,
    _DYNAMIC_SLOT_REGISTRY,
)


class TestOptionalSlots:
    """T-OPT-SLOTS-01~04锛歄PTIONAL_SLOTS 澶氭Ы浣嶆灦鏋勬祴璇?""

    def setup_method(self):
        """姣忎釜娴嬭瘯鍓嶆竻绌哄姩鎬佹Ы浣嶆敞鍐岃〃"""
        _DYNAMIC_SLOT_REGISTRY.clear()

    def test_register_slot_appears_in_build_dynamic_slots(self):
        """T-OPT-SLOTS-01锛歳egister_slot 鍚庯紝build_dynamic_slots 杈撳嚭鍚妲戒綅"""
        register_slot("emotion_pulse", lambda ctx: {"valence": 0.5, "arousal": 0.3})
        result = build_dynamic_slots({})
        assert "emotion_pulse" in result
        assert result["emotion_pulse"]["valence"] == 0.5

    def test_failing_slot_builder_is_skipped(self):
        """T-OPT-SLOTS-02锛氭Ы浣?builder 鎶涘嚭寮傚父鏃讹紝璇ユЫ浣嶈璺宠繃锛屼笉褰卞搷鍏朵粬妲戒綅"""
        register_slot("bad_slot", lambda ctx: 1 / 0)  # 浼氭姏鍑?ZeroDivisionError
        register_slot("good_slot", lambda ctx: {"ok": True})
        result = build_dynamic_slots({})
        assert "bad_slot" not in result
        assert "good_slot" in result

    def test_unregistered_slot_not_in_output(self):
        """T-OPT-SLOTS-03锛氭湭娉ㄥ唽妲戒綅涓嶅嚭鐜板湪杈撳嚭涓?""
        result = build_dynamic_slots({})
        assert "emotion_pulse" not in result
        assert "bond_texture" not in result

    def test_optional_slots_contains_emotion_slots(self):
        """T-OPT-SLOTS-04锛歄PTIONAL_SLOTS 鍖呭惈鎯呮劅妲戒綅瀹氫箟"""
        assert "emotion_pulse" in OPTIONAL_SLOTS
        assert "bond_texture" in OPTIONAL_SLOTS
        assert "memory_emotion_hint" in OPTIONAL_SLOTS

    def test_none_returning_builder_not_in_output(self):
        """builder 杩斿洖 None 鏃讹紝璇ユЫ浣嶄笉鍑虹幇鍦ㄨ緭鍑轰腑"""
        register_slot("null_slot", lambda ctx: None)
        result = build_dynamic_slots({})
        assert "null_slot" not in result

