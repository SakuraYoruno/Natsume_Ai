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
            detail="memory written",
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

    def recall(self, *, query_text: str, top_k: int = 5) -> list[dict[str, Any]]:
        embedding, _ = embed_text(query_text)
        return self.chroma.search(query_embedding=embedding, top_k=top_k)

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
    ) -> EpisodicRecord:
        memory_id = f"mem_{uuid.uuid4().hex}"
        normalized_text = str(text).strip()
        text_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        embedding, model_id = embed_text(normalized_text)
        record = EpisodicRecord(
            memory_id=memory_id,
            text=normalized_text,
            ts_ms=ts_ms,
            text_hash=text_hash,
            decay_profile_id=str(decay_profile_id).strip() or "default",
            evidence_refs=list(evidence_refs),
            visibility=str(visibility).strip() or "internal",
            embedding_model_id=model_id,
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
