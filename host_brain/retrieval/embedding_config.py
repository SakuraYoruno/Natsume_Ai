"""Embedding config loading, route resolution, and provider builder helpers."""
from __future__ import annotations

import inspect
import json
import logging
import os
from typing import Any, Callable, Mapping

from host_brain.core.errors import CONFIG_REQUIRED_MISSING, CONFIG_VALUE_INVALID
from host_brain.memory.chroma_client import EMBEDDING_MODEL_ID, embed_text
from host_brain.retrieval.embedding_schemas import (
    EmbeddingConfigIssue,
    EmbeddingConfigLoadResult,
    EmbeddingRouteConfig,
    EmbeddingRuntimeConfig,
    EmbeddingResult,
)

DEFAULT_EMBED_PROVIDER = "builtin_hash"
DEFAULT_EMBED_ROUTE = "default"
DEFAULT_EMBED_DIM = 16
DEFAULT_EMBED_TIMEOUT_MS = 500
DEFAULT_NODE_SELECTOR = "default"
_COLD_START_RETRYABLE_EMBED_PROVIDERS = frozenset({"sentence_transformers"})
_TIMEOUT_FALLBACK_EMBED_PROVIDERS = frozenset({"sentence_transformers"})

EmbeddingProvider = Callable[..., tuple[list[float], str]]
logger = logging.getLogger("Natsume.retrieval.embedding_config")


def load_embedding_runtime_config(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
) -> EmbeddingConfigLoadResult:
    merged: dict[str, Any] = dict(env or os.environ)
    if local_overrides:
        merged.update(dict(local_overrides))

    issues: list[EmbeddingConfigIssue] = []
    enabled = _parse_bool(merged.get("RETR_EMBED_ENABLED"), default=True)
    default_route = str(merged.get("RETR_EMBED_DEFAULT_ROUTE") or DEFAULT_EMBED_ROUTE).strip()
    routes = _parse_routes(merged=merged, default_route=default_route, issues=issues)

    if not default_route:
        issues.append(
            EmbeddingConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail="RETR_EMBED_DEFAULT_ROUTE is required",
                field="RETR_EMBED_DEFAULT_ROUTE",
            )
        )

    if routes and default_route not in routes:
        issues.append(
            EmbeddingConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"default_route '{default_route}' not found in routes",
                field="RETR_EMBED_DEFAULT_ROUTE",
            )
        )
    if not routes:
        issues.append(
            EmbeddingConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail="embedding routes are required",
                field="RETR_EMBED_ROUTES_JSON",
            )
        )

    if issues:
        return EmbeddingConfigLoadResult(
            success=False,
            stage="S_RET_EMBED_CONFIG",
            reason_code=issues[0].reason_code,
            config=None,
            issues=tuple(issues),
        )

    config = EmbeddingRuntimeConfig(
        enabled=bool(enabled),
        default_route=default_route,
        routes=routes,
    )
    return EmbeddingConfigLoadResult(
        success=True,
        stage="S_RET_EMBED_CONFIG",
        reason_code=None,
        config=config,
        issues=(),
    )


def resolve_embedding_route(
    config: EmbeddingRuntimeConfig,
    *,
    node_id: str | None,
) -> EmbeddingRouteConfig:
    if node_id is not None and str(node_id).strip():
        node = str(node_id).strip()
        for route_id in sorted(config.routes.keys()):
            route = config.routes[route_id]
            if _node_selector_match(route.node_selector, node):
                return route
    return config.routes[config.default_route]


def build_embedding_dependency_status(result: EmbeddingResult | Mapping[str, Any]) -> dict[str, Any]:
    STATUS_OK = "OK"
    if isinstance(result, EmbeddingResult):
        status = result.status
        reason_code = result.reason_code
        detail = result.detail
    else:
        status = str(result.get("status") or "")
        reason_code = result.get("reason_code")
        detail = str(result.get("detail") or "embedding degraded")

    if status == STATUS_OK:
        return {"available": True, "detail": "embedding dependency healthy"}
    return {
        "available": False,
        "unavailable_kind": "dependency",
        "detail": f"{reason_code}: {detail}",
    }


