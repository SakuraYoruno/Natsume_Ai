"""语义去重过滤器（Phase X 方案 3）。

职责：在 Rerank 之后，检测语义近似的召回条目并合并去重。
设计原则：轻量级文本相似度，不依赖 embedding 模型。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

_logger = logging.getLogger("Natsume.retrieval.semantic_dedup")

# ---------------------------------------------------------------------------
# 默认阈值
# ---------------------------------------------------------------------------
DEFAULT_SIMILARITY_THRESHOLD = 0.65  # 相似度超过此值视为重复
DEFAULT_MIN_CANDIDATES = 3           # 去重后最少保留条数


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class DedupResult:
    """去重结果。"""

    original_count: int
    deduped_count: int
    removed_count: int
    candidates: list[dict[str, Any]]
    merge_log: list[dict[str, str]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "original_count": self.original_count,
            "deduped_count": self.deduped_count,
            "removed_count": self.removed_count,
            "merge_log_count": len(self.merge_log),
        }


# ---------------------------------------------------------------------------
# SemanticDedup
# ---------------------------------------------------------------------------
class SemanticDedup:
    """语义去重——检测并合并近似召回条目。

    使用 Jaccard 字符 n-gram 相似度（不依赖 embedding 模型）。
    保留高分条目，丢弃低分的近似重复。
    """

    def __init__(
        self,
        *,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        min_candidates: int = DEFAULT_MIN_CANDIDATES,
        ngram_size: int = 3,
    ) -> None:
        self._threshold = float(similarity_threshold)
        self._min_candidates = max(1, int(min_candidates))
        self._ngram_size = max(2, int(ngram_size))

    def dedup(
        self,
        candidates: list[dict[str, Any] | Mapping[str, Any]],
    ) -> DedupResult:
        """对召回候选列表执行语义去重。

        假设输入已按分数降序排列。
        """
        original_count = len(candidates)
        if original_count <= self._min_candidates:
            return DedupResult(
                original_count=original_count,
                deduped_count=original_count,
                removed_count=0,
                candidates=list(candidates),
                merge_log=[],
            )

        kept: list[dict[str, Any]] = []
        kept_texts: list[set[str]] = []  # 每个 kept 条目的 ngram 集合
        merge_log: list[dict[str, str]] = []

        for item in candidates:
            item_dict = dict(item) if isinstance(item, Mapping) else item
            text = str(
                item_dict.get("snippet")
                or item_dict.get("text")
                or ""
            ).strip().lower()

            if not text:
                kept.append(item_dict)
                kept_texts.append(set())
                continue

            ngrams = self._compute_ngrams(text)

            # 检查是否与已保留的条目重复
            is_dup = False
            for idx, kept_ngrams in enumerate(kept_texts):
                if not kept_ngrams:
                    continue
                sim = self._jaccard(ngrams, kept_ngrams)
                if sim >= self._threshold:
                    is_dup = True
                    kept_id = str(kept[idx].get("doc_id") or "?")[:30]
                    item_id = str(item_dict.get("doc_id") or "?")[:30]
                    merge_log.append({
                        "kept": kept_id,
                        "removed": item_id,
                        "similarity": f"{sim:.3f}",
                    })
                    break

            if not is_dup:
                kept.append(item_dict)
                kept_texts.append(ngrams)

        # 确保最少保留 min_candidates 条
        if len(kept) < self._min_candidates and original_count >= self._min_candidates:
            # 把被去掉的补回来
            removed_items = [c for c in candidates if dict(c) not in kept]
            for extra in removed_items:
                if len(kept) >= self._min_candidates:
                    break
                kept.append(dict(extra))

        removed_count = original_count - len(kept)
        _logger.debug(
            "SemanticDedup: %d → %d (removed %d, threshold=%.2f)",
            original_count, len(kept), removed_count, self._threshold,
        )

        return DedupResult(
            original_count=original_count,
            deduped_count=len(kept),
            removed_count=removed_count,
            candidates=kept,
            merge_log=merge_log,
        )

    def _compute_ngrams(self, text: str) -> set[str]:
        """计算字符 n-gram 集合。"""
        clean = "".join(ch for ch in text if not ch.isspace())
        if len(clean) < self._ngram_size:
            return {clean} if clean else set()
        return {
            clean[i: i + self._ngram_size]
            for i in range(len(clean) - self._ngram_size + 1)
        }

    @staticmethod
    def _jaccard(set_a: set[str], set_b: set[str]) -> float:
        """Jaccard 相似度。"""
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0
