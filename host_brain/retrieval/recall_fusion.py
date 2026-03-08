"""Multi-route recall fusion contract over Chroma candidates."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
import re
import sqlite3
from typing import Any, Mapping

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    CHROMA_COLLECTION_MISSING,
    CHROMA_ENDPOINT_UNAVAILABLE,
    CHROMA_VECTOR_DIM_MISMATCH,
    CONFIG_REQUIRED_MISSING,
    CONFIG_VALUE_INVALID,
    RETR_LEDGER_UNAVAILABLE,
    RETR_BOND_UNAVAILABLE,
    RETR_STYLE_UNAVAILABLE,
    RETR_WORLD_UNAVAILABLE,
    RETR_VAULT_UNAVAILABLE,
    RETR_RECALL_INPUT_INVALID,
    RETR_RECALL_UNAVAILABLE,
)
from host_brain.core.ids import now_ms
from host_brain.memory.chroma_client import InMemoryChromaClient
from host_brain.retrieval.recall_schemas import (
    RecallConfigIssue,
    RecallConfigLoadResult,
    RecallFusionResult,
    RecallRouteConfig,
    RecallRuntimeConfig,
)



S_RET_RECALL = "S_RET_RECALL"
S_RET_RECALL_CONFIG = "S_RET_RECALL_CONFIG"
RET_OP_RECALL = "RECALL"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"

logger = logging.getLogger("Natsume.retrieval.recall_fusion")

DEFAULT_RECALL_ROUTE = "default"
DEFAULT_RECALL_COLLECTION = "episodic"
DEFAULT_CANON_VECTOR_COLLECTION = "canon"
DEFAULT_RECALL_STORE_ID = "episodic_vector"
DEFAULT_RECALL_TOP_K = 8
DEFAULT_RECALL_SCORE_WEIGHT = 1.0
DEFAULT_NODE_SELECTOR = "default"
CANON_VECTOR_FETCH_FACTOR = 5
SQLITE_SESSION_BOOST = 0.25
SQLITE_RECENCY_BOOST_5M = 0.20
SQLITE_RECENCY_BOOST_1H = 0.10
SQLITE_RECENCY_BOOST_1D = 0.05
_SESSION_REF_PREFIX = "chat://session/"

STORE_SQLITE_LEDGER = "sqlite_ledger"
STORE_NEO4J_BOND = "neo4j_bond"
STORE_EPISODIC_VECTOR = "episodic_vector"
STORE_STYLE_CANON = "style_canon"
STORE_WORLD_CANON = "world_canon"
STORE_ARTIFACT_VAULT = "artifact_vault"

SUPPORTED_RECALL_STORE_IDS: frozenset[str] = frozenset(
    {
        STORE_SQLITE_LEDGER,
        STORE_NEO4J_BOND,
        STORE_EPISODIC_VECTOR,
        STORE_STYLE_CANON,
        STORE_WORLD_CANON,
        STORE_ARTIFACT_VAULT,
    }
)

# 混合检索默认权重（TASK-0202）
DEFAULT_VECTOR_WEIGHT: float = 0.7
DEFAULT_FTS_WEIGHT: float = 0.3


class RecallFusionServiceContract:
    """Recall candidates from multiple routes and fuse into a stable list."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        chroma_client: InMemoryChromaClient | None = None,
        neo4j_client: Any | None = None,
        style_store: Any | None = None,
        world_store: Any | None = None,
        artifact_vault: Any | None = None,
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
        self.neo4j = neo4j_client
        self.style_store = style_store
        self.world_store = world_store
        self.artifact_vault = artifact_vault
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

    def _search_route(
        self,
        *,
        route: RecallRouteConfig,
        query: str,
        query_embedding: list[float],
        session_id: str | None = None,
        now_ts_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        store_id = str(route.store_id or STORE_EPISODIC_VECTOR).strip().lower()
        if store_id == STORE_EPISODIC_VECTOR:
            collection = str(route.collection or DEFAULT_RECALL_COLLECTION)
            return self.chroma.search(
                query_embedding=query_embedding,
                top_k=int(route.top_k),
                collection=collection,
            )
        if store_id == STORE_SQLITE_LEDGER:
            return self._search_sqlite_ledger(
                query=query,
                top_k=int(route.top_k),
                session_id=session_id,
                now_ts_ms=now_ts_ms,
            )
        if store_id == STORE_NEO4J_BOND:
            return self._search_neo4j_bond(query=query, top_k=int(route.top_k))
        if store_id == STORE_STYLE_CANON:
            return self._search_style_canon(
                query=query,
                query_embedding=query_embedding,
                top_k=int(route.top_k),
                collection=route.collection,
            )
        if store_id == STORE_WORLD_CANON:
            return self._search_world_canon(
                query=query,
                query_embedding=query_embedding,
                top_k=int(route.top_k),
                collection=route.collection,
            )
        if store_id == STORE_ARTIFACT_VAULT:
            return self._search_artifact_vault(query=query, top_k=int(route.top_k))
        raise RuntimeError(f"unsupported recall store_id: {store_id}")

    def _search_sqlite_ledger(
        self,
        *,
        query: str,
        top_k: int,
        session_id: str | None = None,
        now_ts_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        if self.ledger is None:
            raise RuntimeError("sqlite ledger unavailable")
        current_ts = int(now_ts_ms if now_ts_ms is not None else now_ms())
        normalized_session_id = str(session_id or "").strip()
        scored: list[tuple[float, dict[str, Any]]] = []

        for row in list(self.ledger.intent_log):
            if not isinstance(row, Mapping):
                continue
            raw_text = str(row.get("raw_text") or "").strip()
            normalized = row.get("normalized_json")
            normalized_text = (
                json.dumps(normalized, ensure_ascii=False, sort_keys=True)
                if isinstance(normalized, Mapping)
                else str(normalized or "")
            )
            text = f"{raw_text}\n{normalized_text}".strip()
            score = _text_similarity_score(query, text)
            if score <= 0.0:
                continue
            row_session_id = str(row.get("session_id") or "").strip() or None
            row_ts = _coerce_non_negative_int(row.get("ts_ms"), default=0)
            boosted_score, boost_meta = _apply_sqlite_ledger_boost(
                base_score=score,
                row_ts_ms=row_ts,
                now_ts_ms=current_ts,
                row_session_id=row_session_id,
                target_session_id=normalized_session_id,
            )
            request_id = str(row.get("request_id") or "").strip() or None
            intent_id = str(row.get("intent_id") or "").strip() or None
            memory_id = str(intent_id or request_id or f"ledger_{len(scored)}")
            evidence_refs = _normalize_recall_refs(
                row.get("evidence_refs"),
                fallback=(f"intent://{intent_id}" if intent_id else None),
            )
            scored.append(
                (
                    boosted_score,
                    {
                        "memory_id": memory_id,
                        "text": _truncate_text_for_recall(text, limit=512),
                        "score": boosted_score,
                        "metadata": {
                            "store_id": STORE_SQLITE_LEDGER,
                            "request_id": request_id,
                            "intent_id": intent_id,
                            "session_id": row_session_id,
                            "event_id": (str(row.get("event_id") or "").strip() or None),
                            "base_score": score,
                            "score_boost": boost_meta,
                            "evidence_refs": evidence_refs,
                            "source_ref": f"ledger://intent_log/{memory_id}",
                            "evidence_refs_primary": evidence_refs[0] if evidence_refs else f"ledger://intent/{memory_id}",
                        },
                    },
                )
            )

        for row in list(self.ledger.memory_ops_log):
            if not isinstance(row, Mapping):
                continue
            op_kind = str(row.get("op_kind") or "").strip().upper()
            if op_kind != "WRITE":
                continue
            status = str(row.get("status") or "").strip().upper()
            if status and status != "SUCCESS":
                continue

            memory_id = str(row.get("memory_id") or "").strip() or f"memop_{len(scored)}"
            request_id = str(row.get("request_id") or "").strip() or None
            intent_id = str(row.get("intent_id") or "").strip() or None
            detail = str(row.get("detail") or "").strip()
            evidence_refs = _normalize_recall_refs(
                row.get("evidence_refs"),
                fallback=(f"memory://episodic/{memory_id}" if memory_id else None),
            )
            row_session_id = _extract_session_id_from_refs(evidence_refs)
            row_ts = _coerce_non_negative_int(row.get("ts_ms"), default=0)
            text = " ".join(
                part
                for part in (
                    detail,
                    memory_id,
                    " ".join(evidence_refs),
                )
                if part
            ).strip()
            score = _text_similarity_score(query, text)
            if score <= 0.0:
                continue
            boosted_score, boost_meta = _apply_sqlite_ledger_boost(
                base_score=score,
                row_ts_ms=row_ts,
                now_ts_ms=current_ts,
                row_session_id=row_session_id,
                target_session_id=normalized_session_id,
            )
            scored.append(
                (
                    boosted_score,
                    {
                        "memory_id": memory_id,
                        "text": _truncate_text_for_recall(text, limit=512),
                        "score": boosted_score,
                        "metadata": {
                            "store_id": STORE_SQLITE_LEDGER,
                            "request_id": request_id,
                            "intent_id": intent_id,
                            "session_id": row_session_id,
                            "op_kind": op_kind,
                            "status": status or None,
                            "base_score": score,
                            "score_boost": boost_meta,
                            "evidence_refs": evidence_refs,
                            "source_ref": f"ledger://memory_ops_log/{memory_id}",
                            "evidence_refs_primary": (
                                evidence_refs[0] if evidence_refs else f"memory://episodic/{memory_id}"
                            ),
                        },
                    },
                )
            )
        return [
            item
            for _score, item in sorted(
                scored,
                key=lambda pair: _sqlite_candidate_sort_key(score=pair[0], candidate=pair[1]),
            )[: max(1, int(top_k))]
        ]

    def _search_neo4j_bond(self, *, query: str, top_k: int) -> list[dict[str, Any]]:
        records: list[Mapping[str, Any]] = []
        try:
            events_attr = getattr(self.neo4j, "events", None)
            if isinstance(events_attr, list):
                records = [row for row in events_attr if isinstance(row, Mapping)]
        except Exception as exc:
            # Neo4j route is best-effort in online chat retrieval:
            # when graph store is unavailable, fallback to sqlite bond_update_log.
            logger.warning("neo4j recall route unavailable, fallback to sqlite bond log: %s", exc)
            records = []
        if not records and self.ledger is not None:
            records = [row for row in self.ledger.bond_update_log if isinstance(row, Mapping)]
        if not records:
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in records:
            payload = row.get("payload")
            payload_text = (
                json.dumps(payload, ensure_ascii=False, sort_keys=True)
                if isinstance(payload, Mapping)
                else str(payload or "")
            )
            text = " ".join(
                part for part in (
                    str(row.get("topic_id") or "").strip(),
                    str(row.get("capability_hint") or "").strip(),
                    payload_text,
                )
                if part
            )
            score = _text_similarity_score(query, text)
            if score <= 0.0:
                continue
            event_id = str(row.get("event_id") or "").strip() or f"bond_event_{len(scored)}"
            refs = _normalize_recall_refs(
                row.get("evidence_refs"),
                fallback=f"evd://bond/{event_id}",
            )
            scored.append(
                (
                    score,
                    {
                        "memory_id": event_id,
                        "text": _truncate_text_for_recall(text, limit=512),
                        "score": score,
                        "metadata": {
                            "store_id": STORE_NEO4J_BOND,
                            "event_id": event_id,
                            "topic_id": str(row.get("topic_id") or "").strip() or None,
                            "capability_hint": str(row.get("capability_hint") or "").strip() or None,
                            "evidence_refs": refs,
                            "source_ref": f"neo4j://bond/event/{event_id}",
                            "evidence_refs_primary": refs[0] if refs else f"evd://bond/{event_id}",
                        },
                    },
                )
            )
        return [item for _score, item in sorted(scored, key=lambda pair: -pair[0])[: max(1, int(top_k))]]

    def _search_style_canon(
        self,
        *,
        query: str,
        query_embedding: list[float],
        top_k: int,
        collection: str | None,
    ) -> list[dict[str, Any]]:
        # Vector projection is preferred; file search is deterministic fallback.
        try:
            vector_rows = self._search_canon_projection_vector(
                query_embedding=query_embedding,
                top_k=top_k,
                collection=collection,
                canon_kind="style",
                store_id=STORE_STYLE_CANON,
            )
            if vector_rows:
                return vector_rows
        except Exception:
            pass
        return self._search_style_canon_file(query=query, top_k=top_k)

    def _search_style_canon_file(self, *, query: str, top_k: int) -> list[dict[str, Any]]:
        if self.style_store is None:
            return []
        entries = getattr(self.style_store, "list_entries", lambda: [])()
        scored: list[tuple[float, dict[str, Any]]] = []
        for entry in entries or []:
            canon_id = str(getattr(entry, "canon_id", "") or "").strip()
            content = str(getattr(entry, "content", "") or "").strip()
            tags = list(getattr(entry, "tags", []) or [])
            text = " ".join([content] + [str(tag) for tag in tags if str(tag).strip()])
            score = _text_similarity_score(query, text)
            if score <= 0.0:
                continue
            version = int(getattr(entry, "version", 0) or 0)
            content_hash = str(getattr(entry, "hash", "") or "").strip() or "unknown"
            source_ref = f"style_canon://{canon_id}@v{version}"
            scored.append(
                (
                    score,
                    {
                        "memory_id": canon_id,
                        "text": _truncate_text_for_recall(content, limit=512),
                        "score": score,
                        "metadata": {
                            "store_id": STORE_STYLE_CANON,
                            "canon_id": canon_id,
                            "version": version,
                            "hash": content_hash,
                            "evidence_refs": [source_ref],
                            "source_ref": source_ref,
                            "evidence_refs_primary": source_ref,
                        },
                    },
                )
            )
        return [item for _score, item in sorted(scored, key=lambda pair: -pair[0])[: max(1, int(top_k))]]

    def _search_world_canon(
        self,
        *,
        query: str,
        query_embedding: list[float],
        top_k: int,
        collection: str | None,
    ) -> list[dict[str, Any]]:
        # Vector projection is preferred; file search is deterministic fallback.
        try:
            vector_rows = self._search_canon_projection_vector(
                query_embedding=query_embedding,
                top_k=top_k,
                collection=collection,
                canon_kind="world",
                store_id=STORE_WORLD_CANON,
            )
            if vector_rows:
                return vector_rows
        except Exception:
            pass
        return self._search_world_canon_file(query=query, top_k=top_k)

    def _search_world_canon_file(self, *, query: str, top_k: int) -> list[dict[str, Any]]:
        if self.world_store is None:
            return []
        docs = getattr(self.world_store, "list_documents", lambda: [])()
        scored: list[tuple[float, dict[str, Any]]] = []
        for doc in docs or []:
            doc_id = str(getattr(doc, "doc_id", "") or "").strip()
            title = str(getattr(doc, "title", "") or "").strip()
            body = str(getattr(doc, "body", "") or "").strip()
            tags = list(getattr(doc, "tags", []) or [])
            text = " ".join(part for part in [title, body, *[str(tag) for tag in tags]] if part)
            score = _text_similarity_score(query, text)
            if score <= 0.0:
                continue
            version = int(getattr(doc, "version", 0) or 0)
            doc_hash = str(getattr(doc, "hash", "") or "").strip() or "unknown"
            source_ref = f"world_canon://{doc_id}@v{version}"
            scored.append(
                (
                    score,
                    {
                        "memory_id": doc_id,
                        "text": _truncate_text_for_recall(body or title, limit=512),
                        "score": score,
                        "metadata": {
                            "store_id": STORE_WORLD_CANON,
                            "doc_id": doc_id,
                            "version": version,
                            "hash": doc_hash,
                            "evidence_refs": [source_ref],
                            "source_ref": source_ref,
                            "evidence_refs_primary": source_ref,
                        },
                    },
                )
            )
        return [item for _score, item in sorted(scored, key=lambda pair: -pair[0])[: max(1, int(top_k))]]

    def _search_canon_projection_vector(
        self,
        *,
        query_embedding: list[float],
        top_k: int,
        collection: str | None,
        canon_kind: str,
        store_id: str,
    ) -> list[dict[str, Any]]:
        target_collection = str(collection or DEFAULT_CANON_VECTOR_COLLECTION).strip()
        if not target_collection:
            target_collection = DEFAULT_CANON_VECTOR_COLLECTION
        fetch_k = max(1, int(top_k)) * CANON_VECTOR_FETCH_FACTOR
        rows = self.chroma.search(
            query_embedding=query_embedding,
            top_k=fetch_k,
            collection=target_collection,
        )

        candidates: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            metadata = row.get("metadata")
            normalized_metadata = dict(metadata or {}) if isinstance(metadata, Mapping) else {}
            row_kind = str(normalized_metadata.get("canon_kind") or "").strip().lower()
            if row_kind and row_kind != canon_kind:
                continue

            memory_id = str(row.get("memory_id") or "").strip()
            if not memory_id:
                continue

            score = _coerce_score(row.get("score"))
            if score <= 0.0:
                continue

            version = _coerce_non_negative_int(normalized_metadata.get("version"), default=0)
            content_hash = str(normalized_metadata.get("hash") or "").strip() or "unknown"
            snippet = _truncate_text_for_recall(str(row.get("text") or ""), limit=512)

            if canon_kind == "style":
                canon_id = str(normalized_metadata.get("canon_id") or "").strip()
                if not canon_id:
                    canon_id = _extract_canon_entity_id(memory_id, "canon_style") or memory_id
                source_ref = str(normalized_metadata.get("source_ref") or f"style_canon://{canon_id}@v{version}")
                evidence_primary = str(
                    normalized_metadata.get("evidence_ref")
                    or normalized_metadata.get("evidence_refs_primary")
                    or source_ref
                )
                payload = {
                    "memory_id": canon_id,
                    "text": snippet,
                    "score": score,
                    "metadata": {
                        "store_id": store_id,
                        "canon_kind": canon_kind,
                        "canon_id": canon_id,
                        "version": version,
                        "hash": content_hash,
                        "vector_collection": target_collection,
                        "evidence_refs": [source_ref],
                        "source_ref": source_ref,
                        "evidence_refs_primary": evidence_primary,
                    },
                }
            else:
                doc_id = str(normalized_metadata.get("doc_id") or "").strip()
                if not doc_id:
                    doc_id = _extract_canon_entity_id(memory_id, "canon_world") or memory_id
                source_ref = str(normalized_metadata.get("source_ref") or f"world_canon://{doc_id}@v{version}")
                evidence_primary = str(
                    normalized_metadata.get("evidence_ref")
                    or normalized_metadata.get("evidence_refs_primary")
                    or source_ref
                )
                payload = {
                    "memory_id": doc_id,
                    "text": snippet,
                    "score": score,
                    "metadata": {
                        "store_id": store_id,
                        "canon_kind": canon_kind,
                        "doc_id": doc_id,
                        "version": version,
                        "hash": content_hash,
                        "vector_collection": target_collection,
                        "evidence_refs": [source_ref],
                        "source_ref": source_ref,
                        "evidence_refs_primary": evidence_primary,
                    },
                }
            candidates.append(payload)
        return sorted(candidates, key=lambda row: -float(row["score"]))[: max(1, int(top_k))]

    def _search_artifact_vault(self, *, query: str, top_k: int) -> list[dict[str, Any]]:
        # M31-A：通过 SSOT 接口读取，消除对 self.ledger.vault_manifest_index 的旁路访问
        if self.artifact_vault is None:
            return []
        try:
            raw_results = self.artifact_vault.search(query, topk=int(top_k))
        except Exception:
            return []
        candidates: list[dict[str, Any]] = []
        for row in raw_results or []:
            if not isinstance(row, Mapping):
                continue
            manifest_id = str(row.get("manifest_id") or "").strip()
            if not manifest_id:
                continue
            integrity = str(row.get("hash") or row.get("integrity_digest") or "").strip()
            refs = _normalize_recall_refs(
                row.get("evidence_refs"),
                fallback=f"vault://manifest/{manifest_id}",
            )
            text = " ".join(
                part
                for part in (
                    manifest_id,
                    str(row.get("intent_id") or "").strip(),
                    str(row.get("policy_profile_id") or "").strip(),
                    integrity,
                    " ".join(refs),
                )
                if part
            )
            score = _text_similarity_score(query, text)
            if score <= 0.0:
                continue
            source_ref = f"vault://manifest/{manifest_id}"
            # M31-D：从 vault 返回数据中提取 artifact_id，不再硬编码 None
            artifact_id_raw = row.get("artifact_id")
            artifact_id = str(artifact_id_raw).strip() if artifact_id_raw is not None else ""
            candidates.append(
                {
                    "memory_id": manifest_id,
                    "text": _truncate_text_for_recall(text, limit=512),
                    "score": score,
                    "metadata": {
                        "store_id": STORE_ARTIFACT_VAULT,
                        "manifest_id": manifest_id,
                        "hash": integrity or None,
                        "artifact_id": artifact_id if artifact_id else None,
                        "evidence_refs": refs,
                        "source_ref": source_ref,
                        "evidence_refs_primary": refs[0] if refs else source_ref,
                    },
                }
            )
        return sorted(candidates, key=lambda c: -float(c["score"]))[: max(1, int(top_k))]

    def recall(
        self,
        *,
        request_id: str,
        query: str,
        query_embedding: list[float] | tuple[float, ...] | Any,
        intent_id: str | None = None,
        session_id: str | None = None,
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
                rows = self._search_route(
                    route=route,
                    query=normalized_query,
                    query_embedding=normalized_vector,
                    session_id=session_id,
                    now_ts_ms=now_value,
                )
            except Exception as exc:
                reason_code = _map_route_exception(exc, store_id=route.store_id)
                route_error = {
                    "route_id": route.route_id,
                    "store_id": route.store_id,
                    "collection": route.collection,
                    "reason_code": reason_code,
                    "detail": str(exc),
                }
                route_errors.append(route_error)
                route_rows.append(
                    {
                        "route_id": route.route_id,
                        "store_id": route.store_id,
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
                    "store_id": route.store_id,
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

        candidates = sorted(fused_candidates.values(), key=_candidate_sort_key)

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
                "store_id": str(merged.get("RETR_RECALL_STORE_ID") or DEFAULT_RECALL_STORE_ID),
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

    store_raw = raw_route.get("store_id")
    store_id = str(store_raw or "").strip().lower()
    collection_raw = raw_route.get("collection")
    collection = str(collection_raw).strip() if collection_raw is not None else ""
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

    if not store_id:
        issues.append(
            RecallConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail=f"{field_name}.{route_id}.store_id is required",
                field=f"{field_name}.{route_id}.store_id",
            )
        )
    if store_id not in SUPPORTED_RECALL_STORE_IDS:
        issues.append(
            RecallConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=(
                    f"{field_name}.{route_id}.store_id must be one of "
                    f"{sorted(SUPPORTED_RECALL_STORE_IDS)}"
                ),
                field=f"{field_name}.{route_id}.store_id",
            )
        )
    if store_id == STORE_EPISODIC_VECTOR and not collection:
        issues.append(
            RecallConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail=f"{field_name}.{route_id}.collection is required for store_id={STORE_EPISODIC_VECTOR}",
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
    if (
        top_k is None
        or score_weight is None
        or not node_selector
        or not store_id
        or store_id not in SUPPORTED_RECALL_STORE_IDS
    ):
        return None

    normalized_collection = collection if collection else store_id
    return RecallRouteConfig(
        route_id=route_id,
        store_id=store_id,
        collection=normalized_collection,
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
    collection_for_ref = str(route.collection or route.store_id or DEFAULT_RECALL_COLLECTION)
    source_ref = str(metadata.get("source_ref") or f"memory://{collection_for_ref}/{memory_id}")
    evidence_refs_primary = str(metadata.get("evidence_refs_primary") or metadata.get("evidence_ref") or f"evd://{collection_for_ref}/{memory_id}")
    merged_metadata = dict(metadata)
    merged_metadata["store_id"] = route.store_id
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
        "evidence_refs": evidence_refs_primary,
    }


def _truncate_text_for_recall(text: str, *, limit: int = 512) -> str:
    value = str(text or "").strip()
    if len(value) <= int(limit):
        return value
    return value[: max(1, int(limit) - 3)] + "..."


def _normalize_recall_refs(raw_refs: Any, *, fallback: str | None = None) -> list[str]:
    refs: list[str] = []
    if isinstance(raw_refs, (list, tuple)):
        for item in raw_refs:
            cleaned = str(item or "").strip()
            if cleaned and cleaned not in refs:
                refs.append(cleaned)
    elif isinstance(raw_refs, str):
        cleaned = raw_refs.strip()
        if cleaned:
            refs.append(cleaned)
    if not refs and fallback:
        fallback_clean = str(fallback).strip()
        if fallback_clean:
            refs.append(fallback_clean)
    return refs


def _extract_session_id_from_refs(refs: list[str]) -> str | None:
    prefix = _SESSION_REF_PREFIX
    for ref in refs:
        value = str(ref or "").strip()
        if value.startswith(prefix):
            session_id = value[len(prefix):].strip()
            if session_id:
                return session_id
    return None


def _compute_recency_boost(*, row_ts_ms: int, now_ts_ms: int) -> float:
    if row_ts_ms <= 0 or now_ts_ms <= 0:
        return 0.0
    age_ms = max(0, int(now_ts_ms) - int(row_ts_ms))
    if age_ms <= 5 * 60 * 1000:
        return SQLITE_RECENCY_BOOST_5M
    if age_ms <= 60 * 60 * 1000:
        return SQLITE_RECENCY_BOOST_1H
    if age_ms <= 24 * 60 * 60 * 1000:
        return SQLITE_RECENCY_BOOST_1D
    return 0.0


def _apply_sqlite_ledger_boost(
    *,
    base_score: float,
    row_ts_ms: int,
    now_ts_ms: int,
    row_session_id: str | None,
    target_session_id: str | None,
) -> tuple[float, dict[str, Any]]:
    normalized_row_session = str(row_session_id or "").strip()
    normalized_target_session = str(target_session_id or "").strip()
    session_boost = (
        SQLITE_SESSION_BOOST
        if normalized_row_session and normalized_row_session == normalized_target_session
        else 0.0
    )
    recency_boost = _compute_recency_boost(row_ts_ms=row_ts_ms, now_ts_ms=now_ts_ms)
    boosted = round(min(1.0, max(0.0, float(base_score) + session_boost + recency_boost)), 6)
    return boosted, {
        "session_boost": round(session_boost, 6),
        "recency_boost": round(recency_boost, 6),
        "row_ts_ms": int(row_ts_ms),
        "now_ts_ms": int(now_ts_ms),
    }


def _text_similarity_score(query: str, text: str) -> float:
    query_tokens = _tokenize_for_similarity(query)
    if not query_tokens:
        return 0.0
    text_tokens = _tokenize_for_similarity(text)
    if not text_tokens:
        return 0.0
    overlap = len(set(query_tokens).intersection(set(text_tokens)))
    if overlap <= 0:
        return 0.0
    return round(max(0.0, min(1.0, overlap / max(1, len(set(query_tokens))))), 6)


def _tokenize_for_similarity(text: str) -> list[str]:
    value = str(text or "").strip().lower()
    if not value:
        return []
    latin_tokens = re.findall(r"[a-z0-9_]+", value)
    cjk_tokens = re.findall(r"[\u4e00-\u9fff]", value)
    tokens = [token for token in latin_tokens + cjk_tokens if token]
    if tokens:
        return tokens
    return [value]


def _coerce_score(raw_value: Any) -> float:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(1.0, value)), 6)


def _candidate_sort_key(row: Mapping[str, Any]) -> tuple[float, float, int, str, str]:
    score = _coerce_score(row.get("score"))
    metadata = row.get("metadata")
    boost_total = 0.0
    row_ts_ms = 0
    if isinstance(metadata, Mapping):
        boost_total, row_ts_ms = _score_boost_priority(metadata)
    return (
        -score,
        -boost_total,
        -int(row_ts_ms),
        str(row.get("doc_id") or ""),
        str(row.get("source_ref") or ""),
    )


def _sqlite_candidate_sort_key(*, score: float, candidate: Mapping[str, Any]) -> tuple[float, float, int, str]:
    metadata = candidate.get("metadata")
    boost_total = 0.0
    row_ts_ms = 0
    if isinstance(metadata, Mapping):
        boost_total, row_ts_ms = _score_boost_priority(metadata)
    return (-float(score), -boost_total, -int(row_ts_ms), str(candidate.get("memory_id") or ""))


def _score_boost_priority(metadata: Mapping[str, Any]) -> tuple[float, int]:
    raw_boost = metadata.get("score_boost")
    if not isinstance(raw_boost, Mapping):
        return 0.0, 0
    session_boost = _coerce_non_negative_float(raw_boost.get("session_boost"), default=0.0)
    recency_boost = _coerce_non_negative_float(raw_boost.get("recency_boost"), default=0.0)
    row_ts_ms = _coerce_non_negative_int(raw_boost.get("row_ts_ms"), default=0)
    return round(session_boost + recency_boost, 6), row_ts_ms


def _coerce_non_negative_float(raw_value: Any, *, default: float = 0.0) -> float:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return float(default)
    return max(0.0, float(value))


def _coerce_non_negative_int(raw_value: Any, *, default: int = 0) -> int:
    try:
        value = int(raw_value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return int(default)
    return max(0, int(value))


def _extract_canon_entity_id(memory_id: str, prefix: str) -> str | None:
    value = str(memory_id or "").strip()
    marker = f"{str(prefix).strip()}::"
    if value.startswith(marker):
        tail = value[len(marker):].strip()
        return tail or None
    return None


def _map_route_exception(exc: Exception, *, store_id: str | None = None) -> str:
    """按 store_id 返回专属 reason_code（M31-B，DevSpec 8.1 审计可追溯性）。"""
    normalized_store = str(store_id or STORE_EPISODIC_VECTOR).strip().lower()
    # 各库专属 reason_code 分发
    if normalized_store == STORE_SQLITE_LEDGER:
        return RETR_LEDGER_UNAVAILABLE
    if normalized_store == STORE_NEO4J_BOND:
        return RETR_BOND_UNAVAILABLE
    if normalized_store == STORE_STYLE_CANON:
        return RETR_STYLE_UNAVAILABLE
    if normalized_store == STORE_WORLD_CANON:
        return RETR_WORLD_UNAVAILABLE
    if normalized_store == STORE_ARTIFACT_VAULT:
        return RETR_VAULT_UNAVAILABLE
    # episodic_vector：保持原有 Chroma 错误码分发（向后兼容）
    text = str(exc)
    if CHROMA_COLLECTION_MISSING in text:
        return CHROMA_COLLECTION_MISSING
    if CHROMA_VECTOR_DIM_MISMATCH in text:
        return CHROMA_VECTOR_DIM_MISMATCH
    text_lc = text.lower()
    if "expecting embedding with dimension" in text_lc:
        return CHROMA_VECTOR_DIM_MISMATCH
    return CHROMA_ENDPOINT_UNAVAILABLE


def _default_runtime_config() -> RecallRuntimeConfig:
    route = RecallRouteConfig(
        route_id=DEFAULT_RECALL_ROUTE,
        store_id=STORE_EPISODIC_VECTOR,
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


# ── HybridFusionStrategy（TASK-0202）────────────────────────────────────────


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
