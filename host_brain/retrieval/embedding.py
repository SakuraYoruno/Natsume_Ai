"""Embedding service contract with deterministic degrade semantics."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
import os
from time import perf_counter
from typing import Any, Callable, Mapping

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    CONFIG_REQUIRED_MISSING,
    CONFIG_VALUE_INVALID,
    RETR_EMBED_INPUT_INVALID,
    RETR_EMBED_TIMEOUT,
    RETR_EMBED_UNAVAILABLE,
)
from host_brain.core.ids import now_ms
from host_brain.memory.chroma_client import EMBEDDING_MODEL_ID, embed_text


S_RET_EMBEDDING = "S_RET_EMBEDDING"
S_RET_EMBED_CONFIG = "S_RET_EMBED_CONFIG"
RET_OP_EMBEDDING = "EMBEDDING"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"

DEFAULT_EMBED_PROVIDER = "builtin_hash"
DEFAULT_EMBED_ROUTE = "default"
DEFAULT_EMBED_DIM = 16
DEFAULT_EMBED_TIMEOUT_MS = 500
DEFAULT_NODE_SELECTOR = "default"

EmbeddingProvider = Callable[..., tuple[list[float], str]]


@dataclass(frozen=True)
class EmbeddingRouteConfig:
    route_id: str
    provider: str
    model_id: str
    dim: int
    timeout_ms: int
    node_selector: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "provider": self.provider,
            "model_id": self.model_id,
            "dim": int(self.dim),
            "timeout_ms": int(self.timeout_ms),
            "node_selector": self.node_selector,
        }


@dataclass(frozen=True)
class EmbeddingRuntimeConfig:
    enabled: bool
    default_route: str
    routes: dict[str, EmbeddingRouteConfig]

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "default_route": self.default_route,
            "routes": {
                route_id: route.as_dict()
                for route_id, route in sorted(self.routes.items())
            },
        }


@dataclass(frozen=True)
class EmbeddingConfigIssue:
    reason_code: str
    detail: str
    field: str | None = None


@dataclass(frozen=True)
class EmbeddingConfigLoadResult:
    success: bool
    stage: str
    reason_code: str | None
    config: EmbeddingRuntimeConfig | None
    issues: tuple[EmbeddingConfigIssue, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "config": (self.config.as_dict() if self.config is not None else None),
            "issues": [
                {
                    "reason_code": issue.reason_code,
                    "detail": issue.detail,
                    "field": issue.field,
                }
                for issue in self.issues
            ],
        }


@dataclass(frozen=True)
class EmbeddingResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]


class EmbeddingServiceContract:
    """Host embedding service contract with auditable degrade mapping."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        provider: EmbeddingProvider | None = None,
        provider_registry: Mapping[str, EmbeddingProvider] | None = None,
        runtime_config: EmbeddingRuntimeConfig | None = None,
        node_id: str | None = None,
        available: bool = True,
        timeout_ms: int = DEFAULT_EMBED_TIMEOUT_MS,
    ) -> None:
        self.ledger = ledger
        self.provider = provider or embed_text
        self.provider_registry: dict[str, EmbeddingProvider] = dict(provider_registry or {})
        if DEFAULT_EMBED_PROVIDER not in self.provider_registry:
            self.provider_registry[DEFAULT_EMBED_PROVIDER] = self.provider
        self.runtime_config = runtime_config or _default_runtime_config(timeout_ms=timeout_ms)
        self.node_id = (str(node_id).strip() if node_id is not None else None)
        self.available = bool(available)
        self.timeout_ms = int(timeout_ms)

    def embed(
        self,
        *,
        request_id: str,
        text: str,
        intent_id: str | None = None,
        timeout_ms: int | None = None,
        ts_ms: int | None = None,
    ) -> EmbeddingResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        normalized = str(text or "").strip()
        route = resolve_embedding_route(self.runtime_config, node_id=self.node_id)
        timeout_value = int(timeout_ms if timeout_ms is not None else route.timeout_ms or self.timeout_ms)

        if not normalized:
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_EMBED_INPUT_INVALID,
                detail="embedding input text is empty",
                latency_ms=0,
                model_id=route.model_id,
                provider=route.provider,
                route_id=route.route_id,
                expected_dim=route.dim,
                vector=[],
                text=normalized,
                node_selector=route.node_selector,
                ts_ms=now_value,
            )

        if not self.runtime_config.enabled:
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_EMBED_UNAVAILABLE,
                detail="embedding runtime disabled by config",
                latency_ms=0,
                model_id=route.model_id,
                provider=route.provider,
                route_id=route.route_id,
                expected_dim=route.dim,
                vector=[],
                text=normalized,
                node_selector=route.node_selector,
                ts_ms=now_value,
            )

        if not self.available:
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_EMBED_UNAVAILABLE,
                detail="embedding service unavailable; retrieval degraded",
                latency_ms=0,
                model_id=route.model_id,
                provider=route.provider,
                route_id=route.route_id,
                expected_dim=route.dim,
                vector=[],
                text=normalized,
                node_selector=route.node_selector,
                ts_ms=now_value,
            )

        selected_provider = self.provider_registry.get(route.provider)
        if selected_provider is None:
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_EMBED_UNAVAILABLE,
                detail=f"embedding provider not registered: {route.provider}",
                latency_ms=0,
                model_id=route.model_id,
                provider=route.provider,
                route_id=route.route_id,
                expected_dim=route.dim,
                vector=[],
                text=normalized,
                node_selector=route.node_selector,
                ts_ms=now_value,
            )
        start = perf_counter()
        try:
            vector, model_id = _invoke_embedding_provider(selected_provider, normalized, route)
        except TimeoutError:
            latency_ms = int(round((perf_counter() - start) * 1000))
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_EMBED_TIMEOUT,
                detail=f"embedding provider timeout>{timeout_value}ms",
                latency_ms=latency_ms,
                model_id=route.model_id,
                provider=route.provider,
                route_id=route.route_id,
                expected_dim=route.dim,
                vector=[],
                text=normalized,
                node_selector=route.node_selector,
                ts_ms=now_value,
            )
        except Exception as exc:
            latency_ms = int(round((perf_counter() - start) * 1000))
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_EMBED_UNAVAILABLE,
                detail=f"embedding provider unavailable: {exc}",
                latency_ms=latency_ms,
                model_id=route.model_id,
                provider=route.provider,
                route_id=route.route_id,
                expected_dim=route.dim,
                vector=[],
                text=normalized,
                node_selector=route.node_selector,
                ts_ms=now_value,
            )

        latency_ms = int(round((perf_counter() - start) * 1000))
        if latency_ms > timeout_value:
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_EMBED_TIMEOUT,
                detail=f"embedding provider slow: {latency_ms}ms>{timeout_value}ms",
                latency_ms=latency_ms,
                model_id=str(model_id or route.model_id),
                provider=route.provider,
                route_id=route.route_id,
                expected_dim=route.dim,
                vector=[],
                text=normalized,
                node_selector=route.node_selector,
                ts_ms=now_value,
            )

        normalized_vector = _normalize_vector(vector)
        actual_dim = len(normalized_vector)
        if actual_dim != int(route.dim):
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=RETR_EMBED_INPUT_INVALID,
                detail=f"embedding dim drift: expected={int(route.dim)}, got={actual_dim}",
                latency_ms=latency_ms,
                model_id=str(model_id or route.model_id),
                provider=route.provider,
                route_id=route.route_id,
                expected_dim=route.dim,
                vector=normalized_vector,
                text=normalized,
                node_selector=route.node_selector,
                ts_ms=now_value,
            )

        output = {
            "embedding_vector": normalized_vector,
            "embedding_model_id": str(model_id or route.model_id),
            "provider": route.provider,
            "route_id": route.route_id,
            "node_id": self.node_id,
            "node_selector": route.node_selector,
            "expected_dim": int(route.dim),
            "actual_dim": actual_dim,
            "latency_ms": latency_ms,
            "status": STATUS_OK,
            "stage": S_RET_EMBEDDING,
            "reason_code": None,
            "degraded": False,
        }
        self._log_event(
            request_id=request_id,
            intent_id=intent_id,
            status=STATUS_OK,
            reason_code=None,
            detail="embedding generated",
            payload={
                "text_length": len(normalized),
                "embedding_dim": actual_dim,
                "expected_dim": int(route.dim),
                "provider": route.provider,
                "route_id": route.route_id,
                "node_selector": route.node_selector,
                "latency_ms": latency_ms,
                "output_status": STATUS_OK,
            },
            ts_ms=now_value,
        )
        return EmbeddingResult(
            success=True,
            status=STATUS_OK,
            reason_code=None,
            stage=S_RET_EMBEDDING,
            detail="embedding generated",
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
        expected_dim: int,
        vector: list[float],
        text: str,
        node_selector: str,
        ts_ms: int,
    ) -> EmbeddingResult:
        output = {
            "embedding_vector": list(vector),
            "embedding_model_id": str(model_id),
            "provider": str(provider),
            "route_id": str(route_id),
            "node_id": self.node_id,
            "node_selector": str(node_selector),
            "expected_dim": int(expected_dim),
            "actual_dim": len(vector),
            "latency_ms": int(latency_ms),
            "status": STATUS_DEGRADED,
            "stage": S_RET_EMBEDDING,
            "reason_code": reason_code,
            "degraded": True,
        }
        self._log_event(
            request_id=request_id,
            intent_id=intent_id,
            status=STATUS_DEGRADED,
            reason_code=reason_code,
            detail=detail,
            payload={
                "text_length": len(text),
                "embedding_dim": len(vector),
                "expected_dim": int(expected_dim),
                "provider": str(provider),
                "route_id": str(route_id),
                "node_selector": str(node_selector),
                "latency_ms": int(latency_ms),
                "output_status": STATUS_DEGRADED,
            },
            ts_ms=ts_ms,
        )
        return EmbeddingResult(
            success=False,
            status=STATUS_DEGRADED,
            reason_code=reason_code,
            stage=S_RET_EMBEDDING,
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
                    "op_kind": RET_OP_EMBEDDING,
                    "status": status,
                    "reason_code": reason_code,
                    "stage": S_RET_EMBEDDING,
                    "payload": payload,
                    "detail": detail,
                    "ts_ms": ts_ms,
                }
            )
            return

        if self.ledger is not None and hasattr(self.ledger, "log_error") and reason_code is not None:
            self.ledger.log_error(
                request_id=request_id,
                stage=S_RET_EMBEDDING,
                reason_code=reason_code,
                detail=detail,
            )


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
    if default_route and default_route not in routes:
        issues.append(
            EmbeddingConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail=f"default route not found in routes: {default_route}",
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
            stage=S_RET_EMBED_CONFIG,
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
        stage=S_RET_EMBED_CONFIG,
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
        DEFAULT_EMBED_PROVIDER: lambda text, _route=None: embed_text(text),
    }
    embed_device = str(merged.get("RETR_EMBED_DEVICE") or "cpu").strip() or "cpu"
    registry["sentence_transformers"] = _build_sentence_transformers_embedding_provider(device=embed_device)
    resolved_key = str(openai_api_key or merged.get("OPENAI_API_KEY") or "").strip()
    if resolved_key:
        registry["openai"] = _build_openai_embedding_provider(api_key=resolved_key)
    return registry


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
    for raw_route_id, raw_route in route_dict.items():
        route_id = str(raw_route_id or "").strip()
        if not route_id:
            issues.append(
                EmbeddingConfigIssue(
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
    issues: list[EmbeddingConfigIssue],
) -> EmbeddingRouteConfig | None:
    if not isinstance(raw_route, Mapping):
        issues.append(
            EmbeddingConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field_name}.{route_id} must be object",
                field=f"{field_name}.{route_id}",
            )
        )
        return None

    provider = str(raw_route.get("provider") or "").strip()
    model_id = str(raw_route.get("model_id") or "").strip()
    node_selector = str(raw_route.get("node_selector") or "").strip()

    dim = _coerce_positive_int(
        raw_value=raw_route.get("dim"),
        field=f"{field_name}.{route_id}.dim",
        issues=issues,
    )
    timeout_ms = _coerce_positive_int(
        raw_value=raw_route.get("timeout_ms"),
        field=f"{field_name}.{route_id}.timeout_ms",
        issues=issues,
    )

    if not provider:
        issues.append(
            EmbeddingConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail=f"{field_name}.{route_id}.provider is required",
                field=f"{field_name}.{route_id}.provider",
            )
        )
    if not model_id:
        issues.append(
            EmbeddingConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail=f"{field_name}.{route_id}.model_id is required",
                field=f"{field_name}.{route_id}.model_id",
            )
        )
    if not node_selector:
        issues.append(
            EmbeddingConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail=f"{field_name}.{route_id}.node_selector is required",
                field=f"{field_name}.{route_id}.node_selector",
            )
        )
    if dim is None or timeout_ms is None or not provider or not model_id or not node_selector:
        return None

    return EmbeddingRouteConfig(
        route_id=route_id,
        provider=provider,
        model_id=model_id,
        dim=dim,
        timeout_ms=timeout_ms,
        node_selector=node_selector,
    )


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


def _build_openai_embedding_provider(*, api_key: str) -> EmbeddingProvider:
    key = str(api_key or "").strip()

    def _provider(text: str, route: EmbeddingRouteConfig | None = None) -> tuple[list[float], str]:
        if not key:
            raise RuntimeError("OPENAI_API_KEY missing for openai embedding provider")
        model_id = str(route.model_id).strip() if route is not None else "text-embedding-3-large"
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover - guarded by runtime dependency check
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
        except Exception as exc:  # pragma: no cover - guarded by runtime dependency check
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
