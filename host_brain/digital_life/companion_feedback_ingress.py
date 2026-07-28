"""Signed, structured feedback on one accepted companion response.

The endpoint is deliberately narrower than conversational text analysis.  A
user can explicitly evaluate one receipt-backed response with a small bounded
set of outcomes; the relationship aggregate verifies the response is still an
open provisional episode and remains the sole writer of the resulting
aftereffect.  Raw message bodies, ratings, Soul changes and arbitrary Life
commands never cross this boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any

from host_brain.digital_life.relationship_lifecycle import (
    RelationshipAftereffectResult,
    RelationshipLifecycleService,
)
from host_brain.secure_ingress import (
    IngressSurface,
    SecureIngressAuthority,
    SecureIngressAuthorityEvaluator,
    SecureIngressHttpAuthorityAdapter,
)
from natsume_contracts.life import CompanionResponseFeedbackRequestV1, LifeCommandReceipt


COMPANION_FEEDBACK_INGRESS_UNAVAILABLE = "COMPANION_FEEDBACK_INGRESS_UNAVAILABLE"
COMPANION_FEEDBACK_BODY_INVALID = "COMPANION_FEEDBACK_BODY_INVALID"
COMPANION_FEEDBACK_PRINCIPAL_KIND_DENIED = (
    "COMPANION_FEEDBACK_PRINCIPAL_KIND_DENIED"
)
COMPANION_FEEDBACK_SCOPE_BINDING_DENIED = "COMPANION_FEEDBACK_SCOPE_BINDING_DENIED"
COMPANION_FEEDBACK_EVIDENCE_UNAVAILABLE = "COMPANION_FEEDBACK_EVIDENCE_UNAVAILABLE"
COMPANION_FEEDBACK_RESULT_CONFLICT = "COMPANION_FEEDBACK_RESULT_CONFLICT"
COMPANION_FEEDBACK_RESULT_RECORD_UNAVAILABLE = (
    "COMPANION_FEEDBACK_RESULT_RECORD_UNAVAILABLE"
)
COMPANION_FEEDBACK_COMMAND_NOT_APPLIED = "COMPANION_FEEDBACK_COMMAND_NOT_APPLIED"
COMPANION_FEEDBACK_LIFECYCLE_REJECTED = "COMPANION_FEEDBACK_LIFECYCLE_REJECTED"


class CompanionFeedbackIngressError(RuntimeError):
    """Secret-free failure suitable for the Host HTTP adapter."""

    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        self.reason_code = str(reason_code or COMPANION_FEEDBACK_BODY_INVALID)
        self.detail = str(detail or self.reason_code)
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class CompanionFeedbackIngressResult:
    """Canonical feedback receipt without response or user body content."""

    request: CompanionResponseFeedbackRequestV1
    lifecycle: RelationshipAftereffectResult
    receipt: LifeCommandReceipt
    receipt_ref: str
    ingress_audit_ref: str
    ingress_claim_ref: str
    replayed: bool

    def as_dict(self) -> dict[str, Any]:
        aftereffect = self.lifecycle.aftereffect
        if aftereffect is None:
            raise RuntimeError("companion feedback result lacks canonical aftereffect")
        return {
            "status": self.receipt.status.value,
            "reason_code": self.lifecycle.reason_code,
            "workspace_id": self.request.workspace_id,
            "relationship_subject_id": self.request.relationship_subject_id,
            "session_id": self.request.session_id,
            "response_receipt_ref": self.request.response_receipt_ref,
            "feedback": self.request.feedback,
            "aftereffect_id": aftereffect.aftereffect_id,
            "aftereffect_outcome": aftereffect.outcome,
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
        raise CompanionFeedbackIngressError(
            COMPANION_FEEDBACK_BODY_INVALID,
            "request must be one strict UTF-8 JSON object",
        ) from exc
    if not isinstance(payload, Mapping):
        raise CompanionFeedbackIngressError(
            COMPANION_FEEDBACK_BODY_INVALID,
            "request must be a JSON object",
        )
    return payload


def _request_from_raw_body(raw_body: bytes) -> CompanionResponseFeedbackRequestV1:
    try:
        return CompanionResponseFeedbackRequestV1.from_mapping(
            _strict_json_object(raw_body)
        )
    except CompanionFeedbackIngressError:
        raise
    except (TypeError, ValueError) as exc:
        raise CompanionFeedbackIngressError(
            COMPANION_FEEDBACK_BODY_INVALID,
            "request does not satisfy companion response feedback v1",
        ) from exc


def _rejection_ref(*, authority: SecureIngressAuthority, reason_code: str) -> str:
    material = {
        "schema_version": "companion_feedback_rejection.v1",
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
    return "companion-feedback-rejection://sha256/" + hashlib.sha256(encoded).hexdigest()


class CompanionFeedbackIngress:
    """Bridge one authenticated user response evaluation into Life authority."""

    def __init__(
        self,
        *,
        evaluator: SecureIngressAuthorityEvaluator,
        complete_claim: Callable[..., None],
        relationship_lifecycle_service_provider: Callable[
            [], RelationshipLifecycleService | None
        ],
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(evaluator, SecureIngressAuthorityEvaluator):
            raise TypeError("evaluator must be a SecureIngressAuthorityEvaluator")
        if not callable(complete_claim):
            raise TypeError("complete_claim must be callable")
        if not callable(relationship_lifecycle_service_provider):
            raise TypeError("relationship_lifecycle_service_provider must be callable")
        self._authority_adapter = SecureIngressHttpAuthorityAdapter(
            evaluator=evaluator,
            surface=IngressSurface.COMPANION_FEEDBACK,
        )
        self._complete_claim = complete_claim
        self._relationship_lifecycle_service_provider = (
            relationship_lifecycle_service_provider
        )
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def submit(
        self,
        *,
        request: Any,
        raw_body: bytes,
        received_at_ms: int | None = None,
    ) -> CompanionFeedbackIngressResult:
        """Authenticate and bind one explicit evaluation to an open receipt."""

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
            raise CompanionFeedbackIngressError(authority.reason_code)
        try:
            control = _request_from_raw_body(raw_body)
            self._assert_user_scope(authority=authority, control=control)
        except CompanionFeedbackIngressError as exc:
            self._complete_rejection(
                authority=authority,
                reason_code=exc.reason_code,
                at_ms=now_ms,
            )
            raise

        lifecycle = self._relationship_lifecycle_service_provider()
        if not isinstance(lifecycle, RelationshipLifecycleService):
            raise CompanionFeedbackIngressError(
                COMPANION_FEEDBACK_INGRESS_UNAVAILABLE,
                "canonical relationship lifecycle service is unavailable",
            )
        audit_ref = str(authority.audit_ref or "").strip()
        request_id = str(authority.request_id or "").strip()
        claim_ref = str(authority.claim_ref or "").strip()
        if not audit_ref or not audit_ref.startswith("secure-ingress-audit://"):
            self._complete_rejection(
                authority=authority,
                reason_code=COMPANION_FEEDBACK_EVIDENCE_UNAVAILABLE,
                at_ms=now_ms,
            )
            raise CompanionFeedbackIngressError(
                COMPANION_FEEDBACK_EVIDENCE_UNAVAILABLE,
                "Secure Ingress did not provide durable response-feedback evidence",
            )
        if not request_id or not claim_ref:
            raise CompanionFeedbackIngressError(
                COMPANION_FEEDBACK_INGRESS_UNAVAILABLE,
                "Secure Ingress did not provide replay authority",
            )
        try:
            lifecycle_result = lifecycle.record_explicit_response_feedback(
                workspace_id=control.workspace_id,
                relationship_subject_id=control.relationship_subject_id,
                session_id=control.session_id,
                request_id=request_id,
                response_receipt_ref=control.response_receipt_ref,
                feedback=control.feedback,
                source_ref=audit_ref,
            )
        except (RuntimeError, ValueError) as exc:
            reason_code = self._lifecycle_reason(exc)
            self._complete_rejection(
                authority=authority,
                reason_code=reason_code,
                at_ms=now_ms,
            )
            raise CompanionFeedbackIngressError(reason_code) from exc
        receipt = lifecycle_result.receipt
        if (
            not lifecycle_result.recorded
            or receipt is None
            or receipt.status.value != "APPLIED"
        ):
            raise CompanionFeedbackIngressError(
                COMPANION_FEEDBACK_COMMAND_NOT_APPLIED,
                "canonical relationship aftereffect was not applied",
            )
        receipt_ref = f"life-command://{receipt.command_id}"
        existing_result_ref = str(authority.existing_result_ref or "").strip()
        if existing_result_ref and existing_result_ref != receipt_ref:
            raise CompanionFeedbackIngressError(
                COMPANION_FEEDBACK_RESULT_CONFLICT,
                "Secure Ingress result does not match canonical feedback receipt",
            )
        self._complete_success(
            authority=authority,
            receipt_ref=receipt_ref,
            at_ms=now_ms,
        )
        return CompanionFeedbackIngressResult(
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
        control: CompanionResponseFeedbackRequestV1,
    ) -> None:
        if str(authority.principal_kind or "").strip().lower() != "user":
            raise CompanionFeedbackIngressError(
                COMPANION_FEEDBACK_PRINCIPAL_KIND_DENIED,
                "only a user principal may evaluate a companion response",
            )
        if (
            str(authority.principal_id or "").strip()
            != control.relationship_subject_id
            or str(authority.tenant_id or "").strip() != control.workspace_id
        ):
            raise CompanionFeedbackIngressError(
                COMPANION_FEEDBACK_SCOPE_BINDING_DENIED,
                "signed principal and tenant must match the relationship scope",
            )

    @staticmethod
    def _lifecycle_reason(exc: Exception) -> str:
        candidate = str(exc or "").strip().upper()
        if candidate.startswith("RELATIONSHIP_"):
            return candidate
        return COMPANION_FEEDBACK_LIFECYCLE_REJECTED

    def _complete_success(
        self,
        *,
        authority: SecureIngressAuthority,
        receipt_ref: str,
        at_ms: int,
    ) -> None:
        claim_ref = str(authority.claim_ref or "").strip()
        if not claim_ref:
            raise CompanionFeedbackIngressError(
                COMPANION_FEEDBACK_RESULT_RECORD_UNAVAILABLE,
                "Secure Ingress claim is unavailable",
            )
        try:
            self._complete_claim(
                claim_ref=claim_ref,
                result_ref=receipt_ref,
                at_ms=at_ms,
            )
        except Exception as exc:
            raise CompanionFeedbackIngressError(
                COMPANION_FEEDBACK_RESULT_RECORD_UNAVAILABLE,
                "canonical feedback result could not be attached to Secure Ingress",
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
            raise CompanionFeedbackIngressError(
                COMPANION_FEEDBACK_RESULT_RECORD_UNAVAILABLE,
                "rejected feedback result could not be attached to Secure Ingress",
            ) from exc


__all__ = [
    "COMPANION_FEEDBACK_BODY_INVALID",
    "COMPANION_FEEDBACK_COMMAND_NOT_APPLIED",
    "COMPANION_FEEDBACK_EVIDENCE_UNAVAILABLE",
    "COMPANION_FEEDBACK_INGRESS_UNAVAILABLE",
    "COMPANION_FEEDBACK_LIFECYCLE_REJECTED",
    "COMPANION_FEEDBACK_PRINCIPAL_KIND_DENIED",
    "COMPANION_FEEDBACK_RESULT_CONFLICT",
    "COMPANION_FEEDBACK_RESULT_RECORD_UNAVAILABLE",
    "COMPANION_FEEDBACK_SCOPE_BINDING_DENIED",
    "CompanionFeedbackIngress",
    "CompanionFeedbackIngressError",
    "CompanionFeedbackIngressResult",
]
