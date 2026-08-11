"""Apply one explicit memory proposal after canonical Task Acceptance."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
from typing import Any

from host_brain.contracts.memory_acceptance_effects import (
    EXPLICIT_MEMORY_STORE_SCHEMA_V1,
)


class AcceptedMemoryStoreError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or "MEMORY_ACCEPTANCE_EFFECT_FAILED")
        self.detail = str(detail or "accepted memory write failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class AcceptedMemoryStoreHandler:
    """Write/reconcile a bounded user-requested memory with stable identity."""

    def __init__(self, *, episodic_store_provider: Callable[[], Any]) -> None:
        if not callable(episodic_store_provider):
            raise TypeError("episodic_store_provider must be callable")
        self._episodic_store_provider = episodic_store_provider

    def __call__(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        value = _validate_payload(payload)
        store = self._store()
        evidence_refs = _evidence_refs(value)
        result = store.write_memory(
            text=value["content"],
            evidence_refs=evidence_refs,
            request_id=value["request_id"],
            intent_id=value["intent_id"],
            emotion_tag=value["emotion_tag"],
            ts_ms=value["created_at_ms"],
        )
        if not bool(getattr(result, "success", False)):
            raise AcceptedMemoryStoreError(
                str(getattr(result, "reason_code", None) or "MEMORY_WRITE_FAILED"),
                str(getattr(result, "detail", None) or "episodic memory write failed"),
            )
        memory_id = str(getattr(result, "memory_id", None) or "").strip()
        if memory_id != _expected_memory_id(value["request_id"]):
            raise AcceptedMemoryStoreError(
                "MEMORY_RECEIPT_IDENTITY_DRIFT",
                "episodic memory identity differs from the accepted request",
            )
        return {
            "status": "APPLIED",
            "receipt_ref": f"memory://episodic/{memory_id}",
            "memory_id": memory_id,
            "operation_id": value["operation_id"],
        }

    def reconcile(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        value = _validate_payload(payload)
        store = self._store()
        memory_id = _expected_memory_id(value["request_id"])
        replay = getattr(store, "replay_ops", None)
        if not callable(replay):
            raise AcceptedMemoryStoreError(
                "MEMORY_RECONCILIATION_UNAVAILABLE",
                "episodic store has no read-only operation projection",
            )
        rows = tuple(replay(memory_id=memory_id) or ())
        matched = any(
            isinstance(row, Mapping)
            and str(row.get("memory_id") or "").strip() == memory_id
            and str(row.get("request_id") or "").strip() == value["request_id"]
            and str(row.get("status") or "").strip().upper() in {"SUCCESS", "SKIPPED"}
            for row in rows
        )
        if not matched:
            raise AcceptedMemoryStoreError(
                "MEMORY_RECONCILIATION_PENDING",
                "accepted memory write has no durable operation receipt",
            )
        return {
            "status": "RECONCILED",
            "receipt_ref": f"memory://episodic/{memory_id}",
            "memory_id": memory_id,
            "operation_id": value["operation_id"],
        }

    def _store(self) -> Any:
        store = self._episodic_store_provider()
        if not callable(getattr(store, "write_memory", None)):
            raise AcceptedMemoryStoreError(
                "MEMORY_WRITER_UNAVAILABLE",
                "accepted memory handler has no episodic writer",
            )
        return store


def _validate_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(payload or {})
    expected = {
        "schema_version",
        "operation_id",
        "request_id",
        "intent_id",
        "session_id",
        "person_id",
        "content",
        "memory_type",
        "tags",
        "importance",
        "source",
        "emotion_tag",
        "created_at_ms",
    }
    if set(value) != expected or value.get("schema_version") != EXPLICIT_MEMORY_STORE_SCHEMA_V1:
        raise AcceptedMemoryStoreError(
            "MEMORY_ACCEPTANCE_EFFECT_INVALID",
            "accepted memory payload schema or fields drifted",
        )
    required = ("operation_id", "request_id", "intent_id", "session_id", "person_id", "content")
    if any(not str(value.get(key) or "").strip() for key in required):
        raise AcceptedMemoryStoreError(
            "MEMORY_ACCEPTANCE_EFFECT_INVALID",
            "accepted memory payload is missing a required identity or content",
        )
    if not str(value["operation_id"]).startswith("memory-store://"):
        raise AcceptedMemoryStoreError(
            "MEMORY_ACCEPTANCE_EFFECT_INVALID",
            "accepted memory operation identity is invalid",
        )
    memory_type = str(value.get("memory_type") or "").strip().lower()
    if memory_type not in {"episodic", "semantic"}:
        raise AcceptedMemoryStoreError(
            "MEMORY_ACCEPTANCE_EFFECT_INVALID",
            "accepted memory type is invalid",
        )
    tags = value.get("tags")
    if not isinstance(tags, list) or len(tags) > 16 or any(
        not isinstance(tag, str) or not tag.strip() or len(tag) > 64 for tag in tags
    ):
        raise AcceptedMemoryStoreError(
            "MEMORY_ACCEPTANCE_EFFECT_INVALID",
            "accepted memory tags are invalid",
        )
    try:
        importance = float(value.get("importance"))
        created_at_ms = int(value.get("created_at_ms"))
    except (TypeError, ValueError) as exc:
        raise AcceptedMemoryStoreError(
            "MEMORY_ACCEPTANCE_EFFECT_INVALID",
            "accepted memory numeric fields are invalid",
        ) from exc
    if not 0.0 <= importance <= 1.0 or created_at_ms <= 0:
        raise AcceptedMemoryStoreError(
            "MEMORY_ACCEPTANCE_EFFECT_INVALID",
            "accepted memory numeric fields are outside their bounds",
        )
    value["memory_type"] = memory_type
    value["importance"] = importance
    value["created_at_ms"] = created_at_ms
    value["emotion_tag"] = str(value.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
    value["content"] = str(value["content"]).strip()
    return value


def _evidence_refs(value: Mapping[str, Any]) -> list[str]:
    refs = [
        f"chat://request/{value['request_id']}",
        str(value["operation_id"]),
        f"session:{value['session_id']}",
        f"person:{value['person_id']}",
        f"memory_type:{value['memory_type']}",
    ]
    source = str(value.get("source") or "").strip()
    if source:
        refs.append(f"source:{source}")
    refs.extend(f"tag:{tag}" for tag in value["tags"])
    return refs


def _expected_memory_id(request_id: str) -> str:
    episode_key = f"chat://request/{str(request_id or '').strip()}"
    digest = hashlib.sha256(f"episodic:v1:{episode_key}".encode("utf-8")).hexdigest()
    return f"mem_{digest[:32]}"


__all__ = ["AcceptedMemoryStoreError", "AcceptedMemoryStoreHandler"]
