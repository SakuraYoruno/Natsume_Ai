from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
from typing import Any

import pytest

from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    CredentialProofMetadata,
    IdentitySchemaVersion,
    IdentityContractError,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
)
from host_brain.secure_ingress import (
    AuditStatus,
    ClaimDecision,
    ClaimStatus,
    CredentialVerifierRegistry,
    HmacSha256CredentialVerifier,
    IngressReasonCode,
    IngressStage,
    PrincipalAuthority,
    OpaqueBearerCredentialVerifier,
    RateDecision,
    SecureIngressRequest,
    SecureIngressService,
    build_hmac_sha256_signature,
)


NOW = 1_800_000_000_000
SECRET = "task0487-unit-secret-not-production"
_DEFAULT_AUTHORITY = object()


class _PrincipalStore:
    def __init__(self, authority: PrincipalAuthority | None, *, fail: bool = False) -> None:
        self.authority = authority
        self.fail = fail

    def resolve(self, **_kwargs: Any) -> PrincipalAuthority | None:
        if self.fail:
            raise RuntimeError("principal backend unavailable")
        return self.authority


class _RateLimiter:
    def __init__(
        self,
        decision: RateDecision | None = None,
        *,
        fail: bool = False,
        attempt_decision: RateDecision | None = None,
        attempt_fail: bool = False,
    ) -> None:
        self.decision = decision or RateDecision(True, 1, 10, 60_000, "unit")
        self.fail = fail
        self.attempt_decision = attempt_decision or RateDecision(
            True, 1, 100, 60_000, "unit", lane="ATTEMPT"
        )
        self.attempt_fail = attempt_fail
        self.calls = 0
        self.attempt_calls = 0

    def consume_attempt(self, **_kwargs: Any) -> RateDecision:
        self.attempt_calls += 1
        if self.attempt_fail:
            raise RuntimeError("attempt backend unavailable")
        return self.attempt_decision

    def consume(self, **_kwargs: Any) -> RateDecision:
        self.calls += 1
        if self.fail:
            raise RuntimeError("rate backend unavailable")
        return self.decision


class _ClaimStore:
    def __init__(
        self,
        decision: ClaimDecision | None = None,
        *,
        fail: bool = False,
        audit_sink: _AuditSink | None = None,
    ) -> None:
        self.decision = decision or ClaimDecision(ClaimStatus.NEW, "claim://unit/1")
        self.fail = fail
        self.calls = 0
        self.audit_sink = audit_sink
        self.aborted: list[str] = []

    def try_claim(self, **_kwargs: Any) -> ClaimDecision:
        self.calls += 1
        if self.fail:
            raise RuntimeError("claim backend unavailable")
        return self.decision

    def append(self, event: Any) -> str:
        if self.audit_sink is None:
            raise RuntimeError("primary audit sink missing")
        return self.audit_sink.append(event)

    def commit_with_audit(self, *, claim_ref: str, event: Any) -> str:
        if self.fail:
            raise RuntimeError("atomic claim/audit failed")
        return self.append(event)

    def abort_claim(self, *, claim_ref: str) -> None:
        self.aborted.append(claim_ref)


class _AuditSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[Any] = []

    def append(self, event: Any) -> str:
        self.events.append(event)
        if self.fail:
            raise RuntimeError("audit unavailable")
        return f"audit://unit/{len(self.events)}"


def _authority(
    *,
    principal_id: str = "service.gateway.test",
    scopes: tuple[str, ...] = ("host:write",),
    enabled: bool = True,
) -> PrincipalAuthority:
    return PrincipalAuthority(
        principal_id=principal_id,
        kind=PrincipalKind.SERVICE,
        tenant_id="tenant.test",
        key_id="key.test.1",
        credential_kind=CredentialProofKind.HMAC_SHA256,
        algorithm="hmac-sha256",
        granted_scopes=scopes,
        allowed_audiences=("host.test",),
        allowed_actions=("host:*",),
        version=1,
        enabled=enabled,
    )


