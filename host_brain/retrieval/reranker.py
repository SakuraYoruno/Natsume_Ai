"""Reranker service contract with deterministic degrade semantics."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
import math
import logging
import os
from time import perf_counter
import re
import threading
from typing import Any, Callable, Mapping

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    CONFIG_REQUIRED_MISSING,
    CONFIG_VALUE_INVALID,
    RETR_RERANK_INPUT_INVALID,
    RETR_RERANK_TIMEOUT,
    RETR_RERANK_UNAVAILABLE,
)
from host_brain.core.ids import now_ms
from host_brain.retrieval.model_suite import apply_retrieval_model_suite_env_defaults
from host_brain.retrieval.reranker_schemas import (
    RerankConfigIssue,
    RerankConfigLoadResult,
    RerankRouteConfig,
    RerankRuntimeConfig,
    RerankerResult,
)
from host_brain.retrieval.audit_preview import build_retrieval_items_preview



S_RET_RERANK = "S_RET_RERANK"
S_RET_RERANK_CONFIG = "S_RET_RERANK_CONFIG"
RET_OP_RERANK = "RERANK"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"

RERANK_MODEL_ID = "heuristic-rerank-v1"
DEFAULT_RERANK_PROVIDER = "heuristic"
DEFAULT_RERANK_ROUTE = "default"
DEFAULT_RERANK_TOP_K = 5
DEFAULT_RERANK_TIMEOUT_MS = 500
DEFAULT_NODE_SELECTOR = "default"
_COLD_START_RETRYABLE_RERANK_PROVIDERS = frozenset({"cross_encoder", "qwen3_reranker"})
_UNAVAILABLE_FALLBACK_RERANK_PROVIDERS = frozenset({"cross_encoder", "qwen3_reranker"})

RerankProvider = Callable[..., tuple[list[float], str]]
logger = logging.getLogger("Natsume.retrieval.reranker")


class RerankerServiceContract:
    """Host reranker contract with auditable degrade mapping."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        provider: RerankProvider | None = None,
        provider_registry: Mapping[str, RerankProvider] | None = None,
        runtime_config: RerankRuntimeConfig | None = None,
        node_id: str | None = None,
        available: bool = True,
        timeout_ms: int = DEFAULT_RERANK_TIMEOUT_MS,
    ) -> None:
        self.ledger = ledger
        self.provider = provider or _default_rerank_provider
        self.provider_registry: dict[str, RerankProvider] = dict(provider_registry or {})
        if DEFAULT_RERANK_PROVIDER not in self.provider_registry:
            self.provider_registry[DEFAULT_RERANK_PROVIDER] = self.provider
        self.runtime_config = runtime_config or _default_runtime_config(timeout_ms=timeout_ms)
        self.node_id = (str(node_id).strip() if node_id is not None else None)
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
        # M28-O（OPT-40）：情感感知重排序参数
        emotion_context: str | None = None,
    ) -> RerankerResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        route = resolve_reranker_route(self.runtime_config, node_id=self.node_id)
        timeout_value = int(timeout_ms if timeout_ms is not None else route.timeout_ms or self.timeout_ms)
        normalized_query = str(query or "").strip()
        normalized_candidates = _normalize_candidates(candidates)
        requested_top_k = (route.top_k if top_k is None else int(top_k))
        topk_value = _coerce_top_k(requested_top_k, len(normalized_candidates))

        if not normalized_query:
            fallback_scores = _build_fallback_scores(normalized_candidates)
            fallback_topk = _build_topk(normalized_candidates, fallback_scores, topk_value)
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_RERANK_INPUT_INVALID,
                detail="reranker input invalid: query is required",
                latency_ms=0,
                model_id=route.model_id,
                provider=route.provider,
                route_id=route.route_id,
                query=normalized_query,
                scores=fallback_scores,
                topk=fallback_topk,
                expected_top_k=topk_value,
                node_selector=route.node_selector,
                ts_ms=now_value,
            )
        if not normalized_candidates:
            output = {
                "request_id": request_id,
                "intent_id": intent_id,
                "query": normalized_query,
                "scores": [],
                "rerank_model_id": str(route.model_id),
                "provider": str(route.provider),
                "route_id": str(route.route_id),
                "node_id": self.node_id,
                "node_selector": str(route.node_selector),
                "expected_top_k": int(topk_value),
                "latency_ms": 0,
                "status": STATUS_OK,
                "stage": S_RET_RERANK,
                "reason_code": None,
                "degraded": False,
                "topk": [],
            }
            self._log_event(
                request_id=request_id,
                intent_id=intent_id,
                status=STATUS_OK,
                reason_code=None,
                detail="reranker skipped: no candidates from recall stage",
                payload={
                    "query_length": len(normalized_query),
                    "candidate_count": 0,
                    "topk_count": 0,
                    "expected_top_k": int(topk_value),
                    "provider": str(route.provider),
                    "route_id": str(route.route_id),
                    "node_selector": str(route.node_selector),
                    "latency_ms": 0,
                    "topk": [],
                    "output_status": STATUS_OK,
                },
                ts_ms=now_value,
            )
            return RerankerResult(
                success=True,
                status=STATUS_OK,
                reason_code=None,
                stage=S_RET_RERANK,
                detail="reranker skipped: no candidates from recall stage",
                output=output,
            )

        if not self.runtime_config.enabled:
            fallback_scores = _build_fallback_scores(normalized_candidates)
            fallback_topk = _build_topk(normalized_candidates, fallback_scores, topk_value)
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_RERANK_UNAVAILABLE,
                detail="reranker runtime disabled by config",
                latency_ms=0,
                model_id=route.model_id,
                provider=route.provider,
                route_id=route.route_id,
                query=normalized_query,
                scores=fallback_scores,
                topk=fallback_topk,
                expected_top_k=topk_value,
                node_selector=route.node_selector,
                ts_ms=now_value,
            )

        if not self.available:
            fallback_scores = _build_fallback_scores(normalized_candidates)
            fallback_topk = _build_topk(normalized_candidates, fallback_scores, topk_value)
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_RERANK_UNAVAILABLE,
                detail="reranker unavailable; fallback to recall order",
                latency_ms=0,
                model_id=route.model_id,
                provider=route.provider,
                route_id=route.route_id,
                query=normalized_query,
                scores=fallback_scores,
                topk=fallback_topk,
                expected_top_k=topk_value,
                node_selector=route.node_selector,
                ts_ms=now_value,
            )

        selected_provider = self.provider_registry.get(route.provider)
        if selected_provider is None:
            fallback_scores = _build_fallback_scores(normalized_candidates)
            fallback_topk = _build_topk(normalized_candidates, fallback_scores, topk_value)
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_RERANK_UNAVAILABLE,
                detail=f"reranker provider not registered: {route.provider}",
                latency_ms=0,
                model_id=route.model_id,
                provider=route.provider,
                route_id=route.route_id,
                query=normalized_query,
                scores=fallback_scores,
                topk=fallback_topk,
                expected_top_k=topk_value,
                node_selector=route.node_selector,
                ts_ms=now_value,
            )
        warm_retry_used = False
        fallback_used = False
        fallback_reason_code: str | None = None
        fallback_detail: str | None = None
        effective_provider = route.provider
        configured_model_id = str(route.model_id)
        attempt = 1
        while True:
            start = perf_counter()
            try:
                scores, model_id = _invoke_rerank_provider(
                    selected_provider,
                    normalized_query,
                    normalized_candidates,
                    route,
                )
            except TimeoutError:
                latency_ms = int(round((perf_counter() - start) * 1000))
                if _should_retry_reranker_cold_start(route.provider, attempt):
                    warm_retry_used = True
                    logger.warning(
                        "Retrying reranker after cold-start timeout (%s/%s, %dms, attempt=%d)",
                        route.provider,
                        route.model_id,
                        latency_ms,
                        attempt,
                    )
                    attempt += 1
                    continue
                fallback_scores = _build_fallback_scores(normalized_candidates)
                fallback_topk = _build_topk(normalized_candidates, fallback_scores, topk_value)
                return self._degrade(
                    request_id=request_id,
                    intent_id=intent_id,
                    reason_code=RETR_RERANK_TIMEOUT,
                    detail=f"reranker timeout>{timeout_value}ms",
                    latency_ms=latency_ms,
                    model_id=route.model_id,
                    provider=route.provider,
                    route_id=route.route_id,
                    query=normalized_query,
                    scores=fallback_scores,
                    topk=fallback_topk,
                    expected_top_k=topk_value,
                    node_selector=route.node_selector,
                    ts_ms=now_value,
                )
            except Exception as exc:
                latency_ms = int(round((perf_counter() - start) * 1000))
                if _should_fallback_reranker_unavailable(route.provider):
                    scores, model_id = _build_unavailable_fallback_reranker(
                        normalized_query,
                        normalized_candidates,
                    )
                    fallback_used = True
                    fallback_reason_code = RETR_RERANK_UNAVAILABLE
                    fallback_detail = f"reranker provider unavailable: {exc}"
                    effective_provider = DEFAULT_RERANK_PROVIDER
                    logger.warning(
                        "Using heuristic reranker fallback after provider error (%s/%s): %s",
                        route.provider,
                        route.model_id,
                        exc,
                    )
                    break
                fallback_scores = _build_fallback_scores(normalized_candidates)
                fallback_topk = _build_topk(normalized_candidates, fallback_scores, topk_value)
                return self._degrade(
                    request_id=request_id,
                    intent_id=intent_id,
                    reason_code=RETR_RERANK_UNAVAILABLE,
                    detail=f"reranker provider unavailable: {exc}",
                    latency_ms=latency_ms,
                    model_id=route.model_id,
                    provider=route.provider,
                    route_id=route.route_id,
                    query=normalized_query,
                    scores=fallback_scores,
                    topk=fallback_topk,
                    expected_top_k=topk_value,
                    node_selector=route.node_selector,
                    ts_ms=now_value,
                )

            latency_ms = int(round((perf_counter() - start) * 1000))
            if latency_ms > timeout_value and _should_retry_reranker_cold_start(route.provider, attempt):
                warm_retry_used = True
                logger.warning(
                    "Retrying reranker after cold-start slow call (%s/%s, %dms>%dms, attempt=%d)",
                    route.provider,
                    route.model_id,
                    latency_ms,
                    timeout_value,
                    attempt,
                )
                attempt += 1
                continue
            if latency_ms > timeout_value:
                fallback_scores = _build_fallback_scores(normalized_candidates)
                fallback_topk = _build_topk(normalized_candidates, fallback_scores, topk_value)
                return self._degrade(
                    request_id=request_id,
                    intent_id=intent_id,
                    reason_code=RETR_RERANK_TIMEOUT,
                    detail=f"reranker slow: {latency_ms}ms>{timeout_value}ms",
                    latency_ms=latency_ms,
                    model_id=str(model_id or route.model_id),
                    provider=route.provider,
                    route_id=route.route_id,
                    query=normalized_query,
                    scores=fallback_scores,
                    topk=fallback_topk,
                    expected_top_k=topk_value,
                    node_selector=route.node_selector,
                    ts_ms=now_value,
                )
            break

        normalized_scores, has_invalid_score = _normalize_scores(scores, len(normalized_candidates))
        if has_invalid_score:
            fallback_scores = _build_fallback_scores(normalized_candidates)
            fallback_topk = _build_topk(normalized_candidates, fallback_scores, topk_value)
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_RERANK_INPUT_INVALID,
                detail="reranker invalid score range; fallback to recall order",
                latency_ms=latency_ms,
                model_id=str(model_id or route.model_id),
                provider=route.provider,
                route_id=route.route_id,
                query=normalized_query,
                scores=fallback_scores,
                topk=fallback_topk,
                expected_top_k=topk_value,
                node_selector=route.node_selector,
                ts_ms=now_value,
            )

        topk = _build_topk(normalized_candidates, normalized_scores, topk_value)
        # M28-O（OPT-40）：情感感知重排序 - 对匹配情感的候选项加权 1.3x
        if emotion_context:
            normalized_scores = _apply_emotion_boost(
                normalized_candidates, normalized_scores, emotion_context
            )
            topk = _build_topk(normalized_candidates, normalized_scores, topk_value)
        output = {
            "query": normalized_query,
            "scores": normalized_scores,
            "rerank_model_id": str(model_id or route.model_id),
            "provider": route.provider,
            "effective_provider": effective_provider,
            "configured_model_id": configured_model_id,
            "route_id": route.route_id,
            "node_id": self.node_id,
            "node_selector": route.node_selector,
            "expected_top_k": topk_value,
            "latency_ms": latency_ms,
            "status": STATUS_OK,
            "stage": S_RET_RERANK,
            "reason_code": None,
            "degraded": False,
            "warm_retry_used": warm_retry_used,
            "fallback_used": fallback_used,
            "fallback_reason_code": fallback_reason_code,
            "fallback_detail": fallback_detail,
            "topk": topk,
        }
        success_detail = "rerank completed via heuristic fallback" if fallback_used else "rerank completed"
        self._log_event(
            request_id=request_id,
            intent_id=intent_id,
            status=STATUS_OK,
            reason_code=None,
            detail=success_detail,
            payload={
                "query_length": len(normalized_query),
                "candidate_count": len(normalized_candidates),
                "topk_count": len(topk),
                "expected_top_k": topk_value,
                "provider": route.provider,
                "effective_provider": effective_provider,
                "route_id": route.route_id,
                "node_selector": route.node_selector,
                "latency_ms": latency_ms,
                "topk": build_retrieval_items_preview(topk),
                "output_status": STATUS_OK,
                "fallback_used": fallback_used,
                "fallback_reason_code": fallback_reason_code,
            },
            ts_ms=now_value,
        )
        return RerankerResult(
            success=True,
            status=STATUS_OK,
            reason_code=None,
            stage=S_RET_RERANK,
            detail=success_detail,
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
        provider: str,
        route_id: str,
        query: str,
        scores: list[float],
        topk: list[dict[str, Any]],
        expected_top_k: int,
        node_selector: str,
        ts_ms: int,
    ) -> RerankerResult:
        output = {
            "query": query,
            "scores": list(scores),
            "rerank_model_id": str(model_id),
            "provider": str(provider),
            "route_id": str(route_id),
            "node_id": self.node_id,
            "node_selector": str(node_selector),
            "expected_top_k": int(expected_top_k),
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
                "expected_top_k": int(expected_top_k),
                "provider": str(provider),
                "route_id": str(route_id),
                "node_selector": str(node_selector),
                "latency_ms": int(latency_ms),
                "topk": build_retrieval_items_preview(topk),
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


def load_reranker_runtime_config(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
) -> RerankConfigLoadResult:
    merged: dict[str, Any] = dict(env or os.environ)
    if local_overrides:
        merged.update(dict(local_overrides))
    merged = apply_retrieval_model_suite_env_defaults(merged)

    issues: list[RerankConfigIssue] = []
    enabled = _parse_bool(merged.get("RETR_RERANK_ENABLED"), default=True)
    default_route = str(merged.get("RETR_RERANK_DEFAULT_ROUTE") or DEFAULT_RERANK_ROUTE).strip()
    routes = _parse_routes(merged=merged, default_route=default_route, issues=issues)

    if not default_route:
        issues.append(
            RerankConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail="RETR_RERANK_DEFAULT_ROUTE is required",
                field="RETR_RERANK_DEFAULT_ROUTE",
            )
        )
    if default_route and default_route not in routes:
        issues.append(
            RerankConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail=f"default route not found in routes: {default_route}",
                field="RETR_RERANK_DEFAULT_ROUTE",
            )
        )
    if not routes:
        issues.append(
            RerankConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail="reranker routes are required",
                field="RETR_RERANK_ROUTES_JSON",
            )
        )

    if issues:
        return RerankConfigLoadResult(
            success=False,
            stage=S_RET_RERANK_CONFIG,
            reason_code=issues[0].reason_code,
            config=None,
            issues=tuple(issues),
        )

    config = RerankRuntimeConfig(
        enabled=bool(enabled),
        default_route=default_route,
        routes=routes,
    )
    return RerankConfigLoadResult(
        success=True,
        stage=S_RET_RERANK_CONFIG,
        reason_code=None,
        config=config,
        issues=(),
    )


