"""Multi-route recall fusion contract over Chroma candidates."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import sqlite3
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

# 混合检索默认权重（TASK-0202）
DEFAULT_VECTOR_WEIGHT: float = 0.7
DEFAULT_FTS_WEIGHT: float = 0.3


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
        use_hybrid: bool = True,
        vector_weight: float = DEFAULT_VECTOR_WEIGHT,
        fts_weight: float = DEFAULT_FTS_WEIGHT,
        fts_db_path: str | None = None,
    ) -> None:
        self.ledger = ledger
        self.chroma = chroma_client or InMemoryChromaClient()
        self.runtime_config = runtime_config or _default_runtime_config()
        self.node_id = str(node_id).strip() if node_id is not None else None
        self.available = bool(available)
        self.use_hybrid = bool(use_hybrid)
        self.vector_weight = float(vector_weight)
        self.fts_weight = float(fts_weight)
        # 初始化 FTS5 检索器（如果启用混合检索）
        self._fts_searcher: FullTextSearcher | None = None
        if self.use_hybrid:
            try:
                self._fts_searcher = FullTextSearcher(db_path=fts_db_path)
            except Exception:
                # FTS5 不可用时降级为纯向量检索
                self._fts_searcher = None

    def recall(
        self,
        *,
        request_id: str,
        query: str,
        query_embedding: list[float] | tuple[float, ...] | Any,
        intent_id: str | None = None,
        ts_ms: int | None = None,
        emotion_filter: str | None = None,
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

        # 混合检索：叠加 FTS5 全文检索结果（TASK-0202）
        if self.use_hybrid and self._fts_searcher is not None:
            candidates = self._apply_hybrid_fusion(
                vector_candidates=candidates,
                query=normalized_query,
            )

        # M27-G：情感维度路由（OPT-31）：emotion_filter 非空时，优先召回匹配情感标签的记录
        if emotion_filter is not None and candidates:
            matched = [
                c for c in candidates
                if c.get("metadata", {}).get("emotion_tag") == emotion_filter
            ]
            unmatched = [
                c for c in candidates
                if c.get("metadata", {}).get("emotion_tag") != emotion_filter
            ]
            candidates = matched + unmatched

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


# ── FullTextSearcher（TASK-0202）────────────────────────────────────────────

class FullTextSearcher:
    """基于 SQLite FTS5 的全文检索器。

    使用 episodic_fts 虚拟表（由 0021_episodic_fts5.sql 迁移创建）进行全文检索。
    支持中文字符边界分割（unicode61 tokenizer）。

    TASK-0210 M23-A：修复中文词组检索失效问题。
    unicode61 tokenizer 按单字分词，多字中文词组（如"猫咪"）无法作为整体 token 命中。
    修复方案：对中文字符进行字符级分割，写入和查询时均使用空格分隔的单字序列，
    使 FTS5 MATCH 语法能正确匹配中文内容。
    """

    def __init__(self, *, db_path: str | None = None) -> None:
        """初始化 FTS5 检索器。

        Args:
            db_path: SQLite 数据库路径。None 时使用内存数据库（仅用于测试）。

        Raises:
            RuntimeError: 如果 FTS5 虚拟表不可用。
        """
        self._db_path = db_path or ":memory:"
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_fts_table()

    def _ensure_fts_table(self) -> None:
        """确保 episodic_fts 虚拟表存在（若不存在则创建）。"""
        try:
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS episodic_fts USING fts5(
                    memory_id UNINDEXED,
                    content,
                    tokenize = "unicode61"
                )
                """
            )
            self._conn.commit()
        except sqlite3.OperationalError as exc:
            raise RuntimeError(f"FTS5 虚拟表创建失败（SQLite 可能不支持 FTS5）: {exc}") from exc

    @staticmethod
    def _split_chinese_query(text: str) -> str:
        """将文本中的中文字符进行字符级分割，非中文词保留原样。

        TASK-0210 M23-A：unicode61 tokenizer 对中文按单字分词，
        因此查询词"猫咪"需要拆分为"猫 咪"才能命中索引中的单字 token。
        非中文词（英文、数字等）保留原词，不做拆分。

        Args:
            text: 原始查询文本。

        Returns:
            处理后的 FTS5 查询字符串，中文字符以空格分隔。

        Examples:
            >>> FullTextSearcher._split_chinese_query("猫咪")
            '猫 咪'
            >>> FullTextSearcher._split_chinese_query("工作压力大")
            '工 作 压 力 大'
            >>> FullTextSearcher._split_chinese_query("hello world")
            'hello world'
            >>> FullTextSearcher._split_chinese_query("用户喜欢cats")
            '用 户 喜 欢 cats'
        """
        if not text:
            return text
        tokens: list[str] = []
        current_non_chinese: list[str] = []
        for ch in text:
            # 判断是否为中文字符（CJK 统一汉字基本区及扩展区）
            cp = ord(ch)
            is_chinese = (
                0x4E00 <= cp <= 0x9FFF   # CJK 统一汉字
                or 0x3400 <= cp <= 0x4DBF  # CJK 扩展 A
                or 0x20000 <= cp <= 0x2A6DF  # CJK 扩展 B
                or 0x2A700 <= cp <= 0x2CEAF  # CJK 扩展 C/D/E
                or 0xF900 <= cp <= 0xFAFF   # CJK 兼容汉字
            )
            if is_chinese:
                # 先把积累的非中文词 flush
                if current_non_chinese:
                    tokens.append("".join(current_non_chinese).strip())
                    current_non_chinese = []
                # 中文字符单独作为一个 token
                tokens.append(ch)
            else:
                current_non_chinese.append(ch)
        # flush 剩余非中文
        if current_non_chinese:
            tokens.append("".join(current_non_chinese).strip())
        # 过滤空串，用空格连接
        return " ".join(t for t in tokens if t)

    @staticmethod
    def _prepare_content_for_index(content: str) -> str:
        """将文本内容预处理为适合 FTS5 unicode61 索引的格式。

        中文字符进行字符级分割，确保写入索引时与查询时的分词策略一致。

        Args:
            content: 原始文本内容。

        Returns:
            处理后的文本，中文字符以空格分隔。
        """
        return FullTextSearcher._split_chinese_query(content)

    def index_memory(self, *, memory_id: str, content: str) -> None:
        """将一条 Episodic 记忆写入 FTS5 索引。

        TASK-0210 M23-A：写入时对中文内容进行字符级分割，
        确保索引与查询时的分词策略一致。

        Args:
            memory_id: 记忆唯一 ID。
            content: 记忆文本内容。
        """
        # 对中文内容进行字符级分割，与查询时保持一致
        indexed_content = self._prepare_content_for_index(str(content or ""))
        # 先删除旧记录（幂等写入）
        self._conn.execute("DELETE FROM episodic_fts WHERE memory_id = ?", (memory_id,))
        self._conn.execute(
            "INSERT INTO episodic_fts(memory_id, content) VALUES (?, ?)",
            (memory_id, indexed_content),
        )
        self._conn.commit()

    def search(self, query: str, *, top_k: int = 8) -> list[dict[str, Any]]:
        """全文检索，返回匹配的记忆列表。

        TASK-0210 M23-A：对中文查询词进行字符级分割，
        使 FTS5 MATCH 语法能正确匹配 unicode61 单字索引。

        Args:
            query: 检索查询文本。
            top_k: 最多返回条数。

        Returns:
            候选列表，每条含 doc_id / snippet / fts_score（归一化到 [0, 1]）。
        """
        if not str(query or "").strip():
            return []
        # 对中文查询词进行字符级分割，与索引写入时保持一致
        fts_query = self._split_chinese_query(str(query).strip())
        if not fts_query:
            return []
        try:
            # FTS5 使用 bm25() 函数计算相关性分数（负值，越小越相关）
            rows = self._conn.execute(
                """
                SELECT memory_id, content, bm25(episodic_fts) AS bm25_score
                FROM episodic_fts
                WHERE episodic_fts MATCH ?
                ORDER BY bm25_score
                LIMIT ?
                """,
                (fts_query, int(top_k)),
            ).fetchall()
        except sqlite3.OperationalError:
            # FTS5 查询语法错误或表不存在时降级返回空
            return []

        if not rows:
            return []

        # 将 bm25 负分归一化到 [0, 1]（最相关 → 1.0）
        raw_scores = [float(row["bm25_score"]) for row in rows]
        min_score = min(raw_scores)
        max_score = max(raw_scores)
        score_range = abs(max_score - min_score)

        results: list[dict[str, Any]] = []
        for row, raw_score in zip(rows, raw_scores):
            if score_range < 1e-9:
                # 所有结果分数相同（含单条命中），均视为最相关 → 1.0
                normalized = 1.0
            else:
                # bm25 分数为负，越小（绝对值越大）越相关
                # 最小分（最相关）→ 1.0，最大分（最不相关）→ 0.0
                normalized = round((raw_score - max_score) / (-score_range), 6)
                normalized = max(0.0, min(1.0, normalized))
            results.append({
                "doc_id": str(row["memory_id"]),
                "snippet": str(row["content"])[:512],
                "fts_score": normalized,
            })
        return results

    def close(self) -> None:
        """关闭数据库连接。"""
        try:
            self._conn.close()
        except Exception:
            pass


