"""Reranker service contract with deterministic degrade semantics."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
import re
from typing import Any, Callable

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    RETR_RERANK_INPUT_INVALID,
    RETR_RERANK_TIMEOUT,
    RETR_RERANK_UNAVAILABLE,
)
from host_brain.core.ids import now_ms


S_RET_RERANK = "S_RET_RERANK"
RET_OP_RERANK = "RERANK"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"

RERANK_MODEL_ID = "heuristic-rerank-v1"

RerankProvider = Callable[[str, list[dict[str, Any]]], tuple[list[float], str]]


@dataclass(frozen=True)
class RerankerResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]


class RerankerServiceContract:
    """Host reranker contract with auditable degrade mapping."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        provider: RerankProvider | None = None,
        available: bool = True,
        timeout_ms: int = 500,
    ) -> None:
        self.ledger = ledger
        self.provider = provider or _default_rerank_provider
        self.available = bool(available)
        self.timeout_ms = int(timeout_ms)

    def rerank(
        self,
        *,
        request_id: str,
        query: str,
        candidates: list[dict[str, Any]] | Any,
        intent_id: str | None = None,
        top_k: int | None = None,
        timeout_ms: int | None = None,
        ts_ms: int | None = None,
    ) -> RerankerResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        timeout_value = int(timeout_ms if timeout_ms is not None else self.timeout_ms)
        normalized_query = str(query or "").strip()
        normalized_candidates = _normalize_candidates(candidates)
        topk_value = _coerce_top_k(top_k, len(normalized_candidates))

        if not normalized_query or not normalized_candidates:
            fallback_scores = _build_fallback_scores(normalized_candidates)
            topk = _build_topk(normalized_candidates, fallback_scores, topk_value)
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_RERANK_INPUT_INVALID,
                detail="reranker input invalid: query and candidates are required",
                latency_ms=0,
                model_id=RERANK_MODEL_ID,
                query=normalized_query,
                scores=fallback_scores,
                topk=topk,
                ts_ms=now_value,
            )

        if not self.available:
            fallback_scores = _build_fallback_scores(normalized_candidates)
            topk = _build_topk(normalized_candidates, fallback_scores, topk_value)
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_RERANK_UNAVAILABLE,
                detail="reranker unavailable; fallback to recall order",
                latency_ms=0,
                model_id=RERANK_MODEL_ID,
                query=normalized_query,
                scores=fallback_scores,
                topk=topk,
                ts_ms=now_value,
            )

        start = perf_counter()
        try:
            scores, model_id = self.provider(normalized_query, normalized_candidates)
        except TimeoutError:
            latency_ms = int(round((perf_counter() - start) * 1000))
            fallback_scores = _build_fallback_scores(normalized_candidates)
            topk = _build_topk(normalized_candidates, fallback_scores, topk_value)
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_RERANK_TIMEOUT,
                detail=f"reranker timeout>{timeout_value}ms",
                latency_ms=latency_ms,
                model_id=RERANK_MODEL_ID,
                query=normalized_query,
                scores=fallback_scores,
                topk=topk,
                ts_ms=now_value,
            )
        except Exception as exc:
            latency_ms = int(round((perf_counter() - start) * 1000))
            fallback_scores = _build_fallback_scores(normalized_candidates)
            topk = _build_topk(normalized_candidates, fallback_scores, topk_value)
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_RERANK_UNAVAILABLE,
                detail=f"reranker provider unavailable: {exc}",
                latency_ms=latency_ms,
                model_id=RERANK_MODEL_ID,
                query=normalized_query,
                scores=fallback_scores,
                topk=topk,
                ts_ms=now_value,
            )

        latency_ms = int(round((perf_counter() - start) * 1000))
        if latency_ms > timeout_value:
            fallback_scores = _build_fallback_scores(normalized_candidates)
            topk = _build_topk(normalized_candidates, fallback_scores, topk_value)
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_RERANK_TIMEOUT,
                detail=f"reranker slow: {latency_ms}ms>{timeout_value}ms",
                latency_ms=latency_ms,
                model_id=str(model_id or RERANK_MODEL_ID),
                query=normalized_query,
                scores=fallback_scores,
                topk=topk,
                ts_ms=now_value,
            )

        normalized_scores = _normalize_scores(scores, len(normalized_candidates))
        topk = _build_topk(normalized_candidates, normalized_scores, topk_value)
        output = {
            "query": normalized_query,
            "scores": normalized_scores,
            "rerank_model_id": str(model_id or RERANK_MODEL_ID),
            "latency_ms": latency_ms,
            "status": STATUS_OK,
            "stage": S_RET_RERANK,
            "reason_code": None,
            "degraded": False,
            "topk": topk,
        }
        self._log_event(
            request_id=request_id,
            intent_id=intent_id,
            status=STATUS_OK,
            reason_code=None,
            detail="rerank completed",
            payload={
                "query_length": len(normalized_query),
                "candidate_count": len(normalized_candidates),
                "topk_count": len(topk),
                "latency_ms": latency_ms,
                "output_status": STATUS_OK,
            },
            ts_ms=now_value,
        )
        return RerankerResult(
            success=True,
            status=STATUS_OK,
            reason_code=None,
            stage=S_RET_RERANK,
            detail="rerank completed",
            output=output,
        )

    def _degrade(
        self,
        *,
        request_id: str,
        intent_id: str | None,
        reason_code: str,
        detail: str,
        latency_ms: int,
        model_id: str,
        query: str,
        scores: list[float],
        topk: list[dict[str, Any]],
        ts_ms: int,
    ) -> RerankerResult:
        output = {
            "query": query,
            "scores": list(scores),
            "rerank_model_id": str(model_id),
            "latency_ms": int(latency_ms),
            "status": STATUS_DEGRADED,
            "stage": S_RET_RERANK,
            "reason_code": reason_code,
            "degraded": True,
            "topk": list(topk),
        }
        self._log_event(
            request_id=request_id,
            intent_id=intent_id,
            status=STATUS_DEGRADED,
            reason_code=reason_code,
            detail=detail,
            payload={
                "query_length": len(query),
                "candidate_count": len(scores),
                "topk_count": len(topk),
                "latency_ms": int(latency_ms),
                "output_status": STATUS_DEGRADED,
            },
            ts_ms=ts_ms,
        )
        return RerankerResult(
            success=False,
            status=STATUS_DEGRADED,
            reason_code=reason_code,
            stage=S_RET_RERANK,
            detail=detail,
            output=output,
        )

    def _log_event(
        self,
        *,
        request_id: str,
        intent_id: str | None,
        status: str,
        reason_code: str | None,
        detail: str,
        payload: dict[str, Any],
        ts_ms: int,
    ) -> None:
        if self.ledger is not None and hasattr(self.ledger, "log_retrieval_event"):
            self.ledger.log_retrieval_event(
                {
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "op_kind": RET_OP_RERANK,
                    "status": status,
                    "reason_code": reason_code,
                    "stage": S_RET_RERANK,
                    "payload": payload,
                    "detail": detail,
                    "ts_ms": ts_ms,
                }
            )
            return

        if self.ledger is not None and hasattr(self.ledger, "log_error") and reason_code is not None:
            self.ledger.log_error(
                request_id=request_id,
                stage=S_RET_RERANK,
                reason_code=reason_code,
                detail=detail,
            )


