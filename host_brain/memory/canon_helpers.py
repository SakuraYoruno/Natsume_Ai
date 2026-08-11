"""World Canon helper utilities (extracted from canon_world_store.py)."""
from __future__ import annotations

import hashlib
import json
from typing import Any


WORLD_EXECUTION_FACT_TOKENS = (
    "已执行",
    "执行完成",
    "已保存",
    "保存成功",
    "已确认",
    "confirmed",
    "saved successfully",
    "executed",
    "execution complete",
)

def compute_world_canon_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_doc_id(doc_id: str) -> str:
    value = str(doc_id).strip()
    if not value:
        raise ValueError("doc_id is required")
    if "/" in value or "\\" in value:
        raise ValueError(f"invalid doc_id path segment: {doc_id}")
    allowed = value.replace("-", "").replace("_", "")
    if not allowed.isalnum():
        raise ValueError(f"doc_id contains unsupported characters: {doc_id}")
    return value


def _normalize_tags(tags: list[str] | None) -> list[str]:
    if not tags:
        return []
    normalized: list[str] = []
    for tag in tags:
        cleaned = str(tag).strip()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


def _contains_execution_claim(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in WORLD_EXECUTION_FACT_TOKENS)
