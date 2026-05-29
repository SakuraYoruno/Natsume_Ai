from __future__ import annotations

from host_brain.app.chat_agent import _airlock_sanitize_output, _extract_visible_text


def test_extract_visible_text_strips_complete_think_block() -> None:
    assert _extract_visible_text("<think>internal reasoning</think>哥哥，我在。") == "哥哥，我在。"


def test_extract_visible_text_drops_unclosed_think_block() -> None:
    assert _extract_visible_text("<think>用户问的是这个，先分析一下") == ""


def test_airlock_sanitize_output_strips_memory_discontinuity_fillers() -> None:
    assert _airlock_sanitize_output("嗯？老哥突然问这个……那我选第一本吧。") == "那我选第一本吧。"
    assert _airlock_sanitize_output("硬要说的话，与神在河岸吧。") == "与神在河岸吧。"