def _default_rerank_provider(
    query: str,
    candidates: list[dict[str, Any]],
) -> tuple[list[float], str]:
    query_tokens = set(_tokenize(query))
    denominator = max(1, len(query_tokens))
    scores: list[float] = []
    for idx, candidate in enumerate(candidates):
        snippet = str(candidate.get("snippet") or "")
        doc_id = str(candidate.get("doc_id") or "")
        candidate_tokens = set(_tokenize(f"{doc_id} {snippet}"))
        overlap = len(query_tokens.intersection(candidate_tokens))
        lexical_score = float(overlap) / float(denominator)
        recall_bias = _fallback_score_by_rank(idx, len(candidates)) * 0.01
        scores.append(round(lexical_score + recall_bias, 6))
    return scores, RERANK_MODEL_ID


def _normalize_candidates(candidates: list[dict[str, Any]] | Any) -> list[dict[str, Any]]:
    if not isinstance(candidates, list):
        return []
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(candidates):
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata")
        normalized.append(
            {
                "doc_id": str(item.get("doc_id") or f"cand_{index + 1:03d}"),
                "source_ref": str(item.get("source_ref") or f"source://candidate/{index + 1}"),
                "snippet": str(item.get("snippet") or item.get("text") or ""),
                "metadata": dict(metadata) if isinstance(metadata, dict) else {},
                "recall_rank": index,
            }
        )
    return normalized


def _normalize_scores(raw_scores: list[float] | tuple[float, ...] | Any, size: int) -> list[float]:
    defaults = [_fallback_score_by_rank(index, size) for index in range(size)]
    if not isinstance(raw_scores, (list, tuple)):
        return defaults
    normalized: list[float] = []
    for index in range(size):
        if index < len(raw_scores):
            try:
                normalized.append(round(float(raw_scores[index]), 6))
                continue
            except (TypeError, ValueError):
                pass
        normalized.append(defaults[index])
    return normalized


def _build_fallback_scores(candidates: list[dict[str, Any]]) -> list[float]:
    return [_fallback_score_by_rank(index, len(candidates)) for index in range(len(candidates))]


def _fallback_score_by_rank(index: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(max(0.0, 1.0 - (float(index) / float(total))), 6)


def _coerce_top_k(value: int | None, candidate_count: int) -> int:
    if candidate_count <= 0:
        return 0
    if isinstance(value, int) and value > 0:
        return min(value, candidate_count)
    return min(5, candidate_count)


def _build_topk(
    candidates: list[dict[str, Any]],
    scores: list[float],
    top_k: int,
) -> list[dict[str, Any]]:
    ranked: list[tuple[float, int, dict[str, Any]]] = []
    for index, candidate in enumerate(candidates):
        score = float(scores[index]) if index < len(scores) else _fallback_score_by_rank(index, len(candidates))
        ranked.append(
            (
                score,
                index,
                {
                    "doc_id": str(candidate.get("doc_id") or f"cand_{index + 1:03d}"),
                    "source_ref": str(candidate.get("source_ref") or f"source://candidate/{index + 1}"),
                    "snippet": str(candidate.get("snippet") or ""),
                    "score": round(score, 6),
                    "confidence": _score_to_confidence(score),
                    "metadata": dict(candidate.get("metadata") or {}),
                },
            )
        )
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in ranked[: max(0, top_k)]]


def _score_to_confidence(score: float) -> float:
    if score <= 0.0:
        return 0.0
    if score >= 1.0:
        return 1.0
    return round(float(score), 6)


def _tokenize(text: str) -> list[str]:
    return [token for token in re.split(r"[^\w]+", text.lower()) if token]
