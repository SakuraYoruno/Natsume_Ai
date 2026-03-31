from __future__ import annotations

import pytest

from host_brain.channels.vts_mapping_store import (
    SUB_EMOTION_HOTKEY_MAP,
    sub_emotion_to_hotkey,
)


class TestSubEmotionToHotkeyBasic:
    @pytest.mark.parametrize("sub_emotion,hotkey_id", list(SUB_EMOTION_HOTKEY_MAP.items()))
    def test_known_sub_emotions_map_to_hotkeys(self, sub_emotion: str, hotkey_id: str) -> None:
        assert sub_emotion_to_hotkey(sub_emotion) == hotkey_id


class TestSubEmotionToHotkeyUnknown:
    @pytest.mark.parametrize("sub_emotion", ["unknown", "", None, "INVALID_SUB_EMOTION_XYZ"])
    def test_unknown_sub_emotions_return_none(self, sub_emotion: str | None) -> None:
        assert sub_emotion_to_hotkey(sub_emotion) is None
