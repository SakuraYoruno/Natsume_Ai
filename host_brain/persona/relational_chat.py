"""Pure relational-chat classification shared by routing and persona layers."""

from __future__ import annotations

import re
from typing import Any

from host_brain.core.runtime_helpers import _clean_text as _normalize_text

_CONCRETE_ACTION_HINTS = (
    "代码", "脚本", "函数", "修复", "debug", "报错", "翻译", "英文", "日语", "韩语",
    "搜索", "检索", "网页", "网站", "浏览器", "图片", "看图", "截图", "打开", "启动",
    "运行", "端口", "日志", "进程", "文件", "应用", "软件", "程序", "整理文件", "写代码",
    "写一段", "实现一下",
)

_IDENTITY_PATTERNS = (
    re.compile(r"^\s*你是[谁吗呀啊？?！!。…~～]*\s*$"),
    re.compile(r"^\s*你谁[呀啊吗？?！!。…~～]*\s*$"),
    re.compile(r"^\s*你是谁[呀啊吗？?！!。…~～]*\s*$"),
)

_RELATIONAL_MARKERS: tuple[tuple[str, str], ...] = (
    ("identity", "你是谁"), ("identity", "你是"), ("identity", "你谁"),
    ("relationship_boundary", "我们不熟"), ("relationship_boundary", "不熟吧"),
    ("relationship_boundary", "很熟吗"), ("relationship_boundary", "熟吗"),
    ("motivation", "为什么要帮我"), ("motivation", "为什么帮我"),
    ("motivation", "为什么要管我"), ("motivation", "为什么管我"),
    ("help_commitment", "还会帮我吗"), ("help_commitment", "还会理我吗"),
    ("help_commitment", "还会管我吗"), ("help_commitment", "会不会帮我"),
    ("help_commitment", "会不会理我"), ("help_commitment", "会不会管我"),
    ("rejection", "我不喜欢这样"), ("rejection", "别这么说"),
    ("rejection", "不是这个意思"), ("rejection", "这句不对"),
)


def _normalize_match_text(value: Any) -> str:
    return re.sub(r"[\s，。！？,.!?~～…]+", "", _normalize_text(value).lower())


def detect_relational_chat_topic(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    compact = _normalize_match_text(normalized)
    if not compact or any(hint in normalized for hint in _CONCRETE_ACTION_HINTS):
        return ""
    if any(pattern.match(normalized) for pattern in _IDENTITY_PATTERNS):
        return "identity"
    for topic, marker in _RELATIONAL_MARKERS:
        if marker in compact:
            return topic
    return ""


def is_relational_chat_message(text: str) -> bool:
    return bool(detect_relational_chat_topic(text))


def build_relational_chat_reason_code(text: str) -> str:
    topic = detect_relational_chat_topic(text)
    return f"RELATIONAL_CHAT:{topic}" if topic else ""


__all__ = [
    "build_relational_chat_reason_code",
    "detect_relational_chat_topic",
    "is_relational_chat_message",
]
