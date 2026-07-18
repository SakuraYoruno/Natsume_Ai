"""NE-05 durable Skill lifecycle contracts and fail-closed activation service.

The supply-chain repository owns bundle state.  This module owns the stronger
activation boundary: a staged bundle is not executable until an immutable Lab,
Review, SBOM, signature, sandbox and rollback evidence set is persisted.  The
runtime registry resolves only the repository's active content-addressed
record; it never scans a directory or imports a bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Protocol

from host_brain.persistence.supply_chain_repository import (
    BundleKind,
    BundleState,
    SkillSupplyChainStoreError,
)


_DIGEST_RE = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_REF_RE = re.compile(r"\A[a-z][a-z0-9+.-]*://[^\s\x00-\x1f\x7f]{1,1024}\Z")
_ID_RE = re.compile(r"\A[a-z][a-z0-9+.-]*://[^\s\x00-\x1f\x7f]{1,255}\Z")
_REASON_RE = re.compile(r"\A[A-Z][A-Z0-9_]{2,127}\Z")


class SkillLifecycleError(RuntimeError):
    """A Skill lifecycle transition failed closed."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


def _text(value: Any, field: str, *, maximum: int = 1024) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(ord(char) < 32 or ord(char) == 127 for char in normalized)
    ):
        raise SkillLifecycleError(f"SKILL_LIFECYCLE_{field.upper()}_INVALID")
    return normalized


def _digest(value: Any, field: str) -> str:
    normalized = _text(value, field, maximum=71)
    if _DIGEST_RE.fullmatch(normalized) is None:
        raise SkillLifecycleError(f"SKILL_LIFECYCLE_{field.upper()}_INVALID")
    return normalized


def _ref(value: Any, field: str) -> str:
    normalized = _text(value, field)
    if _REF_RE.fullmatch(normalized) is None:
        raise SkillLifecycleError(f"SKILL_LIFECYCLE_{field.upper()}_INVALID")
    return normalized


def _canonical(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise SkillLifecycleError("SKILL_LIFECYCLE_EVIDENCE_NOT_JSON") from exc


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True, slots=True)
class SkillActivationEvidenceV1:
    """Immutable evidence required before a bundle can become ACTIVE."""

    activation_id: str
    bundle_id: str
    bundle_digest: str
    bundle_version: str
    lab_report_ref: str
    review_ref: str
    sbom_digest: str
    signature_audit_ref: str
    sandbox_receipt_ref: str
    rollback_ref: str
    maturity_level: int
    previous_bundle_id: str | None = None
    new_capability: bool = True
    reason_code: str = "SKILL_ACTIVATION_APPROVED"
    evidence_digest: str = ""

    def __post_init__(self) -> None:
        activation_id = _text(self.activation_id, "activation_id", maximum=255)
        if _ID_RE.fullmatch(activation_id) is None:
            raise SkillLifecycleError("SKILL_LIFECYCLE_ACTIVATION_ID_INVALID")
        object.__setattr__(self, "activation_id", activation_id)
        for name in ("bundle_id", "bundle_version"):
            object.__setattr__(self, name, _text(getattr(self, name), name, maximum=255))
        for name in ("bundle_digest", "sbom_digest"):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        for name in (
            "lab_report_ref",
            "review_ref",
            "signature_audit_ref",
            "sandbox_receipt_ref",
            "rollback_ref",
        ):
            object.__setattr__(self, name, _ref(getattr(self, name), name))
        if self.previous_bundle_id is not None:
            previous = _text(self.previous_bundle_id, "previous_bundle_id", maximum=255)
            if _ID_RE.fullmatch(previous) is None:
                raise SkillLifecycleError("SKILL_LIFECYCLE_PREVIOUS_BUNDLE_ID_INVALID")
            object.__setattr__(self, "previous_bundle_id", previous)
        if isinstance(self.maturity_level, bool) or not isinstance(self.maturity_level, int):
            raise SkillLifecycleError("SKILL_LIFECYCLE_MATURITY_INVALID")
        if not 0 <= self.maturity_level <= 5:
            raise SkillLifecycleError("SKILL_LIFECYCLE_MATURITY_INVALID")
        if not isinstance(self.new_capability, bool):
            raise SkillLifecycleError("SKILL_LIFECYCLE_NEW_CAPABILITY_INVALID")
        if self.new_capability and self.maturity_level > 2:
            raise SkillLifecycleError("SKILL_LIFECYCLE_NEW_CAPABILITY_MATURITY_TOO_HIGH")
        reason = str(self.reason_code or "").strip().upper()
        if _REASON_RE.fullmatch(reason) is None:
            raise SkillLifecycleError("SKILL_LIFECYCLE_REASON_CODE_INVALID")
        object.__setattr__(self, "reason_code", reason)
        computed = _sha256(_canonical(self.to_dict(include_digest=False)))
        supplied = str(self.evidence_digest or "").strip()
        if supplied and supplied != computed:
            raise SkillLifecycleError("SKILL_LIFECYCLE_EVIDENCE_DIGEST_DRIFT")
        object.__setattr__(self, "evidence_digest", computed)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "natsume.skill-activation-evidence.v1",
            "activation_id": self.activation_id,
            "bundle_id": self.bundle_id,
            "bundle_digest": self.bundle_digest,
            "bundle_version": self.bundle_version,
            "lab_report_ref": self.lab_report_ref,
            "review_ref": self.review_ref,
            "sbom_digest": self.sbom_digest,
            "signature_audit_ref": self.signature_audit_ref,
            "sandbox_receipt_ref": self.sandbox_receipt_ref,
            "rollback_ref": self.rollback_ref,
            "previous_bundle_id": self.previous_bundle_id,
            "maturity_level": self.maturity_level,
            "new_capability": self.new_capability,
            "reason_code": self.reason_code,
        }
        if include_digest:
            result["evidence_digest"] = self.evidence_digest
        return result


