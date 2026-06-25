"""Fact handling for isolated surface generation.

Extracted from isolated_surface_harness.py — merges, strips, and
classifies facts/memory alignment terms from an IsolatedSurfaceInput.
"""
from __future__ import annotations

from host_brain.persona.surface.data_models import IsolatedSurfaceInput
from host_brain.persona.surface.scene_constants import (
    _ASCII_TERM_RE,
    _CJK_SEQUENCE_RE,
    _FACT_SPLIT_RE,
    _MEMORY_ACTION_OBJECT_RE,
    _MEMORY_ALIGNMENT_FACT_PREFIXES,
    _MEMORY_ALIGNMENT_HINTS,
    _MEMORY_ALIGNMENT_REQUIRED_HINTS,
    _MEMORY_ALIGNMENT_TASKS,
    _MEMORY_FACT_PREFIXES,
    _MEMORY_TERM_STOP_CHARS,
    _MEMORY_TERM_STOPS,
    _RELATIONSHIP_STYLE_PROFILES,
)
from host_brain.persona.surface.util import _clean_list, _clean_text


def merged_facts(request: IsolatedSurfaceInput) -> list[str]:
    return _clean_list([*request.control.facts, *request.facts])


def merged_must_not_do(request: IsolatedSurfaceInput) -> list[str]:
    return _clean_list([*request.control.must_not_do, *request.must_not_do])


def _strip_memory_fact_prefix(fact: str) -> str:
    text = _clean_text(fact)
    for prefix in _MEMORY_FACT_PREFIXES:
        if text.startswith(prefix):
            return _clean_text(text.removeprefix(prefix))
    return text


def surface_prompt_facts(request: IsolatedSurfaceInput) -> list[str]:
    return _clean_list(
        _strip_memory_fact_prefix(fact) for fact in merged_facts(request)
    )


def memory_carryover_facts(request: IsolatedSurfaceInput) -> list[str]:
    rows: list[str] = []
    for fact in merged_facts(request):
        text = _clean_text(fact)
        stripped = _strip_memory_fact_prefix(text)
        if stripped and stripped != text:
            rows.append(stripped)
    return _clean_list(rows)


def memory_alignment_facts(request: IsolatedSurfaceInput) -> list[str]:
    rows: list[str] = []
    for fact in merged_facts(request):
        text = _clean_text(fact)
        if not any(
            text.startswith(prefix) for prefix in _MEMORY_ALIGNMENT_FACT_PREFIXES
        ):
            continue
        stripped = _strip_memory_fact_prefix(text)
        if stripped and stripped != text:
            rows.append(stripped)
    return _clean_list(rows)


def _memory_fact_requires_lexical_alignment(fact: str) -> bool:
    text = _clean_text(fact)
    normalized = text.lower()
    if text.startswith(("关系线索：", "关系线索:")):
        return False
    if normalized in _RELATIONSHIP_STYLE_PROFILES:
        return False
    if "关系线索" in text and not any(
        token in text for token in ("用户说", "用户提到", "记得", "具体")
    ):
        return False
    return True


def memory_alignment_terms(request: IsolatedSurfaceInput) -> tuple[str, ...]:
    terms: list[str] = []
    for fact in memory_alignment_facts(request):
        fact = _clean_text(fact)
        if not _memory_fact_requires_lexical_alignment(fact):
            continue
        for raw in _ASCII_TERM_RE.findall(fact):
            term = raw.strip().lower()
            if len(term) >= 2 and term not in _MEMORY_TERM_STOPS:
                terms.append(term)
        for part in _FACT_SPLIT_RE.split(fact):
            text = _clean_text(part)
            if not text:
                continue
            if len(text) <= 6 and text not in _MEMORY_TERM_STOPS:
                terms.append(text)
            for sequence in _CJK_SEQUENCE_RE.findall(text):
                if len(sequence) <= 6 and sequence not in _MEMORY_TERM_STOPS:
                    terms.append(sequence)
                for match in _MEMORY_ACTION_OBJECT_RE.finditer(sequence):
                    obj = _clean_text(match.group(1))
                    if (
                        obj
                        and obj not in _MEMORY_TERM_STOPS
                        and not all(
                            char in _MEMORY_TERM_STOP_CHARS for char in obj
                        )
                    ):
                        terms.append(obj)
                for index in range(max(0, len(sequence) - 1)):
                    chunk = sequence[index : index + 2]
                    if chunk not in _MEMORY_TERM_STOPS and not any(
                        char in _MEMORY_TERM_STOP_CHARS for char in chunk
                    ):
                        terms.append(chunk)
                for index in range(max(0, len(sequence) - 2)):
                    chunk = sequence[index : index + 3]
                    if chunk not in _MEMORY_TERM_STOPS and not any(
                        char in _MEMORY_TERM_STOP_CHARS for char in chunk
                    ):
                        terms.append(chunk)
    return tuple(_clean_list(terms)[:24])


def _memory_alignment_relevant(request: IsolatedSurfaceInput) -> bool:
    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    response_move = _clean_text(request.control.response_move).lower()
    user_message = _clean_text(request.user_message).lower()
    if (
        task_mode in _MEMORY_ALIGNMENT_TASKS
        or response_move in _MEMORY_ALIGNMENT_TASKS
    ):
        return True
    if (
        "memory" in task_mode
        or "recall" in task_mode
        or "memory" in response_move
        or "recall" in response_move
    ):
        return True
    return any(hint in user_message for hint in _MEMORY_ALIGNMENT_HINTS)


def _memory_alignment_required(request: IsolatedSurfaceInput) -> bool:
    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    response_move = _clean_text(request.control.response_move).lower()
    user_message = _clean_text(request.user_message).lower()
    if task_mode in {"recall", "recall_memory", "memory_followup"}:
        return True
    if (
        "memory" in task_mode
        or "recall" in task_mode
        or "memory" in response_move
        or "recall" in response_move
    ):
        return True
    return any(hint in user_message for hint in _MEMORY_ALIGNMENT_REQUIRED_HINTS)


def _relationship_memory_boundary_relevant(request: IsolatedSurfaceInput) -> bool:
    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    response_move = _clean_text(request.control.response_move).lower()
    user_message = _clean_text(request.user_message)
    return bool(
        task_mode in {"relationship_boundary", "boundary_repair", "boundary_hurt"}
        or response_move in {"relationship_boundary", "respect_boundary"}
        or any(
            token in user_message
            for token in (
                "不记得",
                "忘了我",
                "是不是不记得",
                "我们很熟吗",
                "装很亲近",
            )
        )
    )


def _short_continue_scene_relevant(request: IsolatedSurfaceInput) -> bool:
    task_mode = _clean_text(
        request.control.task_mode or request.control.response_move
    ).lower()
    response_move = _clean_text(request.control.response_move).lower()
    user_message = _clean_text(request.user_message)
    return bool(
        user_message in {"继续", "接着", "嗯", "行", "好"}
        and (
            task_mode in {"daily_chat", "short_ack", "followup"}
            or response_move
            in {
                "continue_current_task",
                "continue_existing_topic",
                "continue_previous_turn",
            }
        )
    )


def _continuity_boundary_relevant(request: IsolatedSurfaceInput) -> bool:
    from host_brain.persona.surface.scene_constants import _CONTINUITY_BOUNDARY_HINTS

    surface = "\n".join(
        _clean_list(
            [
                request.user_message,
                request.control.task_mode,
                request.control.response_move,
                *request.control.facts,
                *request.control.must_not_do,
                *request.control.style_anchor,
            ]
        )
    )
    return any(hint in surface for hint in _CONTINUITY_BOUNDARY_HINTS)
