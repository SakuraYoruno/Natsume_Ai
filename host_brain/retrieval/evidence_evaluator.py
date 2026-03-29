"""Agentic RAG 证据评估器。

职责：评估检索结果质量，决定是否需要二次检索。
设计原则：启发式规则，毫秒级，零 LLM 调用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

_logger = logging.getLogger("Natsume.retrieval.evidence_evaluator")

# ---------------------------------------------------------------------------
# 评估建议
# ---------------------------------------------------------------------------
SUGGEST_PROCEED = "proceed"                  # 证据充足，继续生成
SUGGEST_RETRY_REWRITE = "retry_with_rewrite" # 质量不足，改写 query 重试
SUGGEST_EXPAND_STORES = "expand_stores"       # 覆盖不足，扩展到更多 Store

# ---------------------------------------------------------------------------
# 默认阈值
# ---------------------------------------------------------------------------
DEFAULT_MIN_EVIDENCE_COUNT = 2   # 最少需要几条证据
DEFAULT_MIN_TOP_SCORE = 0.25     # 最高分至少要达到
DEFAULT_MIN_COVERAGE = 0.4       # 最低关键词覆盖度


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class EvidenceEvaluation:
    """证据评估结果。"""

    sufficient: bool
    coverage_score: float = 0.0
    confidence: float = 0.0
    top_score: float = 0.0
    evidence_count: int = 0
    missing_aspects: list[str] = field(default_factory=list)
    suggestion: str = SUGGEST_PROCEED

    def as_dict(self) -> dict[str, Any]:
        return {
            "sufficient": self.sufficient,
            "coverage_score": round(self.coverage_score, 4),
            "confidence": round(self.confidence, 4),
            "top_score": round(self.top_score, 4),
            "evidence_count": self.evidence_count,
            "missing_aspects": list(self.missing_aspects),
            "suggestion": self.suggestion,
        }


# ---------------------------------------------------------------------------
# EvidenceEvaluator
# ---------------------------------------------------------------------------
class EvidenceEvaluator:
    """证据评估器——判断检索结果是否足够回答问题。

    三维评估：数量 × 质量 × 覆盖度。
    """

    def __init__(
        self,
        *,
        min_evidence_count: int = DEFAULT_MIN_EVIDENCE_COUNT,
        min_top_score: float = DEFAULT_MIN_TOP_SCORE,
        min_coverage: float = DEFAULT_MIN_COVERAGE,
    ) -> None:
        self._min_count = max(1, int(min_evidence_count))
        self._min_top_score = float(min_top_score)
        self._min_coverage = float(min_coverage)

    def evaluate(
        self,
        query: str,
        evidence_items: list[dict[str, Any] | Mapping[str, Any]],
    ) -> EvidenceEvaluation:
        """评估证据质量。

        Args:
            query: 原始用户 query。
            evidence_items: 召回的证据列表，每条需有 'score' 和 'snippet'/'text' 字段。

        Returns:
            EvidenceEvaluation
        """
        if not evidence_items:
            return EvidenceEvaluation(
                sufficient=False,
                evidence_count=0,
                missing_aspects=["no_evidence"],
                suggestion=SUGGEST_RETRY_REWRITE,
            )

        # 1. 数量
        count = len(evidence_items)
        count_ok = count >= self._min_count

        # 2. 质量（最高分）
        scores = []
        for item in evidence_items:
            try:
                s = float(item.get("score", 0) or 0)
            except (TypeError, ValueError):
                s = 0.0
            scores.append(s)
        top_score = max(scores) if scores else 0.0
        score_ok = top_score >= self._min_top_score

        # 3. 覆盖度
        coverage = self._compute_coverage(query, evidence_items)
        coverage_ok = coverage >= self._min_coverage

        # 4. 综合置信度
        confidence = (
            (0.3 if count_ok else 0.0)
            + (0.3 * min(top_score / max(self._min_top_score * 2, 0.5), 1.0))
            + (0.4 * coverage)
        )

        # 5. 判定
        sufficient = count_ok and score_ok and coverage_ok

        # 6. 缺失分析
        missing = self._identify_missing(query, evidence_items, coverage)

        # 7. 建议
        if sufficient:
            suggestion = SUGGEST_PROCEED
        elif not score_ok:
            suggestion = SUGGEST_RETRY_REWRITE
        elif not coverage_ok:
            suggestion = SUGGEST_EXPAND_STORES
        else:
            suggestion = SUGGEST_RETRY_REWRITE

        _logger.debug(
            "EvidenceEvaluation: count=%d top=%.3f coverage=%.3f sufficient=%s suggestion=%s",
            count, top_score, coverage, sufficient, suggestion,
        )

        return EvidenceEvaluation(
            sufficient=sufficient,
            coverage_score=round(coverage, 4),
            confidence=round(confidence, 4),
            top_score=round(top_score, 4),
            evidence_count=count,
            missing_aspects=missing,
            suggestion=suggestion,
        )

    # ── 覆盖度计算 ────────────────────────────────────────────────────────

    def _compute_coverage(
        self,
        query: str,
        evidence: list[dict[str, Any] | Mapping[str, Any]],
    ) -> float:
        """计算证据对 query 的关键词覆盖度。"""
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return 1.0  # 空 query 视为全覆盖

        # 收集所有证据文本
        evidence_text_parts: list[str] = []
        for item in evidence:
            snippet = str(item.get("snippet") or item.get("text") or "")
            evidence_text_parts.append(snippet)
        evidence_text = " ".join(evidence_text_parts).lower()
        evidence_tokens = set(evidence_text.split())

        # 逐字符匹配（适合中文）
        covered = 0
        for token in query_tokens:
            if token in evidence_text or token in evidence_tokens:
                covered += 1

        return covered / len(query_tokens)

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """简易分词：按空白 + 逐字（中文 2-3 字 n-gram）。"""
        text = text.lower().strip()
        tokens: set[str] = set()
        # 空格分词
        for word in text.split():
            if len(word) >= 2:
                tokens.add(word)

        # 中文 2-gram / 3-gram
        clean = "".join(ch for ch in text if not ch.isspace())
        for size in (2, 3):
            for i in range(len(clean) - size + 1):
                gram = clean[i : i + size]
                if any("\u4e00" <= ch <= "\u9fff" for ch in gram):
                    tokens.add(gram)

        return tokens

    # ── 缺失分析 ──────────────────────────────────────────────────────────

    def _identify_missing(
        self,
        query: str,
        evidence: list[dict[str, Any] | Mapping[str, Any]],
        coverage: float,
    ) -> list[str]:
        """识别未被证据覆盖的关键方面。"""
        if coverage >= self._min_coverage:
            return []

        query_tokens = self._tokenize(query)
        evidence_text = " ".join(
            str(item.get("snippet") or item.get("text") or "")
            for item in evidence
        ).lower()

        missing: list[str] = []
        for token in sorted(query_tokens):
            if token not in evidence_text:
                missing.append(token)

        return missing[:5]  # 最多返回 5 个
