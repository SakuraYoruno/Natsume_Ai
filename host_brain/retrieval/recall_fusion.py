"""Multi-route recall fusion contract over Chroma candidates."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Mapping

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    CHROMA_COLLECTION_MISSING,
    CHROMA_ENDPOINT_UNAVAILABLE,
    CHROMA_VECTOR_DIM_MISMATCH,
    CONFIG_REQUIRED_MISSING,
    CONFIG_VALUE_INVALID,
    RETR_RECALL_INPUT_INVALID,
    RETR_RECALL_UNAVAILABLE,
)
from host_brain.core.ids import now_ms
from host_brain.memory.chroma_client import InMemoryChromaClient


S_RET_RECALL = "S_RET_RECALL"
S_RET_RECALL_CONFIG = "S_RET_RECALL_CONFIG"
RET_OP_RECALL = "RECALL"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"

DEFAULT_RECALL_ROUTE = "default"
DEFAULT_RECALL_COLLECTION = "episodic"
DEFAULT_RECALL_TOP_K = 8
DEFAULT_RECALL_SCORE_WEIGHT = 1.0
DEFAULT_NODE_SELECTOR = "default"


@dataclass(frozen=True)
class RecallRouteConfig:
    route_id: str
    collection: str
    top_k: int
    score_weight: float
    node_selector: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "collection": self.collection,
            "top_k": int(self.top_k),
            "score_weight": float(self.score_weight),
            "node_selector": self.node_selector,
        }


@dataclass(frozen=True)
class RecallRuntimeConfig:
    enabled: bool
    default_route: str
    routes: dict[str, RecallRouteConfig]

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
class RecallConfigIssue:
    reason_code: str
    detail: str
    field: str | None = None


@dataclass(frozen=True)
class RecallConfigLoadResult:
    success: bool
    stage: str
    reason_code: str | None
    config: RecallRuntimeConfig | None
    issues: tuple[RecallConfigIssue, ...]

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
class RecallFusionResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]


class RecallFusionServiceContract:
    """Recall candidates from multiple routes and fuse into a stable list."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        chroma_client: InMemoryChromaClient | None = None,
        runtime_config: RecallRuntimeConfig | None = None,
        node_id: str | None = None,
        available: bool = True,
    ) -> None:
        self.ledger = ledger
        self.chroma = chroma_client or InMemoryChromaClient()
        self.runtime_config = runtime_config or _default_runtime_config()
        self.node_id = str(node_id).strip() if node_id is not None else None
        self.available = bool(available)

    def recall(
        self,
        *,
        request_id: str,
        query: str,
        query_embedding: list[float] | tuple[float, ...] | Any,
        intent_id: str | None = None,
        ts_ms: int | None = None,
    ) -> RecallFusionResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        normalized_query = str(query or "").strip()
        normalized_vector = _normalize_vector(query_embedding)

        routes = resolve_recall_routes(self.runtime_config, node_id=self.node_id)
        route_rows: list[dict[str, Any]] = []
        route_errors: list[dict[str, Any]] = []

        if not normalized_query or not normalized_vector:
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                query=normalized_query,
                routes=routes,
                candidates=[],
                route_rows=[],
                route_errors=[],
                reason_code=RETR_RECALL_INPUT_INVALID,
                detail="recall input invalid: query and query_embedding are required",
                ts_ms=now_value,
            )

        if not self.runtime_config.enabled:
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                query=normalized_query,
                routes=routes,
                candidates=[],
                route_rows=[],
                route_errors=[],
                reason_code=RETR_RECALL_UNAVAILABLE,
                detail="recall runtime disabled by config",
                ts_ms=now_value,
            )

        if not self.available:
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                query=normalized_query,
                routes=routes,
                candidates=[],
                route_rows=[],
                route_errors=[],
                reason_code=RETR_RECALL_UNAVAILABLE,
                detail="recall service unavailable",
                ts_ms=now_value,
            )

        fused_candidates: dict[str, dict[str, Any]] = {}
        for route in routes:
            try:
                rows = self.chroma.search(
                    query_embedding=normalized_vector,
                    top_k=int(route.top_k),
                    collection=route.collection,
                )
            except Exception as exc:
                reason_code = _map_route_exception(exc)
                route_error = {
                    "route_id": route.route_id,
                    "collection": route.collection,
                    "reason_code": reason_code,
                    "detail": str(exc),
                }
                route_errors.append(route_error)
                route_rows.append(
                    {
                        "route_id": route.route_id,
                        "collection": route.collection,
                        "status": STATUS_DEGRADED,
                        "reason_code": reason_code,
                        "detail": str(exc),
                        "candidate_count": 0,
                    }
                )
                continue

            route_rows.append(
                {
                    "route_id": route.route_id,
                    "collection": route.collection,
                    "status": STATUS_OK,
                    "reason_code": None,
                    "detail": "recall route completed",
                    "candidate_count": len(rows),
                }
            )

            for rank, raw in enumerate(rows):
                candidate = _normalize_candidate(raw=raw, route=route, rank=rank)
                if not candidate:
                    continue
                key = str(candidate["source_ref"] or candidate["doc_id"])
                existing = fused_candidates.get(key)
                if existing is None:
                    fused_candidates[key] = candidate
                    continue
                if float(candidate["score"]) > float(existing["score"]):
                    merged = dict(candidate)
                else:
                    merged = dict(existing)
                merged_routes = sorted(
                    set(existing["metadata"].get("matched_routes", []))
                    .union(set(candidate["metadata"].get("matched_routes", [])))
                )
                merged["metadata"] = dict(merged["metadata"])
                merged["metadata"]["matched_routes"] = merged_routes
                fused_candidates[key] = merged

        candidates = sorted(
            fused_candidates.values(),
            key=lambda row: (-float(row["score"]), str(row["doc_id"]), str(row["source_ref"])),
        )

        if not candidates:
            reason_code = route_errors[0]["reason_code"] if route_errors else RETR_RECALL_UNAVAILABLE
            detail = (
                route_errors[0]["detail"]
                if route_errors
                else "no recall candidates from configured routes"
            )
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                query=normalized_query,
                routes=routes,
                candidates=[],
                route_rows=route_rows,
                route_errors=route_errors,
                reason_code=reason_code,
                detail=detail,
                ts_ms=now_value,
            )

        degraded = len(route_errors) > 0
        reason_code = route_errors[0]["reason_code"] if degraded else None
        status = STATUS_DEGRADED if degraded else STATUS_OK
        output = {
            "request_id": request_id,
            "intent_id": intent_id,
            "query": normalized_query,
            "node_id": self.node_id,
            "status": status,
            "stage": S_RET_RECALL,
            "reason_code": reason_code,
            "degraded": degraded,
            "routes": [route.as_dict() for route in routes],
            "route_rows": route_rows,
            "route_errors": route_errors,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "ts_ms": now_value,
        }
        detail = "multi-route recall fused"
        self._log_event(
            request_id=request_id,
            intent_id=intent_id,
            status=status,
            reason_code=reason_code,
            detail=detail,
            payload={
                "query_length": len(normalized_query),
                "route_count": len(routes),
                "route_error_count": len(route_errors),
                "candidate_count": len(candidates),
                "output_status": status,
            },
            ts_ms=now_value,
        )
        return RecallFusionResult(
            success=not degraded,
            status=status,
            reason_code=reason_code,
            stage=S_RET_RECALL,
            detail=detail if not degraded else "multi-route recall fused with partial degradation",
            output=output,
        )

    def _degrade(
        self,
        *,
        request_id: str,
        intent_id: str | None,
        query: str,
        routes: list[RecallRouteConfig],
        candidates: list[dict[str, Any]],
        route_rows: list[dict[str, Any]],
        route_errors: list[dict[str, Any]],
        reason_code: str,
        detail: str,
        ts_ms: int,
    ) -> RecallFusionResult:
        output = {
            "request_id": request_id,
            "intent_id": intent_id,
            "query": query,
            "node_id": self.node_id,
            "status": STATUS_DEGRADED,
            "stage": S_RET_RECALL,
            "reason_code": reason_code,
            "degraded": True,
            "routes": [route.as_dict() for route in routes],
            "route_rows": route_rows,
            "route_errors": route_errors,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "ts_ms": ts_ms,
        }
        self._log_event(
            request_id=request_id,
            intent_id=intent_id,
            status=STATUS_DEGRADED,
            reason_code=reason_code,
            detail=detail,
            payload={
                "query_length": len(query),
                "route_count": len(routes),
                "route_error_count": len(route_errors),
                "candidate_count": len(candidates),
                "output_status": STATUS_DEGRADED,
            },
            ts_ms=ts_ms,
        )
        return RecallFusionResult(
            success=False,
            status=STATUS_DEGRADED,
            reason_code=reason_code,
            stage=S_RET_RECALL,
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
                    "op_kind": RET_OP_RECALL,
                    "status": status,
                    "reason_code": reason_code,
                    "stage": S_RET_RECALL,
                    "payload": payload,
                    "detail": detail,
                    "ts_ms": ts_ms,
                }
            )
            return

        if self.ledger is not None and hasattr(self.ledger, "log_error") and reason_code is not None:
            self.ledger.log_error(
                request_id=request_id,
                stage=S_RET_RECALL,
                reason_code=reason_code,
                detail=detail,
            )