def resolve_reranker_route(config: RerankRuntimeConfig, *, node_id: str | None) -> RerankRouteConfig:
    if node_id is not None and str(node_id).strip():
        node = str(node_id).strip()
        for route_id in sorted(config.routes.keys()):
            route = config.routes[route_id]
            if _node_selector_match(route.node_selector, node):
                return route
    return config.routes[config.default_route]


def build_reranker_dependency_status(result: RerankerResult | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(result, RerankerResult):
        status = result.status
        reason_code = result.reason_code
        detail = result.detail
    else:
        status = str(result.get("status") or "")
        reason_code = result.get("reason_code")
        detail = str(result.get("detail") or "reranker degraded")

    if status == STATUS_OK:
        return {"available": True, "detail": "reranker dependency healthy"}
    return {
        "available": False,
        "unavailable_kind": "dependency",
        "detail": f"{reason_code}: {detail}",
    }


def build_reranker_provider_registry(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
) -> dict[str, RerankProvider]:
    merged: dict[str, Any] = dict(env or os.environ)
    if local_overrides:
        merged.update(dict(local_overrides))

    registry: dict[str, RerankProvider] = {
        DEFAULT_RERANK_PROVIDER: _default_rerank_provider,
    }
    device = str(merged.get("RETR_RERANK_DEVICE") or "cpu").strip() or "cpu"
    registry["cross_encoder"] = _build_cross_encoder_provider(device=device)
    registry["qwen3_reranker"] = _build_qwen3_reranker_provider(device=device)
    return registry


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
        scores.append(round(min(1.0, lexical_score + recall_bias), 6))
    return scores, RERANK_MODEL_ID


def _parse_routes(
    *,
    merged: Mapping[str, Any],
    default_route: str,
    issues: list[RerankConfigIssue],
) -> dict[str, RerankRouteConfig]:
    routes_raw = merged.get("RETR_RERANK_ROUTES_JSON")
    if routes_raw is None or not str(routes_raw).strip():
        fallback = {
            default_route: {
                "provider": str(merged.get("RETR_RERANK_PROVIDER") or DEFAULT_RERANK_PROVIDER),
                "model_id": str(merged.get("RETR_RERANK_MODEL_ID") or RERANK_MODEL_ID),
                "top_k": merged.get("RETR_RERANK_TOP_K", DEFAULT_RERANK_TOP_K),
                "timeout_ms": merged.get("RETR_RERANK_TIMEOUT_MS", DEFAULT_RERANK_TIMEOUT_MS),
                "node_selector": str(merged.get("RETR_RERANK_NODE_SELECTOR") or DEFAULT_NODE_SELECTOR),
            }
        }
        return _coerce_routes(route_dict=fallback, field_name="RETR_RERANK_ROUTES_JSON", issues=issues)

    try:
        payload = json.loads(str(routes_raw))
    except json.JSONDecodeError:
        issues.append(
            RerankConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail="RETR_RERANK_ROUTES_JSON must be JSON object mapping route->config",
                field="RETR_RERANK_ROUTES_JSON",
            )
        )
        return {}
    return _coerce_routes(route_dict=payload, field_name="RETR_RERANK_ROUTES_JSON", issues=issues)


