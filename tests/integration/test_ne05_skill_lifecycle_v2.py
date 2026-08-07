from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.contracts.evolution_lab import EvolutionProposalV2
from host_brain.contracts.skill_lifecycle_v2 import SkillActivationEvidenceV2
from host_brain.persistence.evolution_lab_trusted_repository import (
    SQLiteTrustedEvolutionLabLedger,
)
from host_brain.persistence.evolution_lab_v2_repository import (
    SQLiteEvolutionLabV2Ledger,
)
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.skill_lifecycle_v2_repository import (
    SQLiteSkillLifecycleV2Repository,
    SkillLifecycleV2RepositoryError,
    activation_review_subject_ref,
)
from host_brain.persistence.supply_chain_repository import (
    SQLiteSkillSupplyChainStore,
)
from host_brain.skills.builtin_registry import build_builtin_skill_registry
from host_brain.skills.capability_scorecard_provider import (
    FederatedCapabilityScorecardProvider,
)
from host_brain.skills.lifecycle_registry import (
    LifecycleBoundSkillRegistry,
)
from host_brain.skills.signed_intake_composition import (
    build_signed_skill_provider_intake_composition,
)
from host_brain.skills.skill_lifecycle_v2 import SkillLifecycleV2Service
from tests.integration.test_ne04_trusted_evolution_lab_ledger import (
    _complete_pair,
    _report,
    _spec,
)


