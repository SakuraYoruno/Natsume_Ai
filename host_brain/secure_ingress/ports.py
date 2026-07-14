"""Dependency ports used by SecureIngressService."""

from __future__ import annotations

from typing import Protocol

from .models import (
    ClaimDecision,
    CredentialDecision,
    IngressAuditEvent,
    PrincipalAuthority,
    RateDecision,
    SecureIngressRequest,
)


class PrincipalStore(Protocol):
    def resolve(
        self,
        *,
        principal_id: str,
        key_id: str,
        at_ms: int,
    ) -> PrincipalAuthority | None: ...


class CredentialVerifier(Protocol):
    def verify(
        self,
        *,
        request: SecureIngressRequest,
        authority: PrincipalAuthority,
    ) -> CredentialDecision: ...


class IngressRateLimiter(Protocol):
    def consume(
        self,
        *,
        principal_id: str,
        action: str,
        required_scopes: tuple[str, ...],
        at_ms: int,
    ) -> RateDecision: ...


class IngressAttemptLimiter(Protocol):
    def consume_attempt(
        self,
        *,
        transport_key: str,
        claimed_principal_id: str | None,
        key_id: str | None,
        at_ms: int,
    ) -> RateDecision: ...


class IdempotencyStore(Protocol):
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
    ) -> ClaimDecision: ...

    def commit_with_audit(
        self,
        *,
        claim_ref: str,
        event: IngressAuditEvent,
    ) -> str: ...

    def abort_claim(self, *, claim_ref: str) -> None: ...


class IngressAuditSink(Protocol):
    def append(self, event: IngressAuditEvent) -> str: ...


class SecureIngressStateStore(IdempotencyStore, IngressAuditSink, Protocol):
    """Primary store that atomically commits an ALLOW claim and its audit."""


__all__ = [
    "CredentialVerifier",
    "IdempotencyStore",
    "IngressAuditSink",
    "IngressAttemptLimiter",
    "IngressRateLimiter",
    "PrincipalStore",
    "SecureIngressStateStore",
]