def _coerce_routes(
    *,
    route_dict: Any,
    field_name: str,
    issues: list[RerankConfigIssue],
) -> dict[str, RerankRouteConfig]:
    if not isinstance(route_dict, dict):
        issues.append(
            RerankConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field_name} must be JSON object mapping route->config",
                field=field_name,
            )
        )
        return {}

    routes: dict[str, RerankRouteConfig] = {}
    for raw_route_id, raw_route in route_dict.items():
        route_id = str(raw_route_id or "").strip()
        if not route_id:
            issues.append(
                RerankConfigIssue(
                    reason_code=CONFIG_VALUE_INVALID,
                    detail=f"{field_name} contains empty route id",
                    field=field_name,
                )
            )
            continue
        route = _coerce_route(route_id=route_id, raw_route=raw_route, field_name=field_name, issues=issues)
        if route is not None:
            routes[route_id] = route
    return routes


def _coerce_route(
    *,
    route_id: str,
    raw_route: Any,
    field_name: str,
    issues: list[RerankConfigIssue],
) -> RerankRouteConfig | None:
    if not isinstance(raw_route, Mapping):
        issues.append(
            RerankConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field_name}.{route_id} must be object",
                field=f"{field_name}.{route_id}",
            )
        )
        return None

    provider = str(raw_route.get("provider") or "").strip()
    model_id = str(raw_route.get("model_id") or "").strip()
    node_selector = str(raw_route.get("node_selector") or "").strip()

    top_k = _coerce_positive_int(
        raw_value=raw_route.get("top_k"),
        field=f"{field_name}.{route_id}.top_k",
        issues=issues,
    )
    timeout_ms = _coerce_positive_int(
        raw_value=raw_route.get("timeout_ms"),
        field=f"{field_name}.{route_id}.timeout_ms",
        issues=issues,
    )

    if not provider:
        issues.append(
            RerankConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail=f"{field_name}.{route_id}.provider is required",
                field=f"{field_name}.{route_id}.provider",
            )
        )
    if not model_id:
        issues.append(
            RerankConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail=f"{field_name}.{route_id}.model_id is required",
                field=f"{field_name}.{route_id}.model_id",
            )
        )
    if not node_selector:
        issues.append(
            RerankConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail=f"{field_name}.{route_id}.node_selector is required",
                field=f"{field_name}.{route_id}.node_selector",
            )
        )
    if top_k is None or timeout_ms is None or not provider or not model_id or not node_selector:
        return None

    return RerankRouteConfig(
        route_id=route_id,
        provider=provider,
        model_id=model_id,
        top_k=top_k,
        timeout_ms=timeout_ms,
        node_selector=node_selector,
    )


