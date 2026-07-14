"""Authenticated, raw-body-bound intake for signed Skill/Provider bundles.

The caller supplies only an opaque server inbox reference.  Authentication,
scope, rate, replay, and durable claim/audit checks complete before JSON is
parsed or the source resolver touches the filesystem.  The domain service may
quarantine or stage verified bytes; this boundary never imports or activates
bundle code.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Protocol

from natsume_contracts.identity import CredentialProof, IngressIdentityEnvelope

from host_brain.secure_ingress import (
    IngressAuditEvent,
    IngressReasonCode,
    SecureIngressAdapter,
    SecureIngressRequest,
    SecureIngressService,
)
from host_brain.secure_ingress.models import digest_identifier

from .supply_chain_service import (
    SignedBundleIntakeDecision,
    SkillSupplyChainService,
)


SKILL_PROVIDER_INTAKE_SCHEMA = "natsume.skill-provider-intake.request.v1"
SKILL_PROVIDER_INTAKE_PATH = "/v1/host/skill-provider/intake"
SKILL_PROVIDER_INTAKE_SURFACE = "skill-provider.intake"
SKILL_PROVIDER_INTAKE_ACTION = "skill-provider:intake"
SKILL_PROVIDER_INTAKE_AUDIENCE = "host"
SKILL_PROVIDER_INTAKE_SCOPES = ("skill-provider:write",)
MAX_SKILL_PROVIDER_INTAKE_BODY_BYTES = 64 * 1024

_BODY_FIELDS = frozenset(
    {"schema_version", "source_ref", "bundle_kind", "publisher_id"}
)
_SOURCE_REF_RE = re.compile(r"\Aintake://[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_REASON_RE = re.compile(r"\A[A-Z][A-Z0-9_]{2,127}\Z")
_SUCCESS_REASONS = frozenset(
    {"SUPPLY_CHAIN_INTAKE_QUARANTINED", "SUPPLY_CHAIN_INTAKE_STAGED"}
)


class SkillProviderIntakeSourceResolver(Protocol):
    def resolve(self, source_ref: str) -> object: ...


class SkillProviderIntakeClaimStore(Protocol):
    def append(self, event: IngressAuditEvent) -> str: ...

    def complete_claim(
        self,
        *,
        claim_ref: str,
        result_ref: str,
        at_ms: int | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SignedSkillProviderIntakeRequest:
    raw_body: bytes
    identity: IngressIdentityEnvelope | None
    credential: CredentialProof | None
    idempotency_key: str
    nonce: str
    received_at_epoch_ms: int
    transport_key: str
    method: str = "POST"
    normalized_path: str = SKILL_PROVIDER_INTAKE_PATH
    correlation_id: str | None = None
    contract_error_reason: str | None = None


@dataclass(frozen=True, slots=True)
class SignedSkillProviderIntakeOutcome:
    allowed: bool
    executed: bool
    duplicate: bool
    quarantined: bool
    staged: bool
    activation_performed: bool
    import_performed: bool
    reason_code: str
    claim_ref: str | None = None
    ingress_audit_ref: str | None = None
    decision_audit_ref: str | None = None
    result_ref: str | None = None
    bundle_id: str | None = None
    bundle_digest: str | None = None
    repository_state: str | None = None


@dataclass(frozen=True, slots=True)
class _ParsedIntake:
    source_ref: str
    bundle_kind: str
    publisher_id: str


class _IntakeContractError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


class SecureSignedSkillProviderIntake:
    """Authorize one request, then call the quarantine-only service once."""

    def __init__(
        self,
        *,
        ingress_adapter: SecureIngressAdapter | None,
        ingress_service: SecureIngressService | None,
        claim_store: SkillProviderIntakeClaimStore | None,
        supply_chain_service: SkillSupplyChainService | None,
        source_resolver: SkillProviderIntakeSourceResolver | None,
    ) -> None:
        self._adapter = ingress_adapter
        self._ingress_service = ingress_service
        self._claim_store = claim_store
        self._supply_chain = supply_chain_service
        self._source_resolver = source_resolver

    async def execute(
        self,
        request: SignedSkillProviderIntakeRequest,
    ) -> SignedSkillProviderIntakeOutcome:
        return await asyncio.to_thread(self._execute_sync, request)

    def _execute_sync(
        self,
        request: SignedSkillProviderIntakeRequest,
    ) -> SignedSkillProviderIntakeOutcome:
        if not isinstance(request, SignedSkillProviderIntakeRequest):
            return self._unavailable("SKILL_PROVIDER_INTAKE_REQUEST_INVALID")
        if self._adapter is None or self._ingress_service is None:
            return self._unavailable("SKILL_PROVIDER_INTAKE_DISABLED")

        contract_error = str(request.contract_error_reason or "").strip()
        if contract_error:
            return self._reject_unparsed(request, contract_error)
        if (
            request.identity is None
            or request.identity.audience != SKILL_PROVIDER_INTAKE_AUDIENCE
        ):
            return self._reject_unparsed(
                request,
                "SKILL_PROVIDER_INTAKE_IDENTITY_INVALID",
            )
        if (
            str(request.method).upper() != "POST"
            or str(request.normalized_path) != SKILL_PROVIDER_INTAKE_PATH
        ):
            return self._reject_unparsed(
                request,
                "SKILL_PROVIDER_INTAKE_PATH_INVALID",
            )
        if (
            not isinstance(request.raw_body, bytes)
            or len(request.raw_body) > MAX_SKILL_PROVIDER_INTAKE_BODY_BYTES
            or type(request.received_at_epoch_ms) is not int
        ):
            return self._reject_unparsed(
                request,
                "SKILL_PROVIDER_INTAKE_BODY_INVALID",
            )

        secure_request = SecureIngressRequest(
            identity=request.identity,
            credential=request.credential,
            surface=SKILL_PROVIDER_INTAKE_SURFACE,
            method="POST",
            normalized_path=SKILL_PROVIDER_INTAKE_PATH,
            action=SKILL_PROVIDER_INTAKE_ACTION,
            required_scopes=SKILL_PROVIDER_INTAKE_SCOPES,
            idempotency_key=str(request.idempotency_key),
            nonce=str(request.nonce),
            raw_body=request.raw_body,
            received_at_epoch_ms=request.received_at_epoch_ms,
            transport_key=str(request.transport_key),
            correlation_id=request.correlation_id,
        )
        try:
            ingress = self._adapter.evaluate(secure_request)
        except Exception:
            return self._unavailable("SKILL_PROVIDER_INTAKE_INGRESS_UNAVAILABLE")
        decision = ingress.decision
        if decision is None:
            return self._unavailable("SKILL_PROVIDER_INTAKE_DISABLED")

        if decision.reason_code is IngressReasonCode.IDEMPOTENCY_DUPLICATE:
            result_ref = str(decision.existing_result_ref or "").strip()
            if not result_ref:
                return self._unavailable(
                    "SKILL_PROVIDER_INTAKE_RESULT_MISSING",
                    duplicate=True,
                    claim_ref=decision.claim_ref,
                    ingress_audit_ref=decision.audit_ref,
                )
            original_reason = _reason_from_result_ref(result_ref)
            if original_reason is None:
                return self._unavailable(
                    "SKILL_PROVIDER_INTAKE_RESULT_INVALID",
                    duplicate=True,
                    claim_ref=decision.claim_ref,
                    ingress_audit_ref=decision.audit_ref,
                    result_ref=result_ref,
                )
            return SignedSkillProviderIntakeOutcome(
                allowed=original_reason in _SUCCESS_REASONS,
                executed=False,
                duplicate=True,
                quarantined=False,
                staged=False,
                activation_performed=False,
                import_performed=False,
                reason_code=original_reason,
                claim_ref=decision.claim_ref,
                ingress_audit_ref=decision.audit_ref,
                result_ref=result_ref,
            )

        if not ingress.permit_execution or not decision.allowed:
            return SignedSkillProviderIntakeOutcome(
                allowed=False,
                executed=False,
                duplicate=False,
                quarantined=False,
                staged=False,
                activation_performed=False,
                import_performed=False,
                reason_code=decision.reason_code.value,
                claim_ref=decision.claim_ref,
                ingress_audit_ref=decision.audit_ref,
            )
        if (
            self._claim_store is None
            or self._supply_chain is None
            or self._source_resolver is None
            or not decision.claim_ref
            or not decision.audit_ref
        ):
            return self._unavailable(
                "SKILL_PROVIDER_INTAKE_DEPENDENCY_UNAVAILABLE",
                claim_ref=decision.claim_ref,
                ingress_audit_ref=decision.audit_ref,
            )

        try:
            parsed = _parse_intake_body(request.raw_body)
        except _IntakeContractError as exc:
            return self._finalize_application_decision(
                request=request,
                secure_request=secure_request,
                claim_ref=decision.claim_ref,
                ingress_audit_ref=decision.audit_ref,
                reason_code=exc.reason_code,
                accepted=False,
            )

        try:
            source = self._source_resolver.resolve(parsed.source_ref)
        except Exception:
            return self._finalize_application_decision(
                request=request,
                secure_request=secure_request,
                claim_ref=decision.claim_ref,
                ingress_audit_ref=decision.audit_ref,
                reason_code="SKILL_PROVIDER_INTAKE_SOURCE_UNAVAILABLE",
                accepted=False,
            )

        try:
            result = self._supply_chain.intake_signed_bundle(
                source,
                kind=parsed.bundle_kind,
                publisher=parsed.publisher_id,
                idempotency=request.idempotency_key,
            )
        except Exception:
            result = None
        if not isinstance(result, SignedBundleIntakeDecision):
            return self._finalize_application_decision(
                request=request,
                secure_request=secure_request,
                claim_ref=decision.claim_ref,
                ingress_audit_ref=decision.audit_ref,
                reason_code="SKILL_PROVIDER_INTAKE_SERVICE_FAILED",
                accepted=False,
            )
        if result.activation_performed or result.import_performed:
            return self._finalize_application_decision(
                request=request,
                secure_request=secure_request,
                claim_ref=decision.claim_ref,
                ingress_audit_ref=decision.audit_ref,
                reason_code="SKILL_PROVIDER_INTAKE_RESULT_INVALID",
                accepted=False,
                service_result=result,
            )
        reason_code = _stable_reason(result.reason_code)
        accepted = bool(
            reason_code in _SUCCESS_REASONS
            and result.quarantined
            and not result.activation_performed
            and not result.import_performed
        )
        return self._finalize_application_decision(
            request=request,
            secure_request=secure_request,
            claim_ref=decision.claim_ref,
            ingress_audit_ref=decision.audit_ref,
            reason_code=reason_code,
            accepted=accepted,
            service_result=result,
        )

    def _finalize_application_decision(
        self,
        *,
        request: SignedSkillProviderIntakeRequest,
        secure_request: SecureIngressRequest,
        claim_ref: str,
        ingress_audit_ref: str,
        reason_code: str,
        accepted: bool,
        service_result: SignedBundleIntakeDecision | None = None,
    ) -> SignedSkillProviderIntakeOutcome:
        stable_reason = _stable_reason(reason_code)
        result_ref = _result_ref(claim_ref, stable_reason)
        decision_audit_ref: str | None = None
        try:
            if self._claim_store is None:
                raise RuntimeError("claim store unavailable")
            decision_audit_ref = str(
                self._claim_store.append(
                    _application_audit_event(
                        request=request,
                        secure_request=secure_request,
                        claim_ref=claim_ref,
                        reason_code=stable_reason,
                        accepted=accepted,
                    )
                )
            ).strip()
            if not decision_audit_ref:
                raise RuntimeError("empty audit ref")
        except Exception:
            return self._unavailable(
                "SKILL_PROVIDER_INTAKE_DECISION_AUDIT_FAILED",
                executed=bool(service_result and service_result.quarantined),
                quarantined=bool(service_result and service_result.quarantined),
                staged=bool(service_result and service_result.staged),
                activation_performed=bool(
                    service_result and service_result.activation_performed
                ),
                import_performed=bool(
                    service_result and service_result.import_performed
                ),
                claim_ref=claim_ref,
                ingress_audit_ref=ingress_audit_ref,
                result_ref=result_ref,
            )
        try:
            self._claim_store.complete_claim(
                claim_ref=claim_ref,
                result_ref=result_ref,
                at_ms=int(request.received_at_epoch_ms),
            )
        except Exception:
            return self._unavailable(
                "SKILL_PROVIDER_INTAKE_RESULT_COMMIT_FAILED",
                executed=bool(service_result and service_result.quarantined),
                quarantined=bool(service_result and service_result.quarantined),
                staged=bool(service_result and service_result.staged),
                activation_performed=bool(
                    service_result and service_result.activation_performed
                ),
                import_performed=bool(
                    service_result and service_result.import_performed
                ),
                claim_ref=claim_ref,
                ingress_audit_ref=ingress_audit_ref,
                decision_audit_ref=decision_audit_ref,
                result_ref=result_ref,
            )

        return SignedSkillProviderIntakeOutcome(
            allowed=bool(accepted),
            executed=bool(service_result and service_result.quarantined),
            duplicate=False,
            quarantined=bool(service_result and service_result.quarantined),
            staged=bool(service_result and service_result.staged),
            activation_performed=bool(
                service_result and service_result.activation_performed
            ),
            import_performed=bool(service_result and service_result.import_performed),
            reason_code=stable_reason,
            claim_ref=claim_ref,
            ingress_audit_ref=ingress_audit_ref,
            decision_audit_ref=decision_audit_ref,
            result_ref=result_ref,
            bundle_id=(service_result.bundle_id if service_result else None),
            bundle_digest=(service_result.bundle_digest if service_result else None),
            repository_state=(
                service_result.repository_state if service_result else None
            ),
        )

    def _reject_unparsed(
        self,
        request: SignedSkillProviderIntakeRequest,
        reason_code: str,
    ) -> SignedSkillProviderIntakeOutcome:
        if self._ingress_service is None:
            return self._unavailable("SKILL_PROVIDER_INTAKE_INGRESS_UNAVAILABLE")
        try:
            decision = self._ingress_service.reject_unparsed_attempt(
                transport_key=str(request.transport_key),
                surface=SKILL_PROVIDER_INTAKE_SURFACE,
                method=str(request.method).upper(),
                normalized_path=SKILL_PROVIDER_INTAKE_PATH,
                action=SKILL_PROVIDER_INTAKE_ACTION,
                required_scopes=SKILL_PROVIDER_INTAKE_SCOPES,
                received_at_epoch_ms=int(request.received_at_epoch_ms),
                correlation_id=request.correlation_id,
                claimed_principal_id=(
                    request.identity.principal.principal_id
                    if request.identity is not None
                    else None
                ),
                key_id=(
                    request.identity.credential_proof.key_id
                    if request.identity is not None
                    else None
                ),
            )
        except Exception:
            return self._unavailable("SKILL_PROVIDER_INTAKE_INGRESS_UNAVAILABLE")
        reported = (
            _stable_reason(reason_code)
            if decision.reason_code is IngressReasonCode.SCHEMA_INVALID
            else decision.reason_code.value
        )
        return SignedSkillProviderIntakeOutcome(
            allowed=False,
            executed=False,
            duplicate=False,
            quarantined=False,
            staged=False,
            activation_performed=False,
            import_performed=False,
            reason_code=reported,
            ingress_audit_ref=decision.audit_ref,
        )

    @staticmethod
    def _unavailable(
        reason_code: str,
        *,
        executed: bool = False,
        duplicate: bool = False,
        quarantined: bool = False,
        staged: bool = False,
        activation_performed: bool = False,
        import_performed: bool = False,
        claim_ref: str | None = None,
        ingress_audit_ref: str | None = None,
        decision_audit_ref: str | None = None,
        result_ref: str | None = None,
    ) -> SignedSkillProviderIntakeOutcome:
        return SignedSkillProviderIntakeOutcome(
            allowed=False,
            executed=bool(executed),
            duplicate=bool(duplicate),
            quarantined=bool(quarantined),
            staged=bool(staged),
            activation_performed=bool(activation_performed),
            import_performed=bool(import_performed),
            reason_code=_stable_reason(reason_code),
            claim_ref=claim_ref,
            ingress_audit_ref=ingress_audit_ref,
            decision_audit_ref=decision_audit_ref,
            result_ref=result_ref,
        )


def _parse_intake_body(raw_body: bytes) -> _ParsedIntake:
    if (
        not isinstance(raw_body, bytes)
        or not raw_body
        or len(raw_body) > MAX_SKILL_PROVIDER_INTAKE_BODY_BYTES
    ):
        raise _IntakeContractError("SKILL_PROVIDER_INTAKE_BODY_INVALID")
    try:
        decoded = json.loads(
            raw_body.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_nonfinite,
        )
    except _IntakeContractError:
        raise
    except Exception as exc:
        raise _IntakeContractError("SKILL_PROVIDER_INTAKE_BODY_INVALID") from exc
    if not isinstance(decoded, dict) or set(decoded) != _BODY_FIELDS:
        raise _IntakeContractError("SKILL_PROVIDER_INTAKE_BODY_INVALID")
    if decoded.get("schema_version") != SKILL_PROVIDER_INTAKE_SCHEMA:
        raise _IntakeContractError("SKILL_PROVIDER_INTAKE_SCHEMA_UNSUPPORTED")
    source_ref = decoded.get("source_ref")
    bundle_kind = decoded.get("bundle_kind")
    publisher_id = decoded.get("publisher_id")
    if not isinstance(source_ref, str) or _SOURCE_REF_RE.fullmatch(source_ref) is None:
        raise _IntakeContractError("SKILL_PROVIDER_INTAKE_SOURCE_REF_INVALID")
    if bundle_kind not in {"SKILL", "PROVIDER"}:
        raise _IntakeContractError("SKILL_PROVIDER_INTAKE_KIND_INVALID")
    if (
        not isinstance(publisher_id, str)
        or not publisher_id.strip()
        or len(publisher_id.encode("utf-8", errors="strict")) > 255
    ):
        raise _IntakeContractError("SKILL_PROVIDER_INTAKE_PUBLISHER_INVALID")
    return _ParsedIntake(
        source_ref=source_ref,
        bundle_kind=bundle_kind,
        publisher_id=publisher_id,
    )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for key, value in pairs:
        if key in parsed:
            raise _IntakeContractError("SKILL_PROVIDER_INTAKE_DUPLICATE_FIELD")
        parsed[key] = value
    return parsed


def _reject_nonfinite(_value: str) -> None:
    raise _IntakeContractError("SKILL_PROVIDER_INTAKE_NONFINITE_VALUE")


def _stable_reason(value: object) -> str:
    normalized = str(value or "").strip().upper()
    if _REASON_RE.fullmatch(normalized) is None:
        return "SKILL_PROVIDER_INTAKE_FAILED"
    return normalized


def _result_ref(claim_ref: str, reason_code: str) -> str:
    digest = hashlib.sha256(
        f"{claim_ref}\n{reason_code}".encode("utf-8")
    ).hexdigest()
    return f"skill-provider-intake-result://{digest}/{reason_code}"


def _reason_from_result_ref(result_ref: str) -> str | None:
    prefix = "skill-provider-intake-result://"
    if not result_ref.startswith(prefix) or "/" not in result_ref[len(prefix):]:
        return None
    digest, reason = result_ref[len(prefix):].split("/", 1)
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return None
    normalized = _stable_reason(reason)
    return normalized if normalized == reason else None


def _application_audit_event(
    *,
    request: SignedSkillProviderIntakeRequest,
    secure_request: SecureIngressRequest,
    claim_ref: str,
    reason_code: str,
    accepted: bool,
) -> IngressAuditEvent:
    identity = request.identity
    if identity is None:
        raise ValueError("verified identity required")
    return IngressAuditEvent(
        request_id=identity.request_id,
        correlation_id=request.correlation_id,
        surface=SKILL_PROVIDER_INTAKE_SURFACE,
        action=SKILL_PROVIDER_INTAKE_ACTION,
        method="POST",
        normalized_path=SKILL_PROVIDER_INTAKE_PATH,
        principal_id=identity.principal.principal_id,
        tenant_id=identity.principal.tenant_id,
        key_id=identity.credential_proof.key_id,
        credential_kind=identity.credential_proof.kind.value,
        credential_ref_digest=None,
        requested_scopes=identity.requested_scopes,
        required_scopes=SKILL_PROVIDER_INTAKE_SCOPES,
        body_sha256=secure_request.body_sha256,
        idempotency_digest=digest_identifier(request.idempotency_key),
        nonce_digest=digest_identifier(request.nonce),
        transport_key_digest=digest_identifier(request.transport_key),
        decision="ALLOW" if accepted else "DENY",
        reason_code=reason_code,
        stage="APPLICATION",
        claim_ref=claim_ref,
        rate_snapshot=None,
        side_effects_executed=False,
        ts_ms=int(request.received_at_epoch_ms),
    )


__all__ = [
    "MAX_SKILL_PROVIDER_INTAKE_BODY_BYTES",
    "SKILL_PROVIDER_INTAKE_ACTION",
    "SKILL_PROVIDER_INTAKE_AUDIENCE",
    "SKILL_PROVIDER_INTAKE_PATH",
    "SKILL_PROVIDER_INTAKE_SCHEMA",
    "SKILL_PROVIDER_INTAKE_SCOPES",
    "SKILL_PROVIDER_INTAKE_SURFACE",
    "SecureSignedSkillProviderIntake",
    "SignedSkillProviderIntakeOutcome",
    "SignedSkillProviderIntakeRequest",
    "SkillProviderIntakeSourceResolver",
]
