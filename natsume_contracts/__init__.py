"""Stable, runtime-independent contracts shared by Natsume components.

This package intentionally depends on the Python standard library only.  It is
safe for edge and host processes to import without pulling either runtime into
the other one.
"""

from .identity import (
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
