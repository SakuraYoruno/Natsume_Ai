"""Secure Ingress public API."""

from .adapter import (
    SECURE_INGRESS_MODE_ENV,
    SecureIngressAdapter,
    SecureIngressAdapterOutcome,
    SecureIngressMode,
    load_secure_ingress_mode,
)
from .authority import (
    SECURE_INGRESS_ENFORCE_REQUIRED,
    SECURE_INGRESS_HTTP_CONTRACT_INVALID,
    SecureIngressAuthority,
    SecureIngressAuthorityEvaluator,
    SecureIngressHttpAuthorityAdapter,
)
from .credential import (
    CredentialVerifierRegistry,
    HMAC_SHA256_ALGORITHM,
    HmacSha256CredentialVerifier,
    OpaqueBearerCredentialVerifier,
    OPAQUE_BEARER_ALGORITHM,
    build_hmac_sha256_signature,
)
from .emergency_audit import FileEmergencyAuditSink
from .models import (
    AuditStatus,
    ClaimDecision,
    ClaimStatus,
    CredentialDecision,
    IngressAuditEvent,
    IngressReasonCode,
    IngressStage,
    PrincipalAuthority,
    RateDecision,
    SecureIngressDecision,
    SecureIngressRequest,
)
from .service import SecureIngressService
from .store import SQLiteSecureIngressStore, SecureIngressStoreError
from .surfaces import (
    IngressSurface,
    IngressSurfacePolicy,
    M48B_P0_SURFACE_POLICIES,
    SECURE_INGRESS_SURFACE_POLICIES,
    build_surface_request,
)

__all__ = [
    "ClaimDecision",
    "ClaimStatus",
    "CredentialDecision",
    "CredentialVerifierRegistry",
    "HmacSha256CredentialVerifier",
    "FileEmergencyAuditSink",
    "HMAC_SHA256_ALGORITHM",
    "IngressAuditEvent",
    "AuditStatus",
    "IngressReasonCode",
    "IngressStage",
    "IngressSurface",
    "IngressSurfacePolicy",
    "M48B_P0_SURFACE_POLICIES",
    "SECURE_INGRESS_SURFACE_POLICIES",
    "OpaqueBearerCredentialVerifier",
    "OPAQUE_BEARER_ALGORITHM",
    "PrincipalAuthority",
    "RateDecision",
    "SecureIngressDecision",
    "SECURE_INGRESS_ENFORCE_REQUIRED",
    "SECURE_INGRESS_HTTP_CONTRACT_INVALID",
    "SECURE_INGRESS_MODE_ENV",
    "SecureIngressAdapter",
    "SecureIngressAdapterOutcome",
    "SecureIngressAuthority",
    "SecureIngressAuthorityEvaluator",
    "SecureIngressHttpAuthorityAdapter",
    "SecureIngressMode",
    "SecureIngressRequest",
    "SecureIngressService",
    "SQLiteSecureIngressStore",
    "SecureIngressStoreError",
    "build_hmac_sha256_signature",
    "build_surface_request",
    "load_secure_ingress_mode",
]