def _default_runtime_config(*, timeout_ms: int) -> RerankRuntimeConfig:
    route = RerankRouteConfig(
        route_id=DEFAULT_RERANK_ROUTE,
        provider=DEFAULT_RERANK_PROVIDER,
        model_id=RERANK_MODEL_ID,
        top_k=DEFAULT_RERANK_TOP_K,
        timeout_ms=int(timeout_ms),
        node_selector=DEFAULT_NODE_SELECTOR,
    )
    return RerankRuntimeConfig(
        enabled=True,
        default_route=DEFAULT_RERANK_ROUTE,
        routes={DEFAULT_RERANK_ROUTE: route},
    )


def _coerce_positive_int(
    *,
    raw_value: Any,
    field: str,
    issues: list[RerankConfigIssue],
) -> int | None:
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        issues.append(
            RerankConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field} must be integer > 0",
                field=field,
            )
        )
        return None
    if value <= 0:
        issues.append(
            RerankConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field} must be integer > 0",
                field=field,
            )
        )
        return None
    return value


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


def _normalize_scores(raw_scores: list[float] | tuple[float, ...] | Any, size: int) -> tuple[list[float], bool]:
    defaults = [_fallback_score_by_rank(index, size) for index in range(size)]
    if not isinstance(raw_scores, (list, tuple)):
        return defaults, True

    normalized: list[float] = []
    has_invalid = False
    for index in range(size):
        if index < len(raw_scores):
            try:
                value = float(raw_scores[index])
                if not math.isfinite(value):
                    raise ValueError("non-finite")
                if value < 0.0 or value > 1.0:
                    has_invalid = True
                value = min(1.0, max(0.0, value))
                normalized.append(round(value, 6))
                continue
            except (TypeError, ValueError):
                has_invalid = True
        normalized.append(defaults[index])
    return normalized, has_invalid


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
    return min(DEFAULT_RERANK_TOP_K, candidate_count)