class _Clock:
    def __init__(self, value: int = 2_200_000_000_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value

    def advance(self, milliseconds: int) -> None:
        self.value += int(milliseconds)


def _sha_ref(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _hex(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _stage(
    store: SQLiteSkillSupplyChainStore,
    *,
    bundle_id: str,
    name: str,
    version: str,
) -> dict[str, str]:
    bundle_digest = _sha_ref(f"bundle:{bundle_id}")
    sbom_digest = _sha_ref(f"sbom:{bundle_id}")
    signature_ref = f"audit://signature/{name}/{version}"
    store.intake_bundle(
        bundle_id=bundle_id,
        bundle_kind="SKILL",
        bundle_name=name,
        bundle_version=version,
        publisher_id="publisher://ne05/integration",
        bundle_digest=bundle_digest,
        quarantine_audit_ref=f"audit://quarantine/{name}/{version}",
        idempotency_key=f"intake:{bundle_id}",
    )
    store.stage_bundle(
        bundle_id=bundle_id,
        expected_status_version=0,
        signature_key_id="ne05-integration-key",
        signature_algorithm="HMAC_SHA256",
        signature_verification_audit_ref=signature_ref,
        sbom_digest=sbom_digest,
        sandbox_profile_id="ne05-isolated-v1",
        sandbox_profile_digest=_sha_ref("ne05-isolated-v1"),
        audit_ref=f"audit://stage/{name}/{version}",
        idempotency_key=f"stage:{bundle_id}",
    )
    return {
        "bundle_digest": bundle_digest,
        "sbom_digest": sbom_digest,
        "signature_ref": signature_ref,
    }


def _trusted_report(
    *,
    v2: SQLiteEvolutionLabV2Ledger,
    trusted: SQLiteTrustedEvolutionLabLedger,
    bundle_id: str,
    version: str,
    suffix: str,
):
    spec = _spec(f"skill_lifecycle_{suffix}")
    baseline, candidate = _complete_pair(
        v2_ledger=v2,
        trusted_ledger=trusted,
        spec=spec,
    )
    report = replace(
        _report(
            spec=spec,
            kind="skill_lifecycle",
            baseline=baseline,
            candidate=candidate,
        ),
        report_id=f"report-skill-lifecycle-{suffix}",
    )
    trusted.append_report(report)
    proposal = EvolutionProposalV2(
        proposal_id=f"proposal-skill-{suffix}",
        report_digest=report.report_digest,
        target_kind=(
            "skill-bundle-sha256:"
            + hashlib.sha256(bundle_id.encode("utf-8")).hexdigest()
        ),
        target_version=version,
        rationale="可信 Skill Lab 只生成待 Review 的激活提案。",
    )
    trusted.append_proposal(proposal)
    return report, proposal, candidate


def _approved_review(
    *,
    store: ReviewStore,
    bundle_id: str,
    bundle_digest: str,
    version: str,
    report_digest: str,
    proposal_digest: str,
    canary_digest: str,
    review_id: str,
) -> dict:
    item = ReviewItem(
        review_id=review_id,
        review_type=ReviewType.PRE_EXECUTION_APPROVAL,
        subject_ref=activation_review_subject_ref(
            bundle_id=bundle_id,
            bundle_digest=bundle_digest,
            bundle_version=version,
        ),
        subject_version=1,
        requester="ne05-lifecycle",
        reason="批准可信 Skill 版本进入受控 active projection",
        risk_level=4,
        side_effects=("skill-version-activation",),
        evidence_refs=(
            f"evolution-lab-report://sha256/{report_digest}",
            f"evolution-proposal://sha256/{proposal_digest}",
            f"skill-canary://sha256/{canary_digest}",
        ),
        status=ReviewStatus.PENDING,
        expires_at=2_200_000_100_000,
        resume_phase=ReviewResumePhase.PRE_EXECUTION,
        checkpoint_id=f"skill-checkpoint://{review_id}",
    )
    store.create_review(item)
    approved = store.approve_review(review_id, actor_id="operator-ne05")
    return {
        **approved,
        "_review_id": review_id,
        "_status_version": int(approved["review"]["status_version"]),
    }


def _evidence(
    *,
    bundle_id: str,
    version: str,
    staged: dict[str, str],
    report,
    proposal,
    sandbox_digest: str,
    canary: dict,
    approved: dict,
    suffix: str,
    previous_bundle_id: str | None = None,
) -> SkillActivationEvidenceV2:
    return SkillActivationEvidenceV2(
        activation_id=f"skill-activation://ne05/{suffix}",
        workspace_id="workspace-ne04-ledger",
        bundle_id=bundle_id,
        bundle_digest=staged["bundle_digest"],
        bundle_version=version,
        runtime_adapter_id="builtin-skill-alias.v1",
        runtime_protocol_version="v1",
        lab_report_digest=report.report_digest,
        review_id=str(approved["_review_id"]),
        review_status_version=int(approved["_status_version"]),
        sbom_digest=staged["sbom_digest"],
        signature_audit_ref=staged["signature_ref"],
        sandbox_attestation_digest=sandbox_digest,
        canary_receipt_digest=str(canary["canary_receipt_digest"]),
        maturity_decision_id=f"skill-maturity-decision://ne05/{suffix}",
        maturity_level=2,
        previous_bundle_id=previous_bundle_id,
        new_capability=previous_bundle_id is None,
    )


def _canary(
    *,
    lifecycle: SQLiteSkillLifecycleV2Repository,
    clock: _Clock,
    bundle_id: str,
    sandbox_digest: str,
    suffix: str,
) -> dict:
    pending = lifecycle.request_canary(
        workspace_id="workspace-ne04-ledger",
        bundle_id=bundle_id,
        runtime_adapter_id="builtin-skill-alias.v1",
        runtime_protocol_version="v1",
        sandbox_attestation_digest=sandbox_digest,
        idempotency_key=f"canary:{suffix}",
    )
    claim = lifecycle.claim_canary_outbox(
        lease_owner=f"worker-{suffix}",
        lease_ms=10_000,
    )
    assert claim is not None
    clock.advance(1)
    terminal = lifecycle.settle_canary(
        operation_id=pending["operation_id"],
        lease_owner=f"worker-{suffix}",
        fencing_token=int(claim["fencing_token"]),
        outcome="PASS",
        downstream_receipt_digest=_hex(f"downstream:{suffix}"),
        verifier_identity="ne05-canary-verifier.v1",
        result={"status": "PASS", "external_effects": 0},
    )
    assert terminal["status"] == "PASSED"
    return terminal


def _runtime(tmp_path: Path):
    db = tmp_path / "ne05-v2.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db).apply_migrations(
        request_id="ne05-v2-integration",
        ts_ms=2_200_000_000_000,
    )
    assert migrated.success, migrated.as_dict()
    assert migrated.summary["current_version"] == migrated.summary["latest_version"]
    assert migrated.summary["latest_version"] >= 152
    clock = _Clock()
    return (
        db,
        clock,
        SQLiteSkillSupplyChainStore(db, clock=clock),
        SQLiteSkillLifecycleV2Repository(db, clock=clock),
        SQLiteEvolutionLabV2Ledger(db),
        SQLiteTrustedEvolutionLabLedger(db, clock=clock),
        ReviewStore(db, clock=clock),
    )


def test_ne05_v2_atomic_activation_exact_replay_rollback_and_degrade(
    tmp_path: Path,
) -> None:
    db, clock, supply, lifecycle, v2, trusted, reviews = _runtime(tmp_path)
    first_id = "skill://ne05-v2/demo/1"
    second_id = "skill://ne05-v2/demo/2"
    first = _stage(
        supply, bundle_id=first_id, name="demo", version="1.0.0"
    )
    second = _stage(
        supply, bundle_id=second_id, name="demo", version="2.0.0"
    )

    report1, proposal1, run1 = _trusted_report(
        v2=v2,
        trusted=trusted,
        bundle_id=first_id,
        version="1.0.0",
        suffix="one",
    )
    canary1 = _canary(
        lifecycle=lifecycle,
        clock=clock,
        bundle_id=first_id,
        sandbox_digest=run1.sandbox_attestation.receipt_digest,
        suffix="one",
    )
    approved1 = _approved_review(
        store=reviews,
        bundle_id=first_id,
        bundle_digest=first["bundle_digest"],
        version="1.0.0",
        report_digest=report1.report_digest,
        proposal_digest=proposal1.proposal_digest,
        canary_digest=str(canary1["canary_receipt_digest"]),
        review_id="review-ne05-one",
    )
    evidence1 = _evidence(
        bundle_id=first_id,
        version="1.0.0",
        staged=first,
        report=report1,
        proposal=proposal1,
        sandbox_digest=run1.sandbox_attestation.receipt_digest,
        canary=canary1,
        approved=approved1,
        suffix="one",
    )
    activated1 = lifecycle.activate(
        evidence1,
        expected_activation_generation=0,
        fencing_token=1,
        idempotency_key="activate-one",
    )
    assert activated1.event == "ACTIVATED"
    assert (
        lifecycle.activate(
            evidence1,
            expected_activation_generation=0,
            fencing_token=1,
            idempotency_key="activate-one",
        ).receipt_digest
        == activated1.receipt_digest
    )

    report2, proposal2, run2 = _trusted_report(
        v2=v2,
        trusted=trusted,
        bundle_id=second_id,
        version="2.0.0",
        suffix="two",
    )
    canary2 = _canary(
        lifecycle=lifecycle,
        clock=clock,
        bundle_id=second_id,
        sandbox_digest=run2.sandbox_attestation.receipt_digest,
        suffix="two",
    )
    approved2 = _approved_review(
        store=reviews,
        bundle_id=second_id,
        bundle_digest=second["bundle_digest"],
        version="2.0.0",
        report_digest=report2.report_digest,
        proposal_digest=proposal2.proposal_digest,
        canary_digest=str(canary2["canary_receipt_digest"]),
        review_id="review-ne05-two",
    )
    evidence2 = _evidence(
        bundle_id=second_id,
        version="2.0.0",
        staged=second,
        report=report2,
        proposal=proposal2,
        sandbox_digest=run2.sandbox_attestation.receipt_digest,
        canary=canary2,
        approved=approved2,
        suffix="two",
        previous_bundle_id=first_id,
    )
    activated2 = lifecycle.activate(
        evidence2,
        expected_activation_generation=1,
        fencing_token=2,
        idempotency_key="activate-two",
    )
    assert activated2.previous_bundle_id == first_id
    assert lifecycle.resolve_active(
        bundle_kind="SKILL", bundle_name="demo"
    )["bundle_id"] == second_id

    rolled_back = lifecycle.rollback(
        current_bundle_id=second_id,
        target_bundle_id=first_id,
        expected_activation_generation=2,
        fencing_token=3,
        idempotency_key="rollback-one",
    )
    assert rolled_back.event == "ROLLED_BACK"
    assert rolled_back.bundle_id == first_id
    assert lifecycle.resolve_active(
        bundle_kind="SKILL", bundle_name="demo"
    )["bundle_id"] == first_id

    degraded = lifecycle.degrade(
        bundle_id=first_id,
        idempotency_key="degrade-one",
    )
    assert degraded.event == "DEGRADED"
    assert (
        lifecycle.resolve_active(
            bundle_kind="SKILL", bundle_name="demo"
        )
        is None
    )
    with lifecycle._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM skill_lifecycle_operation_v2"
        ).fetchone()[0] == 4
        assert conn.execute(
            "SELECT COUNT(*) FROM skill_lifecycle_receipt_v2"
        ).fetchone()[0] == 4


def test_ne05_v2_rejects_unapproved_uri_evidence_atomically(
    tmp_path: Path,
) -> None:
    db, clock, supply, lifecycle, v2, trusted, reviews = _runtime(tmp_path)
    bundle_id = "skill://ne05-v2/rejected/1"
    staged = _stage(
        supply, bundle_id=bundle_id, name="rejected", version="1.0.0"
    )
    report, proposal, run = _trusted_report(
        v2=v2,
        trusted=trusted,
        bundle_id=bundle_id,
        version="1.0.0",
        suffix="rejected",
    )
    canary = _canary(
        lifecycle=lifecycle,
        clock=clock,
        bundle_id=bundle_id,
        sandbox_digest=run.sandbox_attestation.receipt_digest,
        suffix="rejected",
    )
    approved = _approved_review(
        store=reviews,
        bundle_id=bundle_id,
        bundle_digest=staged["bundle_digest"],
        version="1.0.0",
        report_digest=report.report_digest,
        proposal_digest=proposal.proposal_digest,
        canary_digest=str(canary["canary_receipt_digest"]),
        review_id="review-ne05-rejected",
    )
    forged = _evidence(
        bundle_id=bundle_id,
        version="1.0.0",
        staged=staged,
        report=report,
        proposal=proposal,
        sandbox_digest=run.sandbox_attestation.receipt_digest,
        canary=canary,
        approved={**approved, "_status_version": 999},
        suffix="rejected",
    )
    with pytest.raises(SkillLifecycleV2RepositoryError) as rejected:
        lifecycle.activate(
            forged,
            expected_activation_generation=0,
            fencing_token=1,
            idempotency_key="activate-rejected",
        )
    assert rejected.value.reason_code == "SKILL_LIFECYCLE_V2_REVIEW_REJECTED"
    assert supply.get_bundle(bundle_id=bundle_id)["state"] == "STAGED"
    with lifecycle._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM skill_activation_evidence_v2"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM skill_lifecycle_receipt_v2"
        ).fetchone()[0] == 0


