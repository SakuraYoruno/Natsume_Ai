"""Credential verifiers for Secure Ingress.

Verifiers receive runtime proof material but return only digests suitable for
audit.  They never persist or expose the raw signature/token.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Callable, Mapping

from natsume_contracts.identity import CredentialProofKind

from .models import (
    CredentialDecision,
    IngressReasonCode,
    PrincipalAuthority,
    SecureIngressRequest,
    digest_identifier,
)


SecretResolver = Callable[[PrincipalAuthority], str | bytes | None]
TokenDigestResolver = Callable[[PrincipalAuthority], str | None]
HMAC_SHA256_ALGORITHM = "hmac-sha256"
OPAQUE_BEARER_ALGORITHM = "opaque-sha256"


def build_hmac_sha256_signature(request: SecureIngressRequest, secret: str | bytes) -> str:
    key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return hmac.new(key, request.canonical_auth_bytes(), hashlib.sha256).hexdigest()


def _metadata_matches(request: SecureIngressRequest, authority: PrincipalAuthority) -> bool:
    if request.identity is None or request.credential is None:
        return False
    metadata = request.identity.credential_proof
    return (
        request.credential.metadata == metadata
        and metadata.key_id == authority.key_id
        and metadata.kind is authority.credential_kind
        and metadata.algorithm.lower() == authority.algorithm.lower()
        and request.identity.principal.principal_id == authority.principal_id
    )


class HmacSha256CredentialVerifier:
    def __init__(self, *, secret_resolver: SecretResolver) -> None:
        self._secret_resolver = secret_resolver

    def verify(
        self,
        *,
        request: SecureIngressRequest,
        authority: PrincipalAuthority,
    ) -> CredentialDecision:
        if authority.credential_kind is not CredentialProofKind.HMAC_SHA256:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        if authority.algorithm != HMAC_SHA256_ALGORITHM:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        if not _metadata_matches(request, authority) or request.credential is None:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        secret = self._secret_resolver(authority)
        if not secret:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        raw = request.credential.raw_proof
        try:
            supplied = raw.decode("ascii") if isinstance(raw, bytes) else raw
        except UnicodeDecodeError:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        expected = build_hmac_sha256_signature(request, secret)
        if len(supplied) != len(expected) or not hmac.compare_digest(expected, supplied):
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        return CredentialDecision(
            True,
            None,
            credential_ref_digest=digest_identifier(request.identity.credential_proof.proof_id),
        )


class OpaqueBearerCredentialVerifier:
    def __init__(self, *, token_digest_resolver: TokenDigestResolver) -> None:
        self._token_digest_resolver = token_digest_resolver

    def verify(
        self,
        *,
        request: SecureIngressRequest,
        authority: PrincipalAuthority,
    ) -> CredentialDecision:
        if authority.credential_kind is not CredentialProofKind.BEARER_OPAQUE:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        if authority.algorithm != OPAQUE_BEARER_ALGORITHM:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        if not _metadata_matches(request, authority) or request.credential is None:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        raw = request.credential.raw_proof
        token = raw if isinstance(raw, bytes) else raw.encode("utf-8")
        supplied_digest = f"sha256:{hashlib.sha256(token).hexdigest()}"
        expected_digest = str(self._token_digest_resolver(authority) or "")
        if not expected_digest or not hmac.compare_digest(expected_digest, supplied_digest):
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        return CredentialDecision(
            True,
            None,
            credential_ref_digest=digest_identifier(request.identity.credential_proof.proof_id),
        )


class CredentialVerifierRegistry:
    def __init__(
        self,
        verifiers: Mapping[CredentialProofKind, object],
    ) -> None:
        self._verifiers = dict(verifiers)

    def verify(
        self,
        *,
        request: SecureIngressRequest,
        authority: PrincipalAuthority,
    ) -> CredentialDecision:
        if request.identity is None:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_MISSING)
        verifier = self._verifiers.get(request.identity.credential_proof.kind)
        if verifier is None or not hasattr(verifier, "verify"):
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        try:
            decision = verifier.verify(request=request, authority=authority)
        except Exception:
            return CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        return decision if isinstance(decision, CredentialDecision) else CredentialDecision(
            False,
            IngressReasonCode.CREDENTIAL_INVALID,
        )


__all__ = [
    "CredentialVerifierRegistry",
    "HMAC_SHA256_ALGORITHM",
    "HmacSha256CredentialVerifier",
    "OpaqueBearerCredentialVerifier",
    "OPAQUE_BEARER_ALGORITHM",
    "build_hmac_sha256_signature",
]
