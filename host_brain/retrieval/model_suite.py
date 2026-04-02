from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

_DEFAULT_ROUTE = "host_default"
_DEFAULT_NODE_SELECTOR = "default,natsu-node-01"
_DEFAULT_DEVICE = "cuda"


@dataclass(frozen=True)
class RetrievalModelSuiteProfile:
    profile_id: str
    embed_provider: str
    embed_model_id: str
    embed_dim: int
    embed_timeout_ms: int
    rerank_provider: str
    rerank_model_id: str
    rerank_top_k: int
    rerank_timeout_ms: int
    default_route: str = _DEFAULT_ROUTE
    node_selector: str = _DEFAULT_NODE_SELECTOR
    device: str = _DEFAULT_DEVICE


_MODEL_SUITES: dict[str, RetrievalModelSuiteProfile] = {
    "qwen3_0_6b_local": RetrievalModelSuiteProfile(
        profile_id="qwen3_0_6b_local",
        embed_provider="sentence_transformers",
        embed_model_id="models/Qwen3-Embedding-0.6B",
        embed_dim=1024,
        embed_timeout_ms=20_000,
        rerank_provider="qwen3_reranker",
        rerank_model_id="models/Qwen3-Reranker-0.6B",
        rerank_top_k=8,
        rerank_timeout_ms=30_000,
    ),
}


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _coerce_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return int(default)
    return parsed if parsed > 0 else int(default)


def resolve_retrieval_model_suite(env: Mapping[str, Any] | None) -> RetrievalModelSuiteProfile | None:
    merged = dict(env or {})
    profile_id = str(merged.get("RETR_MODEL_SUITE_PROFILE") or "").strip()
    if not profile_id:
        return None
    base = _MODEL_SUITES.get(profile_id)
    if base is None:
        return None
    return RetrievalModelSuiteProfile(
        profile_id=base.profile_id,
        embed_provider=str(merged.get("RETR_MODEL_SUITE_EMBED_PROVIDER") or base.embed_provider).strip() or base.embed_provider,
        embed_model_id=str(merged.get("RETR_MODEL_SUITE_EMBED_MODEL_ID") or base.embed_model_id).strip() or base.embed_model_id,
        embed_dim=_coerce_positive_int(merged.get("RETR_MODEL_SUITE_EMBED_DIM"), default=base.embed_dim),
        embed_timeout_ms=_coerce_positive_int(merged.get("RETR_MODEL_SUITE_EMBED_TIMEOUT_MS"), default=base.embed_timeout_ms),
        rerank_provider=str(merged.get("RETR_MODEL_SUITE_RERANK_PROVIDER") or base.rerank_provider).strip() or base.rerank_provider,
        rerank_model_id=str(merged.get("RETR_MODEL_SUITE_RERANK_MODEL_ID") or base.rerank_model_id).strip() or base.rerank_model_id,
        rerank_top_k=_coerce_positive_int(merged.get("RETR_MODEL_SUITE_RERANK_TOP_K"), default=base.rerank_top_k),
        rerank_timeout_ms=_coerce_positive_int(merged.get("RETR_MODEL_SUITE_RERANK_TIMEOUT_MS"), default=base.rerank_timeout_ms),
        default_route=str(merged.get("RETR_MODEL_SUITE_DEFAULT_ROUTE") or base.default_route).strip() or base.default_route,
        node_selector=str(merged.get("RETR_MODEL_SUITE_NODE_SELECTOR") or base.node_selector).strip() or base.node_selector,
        device=str(merged.get("RETR_MODEL_SUITE_DEVICE") or base.device).strip() or base.device,
    )


def apply_retrieval_model_suite_env_defaults(env: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = dict(env or {})
    suite = resolve_retrieval_model_suite(merged)
    if suite is None:
        return merged

    force = _parse_bool(merged.get("RETR_MODEL_SUITE_FORCE"), default=False)

    def _set_default(key: str, value: Any) -> None:
        if force or key not in merged or str(merged.get(key) or "").strip() == "":
            merged[key] = value

    _set_default("RETR_EMBED_DEFAULT_ROUTE", suite.default_route)
    _set_default("RETR_EMBED_PROVIDER", suite.embed_provider)
    _set_default("RETR_EMBED_MODEL_ID", suite.embed_model_id)
    _set_default("RETR_EMBED_DIM", suite.embed_dim)
    _set_default("RETR_EMBED_TIMEOUT_MS", suite.embed_timeout_ms)
    _set_default("RETR_EMBED_NODE_SELECTOR", suite.node_selector)
    _set_default("RETR_EMBED_DEVICE", suite.device)

    _set_default("RETR_RERANK_DEFAULT_ROUTE", suite.default_route)
    _set_default("RETR_RERANK_PROVIDER", suite.rerank_provider)
    _set_default("RETR_RERANK_MODEL_ID", suite.rerank_model_id)
    _set_default("RETR_RERANK_TOP_K", suite.rerank_top_k)
    _set_default("RETR_RERANK_TIMEOUT_MS", suite.rerank_timeout_ms)
    _set_default("RETR_RERANK_NODE_SELECTOR", suite.node_selector)
    _set_default("RETR_RERANK_DEVICE", suite.device)

    if force or not str(merged.get("RETR_EMBED_ROUTES_JSON") or "").strip():
        merged["RETR_EMBED_ROUTES_JSON"] = json.dumps(
            {
                str(merged.get("RETR_EMBED_DEFAULT_ROUTE") or suite.default_route): {
                    "provider": str(merged.get("RETR_EMBED_PROVIDER") or suite.embed_provider),
                    "model_id": str(merged.get("RETR_EMBED_MODEL_ID") or suite.embed_model_id),
                    "dim": int(merged.get("RETR_EMBED_DIM") or suite.embed_dim),
                    "timeout_ms": int(merged.get("RETR_EMBED_TIMEOUT_MS") or suite.embed_timeout_ms),
                    "node_selector": str(merged.get("RETR_EMBED_NODE_SELECTOR") or suite.node_selector),
                }
            },
            ensure_ascii=False,
        )

    if force or not str(merged.get("RETR_RERANK_ROUTES_JSON") or "").strip():
        merged["RETR_RERANK_ROUTES_JSON"] = json.dumps(
            {
                str(merged.get("RETR_RERANK_DEFAULT_ROUTE") or suite.default_route): {
                    "provider": str(merged.get("RETR_RERANK_PROVIDER") or suite.rerank_provider),
                    "model_id": str(merged.get("RETR_RERANK_MODEL_ID") or suite.rerank_model_id),
                    "top_k": int(merged.get("RETR_RERANK_TOP_K") or suite.rerank_top_k),
                    "timeout_ms": int(merged.get("RETR_RERANK_TIMEOUT_MS") or suite.rerank_timeout_ms),
                    "node_selector": str(merged.get("RETR_RERANK_NODE_SELECTOR") or suite.node_selector),
                }
            },
            ensure_ascii=False,
        )

    merged["RETR_MODEL_SUITE_EFFECTIVE_PROFILE"] = suite.profile_id
    return merged
