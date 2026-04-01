"""RecallSearchMixin：各路由搜索方法集合（neo4j, style_canon, world_canon, artifact_vault 等）。"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from host_brain.core.ids import now_ms
from host_brain.retrieval.recall_fusion_utils import (
    _apply_sqlite_ledger_boost,
    _coerce_non_negative_int,
    _coerce_score,
    _extract_canon_entity_id,
    _extract_session_id_from_refs,
    _normalize_recall_refs,
    _sqlite_candidate_sort_key,
    _text_similarity_score,
    _truncate_text_for_recall,
)

logger = logging.getLogger("Natsume.retrieval.recall_fusion")

STORE_SQLITE_LEDGER = "sqlite_ledger"
STORE_NEO4J_BOND = "neo4j_bond"
STORE_EPISODIC_VECTOR = "episodic_vector"
STORE_STYLE_CANON = "style_canon"
STORE_WORLD_CANON = "world_canon"
STORE_ARTIFACT_VAULT = "artifact_vault"

DEFAULT_CANON_VECTOR_COLLECTION = "canon"
CANON_VECTOR_FETCH_FACTOR = 5


class RecallSearchMixin:
    """各路由具体搜索实现 Mixin。需要混入 RecallFusionServiceContract 使用 self 属性。"""

    def _search_sqlite_ledger(
        self,
        *,
        query: str,
        top_k: int,
        session_id: str | None = None,
        now_ts_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        if self.ledger is None:  # type: ignore[attr-defined]
            raise RuntimeError("sqlite ledger unavailable")
        current_ts = int(now_ts_ms if now_ts_ms is not None else now_ms())
        normalized_session_id = str(session_id or "").strip()
        scored: list[tuple[float, dict[str, Any]]] = []

        for row in list(self.ledger.intent_log):  # type: ignore[attr-defined]
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

        for row in list(self.ledger.memory_ops_log):  # type: ignore[attr-defined]
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
            events_attr = getattr(self.neo4j, "events", None)  # type: ignore[attr-defined]
            if isinstance(events_attr, list):
                records = [row for row in events_attr if isinstance(row, Mapping)]
        except Exception as exc:
            logger.warning("neo4j recall route unavailable, fallback to sqlite bond log: %s", exc)
            records = []
        if not records and self.ledger is not None:  # type: ignore[attr-defined]
            records = [row for row in self.ledger.bond_update_log if isinstance(row, Mapping)]  # type: ignore[attr-defined]
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
        if self.style_store is None:  # type: ignore[attr-defined]
            return []
        entries = getattr(self.style_store, "list_entries", lambda: [])()  # type: ignore[attr-defined]
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
        if self.world_store is None:  # type: ignore[attr-defined]
            return []
        docs = getattr(self.world_store, "list_documents", lambda: [])()  # type: ignore[attr-defined]
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
        rows = self.chroma.search(  # type: ignore[attr-defined]
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
        if self.artifact_vault is None:  # type: ignore[attr-defined]
            return []
        try:
            raw_results = self.artifact_vault.search(query, topk=int(top_k))  # type: ignore[attr-defined]
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
