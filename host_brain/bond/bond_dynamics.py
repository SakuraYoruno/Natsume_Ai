"""Single-writer entry for Bond SSOT updates."""

from __future__ import annotations

from dataclasses import dataclass
import uuid
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.bond.neo4j_client import BOND_WRITER_ID, InMemoryNeo4jBondClient
from host_brain.core.errors import BOND_EVIDENCE_REQUIRED, BOND_SINGLE_WRITER_VIOLATION
from host_brain.core.ids import now_ms


S_BOND_UPDATE = "S_BOND_UPDATE"

STATUS_OK = "OK"
STATUS_REJECTED = "REJECTED"


@dataclass(frozen=True)
class BondUpdateResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    event: dict[str, Any]


class BondDynamicsWriter:
    """Only legal write path into Bond SSOT graph."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        neo4j_client: InMemoryNeo4jBondClient | None = None,
    ) -> None:
        self.ledger = ledger
        self.neo4j_client = neo4j_client or InMemoryNeo4jBondClient()

    def apply_update(
        self,
        *,
        request_id: str,
        intent_id: str,
        session_id: str,
        seq: int,
        user_id: str,
        topic_id: str,
        capability_hint: str,
        evidence_refs: list[str],
        payload: dict[str, Any] | None = None,
        update_kind: str = "INTERACTION",
        event_id: str | None = None,
        ts_ms: int | None = None,
    ) -> BondUpdateResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        refs = _normalize_evidence_refs(evidence_refs)
        if not refs:
            return self._reject(
                request_id=request_id,
                intent_id=intent_id,
                session_id=session_id,
                seq=seq,
                event_id=event_id or _new_event_id(),
                user_id=user_id,
                topic_id=topic_id,
                capability_hint=capability_hint,
                update_kind=update_kind,
                reason_code=BOND_EVIDENCE_REQUIRED,
                detail="bond update requires non-empty evidence_refs",
                evidence_refs=[],
                payload=dict(payload or {}),
                ts_ms=now_value,
            )

        event_key = str(event_id or _new_event_id()).strip()
        try:
            written = self.neo4j_client.write_bond_event(
                writer_id=BOND_WRITER_ID,
                event_id=event_key,
                user_id=str(user_id),
                session_id=str(session_id),
                topic_id=str(topic_id),
                capability_hint=str(capability_hint),
                evidence_refs=refs,
                payload=dict(payload or {}),
                ts_ms=now_value,
            )
        except PermissionError:
            return self._reject(
                request_id=request_id,
                intent_id=intent_id,
                session_id=session_id,
                seq=seq,
                event_id=event_key,
                user_id=user_id,
                topic_id=topic_id,
                capability_hint=capability_hint,
                update_kind=update_kind,
                reason_code=BOND_SINGLE_WRITER_VIOLATION,
                detail="bond update blocked by single-writer policy",
                evidence_refs=refs,
                payload=dict(payload or {}),
                ts_ms=now_value,
            )

        event = {
            "request_id": request_id,
            "intent_id": intent_id,
            "session_id": session_id,
            "seq": int(seq),
            "event_id": event_key,
            "user_id": str(user_id),
            "topic_id": str(topic_id),
            "capability_hint": str(capability_hint),
            "update_kind": str(update_kind),
            "evidence_refs": refs,
            "payload": dict(payload or {}),
            "neo4j_event": written,
            "stage": S_BOND_UPDATE,
            "status": STATUS_OK,
            "reason_code": None,
            "ts_ms": now_value,
        }
        self._log_update(
            request_id=request_id,
            intent_id=intent_id,
            session_id=session_id,
            seq=seq,
            event_id=event_key,
            user_id=user_id,
            topic_id=topic_id,
            capability_hint=capability_hint,
            update_kind=update_kind,
            status=STATUS_OK,
            reason_code=None,
            evidence_refs=refs,
            payload=event["payload"],
            detail="bond update applied",
            ts_ms=now_value,
        )
        return BondUpdateResult(
            success=True,
            status=STATUS_OK,
            reason_code=None,
            stage=S_BOND_UPDATE,
            detail="bond update applied",
            event=event,
        )

    def _reject(
        self,
        *,
        request_id: str,
        intent_id: str,
        session_id: str,
        seq: int,
        event_id: str,
        user_id: str,
        topic_id: str,
        capability_hint: str,
        update_kind: str,
        reason_code: str,
        detail: str,
        evidence_refs: list[str],
        payload: dict[str, Any],
        ts_ms: int,
    ) -> BondUpdateResult:
        event = {
            "request_id": request_id,
            "intent_id": intent_id,
            "session_id": session_id,
            "seq": int(seq),
            "event_id": str(event_id),
            "user_id": str(user_id),
            "topic_id": str(topic_id),
            "capability_hint": str(capability_hint),
            "update_kind": str(update_kind),
            "evidence_refs": list(evidence_refs),
            "payload": dict(payload),
            "stage": S_BOND_UPDATE,
            "status": STATUS_REJECTED,
            "reason_code": reason_code,
            "ts_ms": ts_ms,
        }
        self._log_update(
            request_id=request_id,
            intent_id=intent_id,
            session_id=session_id,
            seq=seq,
            event_id=str(event_id),
            user_id=user_id,
            topic_id=topic_id,
            capability_hint=capability_hint,
            update_kind=update_kind,
            status=STATUS_REJECTED,
            reason_code=reason_code,
            evidence_refs=list(evidence_refs),
            payload=dict(payload),
            detail=detail,
            ts_ms=ts_ms,
        )
        return BondUpdateResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=reason_code,
            stage=S_BOND_UPDATE,
            detail=detail,
            event=event,
        )

    def _log_update(
        self,
        *,
        request_id: str,
        intent_id: str,
        session_id: str,
        seq: int,
        event_id: str,
        user_id: str,
        topic_id: str,
        capability_hint: str,
        update_kind: str,
        status: str,
        reason_code: str | None,
        evidence_refs: list[str],
        payload: dict[str, Any],
        detail: str,
        ts_ms: int,
    ) -> None:
        if self.ledger is None:
            return
        if hasattr(self.ledger, "log_bond_update"):
            self.ledger.log_bond_update(
                {
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "session_id": session_id,
                    "seq": int(seq),
                    "event_id": event_id,
                    "user_id": str(user_id),
                    "topic_id": str(topic_id),
                    "capability_hint": str(capability_hint),
                    "update_kind": str(update_kind),
                    "status": status,
                    "reason_code": reason_code,
                    "stage": S_BOND_UPDATE,
                    "evidence_refs": list(evidence_refs),
                    "payload": dict(payload),
                    "detail": detail,
                    "ts_ms": int(ts_ms),
                }
            )
            return
        if reason_code is not None and hasattr(self.ledger, "log_error"):
            self.ledger.log_error(
                request_id=request_id,
                stage=S_BOND_UPDATE,
                reason_code=reason_code,
                detail=detail,
            )


def _new_event_id() -> str:
    return f"bond_evt_{uuid.uuid4().hex}"


def _normalize_evidence_refs(value: list[str] | Any) -> list[str]:
    if not isinstance(value, list):
        return []
    refs: list[str] = []
    for item in value:
        ref = str(item or "").strip()
        if ref:
            refs.append(ref)
    return refs
