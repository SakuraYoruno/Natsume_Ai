"""Agentic RAG 检索循环编排器。

职责：Plan → Retrieve → Evaluate → Re-Retrieve 循环。
在现有 RecallFusionServiceContract 上层包装，零侵入。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from host_brain.retrieval.query_planner import (
    ALL_STORE_IDS,
    INTENT_GREETING,
    QueryPlan,
    QueryPlanner,
)
from host_brain.retrieval.evidence_evaluator import (
    SUGGEST_EXPAND_STORES,
    SUGGEST_RETRY_REWRITE,
    EvidenceEvaluation,
    EvidenceEvaluator,
)

_logger = logging.getLogger("Natsume.retrieval.agentic_loop")


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class AgenticRetrievalResult:
    """Agentic 检索结果。"""

    evidence_items: list[dict[str, Any]] = field(default_factory=list)
    rounds: int = 0
    plan: QueryPlan | None = None
    final_evaluation: EvidenceEvaluation | None = None
    elapsed_ms: float = 0.0
    skipped: bool = False
    queries_used: list[str] = field(default_factory=list)
    recall_summary: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_count": len(self.evidence_items),
            "rounds": self.rounds,
            "plan": self.plan.as_dict() if self.plan else None,
            "final_evaluation": self.final_evaluation.as_dict() if self.final_evaluation else None,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "skipped": self.skipped,
            "queries_used": list(self.queries_used),
            "recall_candidate_count": int(self.recall_summary.get("candidate_count") or 0),
            "route_error_count": len(self.recall_summary.get("route_errors") or []),
        }


# ---------------------------------------------------------------------------
# AgenticRetrievalLoop
# ---------------------------------------------------------------------------
class AgenticRetrievalLoop:
    """Agentic RAG 检索循环——在现有召回管线上层包装。

    用法::

        loop = AgenticRetrievalLoop(recall_fusion=recall_service)
        result = loop.retrieve(
            query="你还记得上次聊星星的事吗",
            request_id="req-123",
            turn_index=0,
        )
        # result.evidence_items → 替代原来的 candidates
        # result.rounds → 审计
    """

    def __init__(
        self,
        *,
        recall_fusion: Any,
        planner: QueryPlanner | None = None,
        evaluator: EvidenceEvaluator | None = None,
        embedding_service: Any | None = None,
    ) -> None:
        """初始化。

        Args:
            recall_fusion: RecallFusionServiceContract 实例。
            planner: QueryPlanner（默认创建新实例）。
            evaluator: EvidenceEvaluator（默认创建新实例）。
            embedding_service: EmbeddingServiceContract（用于对改写 query 计算 embedding）。
        """
        self._recall = recall_fusion
        self._planner = planner or QueryPlanner()
        self._evaluator = evaluator or EvidenceEvaluator()
        self._embedding = embedding_service

    def retrieve(
        self,
        *,
        query: str,
        query_embedding: list[float] | None = None,
        request_id: str,
        person_id: str | None = None,
        session_id: str | None = None,
        turn_index: int = 0,
        intent_id: str | None = None,
        emotion_filter: str | None = None,
        render_target_lang: str | None = None,
        ts_ms: int | None = None,
        plan: QueryPlan | None = None,
    ) -> AgenticRetrievalResult:
        """Agentic 检索入口（同步）。

        Args:
            query: 用户消息文本。
            query_embedding: 原始 query 的 embedding 向量。
            request_id: 请求 ID。
            session_id: 会话 ID。
            turn_index: 对话轮次索引。
            intent_id: 意图 ID。
            render_target_lang: 渲染目标语言。
            ts_ms: 时间戳。

        Returns:
            AgenticRetrievalResult
        """
        t0 = time.monotonic()

        # 1. 规划
        plan = plan or self._planner.plan(query, turn_index=turn_index)

        if plan.skip_retrieval:
            elapsed = (time.monotonic() - t0) * 1000
            _logger.info(
                "Agentic skip: intent=%s elapsed=%.1fms",
                plan.intent, elapsed,
            )
            return AgenticRetrievalResult(
                evidence_items=[],
                rounds=0,
                plan=plan,
                final_evaluation=None,
                elapsed_ms=elapsed,
                skipped=True,
                queries_used=[],
                recall_summary={},
            )

        # 2. 检索循环
        all_evidence: list[dict[str, Any]] = []
        evaluation: EvidenceEvaluation | None = None
        rounds_used = 0
        queries_used: list[str] = []
        aggregated_routes: list[dict[str, Any]] = []
        aggregated_route_rows: list[dict[str, Any]] = []
        aggregated_route_errors: list[dict[str, Any]] = []
        recall_status: str | None = None
        recall_reason_code: str | None = None
        recall_detail: str | None = None
        recall_degraded = False

        # 准备所有 query（原始 + 改写）
        queries = [query] + list(plan.rewritten_queries)

        for round_idx in range(plan.max_rounds):
            rounds_used += 1

            # 选择本轮 query
            current_query = (
                queries[round_idx]
                if round_idx < len(queries)
                else query
            )
            queries_used.append(current_query)

            # 计算 embedding
            current_embedding = self._get_embedding(
                current_query, fallback=query_embedding, request_id=request_id,
            )
            if current_embedding is None:
                _logger.warning("Agentic: embedding 不可用，降级跳过")
                break

            # 调用现有 RecallFusion
            try:
                fusion_result = self._recall.recall(
                    request_id=f"{request_id}:agentic_r{round_idx}",
                    query=current_query,
                    query_embedding=current_embedding,
                    intent_id=intent_id,
                    person_id=person_id,
                    session_id=session_id,
                    ts_ms=ts_ms,
                    emotion_filter=emotion_filter,
                    render_target_lang=render_target_lang,
                    target_store_ids=list(plan.target_stores or []),
                    time_lookback_days=plan.time_lookback_days,
                )
            except Exception as exc:
                _logger.warning("Agentic recall 失败 (round %d): %s", round_idx, exc)
                break

            fusion_output = getattr(fusion_result, "output", None)
            if isinstance(fusion_output, dict):
                current_status = str(
                    fusion_output.get("status")
                    or getattr(fusion_result, "status", "")
                    or "",
                ).strip()
                current_reason_code = (
                    fusion_output.get("reason_code")
                    or getattr(fusion_result, "reason_code", None)
                )
                current_detail = str(
                    fusion_output.get("detail")
                    or getattr(fusion_result, "detail", "")
                    or "",
                ).strip()
                current_degraded = bool(
                    fusion_output.get("degraded")
                    or getattr(fusion_result, "status", None) == "DEGRADED"
                    or current_reason_code
                )
                if current_status:
                    recall_status = current_status
                if current_reason_code and recall_reason_code is None:
                    recall_reason_code = str(current_reason_code)
                if current_detail:
                    recall_detail = current_detail
                recall_degraded = recall_degraded or current_degraded
                aggregated_routes.extend(
                    {
                        **dict(route),
                        "round_index": round_idx,
                    }
                    for route in list(fusion_output.get("routes") or [])
                    if isinstance(route, dict)
                )
                aggregated_route_rows.extend(
                    {
                        **dict(row),
                        "round_index": round_idx,
                    }
                    for row in list(fusion_output.get("route_rows") or [])
                    if isinstance(row, dict)
                )
                aggregated_route_errors.extend(
                    {
                        **dict(err),
                        "round_index": round_idx,
                    }
                    for err in list(fusion_output.get("route_errors") or [])
                    if isinstance(err, dict)
                )

            # 提取 candidates
            new_candidates = self._extract_candidates(fusion_result)

            # 按 target_stores 过滤（如果不是全量）
            if plan.target_stores and set(plan.target_stores) != set(ALL_STORE_IDS):
                new_candidates = [
                    c for c in new_candidates
                    if self._candidate_matches_stores(c, plan.target_stores)
                ]

            # 合并去重
            all_evidence = self._merge_evidence(all_evidence, new_candidates)

            # 评估
            evaluation = self._evaluator.evaluate(query, all_evidence)

            _logger.debug(
                "Agentic round %d: query='%s' candidates=%d sufficient=%s",
                round_idx, current_query[:50], len(new_candidates),
                evaluation.sufficient,
            )

            if evaluation.sufficient:
                break

            # 不够 → 调整下一轮策略
            if evaluation.suggestion == SUGGEST_RETRY_REWRITE and round_idx == 0:
                retry_query = self._planner.rewrite_for_retry(
                    query, evaluation.missing_aspects,
                )
                if retry_query not in queries:
                    queries.append(retry_query)
            elif evaluation.suggestion == SUGGEST_EXPAND_STORES:
                plan.target_stores = list(ALL_STORE_IDS)

        elapsed = (time.monotonic() - t0) * 1000
        _logger.info(
            "Agentic done: intent=%s rounds=%d evidence=%d elapsed=%.1fms",
            plan.intent, rounds_used, len(all_evidence), elapsed,
        )

        return AgenticRetrievalResult(
            evidence_items=all_evidence,
            rounds=rounds_used,
            plan=plan,
            final_evaluation=evaluation,
            elapsed_ms=elapsed,
            skipped=False,
            queries_used=queries_used,
            recall_summary={
                "status": recall_status or ("DEGRADED" if recall_degraded else "OK"),
                "reason_code": recall_reason_code,
                "detail": recall_detail or "",
                "degraded": recall_degraded or bool(recall_reason_code),
                "routes": aggregated_routes,
                "route_rows": aggregated_route_rows,
                "route_errors": aggregated_route_errors,
                "candidate_count": len(all_evidence),
                "candidates": list(all_evidence),
            },
        )

    # ── 内部方法 ──────────────────────────────────────────────────────────

    def _get_embedding(
        self,
        text: str,
        *,
        fallback: list[float] | None,
        request_id: str,
    ) -> list[float] | None:
        """获取文本 embedding。优先用 embedding_service，否则用 fallback。"""
        if self._embedding is not None and text:
            try:
                result = self._embedding.embed(
                    request_id=f"{request_id}:agentic_embed",
                    text=text,
                )
                if result.success:
                    output = dict(result.output or {})
                    vec = output.get("embedding_vector")
                    if vec:
                        return [float(v) for v in vec]
            except Exception as exc:
                _logger.debug("Agentic embedding 失败，使用 fallback: %s", exc)
        return fallback if fallback else None

    @staticmethod
    def _extract_candidates(fusion_result: Any) -> list[dict[str, Any]]:
        """从 RecallFusionResult 中提取 candidates。"""
        if fusion_result is None:
            return []
        output = getattr(fusion_result, "output", None)
        if isinstance(output, dict):
            return list(output.get("candidates", []))
        return []

    @staticmethod
    def _candidate_matches_stores(
        candidate: dict[str, Any],
        target_stores: list[str],
    ) -> bool:
        """判断 candidate 是否来自目标 Store。"""
        metadata = candidate.get("metadata", {})
        store_id = str(metadata.get("store_id") or "").strip().lower()
        return store_id in target_stores or not store_id

    @staticmethod
    def _merge_evidence(
        existing: list[dict[str, Any]],
        new_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """合并去重（按 doc_id + source_ref 去重，保留高分）。"""
        merged: dict[str, dict[str, Any]] = {}

        for item in existing:
            key = _evidence_key(item)
            merged[key] = item

        for item in new_items:
            key = _evidence_key(item)
            if key not in merged:
                merged[key] = item
            else:
                # 保留高分
                old_score = float(merged[key].get("score", 0) or 0)
                new_score = float(item.get("score", 0) or 0)
                if new_score > old_score:
                    merged[key] = item

        # 按分数降序
        return sorted(
            merged.values(),
            key=lambda c: -float(c.get("score", 0) or 0),
        )


def _evidence_key(item: dict[str, Any]) -> str:
    """生成证据去重 key。"""
    doc_id = str(item.get("doc_id") or "").strip()
    source_ref = str(item.get("source_ref") or "").strip()
    store_id = str((item.get("metadata") or {}).get("store_id") or "").strip()
    return f"{store_id}::{doc_id or source_ref}"