def test_ne05_v2_expired_canary_claim_requires_reconciliation(
    tmp_path: Path,
) -> None:
    _, clock, supply, lifecycle, _, _, _ = _runtime(tmp_path)
    bundle_id = "skill://ne05-v2/unknown/1"
    _stage(
        supply, bundle_id=bundle_id, name="unknown", version="1.0.0"
    )
    pending = lifecycle.request_canary(
        workspace_id="workspace-ne04-ledger",
        bundle_id=bundle_id,
        runtime_adapter_id="builtin-skill-alias.v1",
        runtime_protocol_version="v1",
        sandbox_attestation_digest=_hex("sandbox-unknown"),
        idempotency_key="canary-unknown",
    )
    assert lifecycle.claim_canary_outbox(
        lease_owner="unknown-worker", lease_ms=10
    )
    clock.advance(11)
    assert (
        lifecycle.claim_canary_outbox(
            lease_owner="replacement-worker", lease_ms=10
        )
        is None
    )
    replay = lifecycle.request_canary(
        workspace_id="workspace-ne04-ledger",
        bundle_id=bundle_id,
        runtime_adapter_id="builtin-skill-alias.v1",
        runtime_protocol_version="v1",
        sandbox_attestation_digest=_hex("sandbox-unknown"),
        idempotency_key="canary-unknown",
    )
    assert replay["operation_id"] == pending["operation_id"]
    assert replay["status"] == "RECONCILIATION_REQUIRED"