def _unsigned_request(
    *,
    authority: PrincipalAuthority | None = None,
    requested_scopes: tuple[str, ...] = ("host:write",),
    required_scopes: tuple[str, ...] = ("host:write",),
    principal_grants: tuple[str, ...] = ("host:write",),
    issued_at: int = NOW,
    expires_at: int | None = NOW + 60_000,
    raw_proof: str = "0" * 64,
) -> SecureIngressRequest:
    authority = authority or _authority()
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id=authority.key_id,
        algorithm=authority.algorithm,
        proof_id="proof.test.1",
        issued_at_epoch_ms=issued_at,
        expires_at_epoch_ms=expires_at,
    )
    identity = IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=authority.principal_id,
            kind=authority.kind,
            tenant_id=authority.tenant_id,
            granted_scopes=principal_grants,
        ),
        credential_proof=metadata,
        audience="host.test",
        request_id="request.test.1",
        requested_scopes=requested_scopes,
    )
    return SecureIngressRequest(
        identity=identity,
        credential=CredentialProof(metadata=metadata, raw_proof=raw_proof),
        surface="host.input",
        method="POST",
        normalized_path="/v1/host/input",
        action="host:input",
        required_scopes=required_scopes,
        idempotency_key="idem.test.1",
        nonce="nonce.test.1",
        raw_body=b'{"message":"hello"}',
        received_at_epoch_ms=NOW,
        transport_key="loopback:unit-test",
        correlation_id="correlation.test.1",
    )


def _signed_request(**kwargs: Any) -> SecureIngressRequest:
    request = _unsigned_request(**kwargs)
    signature = build_hmac_sha256_signature(request, SECRET)
    return replace(
        request,
        credential=CredentialProof(
            metadata=request.identity.credential_proof,
            raw_proof=signature,
        ),
    )


def _service(
    *,
    authority: PrincipalAuthority | None | object = _DEFAULT_AUTHORITY,
    principal_fail: bool = False,
    rate: _RateLimiter | None = None,
    claims: _ClaimStore | None = None,
    audit: _AuditSink | None = None,
    emergency_audit: _AuditSink | None = None,
) -> tuple[SecureIngressService, _RateLimiter, _ClaimStore, _AuditSink]:
    resolved_authority = _authority() if authority is _DEFAULT_AUTHORITY else authority
    rate = rate or _RateLimiter()
    audit = audit or _AuditSink()
    claims = claims or _ClaimStore()
    claims.audit_sink = audit
    emergency_audit = emergency_audit or _AuditSink()
    verifier = CredentialVerifierRegistry(
        {
            CredentialProofKind.HMAC_SHA256: HmacSha256CredentialVerifier(
                secret_resolver=lambda _authority: SECRET,
            )
        }
    )
    return (
        SecureIngressService(
            principal_store=_PrincipalStore(resolved_authority, fail=principal_fail),
            credential_verifier=verifier,
            attempt_limiter=rate,
            rate_limiter=rate,
            state_store=claims,
            emergency_audit_sink=emergency_audit,
        ),
        rate,
        claims,
        audit,
    )


def test_valid_request_is_allowed_only_after_audit_and_claim() -> None:
    service, rate, claims, audit = _service()

    decision = service.decide(_signed_request())

    assert decision.allowed is True
    assert decision.reason_code is IngressReasonCode.ALLOWED
    assert decision.stage is IngressStage.COMPLETE
    assert decision.audit_ref == "audit://unit/1"
    assert decision.claim_ref == "claim://unit/1"
    assert decision.side_effects_executed is False
    assert decision.audit_status is AuditStatus.PRIMARY_DURABLE
    assert rate.attempt_calls == 1
    assert rate.calls == claims.calls == len(audit.events) == 1


def test_missing_principal_and_credential_fail_closed_and_are_audited() -> None:
    service, rate, claims, audit = _service()
    base = _signed_request()

    missing_principal = service.decide(replace(base, identity=None, credential=None))
    missing_credential = service.decide(replace(base, credential=None))

    assert missing_principal.reason_code is IngressReasonCode.PRINCIPAL_INVALID
    assert missing_credential.reason_code is IngressReasonCode.CREDENTIAL_MISSING
    assert not missing_principal.allowed and not missing_credential.allowed
    assert rate.calls == claims.calls == 0
    assert rate.attempt_calls == 2
    assert len(audit.events) == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("raw_body", b'{"message":"hellp"}'),
        ("method", "PUT"),
        ("normalized_path", "/v1/host/other"),
        ("action", "host:other"),
        ("idempotency_key", "idem.test.2"),
        ("nonce", "nonce.test.2"),
        ("correlation_id", "correlation.test.2"),
        ("transport_key", "loopback:other-test"),
    ],
)
def test_signed_security_fields_are_tamper_evident(field: str, value: Any) -> None:
    service, rate, claims, _audit = _service()
    request = replace(_signed_request(), **{field: value})

    decision = service.decide(request)

    assert decision.allowed is False
    assert decision.reason_code is IngressReasonCode.CREDENTIAL_INVALID
    assert rate.attempt_calls == 1
    assert rate.calls == claims.calls == 0


