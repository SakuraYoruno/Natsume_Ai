from __future__ import annotations

from host_brain.app.chat_agent import _extract_visible_text


def test_extract_visible_text_strips_complete_think_block() -> None:
    assert _extract_visible_text("<think>internal reasoning</think>哥哥，我在。") == "哥哥，我在。"


def test_extract_visible_text_drops_unclosed_think_block() -> None:
    assert _extract_visible_text("<think>用户问的是这个，先分析一下") == ""
