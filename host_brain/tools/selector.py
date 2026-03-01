"""Top-K tool metadata selection with WRITE default-hidden policy."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from host_brain.tools.catalog import ToolCatalog, ToolMeta, compute_tool_visibility_hash


_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")


@dataclass(frozen=True)
class ToolSelection:
    """Selection output exposed to context packing."""

    tool_meta_pack: list[dict[str, Any]]
    selected_ids: list[str]
    tool_visibility_hash: str


def select_tool_meta_pack(
    catalog: ToolCatalog,
    *,
    query: str,
    k: int = 6,
    include_write: bool = False,
    required_tags: list[str] | None = None,
) -> ToolSelection:
    """Select Top-K metadata entries for model visibility."""

    limit = max(1, int(k))
    tags = _normalize_tags(required_tags or [])
    candidates = _filter_candidates(
        catalog.list_all(),
        include_write=include_write,
        required_tags=tags,
    )
    ranked = _rank_tools(candidates, query=query)
    picked = ranked[:limit]
    meta_pack = [tool.as_dict() for tool in picked]
    return ToolSelection(
        tool_meta_pack=meta_pack,
        selected_ids=[tool.id for tool in picked],
        tool_visibility_hash=compute_tool_visibility_hash(meta_pack),
    )


def _filter_candidates(
    tools: list[ToolMeta],
    *,
    include_write: bool,
    required_tags: list[str],
) -> list[ToolMeta]:
    filtered: list[ToolMeta] = []
    for tool in tools:
        if (not include_write) and tool.side_effect_level == "WRITE":
            continue
        if required_tags and not set(required_tags).issubset(set(tool.domain_tags)):
            continue
        filtered.append(tool)
    return filtered


def _rank_tools(tools: list[ToolMeta], *, query: str) -> list[ToolMeta]:
    query_tokens = _tokenize(query)
    scored: list[tuple[int, str, ToolMeta]] = []
    for tool in tools:
        score = _score(tool, query_tokens)
        # Secondary ordering keeps deterministic and stable selection.
        scored.append((-score, tool.id, tool))
    scored.sort()
    return [item[2] for item in scored]


def _score(tool: ToolMeta, query_tokens: set[str]) -> int:
    if not query_tokens:
        return 0
    haystack = _tokenize(" ".join([tool.name, tool.desc, *tool.domain_tags]))
    return sum(1 for token in query_tokens if token in haystack)


def _tokenize(value: str) -> set[str]:
    return {token.lower() for token in _WORD_RE.findall(value or "")}


def _normalize_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for tag in tags:
        value = str(tag).strip().lower()
        if value and value not in out:
            out.append(value)
    return out