def test_unknown_and_disabled_principals_share_stable_external_reason() -> None:
    request = _signed_request()
    unknown, *_ = _service(authority=None)
    disabled_authority = _authority(enabled=False)
    disabled, *_ = _service(authority=disabled_authority)

    unknown_decision = unknown.decide(request)
    disabled_decision = disabled.decide(request)

    assert unknown_decision.reason_code is IngressReasonCode.PRINCIPAL_INVALID
    assert disabled_decision.reason_code is IngressReasonCode.PRINCIPAL_INVALID
    assert not unknown_decision.allowed and not disabled_decision.allowed


def test_expired_proof_is_rejected_before_verifier_or_claim() -> None:
    request = _signed_request(
        issued_at=NOW - 120_000,
        expires_at=NOW - 1,
    )
    service, rate, claims, _audit = _service()

    decision = service.decide(request)

    assert decision.reason_code is IngressReasonCode.CREDENTIAL_EXPIRED
    assert rate.calls == claims.calls == 0


def test_scope_claims_are_bounded_by_authority_and_server_requirement() -> None:
    authority = _authority(scopes=("host:read",))
    request = _signed_request(
        authority=authority,
        requested_scopes=("host:write",),
        required_scopes=("host:write",),
        principal_grants=("host:write",),
    )
    service, rate, claims, _audit = _service(authority=authority)

    decision = service.decide(request)

    assert decision.reason_code is IngressReasonCode.SCOPE_DENIED
    assert rate.calls == claims.calls == 0


@pytest.mark.parametrize(
    ("rate_decision", "reason"),
    [
        (RateDecision(False, 11, 10, 60_000, "sqlite"), IngressReasonCode.RATE_LIMITED),
        (
            RateDecision(False, 0, 10, 60_000, "unavailable", available=False),
            IngressReasonCode.RATE_BACKEND_UNAVAILABLE,
        ),
    ],
)
def test_rate_limit_hit_or_backend_failure_denies(
    rate_decision: RateDecision,
    reason: IngressReasonCode,
) -> None:
    rate = _RateLimiter(rate_decision)
    service, _rate, claims, _audit = _service(rate=rate)

    decision = service.decide(_signed_request())

    assert decision.reason_code is reason
    assert not decision.allowed
    assert claims.calls == 0


@pytest.mark.parametrize(
    ("claim", "reason"),
    [
        (
            ClaimDecision(ClaimStatus.DUPLICATE_SAME, "claim://old", "result://old"),
            IngressReasonCode.IDEMPOTENCY_DUPLICATE,
        ),
        (
            ClaimDecision(ClaimStatus.IDEMPOTENCY_CONFLICT, "claim://old"),
            IngressReasonCode.IDEMPOTENCY_CONFLICT,
        ),
        (
            ClaimDecision(ClaimStatus.REPLAY, "claim://old"),
            IngressReasonCode.REPLAY_DETECTED,
        ),
    ],
)
def test_claim_outcomes_are_non_executing_and_stable(
    claim: ClaimDecision,
    reason: IngressReasonCode,
) -> None:
    service, _rate, _claims, _audit = _service(claims=_ClaimStore(claim))

    decision = service.decide(_signed_request())

    assert decision.reason_code is reason
    assert not decision.allowed
    assert decision.side_effects_executed is False
    assert decision.existing_result_ref == claim.existing_result_ref


