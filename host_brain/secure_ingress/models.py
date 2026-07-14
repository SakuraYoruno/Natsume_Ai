"""Contracts internal to the fail-closed Secure Ingress decision chain."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any

from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    IngressIdentityEnvelope,
    PrincipalKind,
)


class IngressReasonCode(str, Enum):
    ALLOWED = "SECURE_INGRESS_ALLOWED"
    SCHEMA_INVALID = "SECURE_INGRESS_SCHEMA_INVALID"
    PRINCIPAL_INVALID = "SECURE_INGRESS_PRINCIPAL_INVALID"
    CREDENTIAL_MISSING = "SECURE_INGRESS_CREDENTIAL_MISSING"
    CREDENTIAL_INVALID = "SECURE_INGRESS_CREDENTIAL_INVALID"
    CREDENTIAL_EXPIRED = "SECURE_INGRESS_CREDENTIAL_EXPIRED"
    SCOPE_DENIED = "SECURE_INGRESS_SCOPE_DENIED"
    RATE_LIMITED = "SECURE_INGRESS_RATE_LIMITED"
    RATE_BACKEND_UNAVAILABLE = "SECURE_INGRESS_RATE_BACKEND_UNAVAILABLE"
    REPLAY_DETECTED = "SECURE_INGRESS_REPLAY_DETECTED"
    IDEMPOTENCY_KEY_MISSING = "SECURE_INGRESS_IDEMPOTENCY_KEY_MISSING"
    IDEMPOTENCY_DUPLICATE = "SECURE_INGRESS_IDEMPOTENCY_DUPLICATE"
    IDEMPOTENCY_CONFLICT = "SECURE_INGRESS_IDEMPOTENCY_CONFLICT"
    AUDIT_UNAVAILABLE = "SECURE_INGRESS_AUDIT_UNAVAILABLE"
    INTERNAL_ERROR = "SECURE_INGRESS_INTERNAL_ERROR"


class IngressStage(str, Enum):
    SCHEMA = "SCHEMA"
    PRINCIPAL = "PRINCIPAL"
    CREDENTIAL = "CREDENTIAL"
    SCOPE = "SCOPE"
    RATE = "RATE"
    CLAIM = "CLAIM"
    AUDIT = "AUDIT"
    COMPLETE = "COMPLETE"


class AuditStatus(str, Enum):
    PRIMARY_DURABLE = "PRIMARY_DURABLE"
    EMERGENCY_DURABLE = "EMERGENCY_DURABLE"
    UNAVAILABLE = "UNAVAILABLE"


class ClaimStatus(str, Enum):
    NEW = "NEW"
    DUPLICATE_SAME = "DUPLICATE_SAME"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    REPLAY = "REPLAY"


@dataclass(frozen=True, slots=True)
class PrincipalAuthority:
    principal_id: str
    kind: PrincipalKind
    tenant_id: str
    key_id: str
    credential_kind: CredentialProofKind
    algorithm: str
    granted_scopes: tuple[str, ...]
    allowed_audiences: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    version: int
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class CredentialDecision:
    valid: bool
    reason_code: IngressReasonCode | None
    credential_ref_digest: str | None = None


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    current_count: int
    limit: int
    window_ms: int
    backend: str
    available: bool = True
    lane: str = "AUTHORITY"


@dataclass(frozen=True, slots=True)
class ClaimDecision:
    status: ClaimStatus
    claim_ref: str | None = None
    existing_result_ref: str | None = None
    canonical_request_id: str | None = None
    canonical_body_sha256: str | None = None
    canonical_audit_ref: str | None = None


@dataclass(frozen=True, slots=True)
class SecureIngressRequest:
    identity: IngressIdentityEnvelope | None
    credential: CredentialProof | None
    surface: str
    method: str
    normalized_path: str
    action: str
    required_scopes: tuple[str, ...]
    idempotency_key: str
    nonce: str
    raw_body: bytes
    received_at_epoch_ms: int
    transport_key: str
    correlation_id: str | None = None

    @property
    def body_sha256(self) -> str:
        return hashlib.sha256(self.raw_body).hexdigest()

    @property
    def request_id(self) -> str | None:
        return self.identity.request_id if self.identity is not None else None

    @property
    def principal_id(self) -> str | None:
        return self.identity.principal.principal_id if self.identity is not None else None

    def canonical_auth_bytes(self) -> bytes:
        if self.identity is None:
            raise ValueError("identity is required for canonical auth input")
        payload = {
            "action": self.action,
            "audience": self.identity.audience,
            "body_sha256": self.body_sha256,
            "correlation_id": self.correlation_id,
            "credential": self.identity.credential_proof.to_dict(),
            "idempotency_key": self.idempotency_key,
            "identity_schema": self.identity.schema_version.value,
            "method": self.method.upper(),
            "nonce": self.nonce,
            "path": self.normalized_path,
            "principal": self.identity.principal.to_dict(),
            "request_id": self.identity.request_id,
            "requested_scopes": list(self.identity.requested_scopes),
            "required_scopes": sorted(set(self.required_scopes)),
            "schema_version": "natsume.secure-ingress.signature.v1",
            "surface": self.surface,
            "transport_key_digest": digest_identifier(self.transport_key),
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class IngressAuditEvent:
    request_id: str | None
    correlation_id: str | None
    surface: str
    action: str
    method: str
    normalized_path: str
    principal_id: str | None
    tenant_id: str | None
    key_id: str | None
    credential_kind: str | None
    credential_ref_digest: str | None
    requested_scopes: tuple[str, ...]
    required_scopes: tuple[str, ...]
    body_sha256: str
    idempotency_digest: str | None
    nonce_digest: str | None
    transport_key_digest: str | None
    decision: str
    reason_code: str
    stage: str
    claim_ref: str | None
    rate_snapshot: dict[str, Any] | None
    side_effects_executed: bool
    ts_ms: int


@dataclass(frozen=True, slots=True)
class SecureIngressDecision:
    allowed: bool
    stage: IngressStage
    reason_code: IngressReasonCode
    audit_ref: str | None
    audit_status: AuditStatus
    primary_reason_code: IngressReasonCode
    claim_ref: str | None
    existing_result_ref: str | None
    canonical_request_id: str | None = None
    canonical_body_sha256: str | None = None
    canonical_audit_ref: str | None = None
    side_effects_executed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "audit_ref": self.audit_ref,
            "audit_status": self.audit_status.value,
            "claim_ref": self.claim_ref,
            "existing_result_ref": self.existing_result_ref,
            "reason_code": self.reason_code.value,
            "primary_reason_code": self.primary_reason_code.value,
            "side_effects_executed": False,
            "stage": self.stage.value,
        }


def digest_identifier(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


__all__ = [
    "ClaimDecision",
    "ClaimStatus",
    "AuditStatus",
    "CredentialDecision",
    "IngressAuditEvent",
    "IngressReasonCode",
    "IngressStage",
    "PrincipalAuthority",
    "RateDecision",
    "SecureIngressDecision",
    "SecureIngressRequest",
    "digest_identifier",
]
