"""Process-local Secure Ingress claim state for OBS SHADOW mode.

Authentication, rate limiting, and audit durability remain owned by the
central SQLite store.  This adapter keeps only idempotency and nonce claims in
memory so SHADOW traffic can never reserve a durable ENFORCE claim.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Protocol

from host_brain.secure_ingress import ClaimDecision, ClaimStatus, IngressAuditEvent
from host_brain.secure_ingress.models import digest_identifier


class _AuditSink(Protocol):
    def append(self, event: IngressAuditEvent) -> str: ...


class ShadowIngressStateError(RuntimeError):
    """Fail-closed process-local claim or durable-audit failure."""


@dataclass(slots=True)
class _ShadowClaim:
    claim_key: tuple[str, str, str]
    nonce_key: tuple[str, str]
    action: str
    required_scopes: tuple[str, ...]
    body_sha256: str
    claim_ref: str
    expires_at_ms: int
    provisional_expires_at_ms: int | None
    committed: bool = False


class ShadowIngressStateStore:
    """Keep SHADOW claims in memory while delegating every audit append."""

    def __init__(
        self,
        *,
        audit_sink: _AuditSink,
        provisional_ttl_ms: int = 30_000,
    ) -> None:
        if not callable(getattr(audit_sink, "append", None)):
            raise ShadowIngressStateError("OBS_SHADOW_AUDIT_SINK_INVALID")
        self._audit_sink = audit_sink
        self._provisional_ttl_ms = max(1, int(provisional_ttl_ms))
        self._claims_by_key: dict[tuple[str, str, str], _ShadowClaim] = {}
        self._claims_by_ref: dict[str, _ShadowClaim] = {}
        self._nonce_claims: dict[tuple[str, str], str] = {}
        self._lock = threading.RLock()

    def try_claim(
        self,
        *,
        principal_id: str,
        surface: str,
        action: str,
        required_scopes: tuple[str, ...],
        idempotency_key: str,
        nonce: str,
        body_sha256: str,
        request_id: str,
        at_ms: int,
        expires_at_ms: int,
    ) -> ClaimDecision:
        del request_id  # Claims are process-local and never become evidence.
        idempotency_digest = digest_identifier(idempotency_key)
        nonce_digest = digest_identifier(nonce)
        at = int(at_ms)
        expires_at = int(expires_at_ms)
        if idempotency_digest is None or nonce_digest is None or expires_at <= at:
            raise ShadowIngressStateError("OBS_SHADOW_CLAIM_INVALID")

        claim_key = (str(principal_id), str(surface), idempotency_digest)
        nonce_key = (str(principal_id), nonce_digest)
        scopes = tuple(required_scopes)
        with self._lock:
            self._purge_expired(at)
            existing = self._claims_by_key.get(claim_key)
            if existing is not None:
                same = (
                    existing.action == str(action)
                    and existing.required_scopes == scopes
                    and existing.body_sha256 == str(body_sha256)
                )
                return ClaimDecision(
                    ClaimStatus.DUPLICATE_SAME
                    if same
                    else ClaimStatus.IDEMPOTENCY_CONFLICT,
                    existing.claim_ref,
                )

            replay_ref = self._nonce_claims.get(nonce_key)
            if replay_ref is not None:
                return ClaimDecision(ClaimStatus.REPLAY, replay_ref)

            claim_ref = f"secure-ingress-shadow-claim://{uuid.uuid4().hex}"
            claim = _ShadowClaim(
                claim_key=claim_key,
                nonce_key=nonce_key,
                action=str(action),
                required_scopes=scopes,
                body_sha256=str(body_sha256),
                claim_ref=claim_ref,
                expires_at_ms=expires_at,
                provisional_expires_at_ms=min(
                    expires_at,
                    at + self._provisional_ttl_ms,
                ),
            )
            self._claims_by_key[claim_key] = claim
            self._claims_by_ref[claim_ref] = claim
            self._nonce_claims[nonce_key] = claim_ref
            return ClaimDecision(ClaimStatus.NEW, claim_ref)

    def commit_with_audit(
        self,
        *,
        claim_ref: str,
        event: IngressAuditEvent,
    ) -> str:
        if (
            event.claim_ref != claim_ref
            or event.decision != "ALLOW"
            or event.reason_code != "SECURE_INGRESS_ALLOWED"
            or event.side_effects_executed
        ):
            raise ShadowIngressStateError("OBS_SHADOW_ALLOW_AUDIT_MISMATCH")
        with self._lock:
            claim = self._claims_by_ref.get(str(claim_ref))
            if claim is None or claim.committed:
                raise ShadowIngressStateError("OBS_SHADOW_CLAIM_NOT_PROVISIONAL")
            audit_ref = str(self._audit_sink.append(event)).strip()
            if not audit_ref:
                raise ShadowIngressStateError("OBS_SHADOW_AUDIT_APPEND_FAILED")
            claim.committed = True
            claim.provisional_expires_at_ms = None
            return audit_ref

    def abort_claim(self, *, claim_ref: str) -> None:
        with self._lock:
            claim = self._claims_by_ref.get(str(claim_ref))
            if claim is not None and not claim.committed:
                self._drop_claim(claim)

    def append(self, event: IngressAuditEvent) -> str:
        return self._audit_sink.append(event)

    def _purge_expired(self, at_ms: int) -> None:
        for claim in tuple(self._claims_by_ref.values()):
            provisional_expired = (
                not claim.committed
                and claim.provisional_expires_at_ms is not None
                and claim.provisional_expires_at_ms <= at_ms
            )
            if claim.expires_at_ms <= at_ms or provisional_expired:
                self._drop_claim(claim)

    def _drop_claim(self, claim: _ShadowClaim) -> None:
        self._claims_by_key.pop(claim.claim_key, None)
        self._claims_by_ref.pop(claim.claim_ref, None)
        if self._nonce_claims.get(claim.nonce_key) == claim.claim_ref:
            self._nonce_claims.pop(claim.nonce_key, None)


__all__ = ["ShadowIngressStateError", "ShadowIngressStateStore"]
