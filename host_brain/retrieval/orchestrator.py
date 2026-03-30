"""Retrieval orchestrator contract for chat paths.

Provides a typed orchestrator layer on top of RetrievalPipelineContract:
- unified request protocol (query/session_state/trace_id)
- unified retrieval_bundle output
- explicit success / partial_failure / all_failure path tagging
"""

from __future__ import annotations

import atexit
import asyncio
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
import hashlib
import json
import os
import threading
import time
from typing import Any, Mapping

from host_brain.app.prompt_builder import _truncate_for_prompt
from host_brain.retrieval.pipeline import RetrievalPipelineContract, RetrievalPipelineResult
from host_brain.retrieval.query_planner import QueryPlan, QueryPlanner


RETRIEVAL_PATH_SUCCESS = "success"
RETRIEVAL_PATH_PARTIAL_FAILURE = "partial_failure"
RETRIEVAL_PATH_ALL_FAILURE = "all_failure"

_ORCHESTRATOR_EXECUTOR = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="retrieval-orchestrator",
)
atexit.register(_ORCHESTRATOR_EXECUTOR.shutdown, wait=False, cancel_futures=True)


def build_skipped_retrieval_summary(reason_code: str) -> dict[str, Any]:
    return {
        "status": "SKIPPED",
        "reason_code": str(reason_code or "HOST_CHAT_RETRIEVAL_DISABLED"),
        "route_count": 0,
        "routes": [],
        "route_rows": [],
        "route_errors": [],
        "candidate_count": 0,
        "rerank_topk": 0,
        "evidence_refs": [],
        "reason_chain": [],
        "degraded": False,
        "agentic_meta": {
            "skipped": True,
            "reason_code": str(reason_code or "HOST_CHAT_RETRIEVAL_DISABLED"),
        },
    }


def build_degraded_retrieval_summary(reason_code: str) -> dict[str, Any]:
    return {
        "status": "DEGRADED",
        "reason_code": str(reason_code or "RETRIEVAL_FAILED"),
        "route_count": 0,
        "routes": [],
        "route_rows": [],
        "route_errors": [],
        "candidate_count": 0,
        "rerank_topk": 0,
        "evidence_refs": [],
        "reason_chain": [],
        "degraded": True,
    }


def _normalize_top_k(value: int | None) -> int:
    try:
        parsed = int(value if value is not None else 8)
    except (TypeError, ValueError):
        parsed = 8
    return max(1, min(parsed, 20))


async def _await_executor_future(
    future: Future[Any],
    *,
    timeout_ms: int | None,
) -> Any:
    if timeout_ms is None:
        while not future.done():
            await asyncio.sleep(0.01)
        return future.result()

    deadline = time.monotonic() + max(0.1, float(max(100, int(timeout_ms))) / 1000.0)
    while True:
        if future.done():
            return future.result()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError
        await asyncio.sleep(min(0.05, remaining))


def _extract_evidence_items(*, rerank_output: Mapping[str, Any]) -> list[dict[str, Any]]:
    try:
        evidence_cap = int(str(os.environ.get("HOST_RETR_EVIDENCE_ITEMS_MAX") or "8").strip())
    except ValueError:
        evidence_cap = 8
    evidence_cap = max(1, min(evidence_cap, 20))
    rerank_topk_items = list(rerank_output.get("topk") or [])
    evidence_items: list[dict[str, Any]] = []
    for item in rerank_topk_items[:evidence_cap]:
        if not isinstance(item, dict):
            continue
        snippet = _truncate_for_prompt(item.get("snippet"), limit=240)
        if not snippet:
            continue
        metadata_raw = item.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
        evidence_items.append(
            {
                "evidence_ref": str(item.get("evidence_ref") or "").strip() or str(item.get("doc_id") or ""),
                "source_ref": str(item.get("source_ref") or "").strip(),
                "snippet": snippet,
                "score": item.get("score"),
                "confidence": item.get("confidence"),
                "metadata": metadata,
                "store_id": str(metadata.get("store_id") or "").strip() or None,
            }
        )
    return evidence_items


