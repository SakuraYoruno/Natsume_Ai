"""NE-05 fail-closed Skill lifecycle application service.

The neutral evidence and receipt definitions live in
``host_brain.contracts.skill_lifecycle``.  They are re-exported here so legacy
callers retain the exact same class and error identities.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Protocol

from host_brain.contracts.skill_lifecycle import (
    SkillActivationEvidenceV1,
    SkillLifecycleError,
    SkillLifecycleReceiptV1,
    _DIGEST_RE,
)
from host_brain.persistence.supply_chain_repository import (
    BundleKind,
    BundleState,
    SkillSupplyChainStoreError,
)


class SkillSupplyChainPort(Protocol):
    def get_bundle(self, *, bundle_id: str) -> Mapping[str, Any] | None: ...

    def get_active_bundle(
        self, *, bundle_kind: BundleKind | str, bundle_name: str
    ) -> Mapping[str, Any] | None: ...

    def activate_bundle(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def reject_bundle(self, **kwargs: Any) -> Mapping[str, Any]: ...


class SkillLifecycleReceiptStore(Protocol):
    def append_evidence(self, evidence: SkillActivationEvidenceV1) -> str: ...

    def append_receipt(self, receipt: SkillLifecycleReceiptV1) -> str: ...

    def latest_activated_evidence(
        self, *, bundle_id: str
    ) -> SkillActivationEvidenceV1 | None: ...

    def list_receipts(
        self, *, bundle_id: str | None = None, limit: int = 100
    ) -> list[Mapping[str, Any]]: ...


class ContentAddressedSkillVersionRegistry:
    """Read-only runtime view over approved active supply-chain records."""

    def __init__(self, *, store: SkillSupplyChainPort) -> None:
        self._store = store

    def resolve_active(
        self, *, bundle_kind: BundleKind | str, bundle_name: str
    ) -> Mapping[str, Any] | None:
        result = self._store.get_active_bundle(
            bundle_kind=bundle_kind, bundle_name=bundle_name
        )
        if result is None or str(result.get("state", "")) != BundleState.ACTIVE.value:
            return None
        if not _DIGEST_RE.fullmatch(str(result.get("bundle_digest", ""))):
            return None
        return dict(result)

    def resolve_content_addressed(
        self, *, bundle_id: str
    ) -> Mapping[str, Any] | None:
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
            operation_id=(
                str(operation.get("operation_id"))
                if isinstance(operation, Mapping)
                else None
            ),
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
            operation_id=(
                str(operation.get("operation_id"))
                if isinstance(operation, Mapping)
                else None
            ),
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
        if not isinstance(evidence, SkillActivationEvidenceV1):
            raise SkillLifecycleError("SKILL_LIFECYCLE_EVIDENCE_INVALID")
        if evidence.previous_bundle_id is None:
            raise SkillLifecycleError("SKILL_LIFECYCLE_ROLLBACK_TARGET_REQUIRED")
        current = self._require_active(evidence)
        self._assert_bundle_evidence(current, evidence)
        target = self._supply_chain.get_bundle(
            bundle_id=evidence.previous_bundle_id
        )
        if target is None or str(target.get("state")) != BundleState.STAGED.value:
            raise SkillLifecycleError(
                "SKILL_LIFECYCLE_ROLLBACK_TARGET_NOT_STAGED"
            )
        if evidence.bundle_id == evidence.previous_bundle_id:
            raise SkillLifecycleError("SKILL_LIFECYCLE_ROLLBACK_TARGET_INVALID")
        if (
            str(target.get("bundle_kind") or "")
            != str(current.get("bundle_kind") or "")
            or str(target.get("bundle_name") or "")
            != str(current.get("bundle_name") or "")
        ):
            raise SkillLifecycleError("SKILL_LIFECYCLE_ROLLBACK_TARGET_SCOPE_INVALID")
        target_activation_evidence = self._receipts.latest_activated_evidence(
            bundle_id=evidence.previous_bundle_id
        )
        if target_activation_evidence is None:
            raise SkillLifecycleError("SKILL_LIFECYCLE_ROLLBACK_TARGET_EVIDENCE_MISSING")
        self._assert_bundle_evidence(target, target_activation_evidence)

        # A rollback is a new fenced activation of the old version.  It must
        # reuse that version's own Lab/Review/SBOM/signature/sandbox evidence;
        # copying evidence from the version being replaced would let a newer
        # approval silently authorize an older bundle.
        rollback_identity = hashlib.sha256(
            (
                f"{evidence.evidence_digest}|{target_activation_evidence.evidence_digest}|"
                f"{evidence.bundle_id}|{evidence.previous_bundle_id}"
            ).encode("utf-8")
        ).hexdigest()
        target_evidence = SkillActivationEvidenceV1(
            activation_id=(
                "skill-activation://rollback/"
                f"{rollback_identity}"
            ),
            bundle_id=evidence.previous_bundle_id,
            bundle_digest=str(target.get("bundle_digest")),
            bundle_version=str(target.get("bundle_version")),
            lab_report_ref=target_activation_evidence.lab_report_ref,
            review_ref=target_activation_evidence.review_ref,
            sbom_digest=target_activation_evidence.sbom_digest,
            signature_audit_ref=target_activation_evidence.signature_audit_ref,
            sandbox_receipt_ref=target_activation_evidence.sandbox_receipt_ref,
            rollback_ref=target_activation_evidence.rollback_ref,
            previous_bundle_id=evidence.bundle_id,
            maturity_level=target_activation_evidence.maturity_level,
            new_capability=target_activation_evidence.new_capability,
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
            receipt_id=(
                "skill-lifecycle-receipt://rollback/"
                f"{receipt.receipt_digest.split(':', 1)[1]}"
            ),
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

    def _require_staged(
        self, evidence: SkillActivationEvidenceV1
    ) -> Mapping[str, Any]:
        bundle = self._supply_chain.get_bundle(bundle_id=evidence.bundle_id)
        if bundle is None or str(bundle.get("state")) != BundleState.STAGED.value:
            raise SkillLifecycleError("SKILL_LIFECYCLE_BUNDLE_NOT_STAGED")
        return bundle

    def _require_active(
        self, evidence: SkillActivationEvidenceV1
    ) -> Mapping[str, Any]:
        bundle = self._supply_chain.get_bundle(bundle_id=evidence.bundle_id)
        if bundle is None or str(bundle.get("state")) != BundleState.ACTIVE.value:
            raise SkillLifecycleError("SKILL_LIFECYCLE_BUNDLE_NOT_ACTIVE")
        return bundle

    @staticmethod
    def _assert_bundle_evidence(
        bundle: Mapping[str, Any], evidence: SkillActivationEvidenceV1
    ) -> None:
        if (
            str(bundle.get("bundle_digest")) != evidence.bundle_digest
            or str(bundle.get("bundle_version")) != evidence.bundle_version
            or str(bundle.get("sbom_digest")) != evidence.sbom_digest
            or str(bundle.get("signature_verification_audit_ref"))
            != evidence.signature_audit_ref
        ):
            raise SkillLifecycleError(
                "SKILL_LIFECYCLE_EVIDENCE_BUNDLE_MISMATCH"
            )

    def _receipt(
        self,
        evidence: SkillActivationEvidenceV1,
        *,
        event: str,
        operation_id: str | None,
    ) -> SkillLifecycleReceiptV1:
        digest_hex = hashlib.sha256(
            (
                f"{evidence.activation_id}|{evidence.bundle_id}|{event}|"
                f"{evidence.evidence_digest}|{operation_id or ''}"
            ).encode("utf-8")
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
