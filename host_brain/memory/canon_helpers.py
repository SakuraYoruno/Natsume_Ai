"""World Canon 存储使用的纯辅助函数。

这些函数仅规范化身份、标签与内容摘要；它们不授予写入权威。执行事实探测用于阻止
Canon 文本把未由 Acceptance 证明的副作用伪装成既成事实。
"""
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
    """对规范 JSON 计算稳定内容摘要，用于版本并发检查与投影一致性。"""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_doc_id(doc_id: str) -> str:
    """限制文档 ID 为安全的单路径段，避免 Canon 文件布局被调用方穿越。"""
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
    """识别“已执行”式断言；未经接受收据验证时不得作为 Canon 事实写入。"""
    lowered = text.lower()
    return any(token in lowered for token in WORLD_EXECUTION_FACT_TOKENS)
