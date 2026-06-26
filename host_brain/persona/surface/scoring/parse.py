"""Candidate parsing, question-budget counting, and control-violation checks.

Extracted from candidate_scoring.py.  All names preserve their original
private (`_`-prefixed) convention so the facade can re-export them
unchanged.
"""
from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.persona.surface.util import _clean_list, _clean_text
from host_brain.persona.surface.fact_handling import merged_must_not_do


# ---------------------------------------------------------------------------
# Constants (moved from candidate_scoring.py lines 1067-1068)
# ---------------------------------------------------------------------------

_IGNORED_REACTION_QUESTION_SEGMENTS: frozenset[str] = frozenset(
    {"哈", "嗯", "欸", "诶", "咦", "啊"}
)

_ALLOWED_RHETORICAL_QUESTION_SEGMENTS: frozenset[str] = frozenset(
    {"累了", "难受", "不舒服", "又炸了", "炸了", "就这", "回来了", "想我", "想你"}
)

_QUESTION_SEGMENT_SPLIT_RE: re.Pattern[str] = re.compile(
    r"[。.!！?？\n\r，,；;：:]"
)

_INFO_SEEKING_QUESTION_RE: re.Pattern[str] = re.compile(
    r"(?:什么|怎么|为什么|哪|哪里|哪儿|谁|几|多少|吗|么|是不是|有没有|能不能|可不可以|要不要)"
)


# ---------------------------------------------------------------------------
# Raw candidate parsing
# ---------------------------------------------------------------------------

_CANDIDATE_LABEL_SPLIT_RE: re.Pattern[str] = re.compile(
    r"(?:^|[\r\n\s]+)(?:candidate|response|reply|候选|回复)\s*\d*[\s:：、.-\]*",
    re.IGNORECASE,
)

_CANDIDATE_LINE_RE: re.Pattern[str] = re.compile(
    r"^\s*(?:[-*]|\d+[.)、]|候选\s*\d*[:：]|回复\s*\d*[:：])\s*(.+?)\s*$"
)


def _payload_candidates(payload: Any) -> list[str]:
    """Extract candidate strings from a JSON / literal payload."""
    candidates: list[str] = []
    if isinstance(payload, Mapping):
        raw_candidates = payload.get("candidates")
        if isinstance(raw_candidates, list):
            candidates.extend(_clean_text(item) for item in raw_candidates)
        reply = _clean_text(payload.get("reply") or payload.get("text"))
        if reply:
            candidates.append(reply)
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, (Mapping, list)):
                candidates.extend(_payload_candidates(item))
            else:
                candidates.append(_clean_text(item))
    return candidates


def _balanced_segment(
    text: str, start: int, *, opener: str = "[", closer: str = "]", depth: int = 0, in_quote: bool = False,
    quote_char: str | None = None,
) -> str:
    """Extract a balanced bracketed segment starting at *opener* in *text*."""
    depth = 0
    quote = "" if quote_char is None else quote_char
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == quote:
                quote = ""
                continue
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
        if depth == 0:
            return text[start : index + 1]
    return ""


def _parse_candidate_array_at(raw: str, start: int) -> list[str]:
    """Try JSON-decode then literal-eval a candidate array at *start*."""
    try:
        value, _ = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError:
        segment = _balanced_segment(raw, start)
        if not segment:
            return []
        try:
            value = ast.literal_eval(segment)
        except (SyntaxError, ValueError):
            return []
    return _payload_candidates(value)


def _extract_embedded_candidate_arrays(raw: str) -> list[str]:
    """Find candidate arrays embedded in raw output (JSON / Python literal)."""
    candidates: list[str] = []
    for match in re.finditer(r'["\']candidates["\']\s*:', raw):
        start = raw.find("[", match.end())
        if start == -1:
            continue
        candidates.extend(_parse_candidate_array_at(raw, start))
    return candidates