def build_embedding_provider_registry(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
    openai_api_key: str | None = None,
) -> dict[str, EmbeddingProvider]:
    merged: dict[str, Any] = dict(env or os.environ)
    if local_overrides:
        merged.update(dict(local_overrides))

    registry: dict[str, EmbeddingProvider] = {
        DEFAULT_EMBED_PROVIDER: lambda text, route=None: embed_text(text, dim=_resolve_route_embedding_dim(route)),
    }
    embed_device = str(merged.get("RETR_EMBED_DEVICE") or "cpu").strip() or "cpu"
    registry["sentence_transformers"] = _build_sentence_transformers_embedding_provider(device=embed_device)
    resolved_key = str(openai_api_key or merged.get("OPENAI_API_KEY") or "").strip()
    if resolved_key:
        registry["openai"] = _build_openai_embedding_provider(api_key=resolved_key)
    return registry


def _default_runtime_config(*, timeout_ms: int) -> EmbeddingRuntimeConfig:
    route = EmbeddingRouteConfig(
        route_id=DEFAULT_EMBED_ROUTE,
        provider=DEFAULT_EMBED_PROVIDER,
        model_id=EMBEDDING_MODEL_ID,
        dim=DEFAULT_EMBED_DIM,
        timeout_ms=int(timeout_ms),
        node_selector=DEFAULT_NODE_SELECTOR,
    )
    return EmbeddingRuntimeConfig(
        enabled=True,
        default_route=DEFAULT_EMBED_ROUTE,
        routes={DEFAULT_EMBED_ROUTE: route},
    )


def _parse_routes(
    *,
    merged: Mapping[str, Any],
    default_route: str,
    issues: list[EmbeddingConfigIssue],
) -> dict[str, EmbeddingRouteConfig]:
    routes_raw = merged.get("RETR_EMBED_ROUTES_JSON")
    if routes_raw is None or not str(routes_raw).strip():
        fallback = {
            default_route: {
                "provider": str(merged.get("RETR_EMBED_PROVIDER") or DEFAULT_EMBED_PROVIDER),
                "model_id": str(merged.get("RETR_EMBED_MODEL_ID") or EMBEDDING_MODEL_ID),
                "dim": merged.get("RETR_EMBED_DIM", DEFAULT_EMBED_DIM),
                "timeout_ms": merged.get("RETR_EMBED_TIMEOUT_MS", DEFAULT_EMBED_TIMEOUT_MS),
                "node_selector": str(merged.get("RETR_EMBED_NODE_SELECTOR") or DEFAULT_NODE_SELECTOR),
            }
        }
        return _coerce_routes(route_dict=fallback, field_name="RETR_EMBED_ROUTES_JSON", issues=issues)

    try:
        payload = json.loads(str(routes_raw))
    except json.JSONDecodeError:
        issues.append(
            EmbeddingConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail="RETR_EMBED_ROUTES_JSON must be JSON object mapping route->config",
                field="RETR_EMBED_ROUTES_JSON",
            )
        )
        return {}
    return _coerce_routes(route_dict=payload, field_name="RETR_EMBED_ROUTES_JSON", issues=issues)


def _coerce_routes(
    *,
    route_dict: Any,
    field_name: str,
    issues: list[EmbeddingConfigIssue],
) -> dict[str, EmbeddingRouteConfig]:
    if not isinstance(route_dict, dict):
        issues.append(
            EmbeddingConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field_name} must be JSON object mapping route->config",
                field=field_name,
            )
        )
        return {}
    routes: dict[str, EmbeddingRouteConfig] = {}
    for route_id, raw in route_dict.items():
        route = _coerce_route(route_id=str(route_id), raw=raw, issues=issues)
        if route is not None:
            routes[route_id] = route
    return routes


def _coerce_route(
    *,
    route_id: str,
    raw: Any,
    issues: list[EmbeddingConfigIssue],
) -> EmbeddingRouteConfig | None:
    if not isinstance(raw, dict):
        issues.append(
            EmbeddingConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"route '{route_id}' config must be a dict",
                field=route_id,
            )
        )
        return None

    provider = str(raw.get("provider") or DEFAULT_EMBED_PROVIDER).strip()
    model_id = str(raw.get("model_id") or EMBEDDING_MODEL_ID).strip()
    node_selector = str(raw.get("node_selector") or DEFAULT_NODE_SELECTOR).strip()

    raw_dim = raw.get("dim")
    if raw_dim is None:
        dim = DEFAULT_EMBED_DIM
    else:
        dim = _coerce_positive_int(raw_value=raw_dim, field=f"routes.{route_id}.dim", issues=issues)
        if dim is None:
            dim = DEFAULT_EMBED_DIM

    raw_timeout = raw.get("timeout_ms")
    if raw_timeout is None:
        timeout_ms = DEFAULT_EMBED_TIMEOUT_MS
    else:
        timeout_ms = _coerce_positive_int(raw_value=raw_timeout, field=f"routes.{route_id}.timeout_ms", issues=issues)
        if timeout_ms is None:
            timeout_ms = DEFAULT_EMBED_TIMEOUT_MS

    return EmbeddingRouteConfig(
        route_id=route_id,
        provider=provider,
        model_id=model_id,
        dim=dim,
        timeout_ms=timeout_ms,
        node_selector=node_selector,
    )


