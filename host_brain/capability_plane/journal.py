"""Durable claim and receipt journal for capability execution.

The journal provides process-independent at-most-once admission.  An
abandoned or timed-out claim is deliberately never stolen automatically:
external side effects may already have happened and require reconciliation.
"""

from __future__ import annotations

from dataclasses import asdict
import json
import sqlite3
import threading
from typing import Any, Protocol

from .contracts import (
    CapabilityAttempt,
    CapabilityEvidence,
    CapabilityKind,
    CapabilityReceipt,
    CapabilityRequest,
    ReceiptStatus,
)


CLAIM_ACQUIRED = "ACQUIRED"
CLAIM_IN_FLIGHT = "IN_FLIGHT"
CLAIM_ABANDONED = "ABANDONED"
CLAIM_CONFLICT = "CONFLICT"
CLAIM_REPLAY = "REPLAY"


class CapabilityExecutionJournal(Protocol):
    def lookup(self, idempotency_key: str) -> tuple[str, CapabilityReceipt] | None: ...

    def claim(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        now_ms: int,
        lease_expires_at_ms: int,
    ) -> str: ...

    def complete(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        attempt: CapabilityAttempt,
        evidence: CapabilityEvidence,
        receipt: CapabilityReceipt,
    ) -> CapabilityReceipt: ...

    def record_late_outcome(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        status: str,
        output_digest: str | None,
        error_type: str | None,
        observed_at_ms: int,
    ) -> None: ...