# ── HybridFusionStrategy（TASK-0202）────────────────────────────────────────

class HybridFusionStrategy:
    """向量检索 + 全文检索混合融合策略。

    融合公式：final_score = vector_score × vector_weight + fts_score × fts_weight
    要求：vector_weight + fts_weight == 1.0（否则抛出 ValueError）。
    """

    def __init__(
        self,
        *,
        vector_weight: float = DEFAULT_VECTOR_WEIGHT,
        fts_weight: float = DEFAULT_FTS_WEIGHT,
    ) -> None:
        """初始化混合融合策略。

        Args:
            vector_weight: 向量检索权重（默认 0.7）。
            fts_weight: 全文检索权重（默认 0.3）。

        Raises:
            ValueError: 如果 vector_weight + fts_weight != 1.0。
        """
        total = round(float(vector_weight) + float(fts_weight), 6)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"vector_weight + fts_weight 必须等于 1.0，当前为 {total}"
            )
        self.vector_weight = float(vector_weight)
        self.fts_weight = float(fts_weight)

    def fuse(
        self,
        *,
        vector_candidates: list[dict[str, Any]],
        fts_candidates: list[dict[str, Any]],
        # M29-O（OPT-?）：情感强度加权
        emotion_context: str | None = None,
        emotion_boost_factor: float = 1.2,
    ) -> list[dict[str, Any]]:
        """融合向量检索和全文检索结果。

        M29-O（OPT-?）：支持情感强度加权——emotion_context 匹配的候选项分数加权。

        Args:
            vector_candidates: 向量检索候选列表（含 doc_id / score 字段）。
            fts_candidates: 全文检索候选列表（含 doc_id / fts_score 字段）。
            emotion_context: 当前情感上下文标签（如 "HAPPY"），匹配时加权
            emotion_boost_factor: 情感匹配加权系数（默认 1.2x）

        Returns:
            融合后的候选列表，按 final_score 降序排列，同 doc_id 去重保留最高分。
        """
        # 构建 doc_id → 候选 映射
        fused: dict[str, dict[str, Any]] = {}

        # 处理向量检索结果
        for cand in vector_candidates:
            doc_id = str(cand.get("doc_id") or "")
            if not doc_id:
                continue
            vector_score = float(cand.get("score") or 0.0)
            entry = dict(cand)
            entry["vector_score"] = vector_score
            entry["fts_score"] = 0.0
            entry["score"] = round(vector_score * self.vector_weight, 6)
            fused[doc_id] = entry

        # 处理全文检索结果，与向量结果融合
        for fts_cand in fts_candidates:
            doc_id = str(fts_cand.get("doc_id") or "")
            if not doc_id:
                continue
            fts_score = float(fts_cand.get("fts_score") or 0.0)
            if doc_id in fused:
                # 已有向量结果，叠加全文分数
                existing = fused[doc_id]
                vector_score = float(existing.get("vector_score") or 0.0)
                final_score = round(
                    vector_score * self.vector_weight + fts_score * self.fts_weight,
                    6,
                )
                existing["fts_score"] = fts_score
                existing["score"] = final_score
                # 更新 metadata 中的融合信息
                metadata = dict(existing.get("metadata") or {})
                metadata["fts_score"] = fts_score
                metadata["hybrid_fused"] = True
                existing["metadata"] = metadata
            else:
                # 仅全文命中，无向量结果
                snippet = str(fts_cand.get("snippet") or "")
                entry = {
                    "doc_id": doc_id,
                    "source_ref": f"memory://episodic/{doc_id}",
                    "snippet": snippet,
                    "score": round(fts_score * self.fts_weight, 6),
                    "confidence": round(fts_score * self.fts_weight, 6),
                    "vector_score": 0.0,
                    "fts_score": fts_score,
                    "metadata": {
                        "fts_score": fts_score,
                        "hybrid_fused": True,
                        "matched_routes": ["fts"],
                    },
                    "evidence_ref": f"evd://episodic/{doc_id}",
                }
                fused[doc_id] = entry

        # M29-O：情感强度加权——emotion_context 匹配的候选项分数加权
        if emotion_context:
            for entry in fused.values():
                meta = entry.get("metadata") or {}
                if meta.get("emotion_tag") == str(emotion_context).strip().upper():
                    entry["score"] = min(1.0, round(float(entry["score"]) * emotion_boost_factor, 6))

        # 按 final_score 降序排列
        return sorted(
            fused.values(),
            key=lambda row: (-float(row["score"]), str(row["doc_id"])),
        )


# ── RecallFusionServiceContract 混合检索方法（TASK-0202）────────────────────

def _apply_hybrid_fusion_impl(
    self: "RecallFusionServiceContract",
    *,
    vector_candidates: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    """在 RecallFusionServiceContract 中执行混合融合。

    Args:
        vector_candidates: 向量检索候选列表。
        query: 检索查询文本。

    Returns:
        融合后的候选列表（FTS5 不可用时返回原向量候选）。
    """
    if self._fts_searcher is None:
        return vector_candidates

    try:
        fts_results = self._fts_searcher.search(query, top_k=DEFAULT_RECALL_TOP_K)
    except Exception:
        # FTS5 检索失败时降级为纯向量
        return vector_candidates

    if not fts_results:
        return vector_candidates

    # 校验权重
    try:
        strategy = HybridFusionStrategy(
            vector_weight=self.vector_weight,
            fts_weight=self.fts_weight,
        )
    except ValueError:
        return vector_candidates

    return strategy.fuse(
        vector_candidates=vector_candidates,
        fts_candidates=fts_results,
    )


# 将混合融合方法绑定到 RecallFusionServiceContract
RecallFusionServiceContract._apply_hybrid_fusion = _apply_hybrid_fusion_impl  # type: ignore[attr-defined]
