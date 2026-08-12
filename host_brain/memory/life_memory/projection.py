"""Durable LifeMemory projection worker and legacy episodic adapter."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any, Protocol
import uuid

from host_brain.persistence.life_memory_repository import LifeMemoryRepository


class EpisodicProjectionStorePort(Protocol):
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
    ) -> Any: ...

    def delete_memory(
        self,
        *,
        memory_id: str,
        evidence_refs: list[str],
        request_id: str | None = None,
        intent_id: str | None = None,
        ts_ms: int | None = None,
    ) -> Any: ...


class LegacyEpisodicProjection:
    """Projection-only adapter over the legacy Chroma-backed store.

    Production chat code never receives this store.  The adapter can only
    consume an already committed LifeMemory episode claimed from the durable
    outbox.
    """

    def __init__(self, store: EpisodicProjectionStorePort) -> None:
        self._store = store

    def project(self, payload: Mapping[str, Any]) -> None:
        episode = _mapping(payload.get("episode"))
        outbox = _mapping(payload.get("outbox"))
        evidence_rows = [
            _mapping(row)
            for row in list(payload.get("evidence") or ())
            if isinstance(row, Mapping)
        ]
        outbox_payload = _json_mapping(outbox.get("payload_json"))
        evidence_refs = [
            str(ref).strip()
            for ref in list(outbox_payload.get("evidence_refs") or ())
            if str(ref or "").strip()
        ]
        episode_id = str(episode.get("episode_id") or "").strip()
        if episode_id:
            evidence_refs.append(f"life://episode/{episode_id}")
        primary_ref = str(episode.get("primary_ref") or "").strip()
        if primary_ref:
            evidence_refs.append(primary_ref)
        evidence_refs = list(dict.fromkeys(evidence_refs))
        if not evidence_refs:
            raise RuntimeError("投影缺少证据引用")

        event_type = str(outbox.get("event_type") or "UPSERT").strip().upper()
        if event_type == "TOMBSTONE":
            self._tombstone(
                episode_id=episode_id,
                evidence_refs=evidence_refs,
                request_id=str(outbox.get("request_id") or "").strip() or None,
                intent_id=str(episode.get("intent") or "").strip() or None,
                ts_ms=int(episode.get("last_touched_ts_ms") or episode.get("created_ts_ms") or 0),
            )
            return

        metadata = _json_mapping(episode.get("metadata"))
        result = self._store.write_memory(
            text=_projection_text(episode, evidence_rows),
            evidence_refs=evidence_refs,
            request_id=str(outbox.get("request_id") or "").strip() or None,
            intent_id=str(episode.get("intent") or "").strip() or None,
            decay_profile_id=str(episode.get("decay_profile_id") or "SLOW").strip() or "SLOW",
            visibility="internal",
            ts_ms=int(episode.get("created_ts_ms") or 0),
            emotion_tag=str(metadata.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL",
            emotion_valence=_safe_float(metadata.get("emotion_valence"), 0.0),
            emotion_arousal=_safe_float(metadata.get("emotion_arousal"), 0.3),
        )
        if not bool(getattr(result, "success", False)):
            reason = str(
                getattr(result, "reason_code", None)
                or getattr(result, "detail", None)
                or "旧向量投影被拒绝"
            )
            raise RuntimeError(reason)

    def _tombstone(
        self,
        *,
        episode_id: str,
        evidence_refs: list[str],
        request_id: str | None,
        intent_id: str | None,
        ts_ms: int,
    ) -> None:
        memory_id = _episodic_memory_id(evidence_refs)
        if not memory_id:
            raise RuntimeError("墓碑投影无法定位稳定向量标识")
        delete_memory = getattr(self._store, "delete_memory", None)
        if callable(delete_memory):
            result = delete_memory(
                memory_id=memory_id,
                evidence_refs=evidence_refs,
                request_id=request_id,
                intent_id=intent_id,
                ts_ms=ts_ms,
            )
            if bool(getattr(result, "success", False)):
                return
        chroma = getattr(self._store, "chroma", None)
        delete = getattr(chroma, "delete", None)
        if callable(delete):
            delete(memory_id)
            return
        raise RuntimeError(f"墓碑投影未删除 Episode {episode_id} 的向量")


class LifeMemoryProjectionWorker:
    """Claim, project and acknowledge durable LifeMemory outbox rows."""

    def __init__(
        self,
        *,
        repository: LifeMemoryRepository,
        projector: LegacyEpisodicProjection,
        owner_id: str | None = None,
        lease_ms: int = 60_000,
    ) -> None:
        self.repository = repository
        self.projector = projector
        self.owner_id = str(owner_id or f"life-memory-projector-{uuid.uuid4().hex}")
        self.lease_ms = max(1, int(lease_ms))

    def project_outbox(self, outbox_id: str) -> str:
        claimed = self.repository.claim_projection(
            outbox_id,
            owner_id=self.owner_id,
            lease_ms=self.lease_ms,
        )
        if claimed is None:
            return self.repository.projection_status(outbox_id) or "MISSING"
        try:
            self.projector.project(claimed)
        except Exception as exc:
            self.repository.mark_projection_failed(
                outbox_id,
                owner_id=self.owner_id,
                detail=str(exc),
            )
            return "FAILED"
        self.repository.mark_projection_published(outbox_id, owner_id=self.owner_id)
        return "PUBLISHED"

    def drain_pending(self, *, limit: int = 100) -> dict[str, int]:
        summary = {"published": 0, "failed": 0, "skipped": 0}
        for outbox_id in self.repository.list_ready_projection_ids(limit=limit):
            status = self.project_outbox(outbox_id)
            if status == "PUBLISHED":
                summary["published"] += 1
            elif status == "FAILED":
                summary["failed"] += 1
            else:
                summary["skipped"] += 1
        return summary


def _projection_text(episode: Mapping[str, Any], evidence: list[Mapping[str, Any]]) -> str:
    user_text = ""
    assistant_text = ""
    for row in evidence:
        kind = str(row.get("evidence_kind") or "").strip()
        if kind == "user_text" and not user_text:
            user_text = str(row.get("content") or "").strip()
        elif kind == "assistant_reply" and not assistant_text:
            assistant_text = str(row.get("content") or "").strip()
    session_id = str(episode.get("session_id") or "").strip()
    parts = [f"[session:{session_id}]" if session_id else "[chat]"]
    if user_text:
        parts.append(f"User: {user_text}")
    if assistant_text:
        parts.append(f"Assistant: {assistant_text}")
    if len(parts) == 1:
        summary = str(episode.get("summary") or "").strip()
        if summary:
            parts.append(summary)
    text = "\n".join(parts).strip()
    if not text or text in {"[chat]", f"[session:{session_id}]"}:
        raise RuntimeError("LIFE_MEMORY_PROJECTION_TEXT_EMPTY")
    return text


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _episodic_memory_id(evidence_refs: list[str]) -> str | None:
    """复用旧 EpisodicStore 的稳定身份算法，保证重启后仍能删除旧向量。"""
    priority = (
        "gateway://message/", "qq://message/", "wechat://message/", "chat://turn/",
        "chat://request/", "surface://reply/", "scheduler://event/", "inner_world://event/",
        "inner_activity://event/", "voice://event/",
    )
    for prefix in priority:
        for reference in evidence_refs:
            value = str(reference or "").strip()
            if value.startswith(prefix) and len(value) > len(prefix):
                digest = hashlib.sha256(f"episodic:v1:{value}".encode("utf-8")).hexdigest()
                return f"mem_{digest[:32]}"
    return None


__all__ = [
    "EpisodicProjectionStorePort",
    "LegacyEpisodicProjection",
    "LifeMemoryProjectionWorker",
]
