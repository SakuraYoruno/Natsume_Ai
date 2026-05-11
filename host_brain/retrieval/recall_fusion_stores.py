"""五路召回 Store 搜索方法模块（Phase XIII 精简后）。

从 recall_fusion.py 拆分，包含 RecallFusionServiceContract 的各路 store 搜索实现：
  - Route 1: episodic_vector（Chroma 向量检索）
  - Route 2: sqlite_ledger（SQLite 意图日志 + 记忆操作日志）
  - Route 3: neo4j_bond（Neo4j 关系图 / SQLite bond 日志降级）
  - Route 4: world_canon（世界观典籍向量投影 + 文件降级）
  - Route 5: artifact_vault（制品库 SSOT 接口）

Phase XIII 已废弃的路由（已彻底删除，不再保留向后兼容代码）：
  - style_canon → StyleCanon v5 常驻注入，不走路由
  - persona_utterance → 已从当前主链移除；表层风格参考走 style_reference_bank
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from host_brain.retrieval.recall_schemas import RecallRouteConfig

logger = logging.getLogger("Natsume.retrieval.recall_fusion")

# ── 常量（与 recall_fusion.py 保持一致）────────────────────────────────────
DEFAULT_RECALL_COLLECTION = "episodic"
DEFAULT_CANON_VECTOR_COLLECTION = "canon"
CANON_VECTOR_FETCH_FACTOR = 5
SQLITE_SESSION_BOOST = 0.25
SQLITE_RECENCY_BOOST_5M = 0.20
SQLITE_RECENCY_BOOST_1H = 0.10
SQLITE_RECENCY_BOOST_1D = 0.05
_SESSION_REF_PREFIX = "chat://session/"

STORE_SQLITE_LEDGER = "sqlite_ledger"
STORE_NEO4J_BOND = "neo4j_bond"
STORE_EPISODIC_VECTOR = "episodic_vector"
STORE_WORLD_CANON = "world_canon"
STORE_ARTIFACT_VAULT = "artifact_vault"


def search_route(
    self: Any,
    *,
    route: RecallRouteConfig,
    query: str,
    query_embedding: list[float],
    render_target_lang: str | None = None,
    person_id: str | None = None,
    session_id: str | None = None,
    now_ts_ms: int | None = None,
) -> list[dict[str, Any]]:
    """按 store_id 分发到对应的搜索实现。"""
    store_id = str(route.store_id or STORE_EPISODIC_VECTOR).strip().lower()
    if store_id == STORE_EPISODIC_VECTOR:
        collection = str(route.collection or DEFAULT_RECALL_COLLECTION)
        return self.chroma.search(
            query_embedding=query_embedding,
            top_k=int(route.top_k),
            collection=collection,
        )
    if store_id == STORE_SQLITE_LEDGER:
        return search_sqlite_ledger(
            self,
            query=query,
            top_k=int(route.top_k),
            person_id=person_id,
            session_id=session_id,
            now_ts_ms=now_ts_ms,
        )
    if store_id == STORE_NEO4J_BOND:
        return search_neo4j_bond(self, query=query, top_k=int(route.top_k))
    if store_id == STORE_WORLD_CANON:
        return search_world_canon(
            self,
            query=query,
            query_embedding=query_embedding,
            top_k=int(route.top_k),
            collection=route.collection,
        )
    if store_id == STORE_ARTIFACT_VAULT:
        return search_artifact_vault(self, query=query, top_k=int(route.top_k))
    raise RuntimeError(f"unsupported recall store_id: {store_id}")


def _normalize_render_target_lang(render_target_lang: str | None) -> str | None:
    normalized = str(render_target_lang or "").strip().lower()
    if normalized in {"zh", "cn", "chinese", "zh-cn", "zh-hans"}:
        return "zh"
    if normalized in {"ja", "jp", "japanese"}:
        return "ja"
    return None


def _matches_render_target_lang(
    *,
    render_target_lang: str | None,
    lang_value: str | None = None,
    canon_id: str | None = None,
    tags: list[str] | None = None,
) -> bool:
    preferred = _normalize_render_target_lang(render_target_lang)
    if preferred is None:
        return True

    normalized_lang = str(lang_value or "").strip().lower()
    if preferred == "zh" and normalized_lang in {"zh", "cn", "chinese", "zh-cn", "zh-hans"}:
        return True
    if preferred == "ja" and normalized_lang in {"ja", "jp", "japanese"}:
        return True

    normalized_canon_id = str(canon_id or "").strip().lower()
    if preferred == "zh" and (
        normalized_canon_id.endswith("_cn")
        or normalized_canon_id.endswith("_zh")
        or "::zh::" in normalized_canon_id
    ):
        return True
    if preferred == "ja" and (
        normalized_canon_id.endswith("_ja")
        or normalized_canon_id.endswith("_jp")
        or "::ja::" in normalized_canon_id
    ):
        return True

    normalized_tags = [str(tag or "").strip().lower() for tag in list(tags or []) if str(tag or "").strip()]
    if preferred == "zh" and any(tag in {"zh", "cn", "chinese", "lang:zh", "lang:cn"} for tag in normalized_tags):
        return True
    if preferred == "ja" and any(tag in {"ja", "jp", "japanese", "lang:ja", "lang:jp"} for tag in normalized_tags):
        return True

    return False


def search_sqlite_ledger(
    self: Any,
    *,
    query: str,
    top_k: int,
    person_id: str | None = None,
    session_id: str | None = None,
    now_ts_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Route 2：SQLite 意图日志 + 记忆操作日志检索。"""
    from host_brain.core.ids import now_ms
    from host_brain.retrieval.recall_fusion_utils import (
        _apply_sqlite_ledger_boost,
        _coerce_non_negative_int,
        _extract_person_id_from_refs,
        _normalize_recall_refs,
        _extract_session_id_from_refs,
        _sqlite_candidate_sort_key,
        _text_similarity_score,
        _truncate_text_for_recall,
    )

    if self.ledger is None:
        raise RuntimeError("sqlite ledger unavailable")
    current_ts = int(now_ts_ms if now_ts_ms is not None else now_ms())
    normalized_person_id = str(person_id or "").strip()
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
            row_person_id=(str(normalized.get("person_id") or "").strip() if isinstance(normalized, Mapping) else None),
            target_person_id=normalized_person_id,
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
        scored.append((
            boosted_score,
            {
                "memory_id": memory_id,
                "text": _truncate_text_for_recall(text, limit=512),
                "score": boosted_score,
                "metadata": {
                    "store_id": STORE_SQLITE_LEDGER,
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "person_id": (str(normalized.get("person_id") or "").strip() if isinstance(normalized, Mapping) else None),
                    "session_id": row_session_id,
                    "event_id": (str(row.get("event_id") or "").strip() or None),
                    "base_score": score,
                    "score_boost": boost_meta,
                    "evidence_refs": evidence_refs,
                    "source_ref": f"ledger://intent_log/{memory_id}",
                    "evidence_refs_primary": evidence_refs[0] if evidence_refs else f"ledger://intent/{memory_id}",
                },
            },
        ))

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
        row_person_id = _extract_person_id_from_refs(evidence_refs)
        row_session_id = _extract_session_id_from_refs(evidence_refs)
        row_ts = _coerce_non_negative_int(row.get("ts_ms"), default=0)

        # FIX: 补充语义更丰富的字段，改善文本相似度搜索质量。
        # 原实现只拼接 detail+memory_id+evidence_refs，三者均无实质语义。
        # 现在额外加入：intent_id（意图标识）、target_memory_id（目标记忆）、
        # source_memory_ids（来源记忆集合）、decay_profile_id（衰减策略）、
        # visibility（可见性标签）。
        target_memory_id = str(row.get("target_memory_id") or "").strip()
        source_memory_ids_raw = row.get("source_memory_ids")
        if isinstance(source_memory_ids_raw, (list, tuple)):
            source_ids_text = " ".join(str(s) for s in source_memory_ids_raw if s)
        else:
            source_ids_text = str(source_memory_ids_raw or "").strip()
        decay_profile_id = str(row.get("decay_profile_id") or "").strip()
        visibility = str(row.get("visibility") or "").strip()

        text = " ".join(
            part for part in (
                intent_id,
                detail,
                memory_id,
                target_memory_id,
                source_ids_text,
                decay_profile_id,
                visibility,
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
            row_person_id=row_person_id,
            target_person_id=normalized_person_id,
            row_session_id=row_session_id,
            target_session_id=normalized_session_id,
        )
        scored.append((
            boosted_score,
            {
                "memory_id": memory_id,
                "text": _truncate_text_for_recall(text, limit=512),
                "score": boosted_score,
                "metadata": {
                    "store_id": STORE_SQLITE_LEDGER,
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "person_id": row_person_id,
                    "session_id": row_session_id,
                    "op_kind": op_kind,
                    "status": status or None,
                    "target_memory_id": target_memory_id or None,
                    "decay_profile_id": decay_profile_id or None,
                    "visibility": visibility or None,
                    "base_score": score,
                    "score_boost": boost_meta,
                    "evidence_refs": evidence_refs,
                    "source_ref": f"ledger://memory_ops_log/{memory_id}",
                    "evidence_refs_primary": (
                        evidence_refs[0] if evidence_refs else f"memory://episodic/{memory_id}"
                    ),
                },
            },
        ))
    return [
        item
        for _score, item in sorted(
            scored,
            key=lambda pair: _sqlite_candidate_sort_key(score=pair[0], candidate=pair[1]),
        )[: max(1, int(top_k))]
    ]


def search_neo4j_bond(self: Any, *, query: str, top_k: int) -> list[dict[str, Any]]:
    """Route 3：Neo4j 关系图检索，降级到 SQLite bond 日志。"""
    from host_brain.retrieval.recall_fusion_utils import (
        _normalize_recall_refs,
        _text_similarity_score,
        _truncate_text_for_recall,
    )

    records: list[Mapping[str, Any]] = []
    try:
        events_attr = getattr(self.neo4j, "events", None)
        if isinstance(events_attr, list):
            records = [row for row in events_attr if isinstance(row, Mapping)]
    except Exception as exc:
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
        scored.append((
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
        ))
    return [item for _score, item in sorted(scored, key=lambda pair: -pair[0])[: max(1, int(top_k))]]


def search_world_canon(
    self: Any,
    *,
    query: str,
    query_embedding: list[float],
    top_k: int,
    collection: str | None,
) -> list[dict[str, Any]]:
    """Route 5：世界观典籍检索（向量投影优先，文件降级）。"""
    try:
        vector_rows = search_canon_projection_vector(
            self,
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
    return search_world_canon_file(self, query=query, top_k=top_k)


def search_world_canon_file(self: Any, *, query: str, top_k: int) -> list[dict[str, Any]]:
    """世界观典籍文件降级检索。"""
    from host_brain.retrieval.recall_fusion_utils import (
        _text_similarity_score,
        _truncate_text_for_recall,
    )

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
        scored.append((
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
        ))
    return [item for _score, item in sorted(scored, key=lambda pair: -pair[0])[: max(1, int(top_k))]]


def search_canon_projection_vector(
    self: Any,
    *,
    query_embedding: list[float],
    top_k: int,
    collection: str | None,
    canon_kind: str,
    store_id: str,
    render_target_lang: str | None = None,
) -> list[dict[str, Any]]:
    """向量投影检索（style_canon / world_canon 共用）。"""
    from host_brain.retrieval.recall_fusion_utils import (
        _coerce_non_negative_int,
        _coerce_score,
        _extract_canon_entity_id,
        _truncate_text_for_recall,
    )

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

        doc_id = str(normalized_metadata.get("doc_id") or "").strip()
        if not doc_id:
            doc_id = _extract_canon_entity_id(memory_id, "canon_world") or memory_id
        source_ref = str(normalized_metadata.get("source_ref") or f"world_canon://{doc_id}@v{version}")
        evidence_primary = str(
            normalized_metadata.get("evidence_ref")
            or normalized_metadata.get("evidence_refs_primary")
            or source_ref
        )
        payload: dict[str, Any] = {
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


def search_artifact_vault(self: Any, *, query: str, top_k: int) -> list[dict[str, Any]]:
    """Route 6：制品库 SSOT 接口检索。"""
    from host_brain.retrieval.recall_fusion_utils import (
        _normalize_recall_refs,
        _text_similarity_score,
        _truncate_text_for_recall,
    )

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
            part for part in (
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
        candidates.append({
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
        })
    return sorted(candidates, key=lambda c: -float(c["score"]))[: max(1, int(top_k))]