class InMemoryCapabilityExecutionJournal:
    """Thread-safe journal for isolated tests only."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._claims: dict[str, tuple[str, str, int]] = {}
        self._receipts: dict[str, tuple[str, CapabilityReceipt]] = {}
        self.late_outcomes: list[dict[str, Any]] = []

    def lookup(self, idempotency_key: str) -> tuple[str, CapabilityReceipt] | None:
        with self._lock:
            return self._receipts.get(str(idempotency_key))

    def claim(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        now_ms: int,
        lease_expires_at_ms: int,
    ) -> str:
        with self._lock:
            receipt = self._receipts.get(request.idempotency_key)
            if receipt is not None:
                return CLAIM_REPLAY if receipt[0] == request.request_hash else CLAIM_CONFLICT
            existing = self._claims.get(request.idempotency_key)
            if existing is not None:
                request_hash, _owner, expiry = existing
                if request_hash != request.request_hash:
                    return CLAIM_CONFLICT
                return CLAIM_IN_FLIGHT if expiry > now_ms else CLAIM_ABANDONED
            self._claims[request.idempotency_key] = (
                request.request_hash,
                owner_token,
                int(lease_expires_at_ms),
            )
            return CLAIM_ACQUIRED

    def complete(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        attempt: CapabilityAttempt,
        evidence: CapabilityEvidence,
        receipt: CapabilityReceipt,
    ) -> CapabilityReceipt:
        del attempt, evidence
        with self._lock:
            existing = self._receipts.get(request.idempotency_key)
            if existing is not None:
                if existing[0] != request.request_hash:
                    raise RuntimeError("CAPABILITY_IDEMPOTENCY_CONFLICT")
                return existing[1]
            claim = self._claims.get(request.idempotency_key)
            if claim is None or claim[0] != request.request_hash or claim[1] != owner_token:
                raise RuntimeError("CAPABILITY_CLAIM_OWNERSHIP_LOST")
            self._receipts[request.idempotency_key] = (request.request_hash, receipt)
            return receipt

    def record_late_outcome(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        status: str,
        output_digest: str | None,
        error_type: str | None,
        observed_at_ms: int,
    ) -> None:
        with self._lock:
            self.late_outcomes.append({
                "idempotency_key": request.idempotency_key,
                "request_hash": request.request_hash,
                "owner_token": owner_token,
                "status": str(status),
                "output_digest": output_digest,
                "error_type": error_type,
                "observed_at_ms": int(observed_at_ms),
            })


class SQLiteCapabilityExecutionJournal:
    """SQLite-backed journal sharing the canonical Host ledger connection."""

    def __init__(self, ledger: Any) -> None:
        connection = getattr(ledger, "_conn", None)
        write_lock = getattr(ledger, "_write_lock", None)
        if not isinstance(connection, sqlite3.Connection) or write_lock is None:
            raise TypeError("ledger must expose the canonical SQLite connection and write lock")
        self._conn = connection
        self._write_lock = write_lock

    def lookup(self, idempotency_key: str) -> tuple[str, CapabilityReceipt] | None:
        with self._write_lock:
            row = self._conn.execute(
                "SELECT request_hash, receipt_json FROM capability_execution_receipt "
                "WHERE idempotency_key = ?",
                (str(idempotency_key),),
            ).fetchone()
        if row is None:
            return None
        return str(row["request_hash"]), _receipt_from_json(str(row["receipt_json"]))

    def claim(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        now_ms: int,
        lease_expires_at_ms: int,
    ) -> str:
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._conn.execute(
                    "SELECT request_hash FROM capability_execution_receipt WHERE idempotency_key = ?",
                    (request.idempotency_key,),
                ).fetchone()
                if receipt is not None:
                    status = CLAIM_REPLAY if str(receipt["request_hash"]) == request.request_hash else CLAIM_CONFLICT
                    self._conn.commit()
                    return status
                claim = self._conn.execute(
                    "SELECT request_hash, lease_expires_at_ms FROM capability_execution_claim "
                    "WHERE idempotency_key = ?",
                    (request.idempotency_key,),
                ).fetchone()
                if claim is not None:
                    if str(claim["request_hash"]) != request.request_hash:
                        status = CLAIM_CONFLICT
                    elif int(claim["lease_expires_at_ms"]) > int(now_ms):
                        status = CLAIM_IN_FLIGHT
                    else:
                        status = CLAIM_ABANDONED
                    self._conn.commit()
                    return status
                self._conn.execute(
                    "INSERT INTO capability_execution_claim "
                    "(idempotency_key, request_hash, request_id, attempt_id, capability_id, "
                    "kind, owner_token, state, lease_expires_at_ms, created_at_ms, updated_at_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'CLAIMED', ?, ?, ?)",
                    (
                        request.idempotency_key,
                        request.request_hash,
                        request.request_id,
                        request.attempt_id,
                        request.capability_id,
                        request.kind.value,
                        owner_token,
                        int(lease_expires_at_ms),
                        int(now_ms),
                        int(now_ms),
                    ),
                )
                self._conn.commit()
                return CLAIM_ACQUIRED
            except Exception:
                self._conn.rollback()
                raise

    def complete(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        attempt: CapabilityAttempt,
        evidence: CapabilityEvidence,
        receipt: CapabilityReceipt,
    ) -> CapabilityReceipt:
        finished_at_ms = int(attempt.finished_at_ms)
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    "SELECT request_hash, receipt_json FROM capability_execution_receipt "
                    "WHERE idempotency_key = ?",
                    (request.idempotency_key,),
                ).fetchone()
                if existing is not None:
                    if str(existing["request_hash"]) != request.request_hash:
                        raise RuntimeError("CAPABILITY_IDEMPOTENCY_CONFLICT")
                    resolved = _receipt_from_json(str(existing["receipt_json"]))
                    self._conn.commit()
                    return resolved
                claim = self._conn.execute(
                    "SELECT request_hash, owner_token FROM capability_execution_claim "
                    "WHERE idempotency_key = ?",
                    (request.idempotency_key,),
                ).fetchone()
                if (
                    claim is None
                    or str(claim["request_hash"]) != request.request_hash
                    or str(claim["owner_token"]) != owner_token
                ):
                    raise RuntimeError("CAPABILITY_CLAIM_OWNERSHIP_LOST")
                self._conn.execute(
                    "INSERT INTO capability_execution_receipt "
                    "(idempotency_key, request_hash, request_id, attempt_id, capability_id, kind, "
                    "status, attempt_json, evidence_json, receipt_json, created_at_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        request.idempotency_key,
                        request.request_hash,
                        request.request_id,
                        request.attempt_id,
                        request.capability_id,
                        request.kind.value,
                        receipt.status.value,
                        _json({**asdict(attempt), "kind": attempt.kind.value}),
                        _json(asdict(evidence)),
                        _json(receipt.to_dict()),
                        finished_at_ms,
                    ),
                )
                claim_state = "IN_DOUBT" if receipt.status is ReceiptStatus.IN_DOUBT else "COMPLETED"
                self._conn.execute(
                    "UPDATE capability_execution_claim SET state = ?, updated_at_ms = ? "
                    "WHERE idempotency_key = ? AND owner_token = ?",
                    (claim_state, finished_at_ms, request.idempotency_key, owner_token),
                )
                self._conn.commit()
                return receipt
            except Exception:
                self._conn.rollback()
                raise

    def record_late_outcome(
        self,
        request: CapabilityRequest,
        *,
        owner_token: str,
        status: str,
        output_digest: str | None,
        error_type: str | None,
        observed_at_ms: int,
    ) -> None:
        with self._write_lock:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO capability_execution_late_outcome "
                    "(idempotency_key, request_hash, owner_token, status, output_digest, "
                    "error_type, observed_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        request.idempotency_key,
                        request.request_hash,
                        owner_token,
                        str(status),
                        output_digest,
                        error_type,
                        int(observed_at_ms),
                    ),
                )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _receipt_from_json(raw: str) -> CapabilityReceipt:
    payload = json.loads(raw)
    return CapabilityReceipt(
        receipt_id=str(payload["receipt_id"]),
        request_id=str(payload["request_id"]),
        attempt_id=str(payload["attempt_id"]),
        idempotency_key=str(payload["idempotency_key"]),
        capability_id=str(payload["capability_id"]),
        kind=CapabilityKind(str(payload["kind"])),
        status=ReceiptStatus(str(payload["status"])),
        reason_code=payload.get("reason_code"),
        detail=str(payload.get("detail") or ""),
        grant_id=payload.get("grant_id"),
        evidence_ref=str(payload.get("evidence_ref") or ""),
        output=payload.get("output"),
        replayed=bool(payload.get("replayed")),
    )


__all__ = [
    "CLAIM_ABANDONED",
    "CLAIM_ACQUIRED",
    "CLAIM_CONFLICT",
    "CLAIM_IN_FLIGHT",
    "CLAIM_REPLAY",
    "CapabilityExecutionJournal",
    "InMemoryCapabilityExecutionJournal",
    "SQLiteCapabilityExecutionJournal",
]