def load_recall_runtime_config(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
) -> RecallConfigLoadResult:
    merged: dict[str, Any] = dict(env or os.environ)
    if local_overrides:
        merged.update(dict(local_overrides))

    issues: list[RecallConfigIssue] = []
    enabled = _parse_bool(merged.get("RETR_RECALL_ENABLED"), default=True)
    default_route = str(merged.get("RETR_RECALL_DEFAULT_ROUTE") or DEFAULT_RECALL_ROUTE).strip()
    routes = _parse_routes(merged=merged, default_route=default_route, issues=issues)

    if not default_route:
        issues.append(
            RecallConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail="RETR_RECALL_DEFAULT_ROUTE is required",
                field="RETR_RECALL_DEFAULT_ROUTE",
            )
        )
    if default_route and default_route not in routes:
        issues.append(
            RecallConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail=f"default route not found in routes: {default_route}",
                field="RETR_RECALL_DEFAULT_ROUTE",
            )
        )
    if not routes:
        issues.append(
            RecallConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail="recall routes are required",
                field="RETR_RECALL_ROUTES_JSON",
            )
        )

    if issues:
        return RecallConfigLoadResult(
            success=False,
            stage=S_RET_RECALL_CONFIG,
            reason_code=issues[0].reason_code,
            config=None,
            issues=tuple(issues),
        )

    config = RecallRuntimeConfig(
        enabled=bool(enabled),
        default_route=default_route,
        routes=routes,
    )
    return RecallConfigLoadResult(
        success=True,
        stage=S_RET_RECALL_CONFIG,
        reason_code=None,
        config=config,
        issues=(),
    )


