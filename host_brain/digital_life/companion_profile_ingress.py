"""Signed user control for a reversible companion-expression profile.

This boundary accepts a fully structured low-risk preference request only. It
does not parse a chat sentence, infer an emotion, accept a prompt fragment, or
let a caller patch Soul, self-affect, relationship consent, or intimacy. The
canonical Life journal remains the only profile writer.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any

from host_brain.digital_life.companion_profile_lifecycle import (
    CompanionProfileLifecycleResult,
    CompanionProfileLifecycleService,
)
from host_brain.secure_ingress import (
    IngressSurface,
    SecureIngressAuthority,
    SecureIngressAuthorityEvaluator,
    SecureIngressHttpAuthorityAdapter,
)
from natsume_contracts.life import (
    CompanionProfileControlRequestV1,
    CompanionProfileRollbackRequestV1,
    LifeCommandReceipt,
)


COMPANION_PROFILE_INGRESS_UNAVAILABLE = "COMPANION_PROFILE_INGRESS_UNAVAILABLE"
COMPANION_PROFILE_BODY_INVALID = "COMPANION_PROFILE_BODY_INVALID"
COMPANION_PROFILE_PRINCIPAL_KIND_DENIED = "COMPANION_PROFILE_PRINCIPAL_KIND_DENIED"
COMPANION_PROFILE_SCOPE_BINDING_DENIED = "COMPANION_PROFILE_SCOPE_BINDING_DENIED"
COMPANION_PROFILE_EVIDENCE_UNAVAILABLE = "COMPANION_PROFILE_EVIDENCE_UNAVAILABLE"
COMPANION_PROFILE_RESULT_CONFLICT = "COMPANION_PROFILE_RESULT_CONFLICT"
COMPANION_PROFILE_RESULT_RECORD_UNAVAILABLE = (
    "COMPANION_PROFILE_RESULT_RECORD_UNAVAILABLE"
)
COMPANION_PROFILE_COMMAND_NOT_APPLIED = "COMPANION_PROFILE_COMMAND_NOT_APPLIED"
COMPANION_PROFILE_LIFECYCLE_REJECTED = "COMPANION_PROFILE_LIFECYCLE_REJECTED"


class CompanionProfileIngressError(RuntimeError):
    """Secret-free error suitable for the Host HTTP adapter."""

    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        self.reason_code = str(reason_code or COMPANION_PROFILE_BODY_INVALID)
        self.detail = str(detail or self.reason_code)
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class CompanionProfileIngressResult:
    """Accepted profile lifecycle receipt without private conversation content."""

    request: CompanionProfileControlRequestV1 | CompanionProfileRollbackRequestV1
    lifecycle: CompanionProfileLifecycleResult
    receipt: LifeCommandReceipt
    receipt_ref: str
    ingress_audit_ref: str
    ingress_claim_ref: str
    replayed: bool

    def as_dict(self) -> dict[str, Any]:
        state = self.lifecycle.state
        candidate = state.canary_profile
        profile = candidate if candidate is not None else state.active_profile
        return {
            "status": self.receipt.status.value,
            "reason_code": self.lifecycle.reason_code,
            "workspace_id": self.request.workspace_id,
            "relationship_subject_id": self.request.relationship_subject_id,
            "session_id": self.request.session_id,
            "profile_id": profile.profile_id,
            "profile_digest": profile.profile_digest,
            "profile_revision": state.revision,
            "profile_stage": "CANARY" if candidate is not None else "ACTIVE",
            "action": (
                "ROLLBACK"
                if isinstance(self.request, CompanionProfileRollbackRequestV1)
                else "CANARY_START"
            ),
            "command_id": self.receipt.command_id,
            "event_id": self.receipt.event_id,
            "state_version": self.receipt.state_version,
            "state_digest": self.receipt.state_digest,
            "receipt_ref": self.receipt_ref,
            "replayed": self.replayed,
            "secure_ingress": {
                "audit_ref": self.ingress_audit_ref,
                "claim_ref": self.ingress_claim_ref,
            },
        }


def _strict_json_object(raw_body: bytes) -> Mapping[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def reject_non_finite(_value: str) -> None:
        raise ValueError("non-finite JSON value")

    try:
        payload = json.loads(
            bytes(raw_body).decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_non_finite,
        )
    except Exception as exc:
        raise CompanionProfileIngressError(
            COMPANION_PROFILE_BODY_INVALID,
            "request must be one strict UTF-8 JSON object",
        ) from exc
    if not isinstance(payload, Mapping):
        raise CompanionProfileIngressError(
            COMPANION_PROFILE_BODY_INVALID,
            "request must be a JSON object",
        )
    return payload


def _request_from_raw_body(raw_body: bytes) -> CompanionProfileControlRequestV1:
    try:
        return CompanionProfileControlRequestV1.from_mapping(
            _strict_json_object(raw_body)
        )
    except CompanionProfileIngressError:
        raise
    except (TypeError, ValueError) as exc:
        raise CompanionProfileIngressError(
            COMPANION_PROFILE_BODY_INVALID,
            "request does not satisfy companion profile control v1",
        ) from exc


def _rollback_request_from_raw_body(
    raw_body: bytes,
) -> CompanionProfileRollbackRequestV1:
    try:
        return CompanionProfileRollbackRequestV1.from_mapping(
            _strict_json_object(raw_body)
        )
    except CompanionProfileIngressError:
        raise
    except (TypeError, ValueError) as exc:
        raise CompanionProfileIngressError(
            COMPANION_PROFILE_BODY_INVALID,
            "request does not satisfy companion profile rollback v1",
        ) from exc


def _rejection_ref(*, authority: SecureIngressAuthority, reason_code: str) -> str:
    material = {
        "schema_version": "companion_profile_rejection.v1",
        "request_id": str(authority.request_id or ""),
        "claim_ref": str(authority.claim_ref or ""),
        "body_sha256": str(authority.body_sha256 or ""),
        "reason_code": str(reason_code or ""),
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "companion-profile-rejection://sha256/" + hashlib.sha256(encoded).hexdigest()


class CompanionProfileIngress:
    """Admit a scoped user preference trial into the canonical Life UoW."""

    def __init__(
        self,
        *,
        evaluator: SecureIngressAuthorityEvaluator,
        complete_claim: Callable[..., None],
        companion_profile_lifecycle_service_provider: Callable[
            [], CompanionProfileLifecycleService | None
        ],
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(evaluator, SecureIngressAuthorityEvaluator):
            raise TypeError("evaluator must be a SecureIngressAuthorityEvaluator")
        if not callable(complete_claim):
            raise TypeError("complete_claim must be callable")
        if not callable(companion_profile_lifecycle_service_provider):
            raise TypeError("companion_profile_lifecycle_service_provider must be callable")
        self._authority_adapter = SecureIngressHttpAuthorityAdapter(
            evaluator=evaluator,
            surface=IngressSurface.COMPANION_PROFILE,
        )
        self._rollback_authority_adapter = SecureIngressHttpAuthorityAdapter(
            evaluator=evaluator,
            surface=IngressSurface.COMPANION_PROFILE_ROLLBACK,
        )
        self._complete_claim = complete_claim
        self._companion_profile_lifecycle_service_provider = (
            companion_profile_lifecycle_service_provider
        )
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def submit(
        self,
        *,
        request: Any,
        raw_body: bytes,
        received_at_ms: int | None = None,
    ) -> CompanionProfileIngressResult:
        """Authenticate one explicit low-risk preference trial request."""

        now_ms = max(
            0,
            int(self._clock_ms() if received_at_ms is None else received_at_ms),
        )
        authority = self._authority_adapter.evaluate(
            request=request,
            raw_body=bytes(raw_body),
            received_at_ms=now_ms,
        )
        if not authority.admissible:
            raise CompanionProfileIngressError(authority.reason_code)
        try:
            control = _request_from_raw_body(raw_body)
            self._assert_user_scope(authority=authority, control=control)
        except CompanionProfileIngressError as exc:
            self._complete_rejection(
                authority=authority,
                reason_code=exc.reason_code,
                at_ms=now_ms,
            )
            raise

        lifecycle = self._companion_profile_lifecycle_service_provider()
        if not isinstance(lifecycle, CompanionProfileLifecycleService):
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_INGRESS_UNAVAILABLE,
                "canonical companion profile lifecycle service is unavailable",
            )
        audit_ref = str(authority.audit_ref or "").strip()
        request_id = str(authority.request_id or "").strip()
        claim_ref = str(authority.claim_ref or "").strip()
        if not audit_ref or not audit_ref.startswith("secure-ingress-audit://"):
            self._complete_rejection(
                authority=authority,
                reason_code=COMPANION_PROFILE_EVIDENCE_UNAVAILABLE,
                at_ms=now_ms,
            )
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_EVIDENCE_UNAVAILABLE,
                "Secure Ingress did not provide durable preference-control evidence",
            )
        if not request_id or not claim_ref:
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_INGRESS_UNAVAILABLE,
                "Secure Ingress did not provide replay authority",
            )
        try:
            lifecycle_result = lifecycle.start_canary(
                workspace_id=control.workspace_id,
                relationship_subject_id=control.relationship_subject_id,
                session_id=control.session_id,
                request_id=request_id,
                preferences=control.preferences,
                evidence_aftereffect_ids=None,
                source_control_ref=audit_ref,
            )
        except (RuntimeError, ValueError) as exc:
            reason_code = self._lifecycle_reason(exc)
            self._complete_rejection(
                authority=authority,
                reason_code=reason_code,
                at_ms=now_ms,
            )
            raise CompanionProfileIngressError(reason_code) from exc
        receipt = lifecycle_result.receipt
        if receipt is None or receipt.status.value != "APPLIED":
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_COMMAND_NOT_APPLIED,
                "canonical companion profile command was not applied",
            )
        receipt_ref = f"life-command://{receipt.command_id}"
        existing_result_ref = str(authority.existing_result_ref or "").strip()
        if existing_result_ref and existing_result_ref != receipt_ref:
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_RESULT_CONFLICT,
                "Secure Ingress result does not match canonical companion profile receipt",
            )
        self._complete_success(
            authority=authority,
            receipt_ref=receipt_ref,
            at_ms=now_ms,
        )
        return CompanionProfileIngressResult(
            request=control,
            lifecycle=lifecycle_result,
            receipt=receipt,
            receipt_ref=receipt_ref,
            ingress_audit_ref=audit_ref,
            ingress_claim_ref=claim_ref,
            replayed=bool(authority.duplicate or receipt.replayed),
        )

    def submit_rollback(
        self,
        *,
        request: Any,
        raw_body: bytes,
        received_at_ms: int | None = None,
    ) -> CompanionProfileIngressResult:
        """Authenticate one user-requested exact rollback of an active profile."""

        now_ms = max(
            0,
            int(self._clock_ms() if received_at_ms is None else received_at_ms),
        )
        authority = self._rollback_authority_adapter.evaluate(
            request=request,
            raw_body=bytes(raw_body),
            received_at_ms=now_ms,
        )
        if not authority.admissible:
            raise CompanionProfileIngressError(authority.reason_code)
        try:
            control = _rollback_request_from_raw_body(raw_body)
            self._assert_user_scope(authority=authority, control=control)
        except CompanionProfileIngressError as exc:
            self._complete_rejection(
                authority=authority,
                reason_code=exc.reason_code,
                at_ms=now_ms,
            )
            raise

        lifecycle = self._companion_profile_lifecycle_service_provider()
        if not isinstance(lifecycle, CompanionProfileLifecycleService):
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_INGRESS_UNAVAILABLE,
                "canonical companion profile lifecycle service is unavailable",
            )
        audit_ref = str(authority.audit_ref or "").strip()
        request_id = str(authority.request_id or "").strip()
        claim_ref = str(authority.claim_ref or "").strip()
        if not audit_ref or not audit_ref.startswith("secure-ingress-audit://"):
            self._complete_rejection(
                authority=authority,
                reason_code=COMPANION_PROFILE_EVIDENCE_UNAVAILABLE,
                at_ms=now_ms,
            )
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_EVIDENCE_UNAVAILABLE,
                "Secure Ingress did not provide durable profile-rollback evidence",
            )
        if not request_id or not claim_ref:
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_INGRESS_UNAVAILABLE,
                "Secure Ingress did not provide replay authority",
            )
        try:
            lifecycle_result = lifecycle.rollback_active(
                workspace_id=control.workspace_id,
                relationship_subject_id=control.relationship_subject_id,
                session_id=control.session_id,
                request_id=request_id,
                expected_active_profile_id=control.expected_active_profile_id,
                source_control_ref=audit_ref,
            )
        except (RuntimeError, ValueError) as exc:
            reason_code = self._lifecycle_reason(exc)
            self._complete_rejection(
                authority=authority,
                reason_code=reason_code,
                at_ms=now_ms,
            )
            raise CompanionProfileIngressError(reason_code) from exc
        receipt = lifecycle_result.receipt
        if receipt is None or receipt.status.value != "APPLIED":
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_COMMAND_NOT_APPLIED,
                "canonical companion profile rollback was not applied",
            )
        receipt_ref = f"life-command://{receipt.command_id}"
        existing_result_ref = str(authority.existing_result_ref or "").strip()
        if existing_result_ref and existing_result_ref != receipt_ref:
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_RESULT_CONFLICT,
                "Secure Ingress result does not match canonical profile rollback receipt",
            )
        self._complete_success(
            authority=authority,
            receipt_ref=receipt_ref,
            at_ms=now_ms,
        )
        return CompanionProfileIngressResult(
            request=control,
            lifecycle=lifecycle_result,
            receipt=receipt,
            receipt_ref=receipt_ref,
            ingress_audit_ref=audit_ref,
            ingress_claim_ref=claim_ref,
            replayed=bool(authority.duplicate or receipt.replayed),
        )

    @staticmethod
    def _assert_user_scope(
        *,
        authority: SecureIngressAuthority,
        control: (
            CompanionProfileControlRequestV1 | CompanionProfileRollbackRequestV1
        ),
    ) -> None:
        if str(authority.principal_kind or "").strip().lower() != "user":
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_PRINCIPAL_KIND_DENIED,
                "only a user principal may trial companion expression preferences",
            )
        if (
            str(authority.principal_id or "").strip()
            != control.relationship_subject_id
            or str(authority.tenant_id or "").strip() != control.workspace_id
        ):
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_SCOPE_BINDING_DENIED,
                "signed principal and tenant must match the relationship scope",
            )

    @staticmethod
    def _lifecycle_reason(exc: Exception) -> str:
        candidate = str(exc or "").strip().upper()
        if candidate.startswith("COMPANION_PROFILE_"):
            return candidate
        return COMPANION_PROFILE_LIFECYCLE_REJECTED

    def _complete_success(
        self,
        *,
        authority: SecureIngressAuthority,
        receipt_ref: str,
        at_ms: int,
    ) -> None:
        claim_ref = str(authority.claim_ref or "").strip()
        if not claim_ref:
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_RESULT_RECORD_UNAVAILABLE,
                "Secure Ingress claim is unavailable",
            )
        try:
            self._complete_claim(
                claim_ref=claim_ref,
                result_ref=receipt_ref,
                at_ms=at_ms,
            )
        except Exception as exc:
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_RESULT_RECORD_UNAVAILABLE,
                "canonical profile result could not be attached to Secure Ingress",
            ) from exc

    def _complete_rejection(
        self,
        *,
        authority: SecureIngressAuthority,
        reason_code: str,
        at_ms: int,
    ) -> None:
        claim_ref = str(authority.claim_ref or "").strip()
        if not claim_ref:
            return
        result_ref = _rejection_ref(authority=authority, reason_code=reason_code)
        existing_result_ref = str(authority.existing_result_ref or "").strip()
        if existing_result_ref and existing_result_ref != result_ref:
            return
        try:
            self._complete_claim(
                claim_ref=claim_ref,
                result_ref=result_ref,
                at_ms=at_ms,
            )
        except Exception as exc:
            raise CompanionProfileIngressError(
                COMPANION_PROFILE_RESULT_RECORD_UNAVAILABLE,
                "rejected profile control result could not be attached to Secure Ingress",
            ) from exc


__all__ = [
    "COMPANION_PROFILE_BODY_INVALID",
    "COMPANION_PROFILE_COMMAND_NOT_APPLIED",
    "COMPANION_PROFILE_EVIDENCE_UNAVAILABLE",
    "COMPANION_PROFILE_INGRESS_UNAVAILABLE",
    "COMPANION_PROFILE_LIFECYCLE_REJECTED",
    "COMPANION_PROFILE_PRINCIPAL_KIND_DENIED",
    "COMPANION_PROFILE_RESULT_CONFLICT",
    "COMPANION_PROFILE_RESULT_RECORD_UNAVAILABLE",
    "COMPANION_PROFILE_SCOPE_BINDING_DENIED",
    "CompanionProfileIngress",
    "CompanionProfileIngressError",
    "CompanionProfileIngressResult",
]