def _split_labeled_candidate_text(raw: str) -> list[str]:
    """Split text by candidate-response labels into individual candidates."""
    parts = [
        part.strip(" \t\r\n,，;；")
        for part in _CANDIDATE_LABEL_SPLIT_RE.split(raw)
    ]
    labeled_parts = [part for part in parts if part]
    if len(labeled_parts) >= 2:
        return _clean_list(labeled_parts)
    line_items: list[str] = []
    for line in raw.splitlines():
        match = _CANDIDATE_LINE_RE.match(line)
        if match:
            line_items.append(match.group(1))
    return _clean_list(line_items)


def parse_surface_candidates(raw_output: Any) -> list[str]:
    """Parse raw LLM output into a list of candidate strings (max 5)."""
    raw = _clean_text(raw_output)
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        try:
            payload = ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            payload = raw
    candidates = _payload_candidates(payload)
    if not candidates:
        candidates = _extract_embedded_candidate_arrays(raw)
    if not candidates:
        candidates = _split_labeled_candidate_text(raw)
    if not candidates:
        candidates.append(raw)
    return _clean_list(candidates)[:5]


# ---------------------------------------------------------------------------
# Question-budget counting
# ---------------------------------------------------------------------------

_QUESTION_MARKER_RE: re.Pattern[str] = re.compile(r"[？?]")


def _question_budget_count(text: str, max_count: int = 2) -> int:
    """Count "real" question marks that exceed the budget.

    Ignores reaction interjections (哈, 嗯, etc.) and allows at most
    one rhetorical question with a landing phrase aftermath.
    """
    value = str(text or "")
    count = 0
    rhetorical_used = False
    for marker in _QUESTION_MARKER_RE.finditer(value):
        segment = _question_segment_before(value, marker.start())
        if segment in _IGNORED_REACTION_QUESTION_SEGMENTS:
            continue
        if (
            not rhetorical_used
            and _allowed_rhetorical_question_segment(segment)
            and _has_post_question_landing(value[marker.end() :])
        ):
            rhetorical_used = True
            continue
        count += 1
        if count >= max_count:
            break
    return count


def _question_segment_before(text: str, marker_index: int) -> str:
    """Extract the text segment immediately preceding a question mark."""
    prefix = text[:marker_index]
    segment = _QUESTION_SEGMENT_SPLIT_RE.split(prefix)[-1]
    return re.sub(r"[「」『』""\"'（）()\[\]\s~～…]+", "", segment)


def _allowed_rhetorical_question_segment(segment: str) -> bool:
    """Return True when *segment* is an allowed rhetorical question fragment."""
    value = _clean_text(segment)
    if not value:
        return False
    if len(value) > 4 and value not in _ALLOWED_RHETORICAL_QUESTION_SEGMENTS:
        return False
    if _INFO_SEEKING_QUESTION_RE.search(value):
        return False
    return value in _ALLOWED_RHETORICAL_QUESTION_SEGMENTS


def _has_post_question_landing(tail: str) -> bool:
    """A rhetorical question must have CJK content after the mark."""
    value = _clean_text(tail)
    if len(value) < 4:
        return False
    if value.startswith(("？", "?", "什么", "怎么", "为什么", "哪", "哪里", "哪儿", "谁")):
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", value))


# ---------------------------------------------------------------------------
# Control-violation detection
# ---------------------------------------------------------------------------


def _control_violations(request: Any, text: str) -> list[str]:
    """Check candidate text against must-include / must-not-do constraints."""
    violations: list[str] = []

    # must_include check  (only on control side)
    for token in request.control.must_include:
        marker = _clean_text(token)
        if marker and marker not in text:
            violations.append(f"missing_must_include:{marker}")

    # must_not_do check  (merged from control + request)
    for item in merged_must_not_do(request):
        marker = _clean_text(item)
        if marker and marker in text:
            violations.append(f"must_not_do:{marker}")

    # question-budget check
    question_count = _question_budget_count(text)
    budget = int(request.control.question_budget)
    if question_count > budget:
        violations.append(
            f"question_budget:{question_count}>{budget}"
        )

    return violations
