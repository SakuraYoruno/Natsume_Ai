"""recall_fusion 工具函数模块。

从 recall_fusion.py 拆分，包含所有纯工具函数，供 recall_fusion.py 和
recall_fusion_stores.py 共用，无循环依赖。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from host_brain.core.errors import (
    CHROMA_COLLECTION_MISSING,
    CHROMA_ENDPOINT_UNAVAILABLE,
    CHROMA_VECTOR_DIM_MISMATCH,
    RETR_BOND_UNAVAILABLE,
    RETR_LEDGER_UNAVAILABLE,
    RETR_VAULT_UNAVAILABLE,
    RETR_WORLD_UNAVAILABLE,
)

STORE_SQLITE_LEDGER = "sqlite_ledger"
STORE_LIFE_EPISODE = "life_episode"
STORE_NEO4J_BOND = "neo4j_bond"
STORE_EPISODIC_VECTOR = "episodic_vector"
STORE_SEMANTIC_MEMORY = "semantic_memory"
STORE_WORLD_CANON = "world_canon"
STORE_ARTIFACT_VAULT = "artifact_vault"

SQLITE_SESSION_BOOST = 0.25
SQLITE_PERSON_BOOST = 0.35
SQLITE_RECENCY_BOOST_5M = 0.20
SQLITE_RECENCY_BOOST_1H = 0.10
SQLITE_RECENCY_BOOST_1D = 0.05
_SESSION_REF_PREFIX = "chat://session/"
_PERSON_REF_PREFIX = "chat://person/"


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


def _extract_person_id_from_refs(refs: list[str]) -> str | None:
    prefix = _PERSON_REF_PREFIX
    for ref in refs:
        value = str(ref or "").strip()
        if value.startswith(prefix):
            person_id = value[len(prefix):].strip()
            if person_id:
                return person_id
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
    row_person_id: str | None,
    target_person_id: str | None,
    row_session_id: str | None,
    target_session_id: str | None,
) -> tuple[float, dict[str, Any]]:
    normalized_row_person = str(row_person_id or "").strip()
    normalized_target_person = str(target_person_id or "").strip()
    person_boost = (
        SQLITE_PERSON_BOOST
        if normalized_row_person and normalized_row_person == normalized_target_person
        else 0.0
    )
    normalized_row_session = str(row_session_id or "").strip()
    normalized_target_session = str(target_session_id or "").strip()
    session_boost = (
        SQLITE_SESSION_BOOST
        if normalized_row_session and normalized_row_session == normalized_target_session
        else 0.0
    )
    recency_boost = _compute_recency_boost(row_ts_ms=row_ts_ms, now_ts_ms=now_ts_ms)
    boosted = round(min(1.0, max(0.0, float(base_score) + person_boost + session_boost + recency_boost)), 6)
    return boosted, {
        "person_boost": round(person_boost, 6),
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
    semantic_kind_boost = _coerce_non_negative_float(raw_boost.get("semantic_kind_boost"), default=0.0)
    tag_match_boost = _coerce_non_negative_float(raw_boost.get("tag_match_boost"), default=0.0)
    row_ts_ms = _coerce_non_negative_int(raw_boost.get("row_ts_ms"), default=0)
    return round(session_boost + recency_boost + semantic_kind_boost + tag_match_boost, 6), row_ts_ms


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
    if normalized_store in {STORE_SQLITE_LEDGER, STORE_LIFE_EPISODE}:
        return RETR_LEDGER_UNAVAILABLE
    if normalized_store == STORE_SEMANTIC_MEMORY:
        return RETR_LEDGER_UNAVAILABLE
    if normalized_store == STORE_NEO4J_BOND:
        return RETR_BOND_UNAVAILABLE
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


def _normalize_vector(raw: list[float] | tuple[float, ...] | Any) -> list[float]:
    if not isinstance(raw, (list, tuple)):
        return []
    return [float(value) for value in raw]


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)
