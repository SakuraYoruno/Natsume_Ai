from __future__ import annotations

import hashlib
from pathlib import Path
from types import MappingProxyType

import pytest

from host_brain.skills.supply_chain import (
    BundleDisposition,
    BundleVerificationDecision,
    SupplyChainActivationMode,
)
from host_brain.skills.supply_chain_quarantine import QuarantineDecision
from host_brain.skills.supply_chain_service import SkillSupplyChainService
from host_brain.persistence.supply_chain_repository import SkillSupplyChainStoreError


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _verification(
    *,
    mode: SupplyChainActivationMode,
    bundle_kind: str = "SKILL",
    publisher_id: str = "publisher://trusted/test",
) -> BundleVerificationDecision:
    staged = mode is SupplyChainActivationMode.STAGED
    bundle_digest = _digest("signed-bundle")
    return BundleVerificationDecision(
        allowed=staged,
        verified=True,
        must_quarantine=True,
        activation_eligible=staged,
        disposition=(
            BundleDisposition.STAGED_ELIGIBLE
            if staged
            else BundleDisposition.QUARANTINE_VERIFIED
        ),
        reason_code=(
            "SUPPLY_CHAIN_STAGED_ELIGIBLE"
            if staged
            else "SUPPLY_CHAIN_QUARANTINE_VERIFIED"
        ),
        bundle_name="external-echo",
        bundle_version="1.0.0",
        bundle_digest=bundle_digest,
        audit=MappingProxyType(
            {
                "event": "skill_supply_chain_verification",
                "reason_code": "SUPPLY_CHAIN_STAGED_ELIGIBLE",
                "bundle_name": "external-echo",
                "bundle_version": "1.0.0",
                "bundle_digest": bundle_digest,
                "bundle_kind": bundle_kind,
                "publisher_id": publisher_id,
                "manifest_digest": _digest("manifest"),
                "signing_key_id": "release-2026-07",
                "signature_algorithm": "hmac-sha256",
                "sbom_digest": _digest("sbom"),
                "sandbox_profile_id": "python-restricted-v1",
                "sandbox_profile_digest": _digest("sandbox-envelope"),
                "sandbox": {
                    "profile": "python-restricted-v1",
                    "secret_ref_count": 0,
                    "network_origin_count": 0,
                    "process": "deny",
                    "workspace": "none",
                },
            }
        ),
    )


def _rejected_verification(reason: str) -> BundleVerificationDecision:
    return BundleVerificationDecision(
        allowed=False,
        verified=False,
        must_quarantine=True,
        activation_eligible=False,
        disposition=BundleDisposition.REJECTED,
        reason_code=reason,
        bundle_name=None,
        bundle_version=None,
        bundle_digest=None,
        audit=MappingProxyType({"reason_code": reason}),
    )


def _quarantine(
    verification: BundleVerificationDecision,
    *,
    accepted: bool = True,
    idempotent: bool = False,
    reason_code: str | None = None,
) -> QuarantineDecision:
    return QuarantineDecision(
        quarantined=accepted,
        idempotent=idempotent if accepted else False,
        activation_eligible=verification.activation_eligible if accepted else False,
        reason_code=reason_code
        or ("SUPPLY_CHAIN_QUARANTINED" if accepted else "SUPPLY_CHAIN_QUARANTINE_IO_FAILED"),
        bundle_name=verification.bundle_name,
        bundle_version=verification.bundle_version,
        bundle_digest=verification.bundle_digest,
        quarantine_path="C:/quarantine/sha256-safe" if accepted else None,
        audit=MappingProxyType({"event": "skill_supply_chain_quarantine"}),
    )


class FakeVerifier:
    def __init__(
        self,
        *,
        mode: SupplyChainActivationMode,
        decision: BundleVerificationDecision,
        valid: bool = True,
    ) -> None:
        self.mode = mode
        self.configuration_valid = valid
        self.decision = decision
        self.calls: list[object] = []
        self.decisions: list[BundleVerificationDecision] = []

    def verify_bundle(self, source: object) -> BundleVerificationDecision:
        self.calls.append(source)
        return self.decisions.pop(0) if self.decisions else self.decision


class FakeQuarantine:
    def __init__(self, decision: QuarantineDecision) -> None:
        self.decision = decision
        self.calls: list[object] = []

    def quarantine_bundle(self, source: object) -> QuarantineDecision:
        self.calls.append(source)
        return self.decision


