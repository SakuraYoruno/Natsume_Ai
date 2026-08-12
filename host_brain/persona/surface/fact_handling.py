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
    # 控制包事实优先于调用方附加事实，随后稳定去重；两者都已通过上游压缩，
    # 此处只组合而不提升任意原始检索/历史内容。
    return _clean_list([*request.control.facts, *request.facts])


def merged_must_not_do(request: IsolatedSurfaceInput) -> list[str]:
    # 禁令来自控制平面与本轮附加边界，二者同等保留；没有“较软禁令”可被风格
    # 规则覆盖，避免表层优化削弱安全或关系限制。
    return _clean_list([*request.control.must_not_do, *request.must_not_do])


def _strip_memory_fact_prefix(fact: str) -> str:
    text = _clean_text(fact)
    for prefix in _MEMORY_FACT_PREFIXES:
        if text.startswith(prefix):
            return _clean_text(text.removeprefix(prefix))
    return text


def surface_prompt_facts(request: IsolatedSurfaceInput) -> list[str]:
    # 记忆前缀只服务于内部分类，不应出现在模型可见事实文本；去除前缀不会改变
    # 事实内容，也不会让非记忆事实伪装成已召回记忆。
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
    # 仅从需词面校准的记忆事实抽取短锚点，用于检查候选是否承接召回对象；术语
    # 有严格停用词与数量上限，避免将关系风格标签或整段记忆泄露为提示指令。
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
    # “相关”只表示可用召回辅助；“required”才让缺失锚点成为候选拒绝理由。两者
    # 分开避免普通寒暄因偶然含回忆词而被过度硬门拦截。
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
    # 连续性边界从已压缩的用户文本与控制字段判断，不读取完整历史；命中时下游
    # 候选守卫会要求自然承接，而不是逐字复述旧对话。
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
