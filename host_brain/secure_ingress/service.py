"""Fail-closed Secure Ingress orchestration with durable audit semantics."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from natsume_contracts.identity import ScopeMatchMode, match_scopes

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
    digest_identifier,
)
from .ports import (
    CredentialVerifier,
    IngressAttemptLimiter,
    IngressAuditSink,
    IngressRateLimiter,
    PrincipalStore,
    SecureIngressStateStore,
)


_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,254}$")
_METHOD_RE = re.compile(r"^[A-Z]{2,16}$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,2047}$")
_BAD_PERCENT_RE = re.compile(r"%(?![0-9A-F]{2})")


class SecureIngressService:
    """Make one authenticated, scoped, rate-limited and replay-safe decision."""

    def __init__(
        self,
        *,
        principal_store: PrincipalStore,
        credential_verifier: CredentialVerifier,
        attempt_limiter: IngressAttemptLimiter,
        rate_limiter: IngressRateLimiter,
        state_store: SecureIngressStateStore,
        emergency_audit_sink: IngressAuditSink,
        auth_window_ms: int = 300_000,
        claim_ttl_ms: int = 86_400_000,
        max_body_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self._principal_store = principal_store
        self._credential_verifier = credential_verifier
        self._attempt_limiter = attempt_limiter
        self._rate_limiter = rate_limiter
        self._state_store = state_store
        self._emergency_audit_sink = emergency_audit_sink
        self._auth_window_ms = max(1, int(auth_window_ms))
        self._claim_ttl_ms = max(1, int(claim_ttl_ms))
        self._max_body_bytes = max(1, int(max_body_bytes))

    def decide(self, request: SecureIngressRequest) -> SecureIngressDecision:
        base_reason = self._validate_attempt_context(request)
        if base_reason is not None:
            return self._finish(request, stage=IngressStage.SCHEMA, reason=base_reason)

        attempt = self._consume_attempt(request)
        if not attempt.available:
            return self._finish(
                request,
                stage=IngressStage.RATE,
                reason=IngressReasonCode.RATE_BACKEND_UNAVAILABLE,
                rate=attempt,
            )
        if not attempt.allowed:
            return self._finish(
                request,
                stage=IngressStage.RATE,
                reason=IngressReasonCode.RATE_LIMITED,
                rate=attempt,
            )

        invalid_reason = self._validate_security_request(request)
        if invalid_reason is not None:
            return self._finish(
                request,
                stage=IngressStage.SCHEMA,
                reason=invalid_reason,
                rate=attempt,
            )

        assert request.identity is not None and request.credential is not None
        identity = request.identity
        metadata = identity.credential_proof
        try:
            authority = self._principal_store.resolve(
                principal_id=identity.principal.principal_id,
                key_id=metadata.key_id,
                at_ms=request.received_at_epoch_ms,
            )
        except Exception:
            return self._finish(
                request,
                stage=IngressStage.PRINCIPAL,
                reason=IngressReasonCode.INTERNAL_ERROR,
                rate=attempt,
            )
        if not self._authority_matches(request, authority):
            return self._finish(
                request,
                stage=IngressStage.PRINCIPAL,
                reason=IngressReasonCode.PRINCIPAL_INVALID,
                authority=authority,
                rate=attempt,
            )

        expiry_reason = self._credential_time_reason(request)
        if expiry_reason is not None:
            return self._finish(
                request,
                stage=IngressStage.CREDENTIAL,
                reason=expiry_reason,
                authority=authority,
                rate=attempt,
            )
        try:
            credential = self._credential_verifier.verify(
                request=request,
                authority=authority,
            )
        except Exception:
            credential = CredentialDecision(False, IngressReasonCode.CREDENTIAL_INVALID)
        if not credential.valid:
            return self._finish(
                request,
                stage=IngressStage.CREDENTIAL,
                reason=credential.reason_code or IngressReasonCode.CREDENTIAL_INVALID,
                authority=authority,
                credential=credential,
                rate=attempt,
            )

        if not self._scopes_allow(request, authority):
            return self._finish(
                request,
                stage=IngressStage.SCOPE,
                reason=IngressReasonCode.SCOPE_DENIED,
                authority=authority,
                credential=credential,
                rate=attempt,
            )

        rate = self._consume_authority_rate(request, authority)
        if not rate.available:
            return self._finish(
                request,
                stage=IngressStage.RATE,
                reason=IngressReasonCode.RATE_BACKEND_UNAVAILABLE,
                authority=authority,
                credential=credential,
                rate=rate,
            )
        if not rate.allowed:
            return self._finish(
                request,
                stage=IngressStage.RATE,
                reason=IngressReasonCode.RATE_LIMITED,
                authority=authority,
                credential=credential,
                rate=rate,
            )

        expires_at = request.received_at_epoch_ms + self._claim_ttl_ms
        if metadata.expires_at_epoch_ms is not None:
            expires_at = min(expires_at, metadata.expires_at_epoch_ms)
        try:
            claim = self._state_store.try_claim(
                principal_id=authority.principal_id,
                surface=request.surface,
                action=request.action,
                required_scopes=tuple(sorted(set(request.required_scopes))),
                idempotency_key=request.idempotency_key,
                nonce=request.nonce,
                body_sha256=request.body_sha256,
                request_id=identity.request_id,
                at_ms=request.received_at_epoch_ms,
                expires_at_ms=expires_at,
            )
        except Exception:
            return self._finish(
                request,
                stage=IngressStage.CLAIM,
                reason=IngressReasonCode.INTERNAL_ERROR,
                authority=authority,
                credential=credential,
                rate=rate,
            )
        reason_by_claim = {
            ClaimStatus.DUPLICATE_SAME: IngressReasonCode.IDEMPOTENCY_DUPLICATE,
            ClaimStatus.IDEMPOTENCY_CONFLICT: IngressReasonCode.IDEMPOTENCY_CONFLICT,
            ClaimStatus.REPLAY: IngressReasonCode.REPLAY_DETECTED,
        }
        if claim.status is not ClaimStatus.NEW:
            return self._finish(
                request,
                stage=IngressStage.CLAIM,
                reason=reason_by_claim.get(claim.status, IngressReasonCode.INTERNAL_ERROR),
                authority=authority,
                credential=credential,
                rate=rate,
                claim=claim,
            )
        return self._finish(
            request,
            stage=IngressStage.COMPLETE,
            reason=IngressReasonCode.ALLOWED,
            authority=authority,
            credential=credential,
            rate=rate,
            claim=claim,
            allow=True,
        )

    def reject_unparsed_attempt(
        self,
        *,
        transport_key: str,
        surface: str,
        method: str,
        normalized_path: str,
        action: str,
        required_scopes: tuple[str, ...],
        received_at_epoch_ms: int,
        correlation_id: str | None = None,
        claimed_principal_id: str | None = None,
        key_id: str | None = None,
    ) -> SecureIngressDecision:
        """Rate and audit a proof/envelope rejected before contract construction."""

        request = SecureIngressRequest(
            identity=None,
            credential=None,
            surface=surface,
            method=method,
            normalized_path=normalized_path,
            action=action,
            required_scopes=required_scopes,
            idempotency_key="invalid-contract",
            nonce="invalid-contract",
            raw_body=b"",
            received_at_epoch_ms=received_at_epoch_ms,
            transport_key=transport_key,
            correlation_id=correlation_id,
        )
        base_reason = self._validate_attempt_context(request)
        if base_reason is not None:
            return self._finish(
                request,
                stage=IngressStage.SCHEMA,
                reason=base_reason,
            )
        try:
            attempt = self._attempt_limiter.consume_attempt(
                transport_key=transport_key,
                claimed_principal_id=claimed_principal_id,
                key_id=key_id,
                at_ms=received_at_epoch_ms,
            )
        except Exception:
            attempt = RateDecision(
                False, 0, 0, 0, "unavailable", available=False, lane="ATTEMPT"
            )
        if not attempt.available:
            reason = IngressReasonCode.RATE_BACKEND_UNAVAILABLE
            stage = IngressStage.RATE
        elif not attempt.allowed:
            reason = IngressReasonCode.RATE_LIMITED
            stage = IngressStage.RATE
        else:
            reason = IngressReasonCode.SCHEMA_INVALID
            stage = IngressStage.SCHEMA
        return self._finish(request, stage=stage, reason=reason, rate=attempt)

    def _consume_attempt(self, request: SecureIngressRequest) -> RateDecision:
        identity = request.identity
        try:
            return self._attempt_limiter.consume_attempt(
                transport_key=request.transport_key,
                claimed_principal_id=(identity.principal.principal_id if identity else None),
                key_id=(identity.credential_proof.key_id if identity else None),
                at_ms=request.received_at_epoch_ms,
            )
        except Exception:
            return RateDecision(
                False,
                0,
                0,
                0,
                "unavailable",
                available=False,
                lane="ATTEMPT",
            )

    def _consume_authority_rate(
        self,
        request: SecureIngressRequest,
        authority: PrincipalAuthority,
    ) -> RateDecision:
        try:
            return self._rate_limiter.consume(
                principal_id=authority.principal_id,
                action=request.action,
                required_scopes=tuple(sorted(set(request.required_scopes))),
                at_ms=request.received_at_epoch_ms,
            )
        except Exception:
            return RateDecision(False, 0, 0, 0, "unavailable", available=False)

    def _validate_attempt_context(self, request: Any) -> IngressReasonCode | None:
        if not isinstance(request, SecureIngressRequest):
            return IngressReasonCode.SCHEMA_INVALID
        if type(request.received_at_epoch_ms) is not int:
            return IngressReasonCode.SCHEMA_INVALID
        if not self._valid_token(request.transport_key):
            return IngressReasonCode.SCHEMA_INVALID
        return None

    def _validate_security_request(self, request: SecureIngressRequest) -> IngressReasonCode | None:
        if not isinstance(request.raw_body, bytes) or len(request.raw_body) > self._max_body_bytes:
            return IngressReasonCode.SCHEMA_INVALID
        if not self._valid_token(request.surface) or not self._valid_token(request.action):
            return IngressReasonCode.SCHEMA_INVALID
        if not isinstance(request.method, str) or not _METHOD_RE.fullmatch(request.method.upper()):
            return IngressReasonCode.SCHEMA_INVALID
        if not self._valid_path(request.normalized_path):
            return IngressReasonCode.SCHEMA_INVALID
        if request.correlation_id is not None and not self._valid_token(request.correlation_id):
            return IngressReasonCode.SCHEMA_INVALID
        if request.identity is None:
            return IngressReasonCode.PRINCIPAL_INVALID
        if request.credential is None:
            return IngressReasonCode.CREDENTIAL_MISSING
        if request.credential.metadata != request.identity.credential_proof:
            return IngressReasonCode.CREDENTIAL_INVALID
        if not request.required_scopes:
            return IngressReasonCode.SCOPE_DENIED
        if not self._valid_token(request.idempotency_key):
            return IngressReasonCode.IDEMPOTENCY_KEY_MISSING
        if not self._valid_token(request.nonce):
            return IngressReasonCode.SCHEMA_INVALID
        return None

    @staticmethod
    def _valid_token(value: Any) -> bool:
        return isinstance(value, str) and _TOKEN_RE.fullmatch(value) is not None

    @staticmethod
    def _valid_path(value: Any) -> bool:
        return (
            isinstance(value, str)
            and "?" not in value
            and "#" not in value
            and "\\" not in value
            and _PATH_RE.fullmatch(value) is not None
            and _BAD_PERCENT_RE.search(value) is None
        )

    def _authority_matches(
        self,
        request: SecureIngressRequest,
        authority: PrincipalAuthority | None,
    ) -> bool:
        if authority is None or not authority.enabled or authority.version < 1 or request.identity is None:
            return False
        principal = request.identity.principal
        if (
            authority.principal_id != principal.principal_id
            or authority.kind is not principal.kind
            or authority.tenant_id != principal.tenant_id
            or authority.key_id != request.identity.credential_proof.key_id
            or authority.credential_kind is not request.identity.credential_proof.kind
            or authority.algorithm != request.identity.credential_proof.algorithm
        ):
            return False
        if not self._matches_any(authority.allowed_audiences, request.identity.audience):
            return False
        return self._matches_any(authority.allowed_actions, request.action)

    def _credential_time_reason(self, request: SecureIngressRequest) -> IngressReasonCode | None:
        assert request.identity is not None
        metadata = request.identity.credential_proof
        if abs(request.received_at_epoch_ms - metadata.issued_at_epoch_ms) > self._auth_window_ms:
            return IngressReasonCode.CREDENTIAL_EXPIRED
        if metadata.expires_at_epoch_ms is not None and metadata.expires_at_epoch_ms <= request.received_at_epoch_ms:
            return IngressReasonCode.CREDENTIAL_EXPIRED
        return None

    def _scopes_allow(self, request: SecureIngressRequest, authority: PrincipalAuthority) -> bool:
        assert request.identity is not None
        try:
            results = (
                match_scopes(
                    authority.granted_scopes,
                    request.identity.principal.granted_scopes,
                    mode=ScopeMatchMode.ALL,
                ),
                match_scopes(
                    authority.granted_scopes,
                    request.identity.requested_scopes,
                    mode=ScopeMatchMode.ALL,
                ),
                match_scopes(
                    authority.granted_scopes,
                    request.required_scopes,
                    mode=ScopeMatchMode.ALL,
                ),
                match_scopes(
                    request.identity.requested_scopes,
                    request.required_scopes,
                    mode=ScopeMatchMode.ALL,
                ),
            )
        except Exception:
            return False
        return all(result.allowed for result in results)

    @staticmethod
    def _matches_any(patterns: tuple[str, ...], value: str) -> bool:
        for pattern in patterns:
            if pattern == "*" or pattern == value:
                return True
            if pattern.endswith(":*") and value.startswith(f"{pattern[:-2]}:"):
                return True
        return False

    def _finish(
        self,
        request: Any,
        *,
        stage: IngressStage,
        reason: IngressReasonCode,
        authority: PrincipalAuthority | None = None,
        credential: CredentialDecision | None = None,
        rate: RateDecision | None = None,
        claim: ClaimDecision | None = None,
        allow: bool = False,
    ) -> SecureIngressDecision:
        event = self._audit_event(
            request,
            stage=stage,
            reason=reason,
            authority=authority,
            credential=credential,
            rate=rate,
            claim=claim,
            allow=allow,
        )
        if allow and claim is not None and claim.status is ClaimStatus.NEW and claim.claim_ref:
            try:
                audit_ref = str(
                    self._state_store.commit_with_audit(
                        claim_ref=claim.claim_ref,
                        event=event,
                    )
                ).strip()
                if not audit_ref:
                    raise RuntimeError("empty audit ref")
            except Exception:
                try:
                    self._state_store.abort_claim(claim_ref=claim.claim_ref)
                except Exception:
                    pass
                return self._audit_failure_decision(
                    event,
                    stage=stage,
                    primary_reason=reason,
                    claim=claim,
                    allow_was_requested=True,
                )
            return SecureIngressDecision(
                allowed=True,
                stage=stage,
                reason_code=reason,
                primary_reason_code=reason,
                audit_ref=audit_ref,
                audit_status=AuditStatus.PRIMARY_DURABLE,
                claim_ref=claim.claim_ref,
                existing_result_ref=claim.existing_result_ref,
            )

        try:
            audit_ref = str(self._state_store.append(event)).strip()
            if not audit_ref:
                raise RuntimeError("empty audit ref")
        except Exception:
            return self._audit_failure_decision(
                event,
                stage=stage,
                primary_reason=reason,
                claim=claim,
                allow_was_requested=False,
            )
        return SecureIngressDecision(
            allowed=False,
            stage=stage,
            reason_code=reason,
            primary_reason_code=reason,
            audit_ref=audit_ref,
            audit_status=AuditStatus.PRIMARY_DURABLE,
            claim_ref=claim.claim_ref if claim else None,
            existing_result_ref=claim.existing_result_ref if claim else None,
        )

    def _audit_failure_decision(
        self,
        event: IngressAuditEvent,
        *,
        stage: IngressStage,
        primary_reason: IngressReasonCode,
        claim: ClaimDecision | None,
        allow_was_requested: bool,
    ) -> SecureIngressDecision:
        try:
            emergency_ref = str(self._emergency_audit_sink.append(event)).strip()
            if not emergency_ref:
                raise RuntimeError("empty emergency audit ref")
            status = AuditStatus.EMERGENCY_DURABLE
        except Exception:
            emergency_ref = None
            status = AuditStatus.UNAVAILABLE
        return SecureIngressDecision(
            allowed=False,
            stage=IngressStage.AUDIT if allow_was_requested or status is AuditStatus.UNAVAILABLE else stage,
            reason_code=(
                IngressReasonCode.AUDIT_UNAVAILABLE
                if allow_was_requested or status is AuditStatus.UNAVAILABLE
                else primary_reason
            ),
            primary_reason_code=primary_reason,
            audit_ref=emergency_ref,
            audit_status=status,
            claim_ref=claim.claim_ref if claim else None,
            existing_result_ref=claim.existing_result_ref if claim else None,
        )

    def _audit_event(
        self,
        request: Any,
        *,
        stage: IngressStage,
        reason: IngressReasonCode,
        authority: PrincipalAuthority | None,
        credential: CredentialDecision | None,
        rate: RateDecision | None,
        claim: ClaimDecision | None,
        allow: bool,
    ) -> IngressAuditEvent:
        valid_request = isinstance(request, SecureIngressRequest)
        identity = request.identity if valid_request else None
        metadata = identity.credential_proof if identity is not None else None
        raw_candidate = request.raw_body if valid_request else b""
        raw_body = raw_candidate if isinstance(raw_candidate, bytes) else b""
        rate_snapshot = None
        if rate is not None:
            rate_snapshot = {
                "allowed": rate.allowed,
                "available": rate.available,
                "backend": rate.backend,
                "current_count": rate.current_count,
                "lane": rate.lane,
                "limit": rate.limit,
                "window_ms": rate.window_ms,
            }
        path = request.normalized_path if valid_request and self._valid_path(request.normalized_path) else "/invalid"
        correlation = (
            request.correlation_id
            if valid_request and request.correlation_id is not None and self._valid_token(request.correlation_id)
            else None
        )
        return IngressAuditEvent(
            request_id=identity.request_id if identity else None,
            correlation_id=correlation,
            surface=request.surface if valid_request and self._valid_token(request.surface) else "invalid",
            action=request.action if valid_request and self._valid_token(request.action) else "invalid",
            method=request.method.upper() if valid_request and isinstance(request.method, str) and _METHOD_RE.fullmatch(request.method.upper()) else "INVALID",
            normalized_path=path,
            principal_id=identity.principal.principal_id if identity else None,
            tenant_id=authority.tenant_id if authority else None,
            key_id=metadata.key_id if metadata else None,
            credential_kind=metadata.kind.value if metadata else None,
            credential_ref_digest=credential.credential_ref_digest if credential else None,
            requested_scopes=identity.requested_scopes if identity else (),
            required_scopes=(
                tuple(sorted(set(request.required_scopes)))
                if valid_request and isinstance(request.required_scopes, tuple)
                else ()
            ),
            body_sha256=hashlib.sha256(raw_body).hexdigest(),
            idempotency_digest=(
                digest_identifier(request.idempotency_key)
                if valid_request and self._valid_token(request.idempotency_key)
                else None
            ),
            nonce_digest=(
                digest_identifier(request.nonce)
                if valid_request and self._valid_token(request.nonce)
                else None
            ),
            transport_key_digest=(
                digest_identifier(request.transport_key)
                if valid_request and self._valid_token(request.transport_key)
                else None
            ),
            decision="ALLOW" if allow and reason is IngressReasonCode.ALLOWED else "DENY",
            reason_code=reason.value,
            stage=stage.value,
            claim_ref=claim.claim_ref if claim else None,
            rate_snapshot=rate_snapshot,
            side_effects_executed=False,
            ts_ms=(
                request.received_at_epoch_ms
                if valid_request and type(request.received_at_epoch_ms) is int
                else 0
            ),
        )


__all__ = ["SecureIngressService"]