def test_ne05_host_resolves_only_v2_active_content_addressed_version(
    tmp_path: Path,
) -> None:
    _, clock, supply, lifecycle, v2, trusted, reviews = _runtime(tmp_path)
    bundle_id = "skill://ne05-v2/noop/1"
    staged = _stage(
        supply,
        bundle_id=bundle_id,
        name="noop",
        version="1.0.0",
    )
    report, proposal, run = _trusted_report(
        v2=v2,
        trusted=trusted,
        bundle_id=bundle_id,
        version="1.0.0",
        suffix="runtime",
    )
    canary = _canary(
        lifecycle=lifecycle,
        clock=clock,
        bundle_id=bundle_id,
        sandbox_digest=run.sandbox_attestation.receipt_digest,
        suffix="runtime",
    )
    approved = _approved_review(
        store=reviews,
        bundle_id=bundle_id,
        bundle_digest=staged["bundle_digest"],
        version="1.0.0",
        report_digest=report.report_digest,
        proposal_digest=proposal.proposal_digest,
        canary_digest=str(canary["canary_receipt_digest"]),
        review_id="review-ne05-runtime",
    )
    receipt = lifecycle.activate(
        _evidence(
            bundle_id=bundle_id,
            version="1.0.0",
            staged=staged,
            report=report,
            proposal=proposal,
            sandbox_digest=run.sandbox_attestation.receipt_digest,
            canary=canary,
            approved=approved,
            suffix="runtime",
        ),
        expected_activation_generation=0,
        fencing_token=1,
        idempotency_key="activate-runtime",
    )
    assert receipt.event == "ACTIVATED"

    bypass_id = "skill://ne05-v1/session-summary/1"
    _stage(
        supply,
        bundle_id=bypass_id,
        name="session_summary",
        version="9.9.9",
    )
    assert not hasattr(supply, "activate_bundle")

    registry = LifecycleBoundSkillRegistry(
        base_registry=build_builtin_skill_registry(),
        active_versions=lifecycle,
    )
    resolved = registry.require("noop")
    assert resolved.version == "1.0.0"
    assert resolved.version_binding is not None
    assert resolved.version_binding.bundle_id == bundle_id
    assert registry.require("session_summary").version_binding is None

    scorecard = FederatedCapabilityScorecardProvider(
        skill_registry=registry,
        clock=clock,
    ).get("noop")
    assert scorecard is not None
    assert scorecard.maturity_level == 2
    assert scorecard.evidence[0].suite_id == "skill-lifecycle-v2"