def _classify_retrieval_path(*, summary: Mapping[str, Any], evidence_items: list[dict[str, Any]]) -> str:
    status = str(summary.get("status") or "").strip().upper()
    if status == "SKIPPED":
        return RETRIEVAL_PATH_SUCCESS
    degraded = bool(summary.get("degraded", False))
    route_count = int(summary.get("route_count") or 0)
    candidate_count = int(summary.get("candidate_count") or 0)
    rerank_topk = int(summary.get("rerank_topk") or 0)
    if status == "OK" and not degraded:
        return RETRIEVAL_PATH_SUCCESS
    if route_count > 0 or candidate_count > 0 or rerank_topk > 0 or len(evidence_items) > 0:
        return RETRIEVAL_PATH_PARTIAL_FAILURE
    return RETRIEVAL_PATH_ALL_FAILURE


def build_summary_from_pipeline_result(result: RetrievalPipelineResult) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    recall_output = dict(result.output.get("recall") or {})
    rerank_output = dict(result.output.get("rerank") or {})
    evidence_pack = dict(result.output.get("evidence_pack") or {})
    query_plan = dict(result.output.get("query_plan") or {})
    agentic_meta = dict(result.output.get("agentic_meta") or {})
    adaptive_budget = dict(result.output.get("adaptive_budget") or {})
    evidence_items = _extract_evidence_items(rerank_output=rerank_output)

    summary: dict[str, Any] = {
        "status": result.status,
        "reason_code": result.reason_code,
        "route_count": len(recall_output.get("routes") or []),
        "routes": list(recall_output.get("routes") or []),
        "route_rows": list(recall_output.get("route_rows") or []),
        "route_errors": list(recall_output.get("route_errors") or []),
        "candidate_count": int(recall_output.get("candidate_count") or 0),
        "rerank_topk": len(list(rerank_output.get("topk") or [])),
        "topk": list(rerank_output.get("topk") or []),
        "evidence_refs": list(evidence_pack.get("evidence_refs") or []),
        "evidence_topk": list(evidence_pack.get("topk") or []),
        "reason_chain": list(result.output.get("reason_chain") or []),
        "degraded": bool(result.output.get("degraded", False)),
        "query_plan": query_plan,
        "agentic_meta": agentic_meta,
        "adaptive_budget": adaptive_budget,
        "slot_budget_overrides": dict(adaptive_budget.get("adjusted_budget") or {}),
    }
    path = _classify_retrieval_path(summary=summary, evidence_items=evidence_items)
    return summary, evidence_items, path


@dataclass(frozen=True)
class RetrievalOrchestratorRequest:
    trace_id: str
    query: str
    session_state: dict[str, Any]
    request_id: str
    session_id: str | None = None
    intent_id: str | None = None
    top_k: int = 8
    ts_ms: int | None = None
    emotion_context: str | None = None
    render_target_lang: str | None = None
    turn_index: int = 0
    query_plan: QueryPlan | Mapping[str, Any] | None = None
    route_target: str = ""  # Phase XIII: 预算双路由（host_8b / cloud）


@dataclass(frozen=True)
class RetrievalOrchestratorResult:
    success: bool
    status: str
    reason_code: str | None
    path: str
    detail: str
    trace_id: str
    query: str
    session_state: dict[str, Any]
    retrieval_summary: dict[str, Any]
    retrieval_evidence_items: list[dict[str, Any]]
    retrieval_bundle: dict[str, Any]


@dataclass(frozen=True)
class _CacheEntry:
    stored_at_ms: int
    expires_at_ms: int
    result: RetrievalOrchestratorResult


