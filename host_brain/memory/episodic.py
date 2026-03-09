"""Episodic memory store with evidence-gated lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any
import uuid

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    MEMORY_EVIDENCE_REQUIRED,
    MEMORY_MERGE_INVALID,
    MEMORY_NOT_FOUND,
)
from host_brain.core.ids import now_ms
from host_brain.memory.chroma_client import InMemoryChromaClient, embed_text


@dataclass
class EpisodicRecord:
    memory_id: str
    text: str
    ts_ms: int
    text_hash: str
    decay_profile_id: str
    evidence_refs: list[str]
    visibility: str
    embedding_model_id: str
    merged_into: str | None = None
    deleted_at: int | None = None
    # M27-D：情感标注字段（OPT-25）
    emotion_tag: str = "NEUTRAL"
    emotion_valence: float = 0.0
    emotion_arousal: float = 0.3

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "text": self.text,
            "ts_ms": self.ts_ms,
            "text_hash": self.text_hash,
            "decay_profile_id": self.decay_profile_id,
            "evidence_refs": list(self.evidence_refs),
            "visibility": self.visibility,
            "embedding_model_id": self.embedding_model_id,
            "merged_into": self.merged_into,
            "deleted_at": self.deleted_at,
            "emotion_tag": self.emotion_tag,
            "emotion_valence": self.emotion_valence,
            "emotion_arousal": self.emotion_arousal,
        }


@dataclass(frozen=True)
class MemoryOpResult:
    success: bool
    reason_code: str | None
    detail: str
    memory_id: str | None = None


class EpisodicStore:
    """Single-writer episodic memory store with lifecycle audit logs."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        chroma_client: InMemoryChromaClient | None = None,
    ) -> None:
        self.ledger = ledger or SQLiteLedger()
        self.chroma = chroma_client or InMemoryChromaClient()
        self._records: dict[str, EpisodicRecord] = {}

    def write_memory(
        self,
        *,
        text: str,
        evidence_refs: list[str],
        request_id: str | None = None,
        intent_id: str | None = None,
        decay_profile_id: str = "default",
        visibility: str = "internal",
        ts_ms: int | None = None,
        emotion_tag: str = "NEUTRAL",
        emotion_valence: float = 0.0,
        emotion_arousal: float = 0.3,
    ) -> MemoryOpResult:
        ts_value = int(ts_ms if ts_ms is not None else now_ms())
        refs = _normalize_evidence_refs(evidence_refs)
        if not refs:
            self._log_memory_op(
                request_id=request_id,
                intent_id=intent_id,
                op_kind="WRITE",
                memory_id=None,
                target_memory_id=None,
                source_memory_ids=None,
                evidence_refs=[],
                status="REJECTED",
                reason_code=MEMORY_EVIDENCE_REQUIRED,
                detail="evidence_refs is required for episodic write",
                decay_profile_id=decay_profile_id,
                visibility=visibility,
                ts_ms=ts_value,
            )
            return MemoryOpResult(
                success=False,
                reason_code=MEMORY_EVIDENCE_REQUIRED,
                detail="evidence_refs is required for episodic write",
            )

        if not str(text).strip():
            self._log_memory_op(
                request_id=request_id,
                intent_id=intent_id,
                op_kind="WRITE",
                memory_id=None,
                target_memory_id=None,
                source_memory_ids=None,
                evidence_refs=refs,
                status="REJECTED",
                reason_code=MEMORY_MERGE_INVALID,
                detail="memory text cannot be empty",
                decay_profile_id=decay_profile_id,
                visibility=visibility,
                ts_ms=ts_value,
            )
            return MemoryOpResult(False, MEMORY_MERGE_INVALID, "memory text cannot be empty")

        record = self._create_record(
            text=text,
            evidence_refs=refs,
            ts_ms=ts_value,
            decay_profile_id=decay_profile_id,
            visibility=visibility,
            emotion_tag=emotion_tag,
            emotion_valence=emotion_valence,
            emotion_arousal=emotion_arousal,
        )
        self._log_memory_op(
            request_id=request_id,
            intent_id=intent_id,
            op_kind="WRITE",
            memory_id=record.memory_id,
            target_memory_id=None,
            source_memory_ids=None,
            evidence_refs=refs,
            status="SUCCESS",
            reason_code=None,
            detail=_build_memory_write_detail(record.text),
            decay_profile_id=record.decay_profile_id,
            visibility=record.visibility,
            ts_ms=ts_value,
        )
        return MemoryOpResult(True, None, "memory written", memory_id=record.memory_id)

    def merge_memories(
        self,
        *,
        source_memory_ids: list[str],
        evidence_refs: list[str],
        request_id: str | None = None,
        intent_id: str | None = None,
        merged_text: str | None = None,
        decay_profile_id: str = "default",
        visibility: str = "internal",
        ts_ms: int | None = None,
    ) -> MemoryOpResult:
        ts_value = int(ts_ms if ts_ms is not None else now_ms())
        refs = _normalize_evidence_refs(evidence_refs)
        source_ids = _normalize_memory_ids(source_memory_ids)

        if not refs:
            self._log_memory_op(
                request_id=request_id,
                intent_id=intent_id,
                op_kind="MERGE",
                memory_id=None,
                target_memory_id=None,
                source_memory_ids=source_ids,
                evidence_refs=[],
                status="REJECTED",
                reason_code=MEMORY_EVIDENCE_REQUIRED,
                detail="evidence_refs is required for episodic merge",
                decay_profile_id=decay_profile_id,
                visibility=visibility,
                ts_ms=ts_value,
            )
            return MemoryOpResult(False, MEMORY_EVIDENCE_REQUIRED, "evidence_refs is required for episodic merge")

        if len(source_ids) < 2:
            self._log_memory_op(
                request_id=request_id,
                intent_id=intent_id,
                op_kind="MERGE",
                memory_id=None,
                target_memory_id=None,
                source_memory_ids=source_ids,
                evidence_refs=refs,
                status="REJECTED",
                reason_code=MEMORY_MERGE_INVALID,
                detail="merge requires at least two source memory_ids",
                decay_profile_id=decay_profile_id,
                visibility=visibility,
                ts_ms=ts_value,
            )
            return MemoryOpResult(False, MEMORY_MERGE_INVALID, "merge requires at least two source memory_ids")

        sources: list[EpisodicRecord] = []
        for source_id in source_ids:
            source = self._records.get(source_id)
            if source is None or source.deleted_at is not None:
                self._log_memory_op(
                    request_id=request_id,
                    intent_id=intent_id,
                    op_kind="MERGE",
                    memory_id=None,
                    target_memory_id=None,
                    source_memory_ids=source_ids,
                    evidence_refs=refs,
                    status="REJECTED",
                    reason_code=MEMORY_NOT_FOUND,
                    detail=f"source memory not found: {source_id}",
                    decay_profile_id=decay_profile_id,
                    visibility=visibility,
                    ts_ms=ts_value,
                )
                return MemoryOpResult(False, MEMORY_NOT_FOUND, f"source memory not found: {source_id}")
            sources.append(source)

        merge_text = str(merged_text).strip() if merged_text is not None else "\n".join(
            source.text for source in sources
        )
        target = self._create_record(
            text=merge_text,
            evidence_refs=refs,
            ts_ms=ts_value,
            decay_profile_id=decay_profile_id,
            visibility=visibility,
        )

        for source in sources:
            source.merged_into = target.memory_id

        self._log_memory_op(
            request_id=request_id,
            intent_id=intent_id,
            op_kind="MERGE",
            memory_id=target.memory_id,
            target_memory_id=target.memory_id,
            source_memory_ids=source_ids,
            evidence_refs=refs,
            status="SUCCESS",
            reason_code=None,
            detail="memory merge completed",
            decay_profile_id=target.decay_profile_id,
            visibility=target.visibility,
            ts_ms=ts_value,
        )
        return MemoryOpResult(True, None, "memory merge completed", memory_id=target.memory_id)

    def decay_memory(
        self,
        *,
        memory_id: str,
        decay_profile_id: str,
        evidence_refs: list[str],
        request_id: str | None = None,
        intent_id: str | None = None,
        ts_ms: int | None = None,
    ) -> MemoryOpResult:
        ts_value = int(ts_ms if ts_ms is not None else now_ms())
        refs = _normalize_evidence_refs(evidence_refs)
        if not refs:
            self._log_memory_op(
                request_id=request_id,
                intent_id=intent_id,
                op_kind="DECAY",
                memory_id=memory_id,
                target_memory_id=None,
                source_memory_ids=None,
                evidence_refs=[],
                status="REJECTED",
                reason_code=MEMORY_EVIDENCE_REQUIRED,
                detail="evidence_refs is required for episodic decay",
                decay_profile_id=decay_profile_id,
                visibility=None,
                ts_ms=ts_value,
            )
            return MemoryOpResult(False, MEMORY_EVIDENCE_REQUIRED, "evidence_refs is required for episodic decay")

        record = self._records.get(memory_id)
        if record is None or record.deleted_at is not None:
            self._log_memory_op(
                request_id=request_id,
                intent_id=intent_id,
                op_kind="DECAY",
                memory_id=memory_id,
                target_memory_id=None,
                source_memory_ids=None,
                evidence_refs=refs,
                status="REJECTED",
                reason_code=MEMORY_NOT_FOUND,
                detail=f"memory not found: {memory_id}",
                decay_profile_id=decay_profile_id,
                visibility=None,
                ts_ms=ts_value,
            )
            return MemoryOpResult(False, MEMORY_NOT_FOUND, f"memory not found: {memory_id}")

        record.decay_profile_id = str(decay_profile_id).strip() or record.decay_profile_id
        self._log_memory_op(
            request_id=request_id,
            intent_id=intent_id,
            op_kind="DECAY",
            memory_id=record.memory_id,
            target_memory_id=None,
            source_memory_ids=None,
            evidence_refs=refs,
            status="SUCCESS",
            reason_code=None,
            detail="memory decay profile updated",
            decay_profile_id=record.decay_profile_id,
            visibility=record.visibility,
            ts_ms=ts_value,
        )
        return MemoryOpResult(True, None, "memory decay profile updated", memory_id=record.memory_id)

    def delete_memory(
        self,
        *,
        memory_id: str,
        evidence_refs: list[str],
        request_id: str | None = None,
        intent_id: str | None = None,
        ts_ms: int | None = None,
    ) -> MemoryOpResult:
        ts_value = int(ts_ms if ts_ms is not None else now_ms())
        refs = _normalize_evidence_refs(evidence_refs)
        if not refs:
            self._log_memory_op(
                request_id=request_id,
                intent_id=intent_id,
                op_kind="DELETE",
                memory_id=memory_id,
                target_memory_id=None,
                source_memory_ids=None,
                evidence_refs=[],
                status="REJECTED",
                reason_code=MEMORY_EVIDENCE_REQUIRED,
                detail="evidence_refs is required for episodic delete",
                decay_profile_id=None,
                visibility=None,
                ts_ms=ts_value,
            )
            return MemoryOpResult(False, MEMORY_EVIDENCE_REQUIRED, "evidence_refs is required for episodic delete")

        record = self._records.get(memory_id)
        if record is None or record.deleted_at is not None:
            self._log_memory_op(
                request_id=request_id,
                intent_id=intent_id,
                op_kind="DELETE",
                memory_id=memory_id,
                target_memory_id=None,
                source_memory_ids=None,
                evidence_refs=refs,
                status="REJECTED",
                reason_code=MEMORY_NOT_FOUND,
                detail=f"memory not found: {memory_id}",
                decay_profile_id=None,
                visibility=None,
                ts_ms=ts_value,
            )
            return MemoryOpResult(False, MEMORY_NOT_FOUND, f"memory not found: {memory_id}")

        record.deleted_at = ts_value
        self.chroma.delete(memory_id)
        self._log_memory_op(
            request_id=request_id,
            intent_id=intent_id,
            op_kind="DELETE",
            memory_id=record.memory_id,
            target_memory_id=None,
            source_memory_ids=None,
            evidence_refs=refs,
            status="SUCCESS",
            reason_code=None,
            detail="memory deleted",
            decay_profile_id=record.decay_profile_id,
            visibility=record.visibility,
            ts_ms=ts_value,
        )
        return MemoryOpResult(True, None, "memory deleted", memory_id=record.memory_id)

    def recall(
        self,
        *,
        query_text: str,
        top_k: int = 5,
        emotion_filter: str | None = None,
        # M29-B（OPT-3）：情感上下文加权召回
        emotion_context: str | None = None,
        emotion_boost_factor: float = 1.3,
    ) -> list[dict[str, Any]]:
        """情感感知记忆召回（M29-B OPT-3）。

        emotion_context 匹配的记录分数加权 emotion_boost_factor（默认 1.3x）。
        emotion_filter 仍保留向后兼容（优先返回匹配情感的记录）。
        """
        embedding, _ = embed_text(query_text)
        results = self.chroma.search(query_embedding=embedding, top_k=top_k)

        # M29-B：情感上下文加权
        if emotion_context:
            boosted: list[dict[str, Any]] = []
            for r in results:
                meta = r.get("metadata") or {}
                score = float(r.get("score") or r.get("distance") or 0.0)
                if meta.get("emotion_tag") == str(emotion_context).strip().upper():
                    score = min(1.0, score * emotion_boost_factor)
                    r = dict(r)
                    r["score"] = score
                boosted.append(r)
            # 按加权后分数降序排列
            results = sorted(boosted, key=lambda x: float(x.get("score") or 0.0), reverse=True)

        if emotion_filter is not None:
            # 向后兼容：优先返回匹配情感标签的记录
            matched = [r for r in results if (r.get("metadata") or {}).get("emotion_tag") == emotion_filter]
            unmatched = [r for r in results if (r.get("metadata") or {}).get("emotion_tag") != emotion_filter]
            return matched + unmatched
        return results

    # M29-I（OPT-?）：decay_profile_id 实装——FAST/SLOW/PERMANENT 三档
    _DECAY_PROFILES: dict[str, dict[str, Any]] = {
        "FAST":      {"ttl_days": 7,   "decay_rate": 0.9, "label": "快速遗忘"},
        "SLOW":      {"ttl_days": 90,  "decay_rate": 0.3, "label": "缓慢遗忘"},
        "PERMANENT": {"ttl_days": None, "decay_rate": 0.0, "label": "永久保留"},
        "default":   {"ttl_days": 30,  "decay_rate": 0.5, "label": "默认"},
    }

    def get_decay_profile(self, decay_profile_id: str) -> dict[str, Any]:
        """M29-I：返回 decay_profile_id 对应的衰减配置。"""
        return self._DECAY_PROFILES.get(
            str(decay_profile_id).strip().upper(),
            self._DECAY_PROFILES["default"],
        )

    def is_expired(self, record: "EpisodicRecord", *, now_ts_ms: int | None = None) -> bool:
        """M29-I：根据 decay_profile_id 判断记忆是否已过期。"""
        profile = self.get_decay_profile(record.decay_profile_id)
        ttl_days = profile.get("ttl_days")
        if ttl_days is None:
            return False  # PERMANENT
        now_value = int(now_ts_ms if now_ts_ms is not None else now_ms())
        age_days = (now_value - record.ts_ms) / (1000 * 86400)
        return age_days > ttl_days

    def get_memory(self, memory_id: str) -> EpisodicRecord | None:
        return self._records.get(memory_id)

    def replay_ops(self, *, memory_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.ledger.memory_ops_log
        if memory_id is None:
            return rows

        filtered: list[dict[str, Any]] = []
        for row in rows:
            if row.get("memory_id") == memory_id:
                filtered.append(row)
                continue
            if row.get("target_memory_id") == memory_id:
                filtered.append(row)
                continue
            sources = row.get("source_memory_ids") or []
            if isinstance(sources, list) and memory_id in sources:
                filtered.append(row)
        return filtered

    def _create_record(
        self,
        *,
        text: str,
        evidence_refs: list[str],
        ts_ms: int,
        decay_profile_id: str,
        visibility: str,
        emotion_tag: str = "NEUTRAL",
        emotion_valence: float = 0.0,
        emotion_arousal: float = 0.3,
    ) -> EpisodicRecord:
        memory_id = f"mem_{uuid.uuid4().hex}"
        normalized_text = str(text).strip()
        text_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        embedding, model_id = embed_text(normalized_text)
        # 截断情感数值到合法范围
        valence = max(-1.0, min(1.0, float(emotion_valence)))
        arousal = max(0.0, min(1.0, float(emotion_arousal)))
        record = EpisodicRecord(
            memory_id=memory_id,
            text=normalized_text,
            ts_ms=ts_ms,
            text_hash=text_hash,
            decay_profile_id=str(decay_profile_id).strip() or "default",
            evidence_refs=list(evidence_refs),
            visibility=str(visibility).strip() or "internal",
            embedding_model_id=model_id,
            emotion_tag=str(emotion_tag).strip().upper() or "NEUTRAL",
            emotion_valence=valence,
            emotion_arousal=arousal,
        )
        self._records[memory_id] = record
        self.chroma.upsert(
            memory_id=memory_id,
            text=normalized_text,
            embedding=embedding,
            metadata={
                "ts_ms": ts_ms,
                "text_hash": text_hash,
                "visibility": record.visibility,
                "evidence_refs": list(evidence_refs),
                "embedding_model_id": model_id,
                "emotion_tag": record.emotion_tag,
                "emotion_valence": record.emotion_valence,
                "emotion_arousal": record.emotion_arousal,
            },
        )
        return record

    def _log_memory_op(
        self,
        *,
        request_id: str | None,
        intent_id: str | None,
        op_kind: str,
        memory_id: str | None,
        target_memory_id: str | None,
        source_memory_ids: list[str] | None,
        evidence_refs: list[str],
        status: str,
        reason_code: str | None,
        detail: str,
        decay_profile_id: str | None,
        visibility: str | None,
        ts_ms: int,
    ) -> None:
        self.ledger.log_memory_op(
            {
                "request_id": request_id,
                "intent_id": intent_id,
                "op_kind": op_kind,
                "memory_id": memory_id,
                "target_memory_id": target_memory_id,
                "source_memory_ids": source_memory_ids,
                "evidence_refs": evidence_refs,
                "status": status,
                "reason_code": reason_code,
                "detail": detail,
                "decay_profile_id": decay_profile_id,
                "visibility": visibility,
                "ts_ms": ts_ms,
            }
        )


def _normalize_evidence_refs(evidence_refs: list[str] | None) -> list[str]:
    if not evidence_refs:
        return []
    normalized: list[str] = []
    for value in evidence_refs:
        item = str(value).strip()
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def _normalize_memory_ids(memory_ids: list[str] | None) -> list[str]:
    if not memory_ids:
        return []
    normalized: list[str] = []
    for value in memory_ids:
        item = str(value).strip()
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def _build_memory_write_detail(text: str, *, limit: int = 240) -> str:
    normalized = " ".join(str(text or "").strip().split())
    if not normalized:
        return "memory written"
    max_len = max(1, int(limit))
    if len(normalized) > max_len:
        normalized = normalized[: max(1, max_len - 3)] + "..."
    return f"memory written: {normalized}"
