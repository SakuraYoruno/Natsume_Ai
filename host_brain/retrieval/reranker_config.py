"""Reranker config loading, route resolution, scoring, and provider helpers."""
from __future__ import annotations

import inspect
import json
import logging
import math
import os
import re
from typing import Any, Callable, Mapping

from host_brain.core.errors import CONFIG_REQUIRED_MISSING, CONFIG_VALUE_INVALID
from host_brain.retrieval.reranker_schemas import (
    RerankConfigIssue,
    RerankConfigLoadResult,
    RerankRouteConfig,
    RerankRuntimeConfig,
    RerankerResult,
)

RerankProvider = Callable[..., tuple[list[float], str]]
logger = logging.getLogger("Natsume.retrieval.reranker_config")

S_RET_RERANK_CONFIG = "S_RET_RERANK_CONFIG"
DEFAULT_RERANK_ROUTE = "default"
DEFAULT_RERANK_PROVIDER = "heuristic"
DEFAULT_RERANK_TIMEOUT_MS = 500
DEFAULT_RERANK_TOP_K = 5
DEFAULT_NODE_SELECTOR = "default"
RERANK_MODEL_ID = "heuristic_v1"
STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"
_COLD_START_RETRYABLE_RERANK_PROVIDERS: frozenset[str] = frozenset({"cross_encoder", "qwen3_reranker"})


def load_reranker_runtime_config(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
) -> RerankConfigLoadResult:
    merged: dict[str, Any] = dict(env or os.environ)
    if local_overrides:
        merged.update(dict(local_overrides))

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



# Re-export for backward compatibility
from host_brain.retrieval.reranker_providers import (  # noqa: E402, F401
    _build_cross_encoder_provider,
    _build_qwen3_reranker_provider,
    _sigmoid,
)