def _build_topk(
    candidates: list[dict[str, Any]],
    scores: list[float],
    top_k: int,
) -> list[dict[str, Any]]:
    # Phase XIII: style_canon/persona_utterance 已走独立通道（不经过 Reranker），
    # 保底机制已无必要（不会有这两类 candidates 进入），使用原始竞争排序。

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


def _node_selector_match(selector: str, node_id: str) -> bool:
    tokens = [item.strip().lower() for item in str(selector).split(",") if item.strip()]
    if not tokens:
        return False
    node = str(node_id).strip().lower()
    for token in tokens:
        if token in {"*", "all"}:
            return True
        if token == node:
            return True
    return False


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _invoke_rerank_provider(
    provider: RerankProvider,
    query: str,
    candidates: list[dict[str, Any]],
    route: RerankRouteConfig,
) -> tuple[list[float], str]:
    if _provider_accepts_route(provider):
        return provider(query, candidates, route)
    return provider(query, candidates)


def _provider_accepts_route(provider: RerankProvider) -> bool:
    try:
        signature = inspect.signature(provider)
    except (TypeError, ValueError):
        return True
    positional = 0
    for param in signature.parameters.values():
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            return True
        if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            positional += 1
    return positional >= 3


def _should_retry_reranker_cold_start(provider_name: str, attempt: int) -> bool:
    return int(attempt) < 2 and str(provider_name or "").strip().lower() in _COLD_START_RETRYABLE_RERANK_PROVIDERS


