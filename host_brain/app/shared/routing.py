"""Contextual continuity / recent-turn / fastpath functions for agent_runtime_shared.

Extracted from agent_runtime_shared.py — covers:
- Conversation continuity and direct-answer fastpaths (recent turn, contextual
  memory/style, task progress, short chat routes)
- Task progress detection and fastpath
- Delegate result rendering
- Retrieval timeout/budget configuration

These are the "shortcut" answer builders that skip the full LLM pipeline.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from host_brain.core.runtime_helpers import _airlock_sanitize_output


# ---------------------------------------------------------------------------
# Regex constants for fastpath detection
# ---------------------------------------------------------------------------
_TASK_CONTEXT_KEYWORDS = (
    "进度",
    "status",
    "做了什么",
    "做到哪一步",
    "step",
    "done",
    "完成",
    "总结",
    "summary",
    "执行结果",
    "结果怎么样",
    "怎么样了",
    "现在什么情况",
    "做得怎样",
    "进行到哪",
    "到什么进度",
    "完成度",
    "step",
    "step",
    "step",
    "step",
    "progress",
)

_EMOTION_INFER_KEYWORDS = [
    ("开心", "joy"),
    ("高兴", "joy"),
    ("伤心", "sadness"),
    ("难过", "sadness"),
    ("生气", "anger"),
    ("愤怒", "anger"),
    ("担心", "fear"),
    ("害怕", "fear"),
    ("惊喜", "surprise"),
    ("惊讶", "surprise"),
    ("平静", "calm"),
    ("困惑", "confused"),
    ("无聊", "bored"),
    ("兴奋", "excited"),
]


def _looks_like_active_task_context(turns: list[dict[str, str]]) -> bool:
    """Check if the recent conversation context is within an active task.

    Heuristic: scan the most recent user+assistant turns for task keywords.
    """
    if not turns:
        return False
    recent_text = " ".join(
        t.get("text", "") for t in turns[-4:] if t.get("role") != "system"
    )
    return any(keyword.lower() in recent_text.lower() for keyword in _TASK_CONTEXT_KEYWORDS)


def _extract_delegate_visible_text(text: str) -> str:
    """Strip markdown fences and delegate-visible formatting from text."""
    cleaned = re.sub(r"```[\s\S]*?```", "", text)
    cleaned = re.sub(r"^\s*[-*>]\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\r\n", "\n", cleaned)
    return cleaned.strip()


def _render_delegated_result_naturally(
    *,
    agent_id: str,
    result_text: str,
    render_target_lang: str,
    bond_summary: Mapping[str, Any] | None = None,
    recent_turns: Sequence[Mapping[str, Any]] | None = None,
    tone: str = "",
) -> str | None:
    """Build a natural-language wrapper around a delegated result.

    Returns None if the result should not be wrapped (e.g. empty or purely
    markdown that would lose meaning).
    """
    visible = _extract_delegate_visible_text(result_text)
    if not visible:
        return None

    # Build a brief prefix based on agent role
    role_prefix = ""
    if agent_id:
        role_prefix = f"[{agent_id}]\n"

    result = f"{role_prefix}{visible}"
    if bond_summary and tone:
        # Trivial tone hint injection — expand as needed
        result = result.rstrip("\n")
    return result


def _resolve_retrieval_timeout_seconds() -> float:
    """Return the configured timeout for persona-input retrieval (seconds)."""
    import os

    return float(os.environ.get("PERSONA_RETRIEVAL_TIMEOUT_SECONDS", "8.0"))


def _resolve_selection_followup_retrieval_timeout_seconds() -> float:
    """Return the timeout for selection-followup retrieval (seconds)."""
    import os

    return float(os.environ.get("PERSONA_SELECTION_FOLLOWUP_TIMEOUT_SECONDS", "4.0"))


def _needs_selection_followup_retrieval_budget(message: str) -> bool:
    """Heuristic: does the message indicate we need follow-up selection retrieval?

    Returns True when the user asks for more details, comparison, or
    clarification on a specific item.
    """
    keywords = (
        "详细",
        "更多",
        "比较",
        "区别",
        "对比",
        "哪个更",
        "为什么",
        "原因",
        "解释",
        "clarify",
        "more detail",
        "compare",
    )
    lower_message = message.lower()
    return any(kw.lower() in lower_message for kw in keywords)