def test_ne05_signed_intake_production_composes_only_v2_authority(
    tmp_path: Path,
) -> None:
    db = tmp_path / "signed-intake-v2.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db).apply_migrations(
        request_id="ne05-signed-intake-v2",
        ts_ms=2_200_000_000_000,
    )
    assert migrated.success, migrated.as_dict()
    inbox = tmp_path / "inbox"
    quarantine = tmp_path / "quarantine"
    inbox.mkdir()
    quarantine.mkdir()
    composition = build_signed_skill_provider_intake_composition(
        env={
            "HOST_SECURE_INGRESS_MODE": "ENFORCE",
            "HOST_SUPPLY_CHAIN_ACTIVATION_MODE": "STAGED",
            "HOST_SECURE_INGRESS_HMAC_SECRETS_JSON": (
                '{"ne05.key":"' + "x" * 32 + '"}'
            ),
            "HOST_SKILL_PROVIDER_INTAKE_ROOT": str(inbox),
            "HOST_SKILL_PROVIDER_QUARANTINE_ROOT": str(quarantine),
        },
        db_path=db,
    )
    try:
        assert composition.enabled is True
        assert isinstance(
            composition.lifecycle_repository,
            SQLiteSkillLifecycleV2Repository,
        )
        assert isinstance(
            composition.lifecycle_service,
            SkillLifecycleV2Service,
        )
    finally:
        composition.close()
