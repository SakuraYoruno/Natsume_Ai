"""Versioned ingress identity contracts."""

from .v1 import (
    CredentialProof,
    CredentialProofKind,
    CredentialProofMetadata,
    IdentityContractError,
    IdentityReasonCode,
    IdentitySchemaVersion,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
    ScopeMatchMode,
    ScopeMatchResult,
    match_scopes,
    parse_identity_envelope,
)

__all__ = [
    "CredentialProof",
    "CredentialProofKind",
    "CredentialProofMetadata",
    "IdentityContractError",
    "IdentityReasonCode",
    "IdentitySchemaVersion",
    "IngressIdentityEnvelope",
    "Principal",
    "PrincipalKind",
    "ScopeMatchMode",
    "ScopeMatchResult",
    "match_scopes",
    "parse_identity_envelope",
]
