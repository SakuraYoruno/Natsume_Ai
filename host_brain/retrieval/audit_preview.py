"""检索审计的有界预览投影。

预览仅保留少量引用、摘要和允许的路由元数据，用于可观测性而非再次作为模型上下文；
它会截断文本并丢弃任意 metadata，避免审计 UI 成为原始记忆的泄露通道。
"""

from __future__ import annotations

from typing import Any, Mapping


DEFAULT_PREVIEW_ITEM_LIMIT = 8
DEFAULT_SNIPPET_MAX_CHARS = 220


def build_retrieval_items_preview(
    items: list[dict[str, Any]] | Any,
    *,
    limit: int = DEFAULT_PREVIEW_ITEM_LIMIT,
    snippet_max_chars: int = DEFAULT_SNIPPET_MAX_CHARS,
) -> list[dict[str, Any]]:
    """将候选结果压缩为有数量和片段上限的审计预览。"""
    if not isinstance(items, list):
        return []

    previews: list[dict[str, Any]] = []
    max_items = max(0, int(limit))
    for raw in items[:max_items]:
        if not isinstance(raw, Mapping):
            continue
        metadata = dict(raw.get("metadata") or {}) if isinstance(raw.get("metadata"), Mapping) else {}
        preview = {
            "doc_id": _string_or_none(raw.get("doc_id") or raw.get("memory_id")),
            "source_ref": _string_or_none(raw.get("source_ref")),
            "evidence_ref": _string_or_none(raw.get("evidence_ref") or raw.get("evidence_refs")),
            "snippet": _truncate_text(raw.get("snippet") or raw.get("text"), max_chars=snippet_max_chars),
            "score": _coerce_float(raw.get("score")),
            "confidence": _coerce_float(raw.get("confidence")),
            "metadata": _compact_metadata(metadata),
        }
        previews.append({key: value for key, value in preview.items() if value not in (None, "", [], {})})
    return previews


def build_retrieval_refs_preview(
    refs: list[str] | tuple[str, ...] | Any,
    *,
    limit: int = DEFAULT_PREVIEW_ITEM_LIMIT,
) -> list[str]:
    """按上限投影引用列表，保留顺序以便关联原始检索结果。"""
    if not isinstance(refs, (list, tuple)):
        return []

    previews: list[str] = []
    max_items = max(0, int(limit))
    for raw in refs[:max_items]:
        text = _string_or_none(raw)
        if text:
            previews.append(text)
    return previews


def _compact_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """只白名单无秘密的路由元数据，任意扩展字段均不进入预览。"""
    compact = {
        "store_id": _string_or_none(metadata.get("store_id")),
        "collection": _string_or_none(metadata.get("collection")),
        "route_id": _string_or_none(metadata.get("route_id")),
        "emotion_tag": _string_or_none(metadata.get("emotion_tag")),
    }
    matched_routes = metadata.get("matched_routes")
    if isinstance(matched_routes, (list, tuple)):
        compact["matched_routes"] = [
            value for value in (_string_or_none(item) for item in matched_routes) if value
        ]
    return {key: value for key, value in compact.items() if value not in (None, "", [], {})}


def _truncate_text(value: Any, *, max_chars: int) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    return text if len(text) <= max_chars else f"{text[: max_chars - 3]}..."


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