@dataclass(frozen=True, slots=True)
class SkillLifecycleReceiptV1:
    """Append-only receipt for an activation, degradation or rollback event."""

    receipt_id: str
    activation_id: str
    event: str
    bundle_id: str
    evidence_digest: str
    operation_id: str | None
    previous_bundle_id: str | None
    created_at_ms: int
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        for name in ("receipt_id", "activation_id", "bundle_id"):
            normalized = _text(getattr(self, name), name, maximum=255)
            if _ID_RE.fullmatch(normalized) is None:
                raise SkillLifecycleError(f"SKILL_LIFECYCLE_{name.upper()}_INVALID")
            object.__setattr__(self, name, normalized)
        event = str(self.event or "").strip().upper()
        if event not in {"REQUESTED", "ACTIVATED", "DEGRADED", "ROLLED_BACK", "REJECTED"}:
            raise SkillLifecycleError("SKILL_LIFECYCLE_EVENT_INVALID")
        object.__setattr__(self, "event", event)
        object.__setattr__(self, "evidence_digest", _digest(self.evidence_digest, "evidence_digest"))
        if self.operation_id is not None:
            object.__setattr__(self, "operation_id", _text(self.operation_id, "operation_id", maximum=255))
        if self.previous_bundle_id is not None:
            previous = _text(self.previous_bundle_id, "previous_bundle_id", maximum=255)
            if _ID_RE.fullmatch(previous) is None:
                raise SkillLifecycleError("SKILL_LIFECYCLE_PREVIOUS_BUNDLE_ID_INVALID")
            object.__setattr__(self, "previous_bundle_id", previous)
        if isinstance(self.created_at_ms, bool) or not isinstance(self.created_at_ms, int) or self.created_at_ms < 0:
            raise SkillLifecycleError("SKILL_LIFECYCLE_TIMESTAMP_INVALID")
        computed = _sha256(_canonical(self.to_dict(include_digest=False)))
        supplied = str(self.receipt_digest or "").strip()
        if supplied and supplied != computed:
            raise SkillLifecycleError("SKILL_LIFECYCLE_RECEIPT_DIGEST_DRIFT")
        object.__setattr__(self, "receipt_digest", computed)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "natsume.skill-lifecycle-receipt.v1",
            "receipt_id": self.receipt_id,
            "activation_id": self.activation_id,
            "event": self.event,
            "bundle_id": self.bundle_id,
            "evidence_digest": self.evidence_digest,
            "operation_id": self.operation_id,
            "previous_bundle_id": self.previous_bundle_id,
            "created_at_ms": self.created_at_ms,
        }
        if include_digest:
            result["receipt_digest"] = self.receipt_digest
        return result