class RetrievalOrchestrator:
    """Concurrent retrieval orchestrator with stable IO contract."""

    def __init__(
        self,
        *,
        pipeline: RetrievalPipelineContract,
        cache_enabled: bool = False,
        cache_ttl_ms: int = 3000,
        cache_max_entries: int = 256,
    ) -> None:
        self.pipeline = pipeline
        ttl_value = max(0, min(int(cache_ttl_ms), 600_000))
        max_entries_value = max(1, min(int(cache_max_entries), 20_000))
        self._cache_enabled = bool(cache_enabled and ttl_value > 0 and max_entries_value > 0)
        self._cache_ttl_ms = ttl_value
        self._cache_max_entries = max_entries_value
        self._cache_lock = threading.Lock()
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()

    @property
    def cache_config_signature(self) -> tuple[bool, int, int]:
        return (self._cache_enabled, self._cache_ttl_ms, self._cache_max_entries)

    def _monotonic_ms(self) -> int:
        return int(time.monotonic() * 1000)

    def _build_cache_key(
        self,
        *,
        request: RetrievalOrchestratorRequest,
        normalized_top_k: int,
    ) -> str:
        session_state = dict(request.session_state or {})
        session_id = str(request.session_id or session_state.get("session_id") or "").strip()
        query_plan_raw = request.query_plan
        if isinstance(query_plan_raw, QueryPlan):
            query_plan_payload: dict[str, Any] = query_plan_raw.as_dict()
        elif isinstance(query_plan_raw, Mapping):
            query_plan_payload = dict(query_plan_raw)
        else:
            query_plan_payload = {}
        payload = {
            "session_id": session_id,
            "query": str(request.query or "").strip(),
            "top_k": int(normalized_top_k),
            "intent_id": str(request.intent_id or "").strip(),
            "emotion_context": str(request.emotion_context or "").strip(),
            "render_target_lang": str(request.render_target_lang or "").strip().lower(),
            "turn_index": int(request.turn_index),
            "query_plan": query_plan_payload,
            "session_state": session_state,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _cache_get(self, *, cache_key: str) -> tuple[RetrievalOrchestratorResult, int] | None:
        if not self._cache_enabled:
            return None
        now_ms = self._monotonic_ms()
        with self._cache_lock:
            entry = self._cache.get(cache_key)
            if entry is None:
                return None
            if entry.expires_at_ms <= now_ms:
                self._cache.pop(cache_key, None)
                return None
            self._cache.move_to_end(cache_key)
            age_ms = max(0, now_ms - int(entry.stored_at_ms))
            return entry.result, age_ms

    def _cache_set(self, *, cache_key: str, result: RetrievalOrchestratorResult) -> None:
        if not self._cache_enabled:
            return
        now_ms = self._monotonic_ms()
        entry = _CacheEntry(
            stored_at_ms=now_ms,
            expires_at_ms=(now_ms + self._cache_ttl_ms),
            result=result,
        )
        with self._cache_lock:
            self._cache[cache_key] = entry
            self._cache.move_to_end(cache_key)
            expired_keys = [
                key for key, row in self._cache.items()
                if int(row.expires_at_ms) <= now_ms
            ]
            for key in expired_keys:
                self._cache.pop(key, None)
            while len(self._cache) > self._cache_max_entries:
                self._cache.popitem(last=False)

    def _should_cache(self, *, result: RetrievalOrchestratorResult) -> bool:
        if not self._cache_enabled:
            return False
        status = str(result.status or "").strip().upper()
        if status != "OK":
            return False
        return True

    def _with_cache_meta(
        self,
        *,
        result: RetrievalOrchestratorResult,
        cache_hit: bool,
        cache_age_ms: int,
    ) -> RetrievalOrchestratorResult:
        summary = dict(result.retrieval_summary or {})
        summary["cache_enabled"] = bool(self._cache_enabled)
        summary["cache_hit"] = bool(cache_hit and self._cache_enabled)
        summary["cache_ttl_ms"] = int(self._cache_ttl_ms if self._cache_enabled else 0)
        summary["cache_age_ms"] = max(0, int(cache_age_ms))
        summary["cache_key_scope"] = "session_id+query+top_k+intent_id+emotion_context+render_target_lang+turn_index+query_plan"
        evidence_items = [
            dict(item) if isinstance(item, Mapping) else item
            for item in list(result.retrieval_evidence_items or [])
        ]
        bundle = dict(result.retrieval_bundle or {})
        bundle["summary"] = dict(bundle.get("summary") or summary)
        bundle["summary"].update(summary)
        bundle["evidence_items"] = list(evidence_items)
        bundle["cache"] = {
            "enabled": bool(self._cache_enabled),
            "hit": bool(cache_hit and self._cache_enabled),
            "ttl_ms": int(self._cache_ttl_ms if self._cache_enabled else 0),
            "age_ms": max(0, int(cache_age_ms)),
            "max_entries": int(self._cache_max_entries),
            "key_scope": "session_id+query+top_k+intent_id+emotion_context+render_target_lang+turn_index+query_plan",
        }
        return RetrievalOrchestratorResult(
            success=bool(result.success),
            status=str(result.status),
            reason_code=result.reason_code,
            path=str(result.path),
            detail=str(result.detail),
            trace_id=str(result.trace_id),
            query=str(result.query),
            session_state=dict(result.session_state or {}),
            retrieval_summary=summary,
            retrieval_evidence_items=list(evidence_items),
            retrieval_bundle=bundle,
        )

    async def arun(
        self,
        *,
        request: RetrievalOrchestratorRequest,
        timeout_ms: int | None = None,
    ) -> RetrievalOrchestratorResult:
        normalized_top_k = _normalize_top_k(request.top_k)
        planner = QueryPlanner()
        resolved_query_plan = _resolve_query_plan(
            planner=planner,
            query=str(request.query or ""),
            turn_index=int(request.turn_index),
            query_plan=request.query_plan,
        )
        if resolved_query_plan.skip_retrieval:
            summary = build_skipped_retrieval_summary("QUERY_PLANNER_SKIP_RETRIEVAL")
            summary["query_plan"] = resolved_query_plan.as_dict()
            summary["agentic_meta"] = {
                **dict(summary.get("agentic_meta") or {}),
                "mode": "QUERY_PLANNER",
                "intent": resolved_query_plan.intent,
                "turn_index": int(request.turn_index),
            }
            out = RetrievalOrchestratorResult(
                success=True,
                status="SKIPPED",
                reason_code=(str(summary.get("reason_code") or "").strip() or None),
                path=RETRIEVAL_PATH_SUCCESS,
                detail="retrieval skipped by query planner",
                trace_id=str(request.trace_id or request.request_id),
                query=str(request.query or ""),
                session_state=dict(request.session_state or {}),
                retrieval_summary=dict(summary),
                retrieval_evidence_items=[],
                retrieval_bundle={
                    "trace_id": str(request.trace_id or request.request_id),
                    "query": str(request.query or ""),
                    "session_state": dict(request.session_state or {}),
                    "path": RETRIEVAL_PATH_SUCCESS,
                    "summary": dict(summary),
                    "evidence_items": [],
                    "query_plan": resolved_query_plan.as_dict(),
                },
            )
            return self._with_cache_meta(
                result=out,
                cache_hit=False,
                cache_age_ms=0,
            )
        cache_key: str | None = None
        if self._cache_enabled:
            cache_key = self._build_cache_key(
                request=RetrievalOrchestratorRequest(
                    trace_id=request.trace_id,
                    query=request.query,
                    session_state=request.session_state,
                    request_id=request.request_id,
                    session_id=request.session_id,
                    intent_id=request.intent_id,
                    top_k=request.top_k,
                    ts_ms=request.ts_ms,
                    emotion_context=request.emotion_context,
                    render_target_lang=request.render_target_lang,
                    turn_index=request.turn_index,
                    query_plan=resolved_query_plan,
                ),
                normalized_top_k=normalized_top_k,
            )
            cached_row = self._cache_get(cache_key=cache_key)
            if cached_row is not None:
                cached_result, cache_age_ms = cached_row
                return self._with_cache_meta(
                    result=cached_result,
                    cache_hit=True,
                    cache_age_ms=cache_age_ms,
                )
        run_future = _ORCHESTRATOR_EXECUTOR.submit(
            partial(
                self.pipeline.run,
                request_id=request.request_id,
                query=request.query,
                intent_id=request.intent_id,
                session_id=request.session_id,
                top_k=normalized_top_k,
                ts_ms=request.ts_ms,
                emotion_context=request.emotion_context,
                render_target_lang=request.render_target_lang,
                turn_index=int(request.turn_index),
                query_plan=resolved_query_plan,
                route_target=str(request.route_target or ""),
            ),
        )
        try:
            run_result = await _await_executor_future(run_future, timeout_ms=timeout_ms)
            summary, evidence_items, path = build_summary_from_pipeline_result(run_result)
            detail = str(run_result.detail or "retrieval orchestrator completed")
            success = str(summary.get("status") or "").upper() == "OK" and not bool(summary.get("degraded", False))
        except asyncio.TimeoutError:
            summary = build_degraded_retrieval_summary("RETRIEVAL_TIMEOUT")
            evidence_items = []
            path = RETRIEVAL_PATH_ALL_FAILURE
            detail = "retrieval orchestrator timeout"
            success = False
        except Exception as exc:  # pragma: no cover - defensive guard
            summary = build_degraded_retrieval_summary("RETRIEVAL_FAILED")
            evidence_items = []
            path = RETRIEVAL_PATH_ALL_FAILURE
            detail = f"retrieval orchestrator failed: {exc}"
            success = False

        bundle = {
            "trace_id": str(request.trace_id or request.request_id),
            "query": str(request.query or ""),
            "session_state": dict(request.session_state or {}),
            "path": path,
            "summary": dict(summary),
            "evidence_items": list(evidence_items),
            "query_plan": resolved_query_plan.as_dict(),
        }
        out = RetrievalOrchestratorResult(
            success=success,
            status=str(summary.get("status") or "DEGRADED"),
            reason_code=(str(summary.get("reason_code") or "").strip() or None),
            path=path,
            detail=detail,
            trace_id=str(request.trace_id or request.request_id),
            query=str(request.query or ""),
            session_state=dict(request.session_state or {}),
            retrieval_summary=dict(summary),
            retrieval_evidence_items=list(evidence_items),
            retrieval_bundle=bundle,
        )
        if cache_key is not None and self._should_cache(result=out):
            self._cache_set(cache_key=cache_key, result=out)
        return self._with_cache_meta(
            result=out,
            cache_hit=False,
            cache_age_ms=0,
        )


def _resolve_query_plan(
    *,
    planner: QueryPlanner,
    query: str,
    turn_index: int,
    query_plan: QueryPlan | Mapping[str, Any] | None,
) -> QueryPlan:
    if isinstance(query_plan, QueryPlan):
        return query_plan
    if isinstance(query_plan, Mapping):
        try:
            return QueryPlan(
                original_query=str(query_plan.get("original_query") or query or "").strip(),
                rewritten_queries=[
                    str(item).strip()
                    for item in list(query_plan.get("rewritten_queries") or [])
                    if str(item).strip()
                ],
                intent=str(query_plan.get("intent") or "").strip() or "chat",
                target_stores=[
                    str(item).strip()
                    for item in list(query_plan.get("target_stores") or [])
                    if str(item).strip()
                ],
                skip_retrieval=bool(query_plan.get("skip_retrieval", False)),
                max_rounds=max(0, int(query_plan.get("max_rounds") or 1)),
                time_lookback_days=(
                    int(query_plan["time_lookback_days"])
                    if query_plan.get("time_lookback_days") is not None
                    else None
                ),
            )
        except Exception:
            pass
    return planner.plan(str(query or ""), turn_index=int(turn_index))