def test_invalid_signature_consumes_attempt_rate_but_not_authority_rate_or_claim() -> None:
    service, rate, claims, audit = _service()

    decision = service.decide(_unsigned_request(raw_proof="0" * 64))

    assert decision.reason_code is IngressReasonCode.CREDENTIAL_INVALID
    assert rate.calls == claims.calls == 0
    assert rate.attempt_calls == 1
    assert len(audit.events) == 1


def test_audit_failure_overrides_allow_and_never_executes() -> None:
    service, _rate, claims, audit = _service(audit=_AuditSink(fail=True))

    decision = service.decide(_signed_request())

    assert claims.calls == 1
    assert len(audit.events) == 1
    assert decision.allowed is False
    assert decision.reason_code is IngressReasonCode.AUDIT_UNAVAILABLE
    assert decision.stage is IngressStage.AUDIT
    assert decision.audit_status is AuditStatus.EMERGENCY_DURABLE
    assert decision.primary_reason_code is IngressReasonCode.ALLOWED
    assert claims.aborted == ["claim://unit/1"]

    audit.fail = False
    retried = service.decide(_signed_request())
    assert retried.allowed is True
    assert retried.audit_status is AuditStatus.PRIMARY_DURABLE


@pytest.mark.parametrize(
    "attempt",
    [
        RateDecision(False, 101, 100, 60_000, "sqlite", lane="ATTEMPT"),
        RateDecision(
            False,
            0,
            100,
            60_000,
            "unavailable",
            available=False,
            lane="ATTEMPT",
        ),
    ],
)
def test_pre_auth_attempt_limiter_blocks_before_principal_or_verifier(
    attempt: RateDecision,
) -> None:
    rate = _RateLimiter(attempt_decision=attempt)
    service, _rate, claims, audit = _service(rate=rate)

    decision = service.decide(_unsigned_request(raw_proof="0" * 64))

    assert decision.reason_code in {
        IngressReasonCode.RATE_LIMITED,
        IngressReasonCode.RATE_BACKEND_UNAVAILABLE,
    }
    assert rate.attempt_calls == 1
    assert rate.calls == claims.calls == 0
    assert len(audit.events) == 1


def test_invalid_path_and_correlation_are_never_persisted() -> None:
    secret_marker = "access_token=trace-secret-marker"
    service, _rate, _claims, audit = _service()
    request = replace(
        _signed_request(),
        normalized_path=f"/callback?{secret_marker}",
        correlation_id=f"bad?{secret_marker}",
    )

    decision = service.decide(request)

    assert decision.reason_code is IngressReasonCode.SCHEMA_INVALID
    assert len(audit.events) == 1
    assert audit.events[0].normalized_path == "/invalid"
    assert audit.events[0].correlation_id is None
    assert secret_marker not in repr(asdict(audit.events[0]))


def test_rejection_primary_audit_failure_preserves_reason_in_emergency_audit() -> None:
    primary = _AuditSink(fail=True)
    emergency = _AuditSink()
    service, _rate, _claims, _audit = _service(
        audit=primary,
        emergency_audit=emergency,
    )

    decision = service.decide(_unsigned_request(raw_proof="0" * 64))

    assert decision.reason_code is IngressReasonCode.CREDENTIAL_INVALID
    assert decision.primary_reason_code is IngressReasonCode.CREDENTIAL_INVALID
    assert decision.audit_status is AuditStatus.EMERGENCY_DURABLE
    assert decision.audit_ref == "audit://unit/1"
    assert len(emergency.events) == 1


def test_both_audit_sinks_unavailable_is_explicit_and_keeps_primary_reason() -> None:
    service, _rate, _claims, _audit = _service(
        audit=_AuditSink(fail=True),
        emergency_audit=_AuditSink(fail=True),
    )

    decision = service.decide(_unsigned_request(raw_proof="0" * 64))

    assert decision.reason_code is IngressReasonCode.AUDIT_UNAVAILABLE
    assert decision.primary_reason_code is IngressReasonCode.CREDENTIAL_INVALID
    assert decision.audit_status is AuditStatus.UNAVAILABLE
    assert decision.audit_ref is None