def _should_fallback_reranker_unavailable(provider_name: str) -> bool:
    return str(provider_name or "").strip().lower() in _UNAVAILABLE_FALLBACK_RERANK_PROVIDERS


def _build_unavailable_fallback_reranker(
    query: str,
    candidates: list[dict[str, Any]],
) -> tuple[list[float], str]:
    return _default_rerank_provider(query, candidates)


def _build_cross_encoder_provider(*, device: str) -> RerankProvider:
    cache: dict[str, Any] = {}
    cache_lock = threading.Lock()
    model_locks: dict[str, threading.Lock] = {}
    runtime_device = str(device or "cpu").strip() or "cpu"

    def _get_model_lock(model_id: str) -> threading.Lock:
        with cache_lock:
            lock = model_locks.get(model_id)
            if lock is None:
                lock = threading.Lock()
                model_locks[model_id] = lock
            return lock

    def _provider(
        query: str,
        candidates: list[dict[str, Any]],
        route: RerankRouteConfig | None = None,
    ) -> tuple[list[float], str]:
        model_id = str(route.model_id).strip() if route is not None else "BAAI/bge-reranker-large"
        try:
            from sentence_transformers import CrossEncoder
        except Exception as exc:  # pragma: no cover - guarded by runtime dependency check
            raise RuntimeError(f"sentence-transformers package unavailable: {exc}") from exc

        model = cache.get(model_id)
        if model is None:
            model_lock = _get_model_lock(model_id)
            with model_lock:
                model = cache.get(model_id)
                if model is None:
                    model = CrossEncoder(model_name_or_path=model_id, device=runtime_device)
                    with cache_lock:
                        cache[model_id] = model

        pairs = [
            [query, str(candidate.get("snippet") or candidate.get("text") or "")]
            for candidate in candidates
        ]
        raw_scores = model.predict(pairs)
        normalized_scores = [_sigmoid(float(value)) for value in raw_scores]
        return normalized_scores, model_id

    return _provider