class FakeStore:
    def __init__(self) -> None:
        self.intake_calls: list[dict[str, object]] = []
        self.stage_calls: list[dict[str, object]] = []
        self.fail_intake: str | None = None
        self.fail_stage: str | None = None

    def intake_bundle(self, **kwargs: object) -> dict[str, object]:
        self.intake_calls.append(dict(kwargs))
        if self.fail_intake:
            raise SkillSupplyChainStoreError(self.fail_intake)
        acceptance = "NEW" if len(self.intake_calls) == 1 else "DUPLICATE_SAME"
        state = "QUARANTINED" if not self.stage_calls else "STAGED"
        return {
            "acceptance_status": acceptance,
            "bundle": {"state": state, "status_version": 0 if state == "QUARANTINED" else 1},
        }

    def stage_bundle(self, **kwargs: object) -> dict[str, object]:
        self.stage_calls.append(dict(kwargs))
        if self.fail_stage:
            raise SkillSupplyChainStoreError(self.fail_stage)
        return {
            "acceptance_status": "NEW" if len(self.stage_calls) == 1 else "DUPLICATE_SAME",
            "bundle": {"state": "STAGED", "status_version": 1},
        }


@pytest.mark.parametrize(
    ("mode", "valid", "reason"),
    [
        (
            SupplyChainActivationMode.BUILTINS_ONLY,
            True,
            "SUPPLY_CHAIN_EXTERNAL_BUNDLES_DISABLED",
        ),
        (
            SupplyChainActivationMode.BUILTINS_ONLY,
            False,
            "SUPPLY_CHAIN_ACTIVATION_MODE_INVALID",
        ),
    ],
)
def test_disabled_or_invalid_policy_returns_before_source_quarantine_or_store(
    mode: SupplyChainActivationMode,
    valid: bool,
    reason: str,
) -> None:
    verifier = FakeVerifier(
        mode=mode,
        decision=_rejected_verification(reason),
        valid=valid,
    )
    quarantine = FakeQuarantine(_quarantine(_rejected_verification(reason), accepted=False))
    store = FakeStore()
    source = object()

    result = SkillSupplyChainService(
        verifier=verifier, quarantine=quarantine, store=store
    ).intake_signed_bundle(
        source,
        kind="SKILL",
        publisher="publisher://trusted/test",
        idempotency="disabled-request",
    )

    assert result.reason_code == reason
    assert verifier.calls == []
    assert quarantine.calls == []
    assert store.intake_calls == []
    assert result.activation_performed is result.import_performed is False


def test_verification_failure_is_a_zero_quarantine_zero_store_mutation() -> None:
    rejected = _rejected_verification("SUPPLY_CHAIN_SIGNATURE_INVALID")
    verifier = FakeVerifier(mode=SupplyChainActivationMode.STAGED, decision=rejected)
    quarantine = FakeQuarantine(_quarantine(rejected, accepted=False))
    store = FakeStore()

    result = SkillSupplyChainService(
        verifier=verifier, quarantine=quarantine, store=store
    ).intake_signed_bundle(
        object(),
        kind="PROVIDER",
        publisher="publisher://trusted/test",
        idempotency="bad-signature",
    )

    assert result.reason_code == "SUPPLY_CHAIN_SIGNATURE_INVALID"
    assert len(verifier.calls) == 1
    assert quarantine.calls == []
    assert store.intake_calls == []


@pytest.mark.parametrize(
    ("kind", "publisher"),
    [
        ("PROVIDER", "publisher://trusted/test"),
        ("SKILL", "publisher://spoofed/admin"),
    ],
)
def test_caller_cannot_relabel_signed_kind_or_publisher(
    kind: str,
    publisher: str,
) -> None:
    verified = _verification(mode=SupplyChainActivationMode.STAGED)
    verifier = FakeVerifier(mode=SupplyChainActivationMode.STAGED, decision=verified)
    quarantine = FakeQuarantine(_quarantine(verified))
    store = FakeStore()

    result = SkillSupplyChainService(
        verifier=verifier,
        quarantine=quarantine,
        store=store,
    ).intake_signed_bundle(
        object(),
        kind=kind,
        publisher=publisher,
        idempotency="identity-relabel",
    )

    assert result.reason_code == "SUPPLY_CHAIN_SIGNED_IDENTITY_MISMATCH"
    assert quarantine.calls == []
    assert store.intake_calls == []
    assert store.stage_calls == []


def test_verifier_exception_is_a_stable_zero_mutation_rejection() -> None:
    verified = _verification(mode=SupplyChainActivationMode.STAGED)
    verifier = FakeVerifier(mode=SupplyChainActivationMode.STAGED, decision=verified)

    def explode(_: object) -> BundleVerificationDecision:
        raise RecursionError("untrusted decoder depth")

    verifier.verify_bundle = explode  # type: ignore[method-assign]
    quarantine = FakeQuarantine(_quarantine(verified))
    store = FakeStore()

    result = SkillSupplyChainService(
        verifier=verifier,
        quarantine=quarantine,
        store=store,
    ).intake_signed_bundle(
        object(),
        kind="SKILL",
        publisher="publisher://trusted/test",
        idempotency="verifier-exception",
    )

    assert result.reason_code == "SUPPLY_CHAIN_VERIFIER_FAILED"
    assert quarantine.calls == []
    assert store.intake_calls == []