class SkillSupplyChainPort(Protocol):
    def get_bundle(self, *, bundle_id: str) -> Mapping[str, Any] | None: ...

    def get_active_bundle(self, *, bundle_kind: BundleKind | str, bundle_name: str) -> Mapping[str, Any] | None: ...

    def activate_bundle(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def reject_bundle(self, **kwargs: Any) -> Mapping[str, Any]: ...


class SkillLifecycleReceiptStore(Protocol):
    def append_evidence(self, evidence: SkillActivationEvidenceV1) -> str: ...

    def append_receipt(self, receipt: SkillLifecycleReceiptV1) -> str: ...

    def list_receipts(self, *, bundle_id: str | None = None, limit: int = 100) -> list[Mapping[str, Any]]: ...


class ContentAddressedSkillVersionRegistry:
    """Read-only runtime view over approved active supply-chain records."""

    def __init__(self, *, store: SkillSupplyChainPort) -> None:
        self._store = store

    def resolve_active(self, *, bundle_kind: BundleKind | str, bundle_name: str) -> Mapping[str, Any] | None:
        result = self._store.get_active_bundle(bundle_kind=bundle_kind, bundle_name=bundle_name)
        if result is None or str(result.get("state", "")) != BundleState.ACTIVE.value:
            return None
        if not _DIGEST_RE.fullmatch(str(result.get("bundle_digest", ""))):
            return None
        return dict(result)

    def resolve_content_addressed(self, *, bundle_id: str) -> Mapping[str, Any] | None:
        result = self._store.get_bundle(bundle_id=bundle_id)
        if result is None or str(result.get("state", "")) != BundleState.ACTIVE.value:
            return None
        if not _DIGEST_RE.fullmatch(str(result.get("bundle_digest", ""))):
            return None
        return dict(result)


class SkillLifecycleService:
    """Evidence-gated activation/degradation/rollback orchestration."""

    def __init__(
        self,
        *,
        supply_chain: SkillSupplyChainPort,
        receipts: SkillLifecycleReceiptStore,
        clock: Any,
    ) -> None:
        if not callable(clock):
            raise SkillLifecycleError("SKILL_LIFECYCLE_CLOCK_INVALID")
        self._supply_chain = supply_chain
        self._receipts = receipts
        self._clock = clock

    def activate(
        self,
        evidence: SkillActivationEvidenceV1,
        *,
        expected_activation_generation: int,
        fencing_token: int,
        idempotency_key: str,
    ) -> SkillLifecycleReceiptV1:
        if not isinstance(evidence, SkillActivationEvidenceV1):
            raise SkillLifecycleError("SKILL_LIFECYCLE_EVIDENCE_INVALID")
        bundle = self._require_staged(evidence)
        self._assert_bundle_evidence(bundle, evidence)
        self._receipts.append_evidence(evidence)
        requested = self._receipt(evidence, event="REQUESTED", operation_id=None)
        self._receipts.append_receipt(requested)
        try:
            result = self._supply_chain.activate_bundle(
                bundle_id=evidence.bundle_id,
                expected_status_version=int(bundle["status_version"]),
                expected_activation_generation=int(expected_activation_generation),
                fencing_token=int(fencing_token),
                audit_ref=evidence.sandbox_receipt_ref,
                idempotency_key=idempotency_key,
                reason_code=evidence.reason_code,
            )
        except SkillSupplyChainStoreError as exc:
            rejected = self._receipt(evidence, event="REJECTED", operation_id=None)
            self._receipts.append_receipt(rejected)
            raise SkillLifecycleError(exc.reason_code) from exc
        operation = result.get("operation") if isinstance(result, Mapping) else None
        receipt = self._receipt(
            evidence,
            event="ACTIVATED",
            operation_id=str(operation.get("operation_id")) if isinstance(operation, Mapping) else None,
        )
        self._receipts.append_receipt(receipt)
        return receipt

    def degrade(
        self,
        evidence: SkillActivationEvidenceV1,
        *,
        idempotency_key: str,
        reason_code: str = "SKILL_DEGRADED_BY_GUARDIAN",
    ) -> SkillLifecycleReceiptV1:
        if not isinstance(evidence, SkillActivationEvidenceV1):
            raise SkillLifecycleError("SKILL_LIFECYCLE_EVIDENCE_INVALID")
        bundle = self._require_active(evidence)
        self._assert_bundle_evidence(bundle, evidence)
        self._receipts.append_evidence(evidence)
        try:
            result = self._supply_chain.reject_bundle(
                bundle_id=evidence.bundle_id,
                expected_status_version=int(bundle["status_version"]),
                audit_ref=evidence.sandbox_receipt_ref,
                idempotency_key=idempotency_key,
                reason_code=reason_code,
            )
        except SkillSupplyChainStoreError as exc:
            raise SkillLifecycleError(exc.reason_code) from exc
        operation = result.get("operation") if isinstance(result, Mapping) else None
        receipt = self._receipt(
            evidence,
            event="DEGRADED",
            operation_id=str(operation.get("operation_id")) if isinstance(operation, Mapping) else None,
        )
        self._receipts.append_receipt(receipt)
        return receipt

    def rollback(
        self,
        evidence: SkillActivationEvidenceV1,
        *,
        expected_activation_generation: int,
        fencing_token: int,
        idempotency_key: str,
    ) -> SkillLifecycleReceiptV1:
        if evidence.previous_bundle_id is None:
            raise SkillLifecycleError("SKILL_LIFECYCLE_ROLLBACK_TARGET_REQUIRED")
        target = self._supply_chain.get_bundle(bundle_id=evidence.previous_bundle_id)
        if target is None or str(target.get("state")) != BundleState.STAGED.value:
            raise SkillLifecycleError("SKILL_LIFECYCLE_ROLLBACK_TARGET_NOT_STAGED")
        if evidence.bundle_id == evidence.previous_bundle_id:
            raise SkillLifecycleError("SKILL_LIFECYCLE_ROLLBACK_TARGET_INVALID")
        # The evidence is for the target version.  This makes rollback a normal
        # fenced activation, while the receipt records the version being
        # replaced in ``previous_bundle_id``.
        target_evidence = SkillActivationEvidenceV1(
            activation_id=f"skill-activation://rollback/{evidence.evidence_digest.split(':', 1)[1]}",
            bundle_id=evidence.previous_bundle_id,
            bundle_digest=str(target.get("bundle_digest")),
            bundle_version=str(target.get("bundle_version")),
            lab_report_ref=evidence.lab_report_ref,
            review_ref=evidence.review_ref,
            sbom_digest=evidence.sbom_digest,
            signature_audit_ref=evidence.signature_audit_ref,
            sandbox_receipt_ref=evidence.sandbox_receipt_ref,
            rollback_ref=evidence.rollback_ref,
            previous_bundle_id=evidence.bundle_id,
            maturity_level=evidence.maturity_level,
            new_capability=False,
            reason_code="SKILL_ROLLBACK_ACTIVATED",
        )
        self._receipts.append_evidence(target_evidence)
        receipt = self.activate(
            target_evidence,
            expected_activation_generation=expected_activation_generation,
            fencing_token=fencing_token,
            idempotency_key=idempotency_key,
        )
        rolled_back = SkillLifecycleReceiptV1(
            receipt_id=f"skill-lifecycle-receipt://rollback/{receipt.receipt_digest.split(':', 1)[1]}",
            activation_id=receipt.activation_id,
            event="ROLLED_BACK",
            bundle_id=receipt.bundle_id,
            evidence_digest=receipt.evidence_digest,
            operation_id=receipt.operation_id,
            previous_bundle_id=evidence.bundle_id,
            created_at_ms=receipt.created_at_ms,
        )
        self._receipts.append_receipt(rolled_back)
        return rolled_back

    def _require_staged(self, evidence: SkillActivationEvidenceV1) -> Mapping[str, Any]:
        bundle = self._supply_chain.get_bundle(bundle_id=evidence.bundle_id)
        if bundle is None or str(bundle.get("state")) != BundleState.STAGED.value:
            raise SkillLifecycleError("SKILL_LIFECYCLE_BUNDLE_NOT_STAGED")
        return bundle

    def _require_active(self, evidence: SkillActivationEvidenceV1) -> Mapping[str, Any]:
        bundle = self._supply_chain.get_bundle(bundle_id=evidence.bundle_id)
        if bundle is None or str(bundle.get("state")) != BundleState.ACTIVE.value:
            raise SkillLifecycleError("SKILL_LIFECYCLE_BUNDLE_NOT_ACTIVE")
        return bundle

    @staticmethod
    def _assert_bundle_evidence(bundle: Mapping[str, Any], evidence: SkillActivationEvidenceV1) -> None:
        if (
            str(bundle.get("bundle_digest")) != evidence.bundle_digest
            or str(bundle.get("bundle_version")) != evidence.bundle_version
            or str(bundle.get("sbom_digest")) != evidence.sbom_digest
            or str(bundle.get("signature_verification_audit_ref")) != evidence.signature_audit_ref
        ):
            raise SkillLifecycleError("SKILL_LIFECYCLE_EVIDENCE_BUNDLE_MISMATCH")

    def _receipt(
        self,
        evidence: SkillActivationEvidenceV1,
        *,
        event: str,
        operation_id: str | None,
    ) -> SkillLifecycleReceiptV1:
        digest_hex = hashlib.sha256(
            f"{evidence.activation_id}|{evidence.bundle_id}|{event}|{evidence.evidence_digest}|{operation_id or ''}".encode(
                "utf-8"
            )
        ).hexdigest()
        return SkillLifecycleReceiptV1(
            receipt_id=f"skill-lifecycle-receipt://{event.lower()}/{digest_hex}",
            activation_id=evidence.activation_id,
            event=event,
            bundle_id=evidence.bundle_id,
            evidence_digest=evidence.evidence_digest,
            operation_id=operation_id,
            previous_bundle_id=evidence.previous_bundle_id,
            created_at_ms=int(self._clock()),
        )


__all__ = [
    "ContentAddressedSkillVersionRegistry",
    "SkillActivationEvidenceV1",
    "SkillLifecycleError",
    "SkillLifecycleReceiptV1",
    "SkillLifecycleReceiptStore",
    "SkillLifecycleService",
    "SkillSupplyChainPort",
]
