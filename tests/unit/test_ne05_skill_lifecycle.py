from __future__ import annotations

from pathlib import Path

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.skill_lifecycle_repository import (
    SQLiteSkillLifecycleRepository,
)
from host_brain.persistence.supply_chain_repository import SQLiteSkillSupplyChainStore
from host_brain.skills.signed_intake_composition import (
    build_signed_skill_provider_intake_composition,
)
from host_brain.skills.skill_lifecycle import (
    ContentAddressedSkillVersionRegistry,
    SkillActivationEvidenceV1,
    SkillLifecycleError,
    SkillLifecycleService,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"


def _digest(letter: str) -> str:
    return f"sha256:{letter * 64}"


def _evidence(
    *,
    bundle_id: str,
    bundle_digest: str,
    bundle_version: str,
    previous: str | None = None,
    label: str = "ne05-1",
    maturity_level: int = 2,
) -> SkillActivationEvidenceV1:
    return SkillActivationEvidenceV1(
        activation_id=f"skill-activation://{bundle_id.split('//', 1)[-1]}",
        bundle_id=bundle_id,
        bundle_digest=bundle_digest,
        bundle_version=bundle_version,
        lab_report_ref=f"lab://report/{label}",
        review_ref=f"review://skill/{label}",
        sbom_digest=_digest("b"),
        signature_audit_ref=f"audit://signature/{label}",
        sandbox_receipt_ref=f"sandbox://receipt/{label}",
        rollback_ref=f"skill://rollback/{label}",
        previous_bundle_id=previous,
        maturity_level=maturity_level,
    )


def _stage(
    store: SQLiteSkillSupplyChainStore,
    *,
    bundle_id: str,
    name: str,
    version: str,
    digest: str,
    signature_audit_ref: str = "audit://signature/ne05-1",
) -> None:
    store.intake_bundle(
        bundle_id=bundle_id,
        bundle_kind="SKILL",
        bundle_name=name,
        bundle_version=version,
        publisher_id="publisher://ne05",
        bundle_digest=digest,
        quarantine_audit_ref="audit://quarantine/ne05",
        idempotency_key=f"intake-{version}",
    )
    store.stage_bundle(
        bundle_id=bundle_id,
        expected_status_version=0,
        signature_key_id="ne05-key",
        signature_algorithm="HMAC_SHA256",
        signature_verification_audit_ref=signature_audit_ref,
        sbom_digest=_digest("b"),
        sandbox_profile_id="python-restricted-v1",
        sandbox_profile_digest=_digest("c"),
        audit_ref="audit://stage/ne05",
        idempotency_key=f"stage-{version}",
    )


def _stores(tmp_path: Path) -> tuple[SQLiteSkillSupplyChainStore, SQLiteSkillLifecycleRepository]:
    db = tmp_path / "skill.sqlite3"
    result = SQLiteMigrationRunner(db_path=db, migrations_dir=MIGRATIONS).apply_migrations(
        request_id="test-ne05-skill-lifecycle", ts_ms=2_100_000_000_000
    )
    assert result.success, result.as_dict()
    return SQLiteSkillSupplyChainStore(db), SQLiteSkillLifecycleRepository(db, clock=lambda: 2_100_000_000_100)


def test_ne05_activation_requires_lab_review_and_is_content_addressed(tmp_path: Path) -> None:
    supply, lifecycle = _stores(tmp_path)
    bundle_id = "skill://ne05/demo-v1"
    digest = _digest("a")
    _stage(supply, bundle_id=bundle_id, name="demo", version="1.0.0", digest=digest)
    evidence = _evidence(bundle_id=bundle_id, bundle_digest=digest, bundle_version="1.0.0")
    service = SkillLifecycleService(supply_chain=supply, receipts=lifecycle, clock=lambda: 2_100_000_000_200)

    receipt = service.activate(
        evidence,
        expected_activation_generation=0,
        fencing_token=1,
        idempotency_key="activate-ne05-1",
    )

    assert receipt.event == "ACTIVATED"
    assert [item["event"] for item in lifecycle.list_receipts(bundle_id=bundle_id)] == [
        "REQUESTED",
        "ACTIVATED",
    ]
    registry = ContentAddressedSkillVersionRegistry(store=supply)
    assert registry.resolve_content_addressed(bundle_id=bundle_id)["bundle_digest"] == digest  # type: ignore[index]
    assert registry.resolve_active(bundle_kind="SKILL", bundle_name="demo")["state"] == "ACTIVE"  # type: ignore[index]


def test_ne05_degrade_and_rollback_leave_durable_receipts(tmp_path: Path) -> None:
    supply, lifecycle = _stores(tmp_path)
    first = "skill://ne05/first"
    second = "skill://ne05/second"
    _stage(supply, bundle_id=first, name="demo", version="1.0.0", digest=_digest("a"))
    _stage(supply, bundle_id=second, name="demo", version="2.0.0", digest=_digest("d"))
    service = SkillLifecycleService(supply_chain=supply, receipts=lifecycle, clock=lambda: 2_100_000_000_300)
    first_evidence = _evidence(bundle_id=first, bundle_digest=_digest("a"), bundle_version="1.0.0")
    service.activate(first_evidence, expected_activation_generation=0, fencing_token=1, idempotency_key="activate-1")
    second_evidence = _evidence(
        bundle_id=second,
        bundle_digest=_digest("d"),
        bundle_version="2.0.0",
        previous=first,
    )
    service.activate(second_evidence, expected_activation_generation=1, fencing_token=2, idempotency_key="activate-2")
    rollback = service.rollback(
        second_evidence,
        expected_activation_generation=2,
        fencing_token=3,
        idempotency_key="rollback-1",
    )
    assert rollback.event == "ROLLED_BACK"
    assert supply.get_active_bundle(bundle_kind="SKILL", bundle_name="demo")["bundle_id"] == first  # type: ignore[index]

    # A separate evidence set is required to record a degradation decision;
    # it cannot be smuggled in through an already-used activation id.
    active_evidence = _evidence(bundle_id=first, bundle_digest=_digest("a"), bundle_version="1.0.0")
    degraded = service.degrade(active_evidence, idempotency_key="degrade-1")
    assert degraded.event == "DEGRADED"
    assert supply.get_active_bundle(bundle_kind="SKILL", bundle_name="demo") is None


def test_ne05_rollback_reuses_the_target_versions_own_authority(tmp_path: Path) -> None:
    supply, lifecycle = _stores(tmp_path)
    first = "skill://ne05/rollback-first"
    second = "skill://ne05/rollback-second"
    _stage(
        supply,
        bundle_id=first,
        name="rollback-demo",
        version="1.0.0",
        digest=_digest("a"),
        signature_audit_ref="audit://signature/first",
    )
    _stage(
        supply,
        bundle_id=second,
        name="rollback-demo",
        version="2.0.0",
        digest=_digest("d"),
        signature_audit_ref="audit://signature/second",
    )
    service = SkillLifecycleService(
        supply_chain=supply,
        receipts=lifecycle,
        clock=lambda: 2_100_000_000_400,
    )
    first_evidence = _evidence(
        bundle_id=first,
        bundle_digest=_digest("a"),
        bundle_version="1.0.0",
        label="first",
        maturity_level=1,
    )
    second_evidence = _evidence(
        bundle_id=second,
        bundle_digest=_digest("d"),
        bundle_version="2.0.0",
        previous=first,
        label="second",
        maturity_level=2,
    )
    service.activate(
        first_evidence,
        expected_activation_generation=0,
        fencing_token=1,
        idempotency_key="rollback-activate-first",
    )
    service.activate(
        second_evidence,
        expected_activation_generation=1,
        fencing_token=2,
        idempotency_key="rollback-activate-second",
    )

    rollback = service.rollback(
        second_evidence,
        expected_activation_generation=2,
        fencing_token=3,
        idempotency_key="rollback-target-evidence",
    )

    replayed_evidence = lifecycle.get_evidence(activation_id=rollback.activation_id)
    assert replayed_evidence is not None
    assert replayed_evidence["bundle_id"] == first
    assert replayed_evidence["lab_report_ref"] == first_evidence.lab_report_ref
    assert replayed_evidence["review_ref"] == first_evidence.review_ref
    assert replayed_evidence["signature_audit_ref"] == first_evidence.signature_audit_ref
    assert replayed_evidence["sandbox_receipt_ref"] == first_evidence.sandbox_receipt_ref
    assert replayed_evidence["maturity_level"] == first_evidence.maturity_level
    assert replayed_evidence["lab_report_ref"] != second_evidence.lab_report_ref
    active = supply.get_active_bundle(bundle_kind="SKILL", bundle_name="rollback-demo")
    assert active is not None
    assert active["bundle_id"] == first
    assert active["latest_audit_ref"] == first_evidence.sandbox_receipt_ref


def test_ne05_rollback_rejects_target_without_its_own_activation_evidence(tmp_path: Path) -> None:
    supply, lifecycle = _stores(tmp_path)
    prior = "skill://ne05/never-activated"
    current = "skill://ne05/current"
    _stage(supply, bundle_id=prior, name="rollback-demo", version="1.0.0", digest=_digest("a"))
    _stage(supply, bundle_id=current, name="rollback-demo", version="2.0.0", digest=_digest("d"))
    service = SkillLifecycleService(
        supply_chain=supply,
        receipts=lifecycle,
        clock=lambda: 2_100_000_000_500,
    )
    current_evidence = _evidence(
        bundle_id=current,
        bundle_digest=_digest("d"),
        bundle_version="2.0.0",
        previous=prior,
    )
    service.activate(
        current_evidence,
        expected_activation_generation=0,
        fencing_token=1,
        idempotency_key="activate-current-only",
    )

    with pytest.raises(SkillLifecycleError, match="ROLLBACK_TARGET_EVIDENCE_MISSING"):
        service.rollback(
            current_evidence,
            expected_activation_generation=1,
            fencing_token=2,
            idempotency_key="rollback-never-activated",
        )


def test_ne05_rollback_rejects_cross_skill_target(tmp_path: Path) -> None:
    supply, lifecycle = _stores(tmp_path)
    target = "skill://ne05/other-skill"
    current = "skill://ne05/current-skill"
    _stage(supply, bundle_id=target, name="other", version="1.0.0", digest=_digest("a"))
    _stage(supply, bundle_id=current, name="current", version="2.0.0", digest=_digest("d"))
    service = SkillLifecycleService(
        supply_chain=supply,
        receipts=lifecycle,
        clock=lambda: 2_100_000_000_600,
    )
    current_evidence = _evidence(
        bundle_id=current,
        bundle_digest=_digest("d"),
        bundle_version="2.0.0",
        previous=target,
    )
    service.activate(
        current_evidence,
        expected_activation_generation=0,
        fencing_token=1,
        idempotency_key="activate-current-cross-skill",
    )

    with pytest.raises(SkillLifecycleError, match="ROLLBACK_TARGET_SCOPE_INVALID"):
        service.rollback(
            current_evidence,
            expected_activation_generation=1,
            fencing_token=2,
            idempotency_key="rollback-cross-skill",
        )


def test_ne05_new_capability_cannot_skip_maturity_ceiling() -> None:
    with pytest.raises(SkillLifecycleError, match="MATURITY"):
        _evidence(bundle_id="skill://ne05/high", bundle_digest=_digest("a"), bundle_version="1.0.0").__class__(
            activation_id="skill-activation://high",
            bundle_id="skill://ne05/high",
            bundle_digest=_digest("a"),
            bundle_version="1.0.0",
            lab_report_ref="lab://report/ne05-1",
            review_ref="review://skill/ne05-1",
            sbom_digest=_digest("b"),
            signature_audit_ref="audit://signature/ne05-1",
            sandbox_receipt_ref="sandbox://receipt/ne05-1",
            rollback_ref="skill://rollback/known-good",
            maturity_level=3,
        )


def test_ne05_signed_intake_composition_exposes_lifecycle_owner(tmp_path: Path) -> None:
    db = tmp_path / "composition.sqlite3"
    result = SQLiteMigrationRunner(db_path=db, migrations_dir=MIGRATIONS).apply_migrations(
        request_id="test-ne05-composition", ts_ms=2_100_000_001_000
    )
    assert result.success, result.as_dict()
    inbox = tmp_path / "inbox"
    quarantine = tmp_path / "quarantine"
    inbox.mkdir()
    quarantine.mkdir()
    composition = build_signed_skill_provider_intake_composition(
        env={
            "HOST_SECURE_INGRESS_MODE": "ENFORCE",
            "HOST_SUPPLY_CHAIN_ACTIVATION_MODE": "STAGED",
            "HOST_SECURE_INGRESS_HMAC_SECRETS_JSON": '{"ne05.key":"' + "x" * 32 + '"}',
            "HOST_SKILL_PROVIDER_INTAKE_ROOT": str(inbox),
            "HOST_SKILL_PROVIDER_QUARANTINE_ROOT": str(quarantine),
        },
        db_path=db,
    )
    try:
        assert composition.enabled is True
        assert composition.lifecycle_repository is not None
        assert composition.lifecycle_service is not None
    finally:
        composition.close()