def test_quarantine_mode_records_content_addressed_identity_but_never_stages() -> None:
    verified = _verification(mode=SupplyChainActivationMode.QUARANTINE)
    verifier = FakeVerifier(mode=SupplyChainActivationMode.QUARANTINE, decision=verified)
    quarantine = FakeQuarantine(_quarantine(verified))
    store = FakeStore()

    result = SkillSupplyChainService(
        verifier=verifier, quarantine=quarantine, store=store
    ).intake_signed_bundle(
        object(),
        kind="SKILL",
        publisher="publisher://trusted/test",
        idempotency="intake-one",
    )

    assert result.reason_code == "SUPPLY_CHAIN_INTAKE_QUARANTINED"
    assert result.stored is result.quarantined is result.quarantine_retained is True
    assert result.staged is False
    assert result.repository_state == "QUARANTINED"
    assert result.bundle_id == (
        "skill-supply-chain-bundle://skill/sha256/"
        + str(verified.bundle_digest).split(":", 1)[1]
    )
    assert len(store.intake_calls) == 1
    assert store.stage_calls == []
    intake = store.intake_calls[0]
    assert intake["signature_key_id"] == verified.audit["signing_key_id"]
    assert intake["signature_algorithm"] == verified.audit["signature_algorithm"]
    assert intake["sbom_digest"] == verified.audit["sbom_digest"]
    assert str(intake["quarantine_audit_ref"]).startswith(
        "skill-supply-chain-audit://quarantine/sha256/"
    )
    assert "quarantine_path" not in result.audit
    assert result.activation_performed is result.import_performed is False


def test_staged_mode_consumes_only_core_safe_metadata_and_stops_at_staged() -> None:
    verified = _verification(
        mode=SupplyChainActivationMode.STAGED,
        bundle_kind="PROVIDER",
    )
    verifier = FakeVerifier(mode=SupplyChainActivationMode.STAGED, decision=verified)
    quarantine = FakeQuarantine(_quarantine(verified))
    store = FakeStore()
    source = object()

    result = SkillSupplyChainService(
        verifier=verifier, quarantine=quarantine, store=store
    ).intake_signed_bundle(
        source,
        kind="PROVIDER",
        publisher="publisher://trusted/test",
        idempotency="stage-one",
    )

    assert verifier.calls == [source, "C:/quarantine/sha256-safe"]
    assert quarantine.calls == [source]
    assert result.reason_code == "SUPPLY_CHAIN_INTAKE_STAGED"
    assert result.repository_state == "STAGED"
    assert result.staged is True
    assert result.activation_performed is result.import_performed is False
    assert len(store.intake_calls) == len(store.stage_calls) == 1
    stage = store.stage_calls[0]
    assert stage["signature_key_id"] == verified.audit["signing_key_id"]
    assert stage["signature_algorithm"] == "hmac-sha256"
    assert stage["sbom_digest"] == verified.audit["sbom_digest"]
    assert stage["sandbox_profile_id"] == verified.audit["sandbox_profile_id"]
    assert stage["sandbox_profile_digest"] == verified.audit["sandbox_profile_digest"]
    assert str(stage["signature_verification_audit_ref"]).startswith(
        "skill-supply-chain-audit://verification/sha256/"
    )
    assert not hasattr(store, "activate_bundle")


def test_repeated_staged_intake_reuses_exact_content_and_audit_idempotency() -> None:
    verified = _verification(mode=SupplyChainActivationMode.STAGED)
    verifier = FakeVerifier(mode=SupplyChainActivationMode.STAGED, decision=verified)
    quarantine = FakeQuarantine(_quarantine(verified))
    store = FakeStore()
    service = SkillSupplyChainService(verifier=verifier, quarantine=quarantine, store=store)

    first = service.intake_signed_bundle(
        object(),
        kind="SKILL",
        publisher="publisher://trusted/test",
        idempotency="same-request",
    )
    quarantine.decision = _quarantine(verified, idempotent=True)
    second = service.intake_signed_bundle(
        object(),
        kind="SKILL",
        publisher="publisher://trusted/test",
        idempotency="same-request",
    )

    assert first.bundle_id == second.bundle_id
    assert first.reason_code == second.reason_code == "SUPPLY_CHAIN_INTAKE_STAGED"
    assert second.intake_acceptance_status == "DUPLICATE_SAME"
    assert second.stage_acceptance_status == "DUPLICATE_SAME"
    assert store.intake_calls[0] == store.intake_calls[1]
    assert store.stage_calls[0] == store.stage_calls[1]