def resolve_recall_routes(config: RecallRuntimeConfig, *, node_id: str | None) -> list[RecallRouteConfig]:
    if node_id is not None and str(node_id).strip():
        node = str(node_id).strip()
        matched = [
            route
            for route_id, route in sorted(config.routes.items())
            if _node_selector_match(route.node_selector, node)
        ]
        if matched:
            return matched
    return [config.routes[config.default_route]]


def _parse_routes(
    *,
    merged: Mapping[str, Any],
    default_route: str,
    issues: list[RecallConfigIssue],
) -> dict[str, RecallRouteConfig]:
    routes_raw = merged.get("RETR_RECALL_ROUTES_JSON")
    if routes_raw is None or not str(routes_raw).strip():
        fallback = {
            default_route: {
                "collection": str(merged.get("RETR_RECALL_COLLECTION") or DEFAULT_RECALL_COLLECTION),
                "top_k": merged.get("RETR_RECALL_TOP_K", DEFAULT_RECALL_TOP_K),
                "score_weight": merged.get("RETR_RECALL_SCORE_WEIGHT", DEFAULT_RECALL_SCORE_WEIGHT),
                "node_selector": str(merged.get("RETR_RECALL_NODE_SELECTOR") or DEFAULT_NODE_SELECTOR),
            }
        }
        return _coerce_routes(route_dict=fallback, field_name="RETR_RECALL_ROUTES_JSON", issues=issues)

    try:
        payload = json.loads(str(routes_raw))
    except json.JSONDecodeError:
        issues.append(
            RecallConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail="RETR_RECALL_ROUTES_JSON must be JSON object mapping route->config",
                field="RETR_RECALL_ROUTES_JSON",
            )
        )
        return {}
    return _coerce_routes(route_dict=payload, field_name="RETR_RECALL_ROUTES_JSON", issues=issues)