def _sigmoid(value: float) -> float:
    clipped = max(-50.0, min(50.0, float(value)))
    return round(1.0 / (1.0 + math.exp(-clipped)), 6)


def _build_qwen3_reranker_provider(*, device: str) -> RerankProvider:
    """Qwen3-Reranker 生成式重排 provider。

    Qwen3-Reranker 是基于 CausalLM 的生成式重排模型，通过判断 "yes"/"no" token
    的 logit 差值作为相关性分数，不能使用 CrossEncoder 接口。

    支持的 model_id 示例：
        - Qwen/Qwen3-Reranker-0.6B
        - 本地路径：models/Qwen3-Reranker-0.6B
    """
    cache: dict[str, Any] = {}
    cache_lock = threading.Lock()
    model_locks: dict[str, threading.Lock] = {}
    runtime_device = str(device or "cpu").strip() or "cpu"

    # Qwen3-Reranker 官方 prompt 模板
    _SYSTEM_PROMPT = (
        "Judge whether the Document meets the requirements based on the Query and the Candidate Document, "
        "output your judgement, the answer should be \"yes\" or \"no\"."
    )
    _PREFIX = "<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n"
    _SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    _QUERY_DOC_TEMPLATE = "Query: {query}\nCandidate Document: {doc}"

    def _get_model_lock(model_id: str) -> threading.Lock:
        with cache_lock:
            lock = model_locks.get(model_id)
            if lock is None:
                lock = threading.Lock()
                model_locks[model_id] = lock
            return lock

    def _get_model_and_tokenizer(model_id: str) -> tuple[Any, Any, int, int]:
        """加载并缓存模型与 tokenizer，返回 (model, tokenizer, yes_token_id, no_token_id)。"""
        cached = cache.get(model_id)
        if cached is not None:
            return cached

        model_lock = _get_model_lock(model_id)
        with model_lock:
            cached = cache.get(model_id)
            if cached is not None:
                return cached
            try:
                import torch
                from transformers import AutoModelForCausalLM, AutoTokenizer
            except Exception as exc:
                raise RuntimeError(f"transformers/torch 包不可用: {exc}") from exc

            tokenizer = AutoTokenizer.from_pretrained(model_id, padding_side="left")
            model = AutoModelForCausalLM.from_pretrained(
                model_id,
                torch_dtype=torch.float16 if "cuda" in runtime_device else torch.float32,
            )
            model.to(runtime_device)
            model.eval()

            # 获取 "yes" / "no" 对应的 token id（Qwen3 tokenizer 中为单 token）
            yes_token_id = tokenizer.convert_tokens_to_ids("yes")
            no_token_id = tokenizer.convert_tokens_to_ids("no")
            if yes_token_id == tokenizer.unk_token_id or no_token_id == tokenizer.unk_token_id:
                # 部分 tokenizer 版本使用大写
                yes_token_id = tokenizer.convert_tokens_to_ids("Yes")
                no_token_id = tokenizer.convert_tokens_to_ids("No")

            resolved = (model, tokenizer, yes_token_id, no_token_id)
            with cache_lock:
                cache[model_id] = resolved
            return resolved

    def _provider(
        query: str,
        candidates: list[dict[str, Any]],
        route: RerankRouteConfig | None = None,
    ) -> tuple[list[float], str]:
        model_id = str(route.model_id).strip() if route is not None else "Qwen/Qwen3-Reranker-0.6B"
        if not model_id:
            model_id = "Qwen/Qwen3-Reranker-0.6B"

        try:
            import torch
        except Exception as exc:
            raise RuntimeError(f"torch 包不可用: {exc}") from exc

        model, tokenizer, yes_token_id, no_token_id = _get_model_and_tokenizer(model_id)

        # 构造每个候选的 prompt
        prompts: list[str] = []
        for candidate in candidates:
            doc_text = str(candidate.get("snippet") or candidate.get("text") or "")
            query_doc = _QUERY_DOC_TEMPLATE.format(query=query, doc=doc_text)
            full_prompt = (
                _PREFIX.format(system=_SYSTEM_PROMPT)
                + query_doc
                + _SUFFIX
            )
            prompts.append(full_prompt)

        # 批量 tokenize（左填充，统一长度）
        inputs = tokenizer(
            prompts,
            padding=True,
            truncation=True,
            max_length=4096,
            return_tensors="pt",
        ).to(runtime_device)

        with torch.no_grad():
            outputs = model(**inputs)
            # 取最后一个 token 位置的 logits
            last_logits = outputs.logits[:, -1, :]  # shape: (batch, vocab)
            yes_logits = last_logits[:, yes_token_id].float()
            no_logits = last_logits[:, no_token_id].float()
            # 用 yes/(yes+no) softmax 作为相关性分数
            scores_tensor = torch.softmax(
                torch.stack([yes_logits, no_logits], dim=-1), dim=-1
            )[:, 0]  # yes 的概率

        scores = [round(float(s), 6) for s in scores_tensor.cpu().tolist()]
        return scores, model_id

    return _provider

def _apply_emotion_boost(
    candidates: list[dict[str, Any]],
    scores: list[float],
    emotion_context: str,
    boost_factor: float = 1.3,
) -> list[float]:
    """对与 emotion_context 匹配的候选项加权 boost_factor（M28-O OPT-40）。

    Args:
        candidates: 候选项列表
        scores: 对应的相关性分数列表
        emotion_context: 当前情感上下文（如 "HAPPY"）
        boost_factor: 情感匹配加权系数，默认 1.3x

    Returns:
        加权后的分数列表（归一化到 [0, 1]）
    """
    if not emotion_context or not candidates or not scores:
        return scores

    normalized_emotion = str(emotion_context).strip().upper()
    boosted = list(scores)

    for i, candidate in enumerate(candidates):
        if i >= len(boosted):
            break
        candidate_emotion = str(
            candidate.get("emotion_tag") or
            candidate.get("metadata", {}).get("emotion_tag") or ""
        ).strip().upper()
        if candidate_emotion == normalized_emotion:
            boosted[i] = min(1.0, boosted[i] * boost_factor)

    return boosted