def _coerce_positive_int(
    *,
    raw_value: Any,
    field: str,
    issues: list[EmbeddingConfigIssue],
) -> int | None:
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        issues.append(
            EmbeddingConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field} must be integer > 0",
                field=field,
            )
        )
        return None
    if value <= 0:
        issues.append(
            EmbeddingConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field} must be integer > 0",
                field=field,
            )
        )
        return None
    return value


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


def _normalize_vector(raw: list[float] | tuple[float, ...] | Any) -> list[float]:
    if not isinstance(raw, (list, tuple)):
        return []
    return [float(value) for value in raw]


def _invoke_embedding_provider(
    provider: EmbeddingProvider,
    text: str,
    route: EmbeddingRouteConfig,
) -> tuple[list[float], str]:
    if _provider_accepts_route(provider):
        return provider(text, route)
    return provider(text)


def _provider_accepts_route(provider: EmbeddingProvider) -> bool:
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
    return positional >= 2


def _resolve_route_embedding_dim(route: EmbeddingRouteConfig | None) -> int:
    if route is None:
        return DEFAULT_EMBED_DIM
    try:
        dim = int(route.dim)
    except (TypeError, ValueError):
        return DEFAULT_EMBED_DIM
    return dim if dim > 0 else DEFAULT_EMBED_DIM


def _should_retry_embedding_cold_start(provider_name: str, attempt: int) -> bool:
    return int(attempt) < 2 and str(provider_name or "").strip().lower() in _COLD_START_RETRYABLE_EMBED_PROVIDERS


def _should_fallback_embedding_timeout(provider_name: str) -> bool:
    return str(provider_name or "").strip().lower() in _TIMEOUT_FALLBACK_EMBED_PROVIDERS


def _build_timeout_fallback_embedding(
    text: str,
    route: EmbeddingRouteConfig,
) -> tuple[list[float], str]:
    return embed_text(text, dim=_resolve_route_embedding_dim(route))


def _build_openai_embedding_provider(*, api_key: str) -> EmbeddingProvider:
    key = str(api_key or "").strip()

    def _provider(text: str, route: EmbeddingRouteConfig | None = None) -> tuple[list[float], str]:
        if not key:
            raise RuntimeError("OPENAI_API_KEY missing for openai embedding provider")
        model_id = str(route.model_id).strip() if route is not None else "text-embedding-3-large"
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"openai package unavailable: {exc}") from exc
        client = OpenAI(api_key=key)
        response = client.embeddings.create(model=model_id, input=text)
        if not response.data:
            raise RuntimeError("openai embeddings response has no data")
        vector = [float(value) for value in response.data[0].embedding]
        return vector, model_id

    return _provider


def _build_sentence_transformers_embedding_provider(*, device: str) -> EmbeddingProvider:
    cache: dict[str, Any] = {}
    runtime_device = str(device or "cpu").strip() or "cpu"

    def _provider(text: str, route: EmbeddingRouteConfig | None = None) -> tuple[list[float], str]:
        model_id = str(route.model_id).strip() if route is not None else "sentence-transformers/all-MiniLM-L6-v2"
        if not model_id:
            model_id = "sentence-transformers/all-MiniLM-L6-v2"
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"sentence-transformers package unavailable: {exc}") from exc

        model = cache.get(model_id)
        if model is None:
            model = SentenceTransformer(model_name_or_path=model_id, device=runtime_device)
            cache[model_id] = model

        raw_vector = model.encode(text, normalize_embeddings=True)
        if hasattr(raw_vector, "tolist"):
            vector_values = raw_vector.tolist()
        else:
            vector_values = list(raw_vector)
        vector = [float(value) for value in vector_values]
        return vector, model_id

    return _provider