def _coerce_routes(
    *,
    route_dict: Any,
    field_name: str,
    issues: list[RecallConfigIssue],
) -> dict[str, RecallRouteConfig]:
    if not isinstance(route_dict, dict):
        issues.append(
            RecallConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field_name} must be JSON object mapping route->config",
                field=field_name,
            )
        )
        return {}

    routes: dict[str, RecallRouteConfig] = {}
    for raw_route_id, raw_route in route_dict.items():
        route_id = str(raw_route_id or "").strip()
        if not route_id:
            issues.append(
                RecallConfigIssue(
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
    issues: list[RecallConfigIssue],
) -> RecallRouteConfig | None:
    if not isinstance(raw_route, Mapping):
        issues.append(
            RecallConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field_name}.{route_id} must be object",
                field=f"{field_name}.{route_id}",
            )
        )
        return None

    collection = str(raw_route.get("collection") or "").strip()
    node_selector = str(raw_route.get("node_selector") or "").strip()
    top_k = _coerce_positive_int(
        raw_value=raw_route.get("top_k"),
        field=f"{field_name}.{route_id}.top_k",
        issues=issues,
    )
    score_weight = _coerce_positive_float(
        raw_value=raw_route.get("score_weight"),
        field=f"{field_name}.{route_id}.score_weight",
        issues=issues,
    )

    if not collection:
        issues.append(
            RecallConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail=f"{field_name}.{route_id}.collection is required",
                field=f"{field_name}.{route_id}.collection",
            )
        )
    if not node_selector:
        issues.append(
            RecallConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail=f"{field_name}.{route_id}.node_selector is required",
                field=f"{field_name}.{route_id}.node_selector",
            )
        )
    if top_k is None or score_weight is None or not collection or not node_selector:
        return None

    return RecallRouteConfig(
        route_id=route_id,
        collection=collection,
        top_k=top_k,
        score_weight=score_weight,
        node_selector=node_selector,
    )


def _normalize_candidate(
    *,
    raw: Mapping[str, Any],
    route: RecallRouteConfig,
    rank: int,
) -> dict[str, Any] | None:
    memory_id = str(raw.get("memory_id") or "").strip()
    if not memory_id:
        return None
    text = str(raw.get("text") or "").strip()
    metadata = dict(raw.get("metadata") or {}) if isinstance(raw.get("metadata"), Mapping) else {}

    base_score = _coerce_score(raw.get("score"))
    weighted_score = round(base_score * float(route.score_weight), 6)
    source_ref = str(metadata.get("source_ref") or f"memory://{route.collection}/{memory_id}")
    evidence_ref = str(metadata.get("evidence_ref") or f"evd://{route.collection}/{memory_id}")
    merged_metadata = dict(metadata)
    merged_metadata["collection"] = route.collection
    merged_metadata["route_id"] = route.route_id
    merged_metadata["route_score_weight"] = float(route.score_weight)
    merged_metadata["base_score"] = base_score
    merged_metadata["matched_routes"] = [route.route_id]
    merged_metadata["recall_rank"] = int(rank)

    return {
        "doc_id": memory_id,
        "source_ref": source_ref,
        "snippet": text,
        "score": weighted_score,
        "confidence": weighted_score,
        "metadata": merged_metadata,
        "evidence_ref": evidence_ref,
    }


def _coerce_score(raw_value: Any) -> float:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(1.0, value)), 6)


def _map_route_exception(exc: Exception) -> str:
    text = str(exc)
    if CHROMA_COLLECTION_MISSING in text:
        return CHROMA_COLLECTION_MISSING
    if CHROMA_VECTOR_DIM_MISMATCH in text:
        return CHROMA_VECTOR_DIM_MISMATCH
    return CHROMA_ENDPOINT_UNAVAILABLE


def _default_runtime_config() -> RecallRuntimeConfig:
    route = RecallRouteConfig(
        route_id=DEFAULT_RECALL_ROUTE,
        collection=DEFAULT_RECALL_COLLECTION,
        top_k=DEFAULT_RECALL_TOP_K,
        score_weight=DEFAULT_RECALL_SCORE_WEIGHT,
        node_selector=DEFAULT_NODE_SELECTOR,
    )
    return RecallRuntimeConfig(
        enabled=True,
        default_route=DEFAULT_RECALL_ROUTE,
        routes={DEFAULT_RECALL_ROUTE: route},
    )


def _coerce_positive_int(
    *,
    raw_value: Any,
    field: str,
    issues: list[RecallConfigIssue],
) -> int | None:
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        issues.append(
            RecallConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field} must be integer > 0",
                field=field,
            )
        )
        return None
    if value <= 0:
        issues.append(
            RecallConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field} must be integer > 0",
                field=field,
            )
        )
        return None
    return value


def _coerce_positive_float(
    *,
    raw_value: Any,
    field: str,
    issues: list[RecallConfigIssue],
) -> float | None:
    try:
        value = float(str(raw_value).strip())
    except (TypeError, ValueError):
        issues.append(
            RecallConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field} must be float > 0",
                field=field,
            )
        )
        return None
    if value <= 0.0:
        issues.append(
            RecallConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field} must be float > 0",
                field=field,
            )
        )
        return None
    return float(value)


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
