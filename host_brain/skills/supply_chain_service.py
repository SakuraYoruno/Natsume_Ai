"""Composition service for verified bundle quarantine and repository intake.

This service never reads a manifest, imports bundle code, or activates a
bundle.  It consumes only the verifier's safe success metadata, requires the
physical quarantine boundary to agree with that decision, and then advances
the catalog-owned repository no further than ``STAGED``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from host_brain.skills.supply_chain import (
    BundleVerificationDecision,
    SupplyChainActivationMode,
)
from host_brain.skills.supply_chain_quarantine import QuarantineDecision
from host_brain.skills.supply_chain_store import (
    BundleKind,
    SkillSupplyChainStoreError,
)


_DIGEST_RE = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_PUBLISHER_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}\Z")


class _VerifierPort(Protocol):
    @property
    def mode(self) -> SupplyChainActivationMode: ...

    @property
    def configuration_valid(self) -> bool: ...

    def verify_bundle(self, bundle_dir: object) -> BundleVerificationDecision: ...


class _QuarantinePort(Protocol):
    def quarantine_bundle(self, source_dir: object) -> QuarantineDecision: ...


class _StorePort(Protocol):
    def intake_bundle(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def stage_bundle(self, **kwargs: Any) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class SignedBundleIntakeDecision:
    stored: bool
    quarantined: bool
    staged: bool
    activation_performed: bool
    import_performed: bool
    quarantine_retained: bool
    reason_code: str
    bundle_id: str | None
    bundle_kind: str | None
    bundle_name: str | None
    bundle_version: str | None
    bundle_digest: str | None
    repository_state: str | None
    intake_acceptance_status: str | None
    stage_acceptance_status: str | None
    audit: Mapping[str, Any]


class SkillSupplyChainService:
    """Verify, physically quarantine, and at most stage a signed bundle."""

    def __init__(
        self,
        *,
        verifier: _VerifierPort,
        quarantine: _QuarantinePort,
        store: _StorePort,
    ) -> None:
        self._verifier = verifier
        self._quarantine = quarantine
        self._store = store

    def intake_signed_bundle(
        self,
        source: object,
        *,
        kind: BundleKind | str,
        publisher: str,
        idempotency: str,
    ) -> SignedBundleIntakeDecision:
        normalized_kind = _bundle_kind(kind)
        normalized_publisher = _safe_text(publisher, maximum=255)
        normalized_idempotency = _safe_text(idempotency, maximum=2048)
        if (
            normalized_kind is None
            or normalized_publisher is None
            or normalized_idempotency is None
        ):
            return self._decision(
                reason_code="SUPPLY_CHAIN_INTAKE_INPUT_INVALID",
                bundle_kind=normalized_kind,
            )

        if not self._verifier.configuration_valid:
            return self._decision(
                reason_code="SUPPLY_CHAIN_ACTIVATION_MODE_INVALID",
                bundle_kind=normalized_kind,
            )
        if self._verifier.mode is SupplyChainActivationMode.BUILTINS_ONLY:
            return self._decision(
                reason_code="SUPPLY_CHAIN_EXTERNAL_BUNDLES_DISABLED",
                bundle_kind=normalized_kind,
            )

        try:
            verification = self._verifier.verify_bundle(source)
        except Exception:
            return self._decision(
                reason_code="SUPPLY_CHAIN_VERIFIER_FAILED",
                bundle_kind=normalized_kind,
            )
        if not _verification_succeeded(verification):
            return self._decision(
                reason_code=_stable_reason(
                    verification.reason_code,
                    fallback="SUPPLY_CHAIN_VERIFICATION_FAILED",
                ),
                bundle_kind=normalized_kind,
                bundle_name=verification.bundle_name,
                bundle_version=verification.bundle_version,
                bundle_digest=verification.bundle_digest,
                audit={"verification_reason_code": verification.reason_code},
            )

        initial_metadata = _success_metadata(verification)
        if initial_metadata is None:
            return self._decision(
                reason_code="SUPPLY_CHAIN_VERIFICATION_METADATA_INVALID",
                bundle_kind=normalized_kind,
                bundle_name=verification.bundle_name,
                bundle_version=verification.bundle_version,
                bundle_digest=verification.bundle_digest,
            )

        if (
            normalized_kind != initial_metadata["bundle_kind"]
            or normalized_publisher != initial_metadata["publisher_id"]
        ):
            return self._decision(
                reason_code="SUPPLY_CHAIN_SIGNED_IDENTITY_MISMATCH",
                bundle_kind=initial_metadata["bundle_kind"],
                bundle_name=verification.bundle_name,
                bundle_version=verification.bundle_version,
                bundle_digest=verification.bundle_digest,
            )
        # From this point onward, repository identity is sourced exclusively
        # from the signed, signer-authorized verification evidence.
        normalized_kind = initial_metadata["bundle_kind"]
        normalized_publisher = initial_metadata["publisher_id"]

        try:
            quarantine = self._quarantine.quarantine_bundle(source)
        except Exception:
            return self._decision(
                reason_code="SUPPLY_CHAIN_QUARANTINE_FAILED",
                bundle_kind=normalized_kind,
                bundle_name=verification.bundle_name,
                bundle_version=verification.bundle_version,
                bundle_digest=verification.bundle_digest,
            )
        if not quarantine.quarantined:
            return self._decision(
                reason_code=_stable_reason(
                    quarantine.reason_code,
                    fallback="SUPPLY_CHAIN_QUARANTINE_FAILED",
                ),
                bundle_kind=normalized_kind,
                bundle_name=verification.bundle_name,
                bundle_version=verification.bundle_version,
                bundle_digest=verification.bundle_digest,
                audit={"quarantine_reason_code": quarantine.reason_code},
            )
        if not quarantine.quarantine_path:
            return self._decision(
                quarantined=True,
                quarantine_retained=True,
                reason_code="SUPPLY_CHAIN_QUARANTINE_DECISION_MISMATCH",
                bundle_kind=normalized_kind,
                bundle_name=verification.bundle_name,
                bundle_version=verification.bundle_version,
                bundle_digest=verification.bundle_digest,
            )
        # The quarantine adapter independently re-verifies while copying.  Use
        # the copied immutable bytes as the only repository evidence, and also
        # require the complete signed evidence to match the pre-copy decision.
        # Payload bundle_digest alone is insufficient because signing/sandbox
        # metadata lives in the manifest rather than the payload inventory.
        try:
            quarantined_verification = self._verifier.verify_bundle(
                quarantine.quarantine_path
            )
        except Exception:
            return self._decision(
                quarantined=True,
                quarantine_retained=True,
                reason_code="SUPPLY_CHAIN_VERIFIER_FAILED",
                bundle_kind=normalized_kind,
                bundle_name=verification.bundle_name,
                bundle_version=verification.bundle_version,
                bundle_digest=verification.bundle_digest,
            )
        quarantined_metadata = _success_metadata(quarantined_verification)
        if (
            quarantined_metadata is None
            or not _same_verified_evidence(
                verification,
                initial_metadata,
                quarantined_verification,
                quarantined_metadata,
            )
            or not _same_quarantined_identity(
                quarantined_verification,
                quarantine,
            )
        ):
            return self._decision(
                quarantined=True,
                quarantine_retained=True,
                reason_code="SUPPLY_CHAIN_QUARANTINE_EVIDENCE_MISMATCH",
                bundle_kind=normalized_kind,
                bundle_name=verification.bundle_name,
                bundle_version=verification.bundle_version,
                bundle_digest=verification.bundle_digest,
            )

        verification = quarantined_verification
        metadata = quarantined_metadata

        bundle_digest = str(verification.bundle_digest)
        bundle_id = _content_addressed_bundle_id(normalized_kind, bundle_digest)
        quarantine_audit_ref = _safe_audit_ref(
            "quarantine",
            {
                "bundle_id": bundle_id,
                "bundle_digest": bundle_digest,
                "bundle_name": verification.bundle_name,
                "bundle_version": verification.bundle_version,
            },
        )
        verification_audit_ref = _safe_audit_ref(
            "verification",
            {
                "bundle_id": bundle_id,
                "bundle_digest": bundle_digest,
                **metadata,
            },
        )
        stage_audit_ref = _safe_audit_ref(
            "stage",
            {
                "bundle_id": bundle_id,
                "verification_audit_ref": verification_audit_ref,
                "quarantine_audit_ref": quarantine_audit_ref,
            },
        )
        safe_audit = {
            "verification_audit_ref": verification_audit_ref,
            "quarantine_audit_ref": quarantine_audit_ref,
            "stage_audit_ref": stage_audit_ref,
        }

        try:
            intake = self._store.intake_bundle(
                bundle_id=bundle_id,
                bundle_kind=normalized_kind,
                bundle_name=str(verification.bundle_name),
                bundle_version=str(verification.bundle_version),
                publisher_id=normalized_publisher,
                bundle_digest=bundle_digest,
                quarantine_audit_ref=quarantine_audit_ref,
                idempotency_key=normalized_idempotency,
                reason_code="BUNDLE_INTAKE_QUARANTINED",
                signature_key_id=metadata["signing_key_id"],
                signature_algorithm=metadata["signature_algorithm"],
                sbom_digest=metadata["sbom_digest"],
            )
        except SkillSupplyChainStoreError as exc:
            return self._decision(
                quarantined=True,
                quarantine_retained=True,
                reason_code="SUPPLY_CHAIN_INTAKE_STORE_FAILED",
                bundle_id=bundle_id,
                bundle_kind=normalized_kind,
                bundle_name=verification.bundle_name,
                bundle_version=verification.bundle_version,
                bundle_digest=bundle_digest,
                audit={**safe_audit, "store_reason_code": exc.reason_code},
            )
        except Exception:
            return self._decision(
                quarantined=True,
                quarantine_retained=True,
                reason_code="SUPPLY_CHAIN_INTAKE_STORE_FAILED",
                bundle_id=bundle_id,
                bundle_kind=normalized_kind,
                bundle_name=verification.bundle_name,
                bundle_version=verification.bundle_version,
                bundle_digest=bundle_digest,
                audit=safe_audit,
            )

        intake_bundle = _result_bundle(intake)
        intake_state = _safe_state(intake_bundle.get("state"))
        intake_acceptance = _safe_acceptance(intake.get("acceptance_status"))
        if intake_state is None or intake_acceptance is None:
            return self._decision(
                stored=True,
                quarantined=True,
                quarantine_retained=True,
                reason_code="SUPPLY_CHAIN_STORE_RESULT_INVALID",
                bundle_id=bundle_id,
                bundle_kind=normalized_kind,
                bundle_name=verification.bundle_name,
                bundle_version=verification.bundle_version,
                bundle_digest=bundle_digest,
                audit=safe_audit,
            )

        if self._verifier.mode is not SupplyChainActivationMode.STAGED:
            return self._decision(
                stored=True,
                quarantined=True,
                quarantine_retained=True,
                staged=intake_state == "STAGED",
                reason_code="SUPPLY_CHAIN_INTAKE_QUARANTINED",
                bundle_id=bundle_id,
                bundle_kind=normalized_kind,
                bundle_name=verification.bundle_name,
                bundle_version=verification.bundle_version,
                bundle_digest=bundle_digest,
                repository_state=intake_state,
                intake_acceptance_status=intake_acceptance,
                audit=safe_audit,
            )

        if not verification.activation_eligible or not quarantine.activation_eligible:
            return self._decision(
                stored=True,
                quarantined=True,
                quarantine_retained=True,
                reason_code="SUPPLY_CHAIN_STAGE_ELIGIBILITY_MISMATCH",
                bundle_id=bundle_id,
                bundle_kind=normalized_kind,
                bundle_name=verification.bundle_name,
                bundle_version=verification.bundle_version,
                bundle_digest=bundle_digest,
                repository_state=intake_state,
                intake_acceptance_status=intake_acceptance,
                audit=safe_audit,
            )
        if intake_state not in {"QUARANTINED", "STAGED"}:
            return self._decision(
                stored=True,
                quarantined=True,
                quarantine_retained=True,
                staged=intake_state == "STAGED",
                reason_code="SUPPLY_CHAIN_INTAKE_STATE_CONFLICT",
                bundle_id=bundle_id,
                bundle_kind=normalized_kind,
                bundle_name=verification.bundle_name,
                bundle_version=verification.bundle_version,
                bundle_digest=bundle_digest,
                repository_state=intake_state,
                intake_acceptance_status=intake_acceptance,
                audit=safe_audit,
            )

        try:
            staged = self._store.stage_bundle(
                bundle_id=bundle_id,
                expected_status_version=0,
                signature_key_id=metadata["signing_key_id"],
                signature_algorithm=metadata["signature_algorithm"],
                signature_verification_audit_ref=verification_audit_ref,
                sbom_digest=metadata["sbom_digest"],
                sandbox_profile_id=metadata["sandbox_profile_id"],
                sandbox_profile_digest=metadata["sandbox_profile_digest"],
                audit_ref=stage_audit_ref,
                idempotency_key=normalized_idempotency,
                reason_code="BUNDLE_VERIFIED_AND_STAGED",
            )
        except SkillSupplyChainStoreError as exc:
            return self._decision(
                stored=True,
                quarantined=True,
                quarantine_retained=True,
                reason_code="SUPPLY_CHAIN_STAGE_STORE_FAILED",
                bundle_id=bundle_id,
                bundle_kind=normalized_kind,
                bundle_name=verification.bundle_name,
                bundle_version=verification.bundle_version,
                bundle_digest=bundle_digest,
                repository_state=intake_state,
                intake_acceptance_status=intake_acceptance,
                audit={**safe_audit, "store_reason_code": exc.reason_code},
            )
        except Exception:
            return self._decision(
                stored=True,
                quarantined=True,
                quarantine_retained=True,
                reason_code="SUPPLY_CHAIN_STAGE_STORE_FAILED",
                bundle_id=bundle_id,
                bundle_kind=normalized_kind,
                bundle_name=verification.bundle_name,
                bundle_version=verification.bundle_version,
                bundle_digest=bundle_digest,
                repository_state=intake_state,
                intake_acceptance_status=intake_acceptance,
                audit=safe_audit,
            )

        staged_bundle = _result_bundle(staged)
        staged_state = _safe_state(staged_bundle.get("state"))
        stage_acceptance = _safe_acceptance(staged.get("acceptance_status"))
        if staged_state != "STAGED" or stage_acceptance is None:
            return self._decision(
                stored=True,
                quarantined=True,
                quarantine_retained=True,
                reason_code="SUPPLY_CHAIN_STORE_RESULT_INVALID",
                bundle_id=bundle_id,
                bundle_kind=normalized_kind,
                bundle_name=verification.bundle_name,
                bundle_version=verification.bundle_version,
                bundle_digest=bundle_digest,
                repository_state=staged_state,
                intake_acceptance_status=intake_acceptance,
                audit=safe_audit,
            )
        return self._decision(
            stored=True,
            quarantined=True,
            quarantine_retained=True,
            staged=True,
            reason_code="SUPPLY_CHAIN_INTAKE_STAGED",
            bundle_id=bundle_id,
            bundle_kind=normalized_kind,
            bundle_name=verification.bundle_name,
            bundle_version=verification.bundle_version,
            bundle_digest=bundle_digest,
            repository_state=staged_state,
            intake_acceptance_status=intake_acceptance,
            stage_acceptance_status=stage_acceptance,
            audit=safe_audit,
        )

    @staticmethod
    def _decision(
        *,
        stored: bool = False,
        quarantined: bool = False,
        staged: bool = False,
        quarantine_retained: bool = False,
        reason_code: str,
        bundle_id: str | None = None,
        bundle_kind: str | None = None,
        bundle_name: str | None = None,
        bundle_version: str | None = None,
        bundle_digest: str | None = None,
        repository_state: str | None = None,
        intake_acceptance_status: str | None = None,
        stage_acceptance_status: str | None = None,
        audit: Mapping[str, Any] | None = None,
    ) -> SignedBundleIntakeDecision:
        return SignedBundleIntakeDecision(
            stored=stored,
            quarantined=quarantined,
            staged=staged,
            activation_performed=False,
            import_performed=False,
            quarantine_retained=quarantine_retained,
            reason_code=reason_code,
            bundle_id=bundle_id,
            bundle_kind=bundle_kind,
            bundle_name=bundle_name,
            bundle_version=bundle_version,
            bundle_digest=bundle_digest,
            repository_state=repository_state,
            intake_acceptance_status=intake_acceptance_status,
            stage_acceptance_status=stage_acceptance_status,
            audit=MappingProxyType(dict(audit or {})),
        )


def _verification_succeeded(decision: BundleVerificationDecision) -> bool:
    return bool(
        decision.verified
        and decision.must_quarantine
        and decision.bundle_name
        and decision.bundle_version
        and decision.bundle_digest
        and _DIGEST_RE.fullmatch(str(decision.bundle_digest))
    )


def _success_metadata(decision: BundleVerificationDecision) -> dict[str, str] | None:
    audit = decision.audit
    fields = (
        "bundle_kind",
        "publisher_id",
        "signing_key_id",
        "signature_algorithm",
        "sbom_digest",
        "sandbox_profile_id",
        "sandbox_profile_digest",
    )
    values: dict[str, str] = {}
    for field in fields:
        maximum = (
            255
            if field in {"publisher_id", "signing_key_id", "sandbox_profile_id"}
            else 128
        )
        value = _safe_text(audit.get(field), maximum=maximum)
        if value is None:
            return None
        values[field] = value
    if not _DIGEST_RE.fullmatch(values["sbom_digest"]):
        return None
    if not _DIGEST_RE.fullmatch(values["sandbox_profile_digest"]):
        return None
    if _bundle_kind(values["bundle_kind"]) != values["bundle_kind"]:
        return None
    if _PUBLISHER_ID_RE.fullmatch(values["publisher_id"]) is None:
        return None
    if audit.get("bundle_digest") != decision.bundle_digest:
        return None
    if audit.get("bundle_name") != decision.bundle_name:
        return None
    if audit.get("bundle_version") != decision.bundle_version:
        return None
    return values


def _same_quarantined_identity(
    verification: BundleVerificationDecision,
    quarantine: QuarantineDecision,
) -> bool:
    return bool(
        quarantine.bundle_name == verification.bundle_name
        and quarantine.bundle_version == verification.bundle_version
        and quarantine.bundle_digest == verification.bundle_digest
    )


def _same_verified_evidence(
    expected: BundleVerificationDecision,
    expected_metadata: Mapping[str, str],
    actual: BundleVerificationDecision,
    actual_metadata: Mapping[str, str],
) -> bool:
    return bool(
        _verification_succeeded(actual)
        and expected.bundle_name == actual.bundle_name
        and expected.bundle_version == actual.bundle_version
        and expected.bundle_digest == actual.bundle_digest
        and expected.audit.get("manifest_digest")
        == actual.audit.get("manifest_digest")
        and dict(expected_metadata) == dict(actual_metadata)
    )


def _bundle_kind(value: BundleKind | str) -> str | None:
    try:
        normalized = value.value if isinstance(value, BundleKind) else str(value).upper()
        return BundleKind(normalized).value
    except ValueError:
        return None


def _safe_text(value: Any, *, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(ord(char) < 32 or ord(char) == 127 for char in normalized)
    ):
        return None
    return normalized


def _stable_reason(value: Any, *, fallback: str) -> str:
    normalized = _safe_text(value, maximum=128)
    if normalized is None or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", normalized):
        return fallback
    return normalized


def _content_addressed_bundle_id(bundle_kind: str, bundle_digest: str) -> str:
    digest_hex = bundle_digest.split(":", 1)[1]
    return f"skill-supply-chain-bundle://{bundle_kind.lower()}/sha256/{digest_hex}"


def _safe_audit_ref(kind: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"skill-supply-chain-audit://{kind}/sha256/{digest}"


def _result_bundle(result: Mapping[str, Any]) -> Mapping[str, Any]:
    bundle = result.get("bundle")
    return bundle if isinstance(bundle, Mapping) else {}


def _safe_state(value: Any) -> str | None:
    normalized = str(value or "").upper()
    return normalized if normalized in {"QUARANTINED", "STAGED", "ACTIVE", "REJECTED"} else None


def _safe_acceptance(value: Any) -> str | None:
    normalized = str(value or "").upper()
    return normalized if normalized in {"NEW", "DUPLICATE_SAME"} else None


__all__ = ["SignedBundleIntakeDecision", "SkillSupplyChainService"]
