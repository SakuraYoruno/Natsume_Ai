"""多轮对话上下文压缩服务（M12-B）。

策略：
  sliding_window  — 保留最近 N 轮，直接截断
  summary         — 调用 LLM 生成摘要（不可用时降级）
  hybrid          — 保留最近 N 轮 + 摘要历史轮次
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Literal

from host_brain.core.errors import (
    CTX_COMPRESS_INPUT_INVALID,
    CTX_COMPRESS_LLM_UNAVAILABLE,
    CTX_EVIDENCE_PACK_INVALID,
)
from host_brain.core.ids import new_event_id, now_ms


# ── 常量 ──────────────────────────────────────────────────────────────────────

STRATEGY_SLIDING = "sliding_window"
STRATEGY_SUMMARY = "summary"
STRATEGY_HYBRID = "hybrid"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"

DEFAULT_MAX_TOKENS = 2048
DEFAULT_RECENT_TURNS = 6          # hybrid 策略保留最近 N 轮
APPROX_TOKENS_PER_CHAR = 0.25    # 粗估：4 字符 ≈ 1 token


# ── Schema ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EvidenceCandidate:
    doc_id: str
    source_ref: str
    snippet: str
    score: float
    confidence: float
    turn_index: int | None = None


@dataclass(frozen=True)
class CompressedContext:
    strategy: str
    turns: list[dict[str, Any]]       # 保留的轮次
    summary: str | None               # summary/hybrid 策略生成的摘要
    total_tokens: int
    degraded: bool
    reason_code: str | None


@dataclass(frozen=True)
class EvidencePack:
    pack_id: str
    request_id: str
    intent_id: str | None
    candidates: list[EvidenceCandidate]
    total_tokens: int
    strategy: str
    ts_ms: int
    hash: str                          # SHA-256(candidates)
    degraded: bool
    reason_code: str | None


# ── LLM 摘要提供者类型 ────────────────────────────────────────────────────────

SummaryProvider = Callable[[list[dict[str, Any]]], str]


# ── ContextCompressor ─────────────────────────────────────────────────────────

class ContextCompressor:
    """多轮对话上下文压缩服务。"""

    def __init__(
        self,
        *,
        summary_provider: SummaryProvider | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        recent_turns: int = DEFAULT_RECENT_TURNS,
    ) -> None:
        self._summary_provider = summary_provider
        self._max_tokens = max(1, int(max_tokens))
        self._recent_turns = max(1, int(recent_turns))

    def compress(
        self,
        turns: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
        strategy: Literal["sliding_window", "summary", "hybrid"] = STRATEGY_HYBRID,
    ) -> CompressedContext:
        """压缩多轮对话上下文。"""
        limit = int(max_tokens) if max_tokens is not None else self._max_tokens
        normalized = _normalize_turns(turns)

        if not normalized:
            return CompressedContext(
                strategy=strategy,
                turns=[],
                summary=None,
                total_tokens=0,
                degraded=True,
                reason_code=CTX_COMPRESS_INPUT_INVALID,
            )

        if strategy == STRATEGY_SLIDING:
            return self._sliding_window(normalized, limit)
        if strategy == STRATEGY_SUMMARY:
            return self._summary(normalized, limit)
        # hybrid（默认）
        return self._hybrid(normalized, limit)

    def build_evidence_pack(
        self,
        compressed: CompressedContext,
        *,
        request_id: str,
        intent_id: str | None = None,
        episodic_hits: list[dict[str, Any]] | None = None,
        canon_slots: dict[str, Any] | None = None,
        ts_ms: int | None = None,
    ) -> EvidencePack:
        """构建结构化 EvidencePack。"""
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        candidates: list[EvidenceCandidate] = []

        # 从压缩后的轮次构建候选
        for idx, turn in enumerate(compressed.turns):
            snippet = _turn_to_snippet(turn)
            score = max(0.0, 1.0 - idx * 0.05)
            candidates.append(
                EvidenceCandidate(
                    doc_id=f"turn_{idx:03d}",
                    source_ref=f"context://turn/{idx}",
                    snippet=snippet,
                    score=round(score, 4),
                    confidence=round(score, 4),
                    turn_index=idx,
                )
            )

        # 追加 episodic 命中
        for idx, hit in enumerate(episodic_hits or []):
            snippet = str(hit.get("snippet") or hit.get("text") or "")
            score = float(hit.get("score") or 0.5)
            candidates.append(
                EvidenceCandidate(
                    doc_id=str(hit.get("doc_id") or f"episodic_{idx:03d}"),
                    source_ref=str(hit.get("source_ref") or f"episodic://hit/{idx}"),
                    snippet=snippet,
                    score=round(score, 4),
                    confidence=round(score, 4),
                    turn_index=None,
                )
            )

        pack_hash = _compute_candidates_hash(candidates)
        pack_id = f"epk_{pack_hash[:16]}"

        return EvidencePack(
            pack_id=pack_id,
            request_id=request_id,
            intent_id=intent_id,
            candidates=candidates,
            total_tokens=compressed.total_tokens,
            strategy=compressed.strategy,
            ts_ms=now_value,
            hash=pack_hash,
            degraded=compressed.degraded,
            reason_code=compressed.reason_code,
        )

    # ── 内部策略 ──────────────────────────────────────────────────────────────

    def _sliding_window(
        self,
        turns: list[dict[str, Any]],
        max_tokens: int,
    ) -> CompressedContext:
        """保留最近 N 轮，直到 token 预算耗尽。"""
        kept: list[dict[str, Any]] = []
        total = 0
        for turn in reversed(turns):
            tokens = _estimate_tokens(turn)
            if total + tokens > max_tokens and kept:
                break
            kept.insert(0, turn)
            total += tokens
        return CompressedContext(
            strategy=STRATEGY_SLIDING,
            turns=kept,
            summary=None,
            total_tokens=total,
            degraded=False,
            reason_code=None,
        )

    def _summary(
        self,
        turns: list[dict[str, Any]],
        max_tokens: int,
    ) -> CompressedContext:
        """调用 LLM 生成摘要；不可用时降级为 sliding_window。"""
        if self._summary_provider is None:
            degraded = self._sliding_window(turns, max_tokens)
            return CompressedContext(
                strategy=STRATEGY_SUMMARY,
                turns=degraded.turns,
                summary=None,
                total_tokens=degraded.total_tokens,
                degraded=True,
                reason_code=CTX_COMPRESS_LLM_UNAVAILABLE,
            )
        try:
            summary_text = self._summary_provider(turns)
        except Exception:
            degraded = self._sliding_window(turns, max_tokens)
            return CompressedContext(
                strategy=STRATEGY_SUMMARY,
                turns=degraded.turns,
                summary=None,
                total_tokens=degraded.total_tokens,
                degraded=True,
                reason_code=CTX_COMPRESS_LLM_UNAVAILABLE,
            )
        summary_tokens = _estimate_tokens_text(summary_text)
        return CompressedContext(
            strategy=STRATEGY_SUMMARY,
            turns=[],
            summary=summary_text,
            total_tokens=summary_tokens,
            degraded=False,
            reason_code=None,
        )

    def _hybrid(
        self,
        turns: list[dict[str, Any]],
        max_tokens: int,
    ) -> CompressedContext:
        """保留最近 N 轮 + 摘要历史轮次。"""
        recent = turns[-self._recent_turns:]
        history = turns[: -self._recent_turns] if len(turns) > self._recent_turns else []

        recent_tokens = sum(_estimate_tokens(t) for t in recent)
        summary_text: str | None = None
        summary_tokens = 0
        degraded = False
        reason_code: str | None = None

        if history and self._summary_provider is not None:
            try:
                summary_text = self._summary_provider(history)
                summary_tokens = _estimate_tokens_text(summary_text)
            except Exception:
                degraded = True
                reason_code = CTX_COMPRESS_LLM_UNAVAILABLE
        elif history:
            degraded = True
            reason_code = CTX_COMPRESS_LLM_UNAVAILABLE

        total = recent_tokens + summary_tokens
        # 若超出预算，进一步截断 recent
        if total > max_tokens:
            kept: list[dict[str, Any]] = []
            budget = max_tokens - summary_tokens
            for turn in reversed(recent):
                tokens = _estimate_tokens(turn)
                if budget - tokens < 0 and kept:
                    break
                kept.insert(0, turn)
                budget -= tokens
            recent = kept
            recent_tokens = sum(_estimate_tokens(t) for t in recent)
            total = recent_tokens + summary_tokens

        return CompressedContext(
            strategy=STRATEGY_HYBRID,
            turns=recent,
            summary=summary_text,
            total_tokens=total,
            degraded=degraded,
            reason_code=reason_code,
        )


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _normalize_turns(turns: list[dict[str, Any]] | Any) -> list[dict[str, Any]]:
    if not isinstance(turns, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in turns:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user").strip()
        content = str(item.get("content") or item.get("text") or "").strip()
        if content:
            normalized.append({"role": role, "content": content})
    return normalized


def _estimate_tokens(turn: dict[str, Any]) -> int:
    text = str(turn.get("content") or "")
    return max(1, int(len(text) * APPROX_TOKENS_PER_CHAR))


def _estimate_tokens_text(text: str) -> int:
    return max(1, int(len(text) * APPROX_TOKENS_PER_CHAR))


def _turn_to_snippet(turn: dict[str, Any]) -> str:
    role = str(turn.get("role") or "user")
    content = str(turn.get("content") or "")
    return f"[{role}] {content}"[:512]


def _compute_candidates_hash(candidates: list[EvidenceCandidate]) -> str:
    payload = [
        {
            "doc_id": c.doc_id,
            "source_ref": c.source_ref,
            "snippet": c.snippet,
            "score": c.score,
        }
        for c in candidates
    ]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
