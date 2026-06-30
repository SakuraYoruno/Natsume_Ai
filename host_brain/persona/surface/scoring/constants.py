"""Scoring constants for candidate scoring.

Extracted from candidate_scoring.py — covers local constants needed by
other scoring modules: question budget segments, jealousy markers, axis
markers, and affect tone markers.
"""
from __future__ import annotations


# Question budget constants
_IGNORED_REACTION_QUESTION_SEGMENTS: frozenset[str] = frozenset(
    {"哈", "嗯", "欸", "诶", "咦", "啊"}
)
_ALLOWED_RHETORICAL_QUESTION_SEGMENTS: frozenset[str] = frozenset({
    "累了", "难受", "不舒服", "又炸了", "炸了", "就这", "回来了", "想我", "想你",
})
_QUESTION_SEGMENT_SPLIT_RE = __import__("re").compile(
    r"[。.!！?？\n\r，,；;：:]"
)
_INFO_SEEKING_QUESTION_RE = __import__("re").compile(
    r"(?:什么|怎么|为什么|哪|哪里|哪儿|谁|几|多少|吗|么|是不是|有没有|能不能|可不可以|要不要)"
)


# Jealousy low-pressure markers
_JEALOUSY_LOW_PRESSURE_MARKERS: tuple[str, ...] = (
    "有一点", "一点点", "别笑", "别太得意", "别得意", "少得意",
    "就一点", "欠不欠", "少来这套", "才怪", "在意你",
)

# Axis marker constants for control-affect scoring
_RELATION_AXIS_PHYSICAL_MARKERS: tuple[str, ...] = (
    "抱一下", "抱抱", "抱", "过来", "靠我", "靠过来", "靠一下",
)
_RELATION_AXIS_ROMANCE_MARKERS: tuple[str, ...] = (
    "爱你", "想你", "想我", "心里有我", "亲爱的", "宝贝", "只属于",
)
