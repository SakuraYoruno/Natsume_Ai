"""§14.2 narrative_only fast-pass 单元测试。"""

from __future__ import annotations

import pytest

from host_brain.app.entry_routing import is_narrative_only_fast_pass


def test_fast_pass_short_pure_narrative() -> None:
    """短句且无执行/代码/翻译关键词 → True。"""
    assert is_narrative_only_fast_pass("你好") is True
    assert is_narrative_only_fast_pass("今天天气怎么样") is True
    assert is_narrative_only_fast_pass("讲个笑话") is True


def test_fast_pass_false_when_exec_keywords() -> None:
    """含执行意图关键词 → False。"""
    assert is_narrative_only_fast_pass("帮我安装一下") is False
    assert is_narrative_only_fast_pass("run this script") is False
    assert is_narrative_only_fast_pass("执行命令") is False


def test_fast_pass_false_when_code_keywords() -> None:
    """含代码关键词 → False。"""
    assert is_narrative_only_fast_pass("写一段 python 代码") is False
    assert is_narrative_only_fast_pass("debug this") is False


def test_fast_pass_false_when_translate_keywords() -> None:
    """含翻译关键词 → False。"""
    assert is_narrative_only_fast_pass("翻译成英文") is False
    assert is_narrative_only_fast_pass("translate to Chinese") is False


def test_fast_pass_false_when_too_long() -> None:
    """超过 max_len → False。"""
    long_msg = "x" * 121  # default max_len=120
    assert is_narrative_only_fast_pass(long_msg) is False
    assert is_narrative_only_fast_pass("短句", max_len=1) is False
