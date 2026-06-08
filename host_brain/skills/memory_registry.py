"""记忆操作 Skill 包。

声明式 Provider：新增记忆类技能时，只需补一个 `DeclarativeSkillSpec`
和对应的参数归一化函数。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec
from host_brain.skills.registry import SkillRegistry

_MEMORY_TYPES = frozenset({"episodic", "semantic", "working", "all"})
_SEARCH_SCOPES = frozenset({"recent_episode", "long_term", "line", "hybrid"})
_SEARCH_SCOPE_ALIASES = {
    "recent": "recent_episode",
    "recent_episodic": "recent_episode",
    "episode": "recent_episode",
    "episodic": "recent_episode",
    "semantic": "long_term",
    "longterm": "long_term",
    "long_term_semantic": "long_term",
    "quote": "line",
    "quotes": "line",
    "dialogue": "line",
    "lines": "line",
    "line_semantic": "line",
    "mixed": "hybrid",
    "auto": "",
}
_VALID_MEMORY_TAGS = frozenset({
    "conversation", "emotion", "event", "fact", "preference",
    "dream", "diary", "task", "reminder", "bond",
    "quote", "line", "dialogue", "style",
})
_RECENT_EPISODE_TAGS = frozenset({"conversation", "event", "task", "reminder", "dream", "diary"})
_LINE_SEMANTIC_TAGS = frozenset({"quote", "line", "dialogue", "style"})
_LONG_TERM_TAGS = frozenset({"fact", "preference", "bond", "emotion"})


def _normalize_search_scope(args: dict[str, Any], *, memory_type: str, tags: list[str]) -> str:
    raw_scope = str((args or {}).get("search_scope") or (args or {}).get("scope") or "").strip().lower()
    scope = _SEARCH_SCOPE_ALIASES.get(raw_scope, raw_scope)
    if scope in _SEARCH_SCOPES:
        return scope
    tag_set = {str(tag or "").strip().lower() for tag in tags if str(tag or "").strip()}
    if memory_type == "semantic":
        return "long_term"
    if memory_type in {"episodic", "working"}:
        return "recent_episode"
    if tag_set & _LINE_SEMANTIC_TAGS:
        return "line"
    if tag_set & _RECENT_EPISODE_TAGS:
        return "recent_episode"
    if tag_set & _LONG_TERM_TAGS:
        return "long_term"
    return "recent_episode"


def _normalize_memory_search(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    query = str((args or {}).get("query") or "").strip()
    memory_type = str((args or {}).get("memory_type") or "all").strip().lower()
    if memory_type not in _MEMORY_TYPES:
        memory_type = "all"
    try:
        top_k = max(1, min(20, int((args or {}).get("top_k") or 5)))
    except (TypeError, ValueError):
        top_k = 5
    try:
        threshold = max(0.0, min(1.0, float((args or {}).get("threshold") or 0.6)))
    except (TypeError, ValueError):
        threshold = 0.6
    tags_raw = (args or {}).get("tags")
    if isinstance(tags_raw, list):
        tags = [str(t).strip().lower() for t in tags_raw if t]
    else:
        tags = []
    session_filter_raw = (args or {}).get("session_filter")
    session_filter = bool(session_filter_raw) if session_filter_raw is not None else False
    search_scope = _normalize_search_scope(args or {}, memory_type=memory_type, tags=tags)
    date_from = str((args or {}).get("date_from") or "").strip() or None
    date_to = str((args or {}).get("date_to") or "").strip() or None

    return {
        "query": query,
        "memory_type": memory_type,
        "search_scope": search_scope,
        "top_k": top_k,
        "threshold": threshold,
        "tags": tags,
        "session_filter": session_filter,
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "date_from": date_from,
        "date_to": date_to,
        "manifest_ref": f"memory_search::{query[:60]}",
        "_context": ctx or {},
    }


def _normalize_memory_store(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    content = str((args or {}).get("content") or "").strip()
    memory_type = str((args or {}).get("memory_type") or "episodic").strip().lower()
    if memory_type not in {"episodic", "semantic"}:
        memory_type = "episodic"
    tags_raw = (args or {}).get("tags")
    if isinstance(tags_raw, list):
        tags = [str(t).strip().lower() for t in tags_raw if t]
    else:
        tags = []
    try:
        importance = max(0.0, min(1.0, float((args or {}).get("importance") or 0.5)))
    except (TypeError, ValueError):
        importance = 0.5
    source = str((args or {}).get("source") or "").strip() or None
    emotion_tag = str(
        (args or {}).get("emotion_tag")
        or (ctx or {}).get("emotion_tag")
        or "NEUTRAL"
    ).strip().upper()

    return {
        "content": content,
        "memory_type": memory_type,
        "tags": tags,
        "importance": importance,
        "source": source,
        "emotion_tag": emotion_tag,
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "manifest_ref": f"memory_store::{memory_type}::{content[:40]}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_memory_preference_extract(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    text = str(
        (args or {}).get("text")
        or (args or {}).get("content")
        or (args or {}).get("source_text")
        or (args or {}).get("user_message")
        or ""
    ).strip()
    try:
        max_candidates = max(1, min(50, int((args or {}).get("max_candidates") or 8)))
    except (TypeError, ValueError):
        max_candidates = 8
    source = str((args or {}).get("source") or "").strip() or "user_text"

    return {
        "text": text,
        "max_candidates": max_candidates,
        "source": source,
        "manifest_ref": f"memory_preference_extract::{text[:40]}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_memory_consolidate(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    text = str(
        (args or {}).get("text")
        or (args or {}).get("content")
        or (args or {}).get("source_text")
        or (args or {}).get("user_message")
        or ""
    ).strip()
    items = (args or {}).get("items")
    if not isinstance(items, list):
        items = []
    try:
        max_candidates = max(1, min(50, int((args or {}).get("max_candidates") or 10)))
    except (TypeError, ValueError):
        max_candidates = 10
    include_preferences_raw = (args or {}).get("include_preferences")
    include_preferences = bool(include_preferences_raw) if include_preferences_raw is not None else True
    source = str((args or {}).get("source") or "").strip() or "memory_consolidate"

    return {
        "text": text,
        "items": items,
        "max_candidates": max_candidates,
        "include_preferences": include_preferences,
        "source": source,
        "manifest_ref": f"memory_consolidate::{(text or str(len(items)) + '_items')[:40]}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_memory_forget(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    memory_id = str((args or {}).get("memory_id") or "").strip() or None
    tags_raw = (args or {}).get("tags")
    if isinstance(tags_raw, list):
        tags = [str(t).strip().lower() for t in tags_raw if t]
    else:
        tags = []
    memory_type = str((args or {}).get("memory_type") or "all").strip().lower()
    if memory_type not in _MEMORY_TYPES:
        memory_type = "all"
    dry_run_raw = (args or {}).get("dry_run")
    dry_run = bool(dry_run_raw) if dry_run_raw is not None else False

    return {
        "memory_id": memory_id,
        "tags": tags,
        "memory_type": memory_type,
        "dry_run": dry_run,
        "manifest_ref": f"memory_forget::{memory_id or '+'.join(tags)}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


class MemorySkillProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="memory_search",
            clearance="LOW",
            risk_level=1,
            scope="memory",
            reversible=True,
            description="按 search_scope 检索记忆：recent_episode 查最近事件；long_term 查长期语义记忆；line 查台词/风格语料；hybrid 显式混合。",
            tool="memory",
            action="search",
            normalize_args=_normalize_memory_search,
        ),
        DeclarativeSkillSpec(
            skill_name="memory_store",
            clearance="MEDIUM",
            risk_level=2,
            scope="memory",
            reversible=True,
            description="将指定内容写入记忆库（Episodic 或 Semantic），支持标签和重要性标注。",
            tool="memory",
            action="store",
            normalize_args=_normalize_memory_store,
        ),
        DeclarativeSkillSpec(
            skill_name="memory_preference_extract",
            clearance="LOW",
            risk_level=1,
            scope="memory",
            reversible=True,
            description="从文本中提取用户偏好、交互风格和禁忌的软记忆候选（只生成候选，不直接写入长期记忆）。",
            tool="memory",
            action="preference_extract",
            normalize_args=_normalize_memory_preference_extract,
            family="memory",
            capability_tags=("preference", "candidate", "safety"),
        ),
        DeclarativeSkillSpec(
            skill_name="memory_consolidate",
            clearance="LOW",
            risk_level=1,
            scope="memory",
            reversible=True,
            description="将对话片段或任务结果整理为可审核的记忆候选（只生成候选，不直接写入长期记忆）。",
            tool="memory",
            action="consolidate",
            normalize_args=_normalize_memory_consolidate,
            family="memory",
            capability_tags=("episodic", "candidate", "consolidation"),
        ),
        DeclarativeSkillSpec(
            skill_name="memory_forget",
            clearance="MEDIUM",
            risk_level=3,
            scope="memory",
            reversible=False,
            require_confirm=True,
            description="从记忆库中删除指定记忆（不可逆，需用户确认；支持按 ID 或标签批量删除）。",
            tool="memory",
            action="forget",
            normalize_args=_normalize_memory_forget,
        ),
    )


def build_memory_skill_registry(provider: MemorySkillProvider | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(provider or MemorySkillProvider())
    return registry