def test_physical_quarantine_failure_never_reaches_repository() -> None:
    verified = _verification(mode=SupplyChainActivationMode.STAGED)
    verifier = FakeVerifier(mode=SupplyChainActivationMode.STAGED, decision=verified)
    quarantine = FakeQuarantine(
        _quarantine(
            verified,
            accepted=False,
            reason_code="SUPPLY_CHAIN_QUARANTINE_REVERIFY_MISMATCH",
        )
    )
    store = FakeStore()

    result = SkillSupplyChainService(
        verifier=verifier, quarantine=quarantine, store=store
    ).intake_signed_bundle(
        object(),
        kind="SKILL",
        publisher="publisher://trusted/test",
        idempotency="copy-failed",
    )

    assert result.reason_code == "SUPPLY_CHAIN_QUARANTINE_REVERIFY_MISMATCH"
    assert result.quarantined is False
    assert store.intake_calls == []


def test_repository_failure_preserves_the_verified_physical_quarantine(
    tmp_path: Path,
) -> None:
    verified = _verification(mode=SupplyChainActivationMode.STAGED)
    retained = tmp_path / "quarantine" / "sha256-safe" / "bundle-marker"
    retained.parent.mkdir(parents=True)
    retained.write_text("verified quarantine remains", encoding="utf-8")
    verifier = FakeVerifier(mode=SupplyChainActivationMode.STAGED, decision=verified)
    quarantine = FakeQuarantine(_quarantine(verified))
    store = FakeStore()
    store.fail_intake = "SKILL_SUPPLY_CHAIN_STORE_BUSY"

    result = SkillSupplyChainService(
        verifier=verifier, quarantine=quarantine, store=store
    ).intake_signed_bundle(
        object(),
        kind="SKILL",
        publisher="publisher://trusted/test",
        idempotency="store-busy",
    )

    assert result.reason_code == "SUPPLY_CHAIN_INTAKE_STORE_FAILED"
    assert result.quarantined is result.quarantine_retained is True
    assert result.stored is False
    assert result.audit["store_reason_code"] == "SKILL_SUPPLY_CHAIN_STORE_BUSY"
    assert retained.read_text(encoding="utf-8") == "verified quarantine remains"


def test_missing_core_success_metadata_fails_before_quarantine_or_store() -> None:
    verified = _verification(mode=SupplyChainActivationMode.STAGED)
    incomplete = BundleVerificationDecision(
        **{
            **verified.__dict__,
            "audit": MappingProxyType(
                {key: value for key, value in verified.audit.items() if key != "sbom_digest"}
            ),
        }
    )
    verifier = FakeVerifier(mode=SupplyChainActivationMode.STAGED, decision=incomplete)
    quarantine = FakeQuarantine(_quarantine(incomplete))
    store = FakeStore()

    result = SkillSupplyChainService(
        verifier=verifier, quarantine=quarantine, store=store
    ).intake_signed_bundle(
        object(),
        kind="SKILL",
        publisher="publisher://trusted/test",
        idempotency="missing-safe-metadata",
    )

    assert result.reason_code == "SUPPLY_CHAIN_VERIFICATION_METADATA_INVALID"
    assert quarantine.calls == []
    assert store.intake_calls == []


def test_manifest_evidence_switch_between_verify_and_quarantine_fails_closed() -> None:
    first = _verification(mode=SupplyChainActivationMode.STAGED)
    switched_audit = dict(first.audit)
    switched_audit["manifest_digest"] = _digest("different-signed-manifest")
    switched_audit["sandbox_profile_digest"] = _digest("different-sandbox")
    switched = BundleVerificationDecision(
        **{
            **first.__dict__,
            "audit": MappingProxyType(switched_audit),
        }
    )
    verifier = FakeVerifier(
        mode=SupplyChainActivationMode.STAGED,
        decision=first,
    )
    verifier.decisions = [first, switched]
    quarantine = FakeQuarantine(_quarantine(switched))
    store = FakeStore()

    result = SkillSupplyChainService(
        verifier=verifier,
        quarantine=quarantine,
        store=store,
    ).intake_signed_bundle(
        object(),
        kind="SKILL",
        publisher="publisher://trusted/test",
        idempotency="manifest-switch",
    )

    assert result.reason_code == "SUPPLY_CHAIN_QUARANTINE_EVIDENCE_MISMATCH"
    assert result.quarantined is result.quarantine_retained is True
    assert result.stored is result.staged is False
    assert store.intake_calls == []
    assert store.stage_calls == []