def test_pre_contract_oversized_proof_rejection_is_attempt_limited_and_audited() -> None:
    request = _unsigned_request()
    with pytest.raises(IdentityContractError):
        CredentialProof(
            metadata=request.identity.credential_proof,
            raw_proof="a" * 4097,
        )
    service, rate, claims, audit = _service()

    decision = service.reject_unparsed_attempt(
        transport_key=request.transport_key,
        surface=request.surface,
        method=request.method,
        normalized_path=request.normalized_path,
        action=request.action,
        required_scopes=request.required_scopes,
        received_at_epoch_ms=request.received_at_epoch_ms,
        correlation_id=request.correlation_id,
        claimed_principal_id=request.identity.principal.principal_id,
        key_id=request.identity.credential_proof.key_id,
    )

    assert decision.reason_code is IngressReasonCode.SCHEMA_INVALID
    assert rate.attempt_calls == 1
    assert rate.calls == claims.calls == 0
    assert len(audit.events) == 1


def test_audit_event_contains_digests_not_raw_proof_or_body() -> None:
    service, _rate, _claims, audit = _service()
    request = _signed_request()

    service.decide(request)

    rendered = repr(asdict(audit.events[0]))
    assert SECRET not in rendered
    assert str(request.credential.raw_proof) not in rendered
    assert request.raw_body.decode("utf-8") not in rendered
    assert audit.events[0].body_sha256 == hashlib.sha256(request.raw_body).hexdigest()
    assert audit.events[0].side_effects_executed is False


def test_port_exceptions_fail_closed_without_claim_side_effect() -> None:
    service, _rate, claims, audit = _service(principal_fail=True)

    decision = service.decide(_signed_request())

    assert decision.reason_code is IngressReasonCode.INTERNAL_ERROR
    assert decision.allowed is False
    assert claims.calls == 0
    assert len(audit.events) == 1


def test_opaque_bearer_verifier_uses_digest_and_authority_binding() -> None:
    token = b"task0487-opaque-test-token"
    authority = replace(
        _authority(),
        credential_kind=CredentialProofKind.BEARER_OPAQUE,
        algorithm="opaque-sha256",
    )
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.BEARER_OPAQUE,
        key_id=authority.key_id,
        algorithm=authority.algorithm,
        proof_id="proof.bearer.test",
        issued_at_epoch_ms=NOW,
        expires_at_epoch_ms=NOW + 60_000,
    )
    identity = IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=authority.principal_id,
            kind=authority.kind,
            tenant_id=authority.tenant_id,
            granted_scopes=authority.granted_scopes,
        ),
        credential_proof=metadata,
        audience="host.test",
        request_id="request.bearer.test",
        requested_scopes=("host:write",),
    )
    request = replace(
        _unsigned_request(authority=authority),
        identity=identity,
        credential=CredentialProof(metadata=metadata, raw_proof=token),
    )
    expected = f"sha256:{hashlib.sha256(token).hexdigest()}"
    verifier = OpaqueBearerCredentialVerifier(
        token_digest_resolver=lambda _authority: expected,
    )

    accepted = verifier.verify(request=request, authority=authority)
    rejected = verifier.verify(
        request=replace(
            request,
            credential=CredentialProof(metadata=metadata, raw_proof=b"wrong"),
        ),
        authority=authority,
    )

    assert accepted.valid is True
    assert accepted.credential_ref_digest is not None
    assert rejected.valid is False
    assert rejected.reason_code is IngressReasonCode.CREDENTIAL_INVALID


@pytest.mark.parametrize(
    ("kind", "algorithm"),
    [
        (CredentialProofKind.HMAC_SHA256, "none"),
        (CredentialProofKind.BEARER_OPAQUE, "sha256"),
    ],
)
def test_verifiers_reject_algorithm_confusion(
    kind: CredentialProofKind,
    algorithm: str,
) -> None:
    authority = replace(
        _authority(),
        credential_kind=kind,
        algorithm=algorithm,
    )
    request = _unsigned_request(authority=authority, raw_proof="0" * 64)
    verifier = (
        HmacSha256CredentialVerifier(secret_resolver=lambda _authority: SECRET)
        if kind is CredentialProofKind.HMAC_SHA256
        else OpaqueBearerCredentialVerifier(
            token_digest_resolver=lambda _authority: "sha256:" + "0" * 64
        )
    )

    decision = verifier.verify(request=request, authority=authority)

    assert decision.valid is False
    assert decision.reason_code is IngressReasonCode.CREDENTIAL_INVALID
