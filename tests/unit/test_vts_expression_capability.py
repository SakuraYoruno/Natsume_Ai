from __future__ import annotations

from edge_client.actuators.vts_driver import _expression_hotkey_match_count


def test_expression_hotkey_capability_counts_only_compatible_safe_hotkeys() -> None:
    catalog = [
        {"id": "model-happy", "name": "开心"},
        {"id": "exp_sad_01", "name": "custom"},
        {"id": "hotkey_remove_watermark", "name": "开心水印"},
        {"id": "unrelated-toggle", "name": "服装切换"},
    ]

    assert _expression_hotkey_match_count(catalog) == 2


def test_expression_hotkey_capability_does_not_invent_support() -> None:
    assert _expression_hotkey_match_count(
        [
            {"id": "costume-a", "name": "衣服"},
            {"id": "accessory-b", "name": "耳机"},
        ]
    ) == 0
