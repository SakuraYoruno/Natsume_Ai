#!/usr/bin/env python3
"""Run real NE-06 Knowledge and Identity Surface causal acceptance.

The runner uses the existing trusted Docker Lab, production ReviewStore,
version activation service and a real external-model Host.  It never starts or
probes Edge, and it stores only synthetic acceptance content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Mapping

import httpx


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.startup_snapshot import build_startup_snapshot  # noqa: E402
from host_brain.app.surface_main_chain_support import (  # noqa: E402
    _surface_active_knowledge_set_digest,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner  # noqa: E402
from host_brain.contracts.agent_runtime.review_policy import (  # noqa: E402
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.contracts.evolution import KnowledgeEntryV2  # noqa: E402
from host_brain.contracts.evolution_lab import EvolutionProposalV2  # noqa: E402
from host_brain.evolution.version_activation import (  # noqa: E402
    EvolutionVersionActivationService,
)
from host_brain.evolution_lab.docker_executor import (  # noqa: E402
    TrustedDockerSandboxExecutor,
)
from host_brain.persistence.evolution_activation_v2_evidence import (  # noqa: E402
    identity_target_kind,
    knowledge_subject_ref,
    knowledge_target_kind,
    payload_digest,
)
from host_brain.persistence.evolution_activation_v2_repository import (  # noqa: E402
    SQLiteEvolutionActivationV2Repository,
)
from host_brain.persistence.evolution_lab_trusted_repository import (  # noqa: E402
    SQLiteTrustedEvolutionLabLedger,
)
from host_brain.persistence.evolution_lab_v2_repository import (  # noqa: E402
    SQLiteEvolutionLabV2Ledger,
)
from host_brain.persistence.review_repository import ReviewStore  # noqa: E402
from host_brain.persona.drift_detector import StyleFingerprint  # noqa: E402
from host_brain.release.identity_activation import (  # noqa: E402
    IdentityReleaseActivationService,
)
from host_brain.release.identity_snapshot import (  # noqa: E402
    IdentitySnapshot,
    IdentitySourceRef,
    build_identity_snapshot,
)
from host_brain.surface_runtime.input_compiler import (  # noqa: E402
    _active_identity_release_directive,
)
from scripts.runtime.run_ne06_expression_live_acceptance import (  # noqa: E402
    _event_text,
    _model_attempt,
    _require_exact_tracked_commit,
    _run_lab_authority as _run_expression_lab_authority,
    _turn_passed,
)


IMAGE_REFERENCE_DEFAULT = (
    "python@sha256:"
    "6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df"
)


def _knowledge_fact(marker: str, verification_token: str) -> str:
    return (
        f"{marker} 对应的暗号是 {verification_token}，"
        "下次问起时请原样说出。"
    )


def _knowledge_question(marker: str) -> str:
    return (
        f"还记得 {marker} 对应的暗号是什么吗？"
        "不记得就直接说不知道。"
    )


def _run_lab_authority(
    *,
    workspace_id: str,
    commit: str,
    baseline_version: str,
    target_version: str,
    baseline_payload: Mapping[str, Any],
    target_payload: Mapping[str, Any],
    target_kind: str,
    owner_id: str,
    executor: TrustedDockerSandboxExecutor,
    spec_registry: SQLiteEvolutionLabV2Ledger,
    trusted_ledger: SQLiteTrustedEvolutionLabLedger,
) -> dict[str, Any]:
    """Run unique Lab evidence, then bind it to the exact activation target.

    The companion-expression experiment identifies a spec from its dataset and
    commit.  Knowledge and Identity may legitimately reuse the same synthetic
    policy comparison, so every acceptance action receives a unique schema
    scope.  Its experiment-only policy versions never impersonate the real
    activation version; a second proposal binds the trusted report to that
    exact version instead.
    """

    scope = hashlib.sha256(
        "\0".join((owner_id, target_kind, target_version)).encode("utf-8")
    ).hexdigest()[:20]
    schema = f"natsume.ne06.knowledge-identity-live.{scope}.v1"
    scoped_baseline = {**dict(baseline_payload), "schema": schema}
    scoped_target = {**dict(target_payload), "schema": schema}
    experiment = _run_expression_lab_authority(
        workspace_id=workspace_id,
        commit=commit,
        baseline_version=f"lab-{scope}-baseline",
        target_version=f"lab-{scope}-candidate",
        baseline_payload=scoped_baseline,
        target_payload=scoped_target,
        target_kind=target_kind,
        owner_id=owner_id,
        executor=executor,
        spec_registry=spec_registry,
        trusted_ledger=trusted_ledger,
    )
    report = experiment["report"]
    binding = hashlib.sha256(
        "\0".join(
            (report.report_digest, target_kind, target_version)
        ).encode("utf-8")
    ).hexdigest()
    proposal = EvolutionProposalV2(
        proposal_id=f"lab-trusted-proposal-ne06-activation-{binding[:40]}",
        report_digest=report.report_digest,
        target_kind=target_kind,
        target_version=target_version,
        rationale=(
            "隔离 Docker 双臂提供确定性因果证据；本提案仅将报告绑定到"
            "真实 Knowledge 或 Identity 目标版本，仍须 production Review。"
        ),
    )
    trusted_ledger.append_proposal(proposal)
    return {
        "report": report,
        "proposal": proposal,
        "experiment_proposal": experiment["proposal"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run Knowledge activate/deprecate and Identity "
            "activate/exact-rollback against a real Host."
        )
    )
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--image-reference", default=IMAGE_REFERENCE_DEFAULT)
    parser.add_argument("--workspace-id", default="host-default")
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--host-base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--operator-id", default="local-owner-acceptance")
    parser.add_argument("--receipt-path", required=True)
    parser.add_argument("--chat-timeout-s", type=float, default=90.0)
    args = parser.parse_args(argv)

    commit = str(args.commit_sha or "").strip().lower()
    _require_exact_tracked_commit(commit)
    db_path = Path(args.db_path).expanduser().resolve()
    runtime_root = Path(args.runtime_root).expanduser().resolve()
    receipt_path = Path(args.receipt_path).expanduser().resolve()
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"ne06-knowledge-identity-live:{commit[:12]}"
    )
    if not migration.success or int(
        migration.summary.get("latest_version", 0)
    ) < 142:
        raise RuntimeError(
            "NE06_KNOWLEDGE_IDENTITY_MIGRATION_FAILED:"
            f"{migration.reason_code}"
        )

    workspace_id = str(args.workspace_id).strip()
    run_id = f"ne06-ki-{int(time.time() * 1000)}"
    repository = SQLiteEvolutionActivationV2Repository(db_path)
    service = EvolutionVersionActivationService(repository)
    release_service = IdentityReleaseActivationService(
        version_activation=service
    )
    reviews = ReviewStore(db_path)
    spec_registry = SQLiteEvolutionLabV2Ledger(db_path)
    trusted_ledger = SQLiteTrustedEvolutionLabLedger(db_path)
    executor = TrustedDockerSandboxExecutor(
        runtime_root=runtime_root,
        image_reference=str(args.image_reference),
        worker_source=(
            ROOT
            / "host_brain"
            / "evolution_lab"
            / "companion_expression_worker.py"
        ),
    )

    knowledge = _run_knowledge_flow(
        run_id=run_id,
        commit=commit,
        workspace_id=workspace_id,
        operator_id=str(args.operator_id),
        host_base_url=str(args.host_base_url),
        timeout_s=float(args.chat_timeout_s),
        db_path=db_path,
        service=service,
        repository=repository,
        reviews=reviews,
        executor=executor,
        spec_registry=spec_registry,
        trusted_ledger=trusted_ledger,
    )
    identity = _run_identity_flow(
        run_id=run_id,
        commit=commit,
        workspace_id=workspace_id,
        operator_id=str(args.operator_id),
        host_base_url=str(args.host_base_url),
        timeout_s=float(args.chat_timeout_s),
        db_path=db_path,
        service=service,
        release_service=release_service,
        reviews=reviews,
        executor=executor,
        spec_registry=spec_registry,
        trusted_ledger=trusted_ledger,
    )
    checks = {
        **{
            f"knowledge_{name}": bool(value)
            for name, value in dict(knowledge["checks"]).items()
        },
        **{
            f"identity_{name}": bool(value)
            for name, value in dict(identity["checks"]).items()
        },
    }
    status = (
        "PASS_REAL_DOCKER_REVIEW_KNOWLEDGE_IDENTITY_"
        "SURFACE_CAUSAL_ROLLBACK"
        if all(checks.values())
        else "FAIL_NE06_KNOWLEDGE_IDENTITY_LIVE_ACCEPTANCE"
    )
    receipt = {
        "schema": "natsume.ne06-knowledge-identity-live-acceptance.v1",
        "status": status,
        "commit_sha": commit,
        "run_id": run_id,
        "workspace_id": workspace_id,
        "db_identity_digest": hashlib.sha256(
            str(db_path).encode("utf-8")
        ).hexdigest(),
        "lab_image_reference": str(args.image_reference),
        "checks": checks,
        "knowledge": knowledge["evidence"],
        "identity": identity["evidence"],
        "ordinary_chat_added_writes_or_model_calls": 0,
        "human_experience_verdict": (
            "UNPROVEN_ENGINEERING_CAUSAL_ACCEPTANCE_ONLY"
        ),
        "voice_avatar": "NOT_TESTED_REMOTE_EDGE_DISABLED",
        "remote_edge": "NOT_STARTED_OR_PROBED",
        "remote_ci": "NOT_RUN_ACCOUNT_QUOTA",
        "created_at_ms": int(time.time() * 1000),
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0 if status.startswith("PASS_") else 1


def _run_knowledge_flow(
    *,
    run_id: str,
    commit: str,
    workspace_id: str,
    operator_id: str,
    host_base_url: str,
    timeout_s: float,
    db_path: Path,
    service: EvolutionVersionActivationService,
    repository: SQLiteEvolutionActivationV2Repository,
    reviews: ReviewStore,
    executor: TrustedDockerSandboxExecutor,
    spec_registry: SQLiteEvolutionLabV2Ledger,
    trusted_ledger: SQLiteTrustedEvolutionLabLedger,
) -> dict[str, Any]:
    run_digest = hashlib.sha256(run_id.encode()).hexdigest()
    marker = f"PROJECT_NE06_{run_digest[:12].upper()}"
    verification_token = f"KIVA_{run_digest[12:24].upper()}"
    answer = _knowledge_fact(marker, verification_token)
    entry = KnowledgeEntryV2(
        namespace=f"project-knowledge://workspace/{workspace_id}",
        logical_key=f"live-acceptance-{run_id}",
        entry_id=f"knowledge://ne06-live/{run_id}",
        workspace_id=workspace_id,
        owner_id="natsume_ai",
        content_summary=answer,
        content_digest=payload_digest(answer),
        provenance_ref=f"task-receipt://ne06-live/{run_id}",
        version=1,
        validity="VALID",
        confidence=1.0,
        expires_at_ms=0,
        conflict_refs=(),
        decision_refs=(),
        supersedes="",
    )
    service.register_knowledge(entry)
    target_kind = knowledge_target_kind(entry_id=entry.entry_id)
    apply_authority = _run_lab_authority(
        workspace_id=workspace_id,
        commit=commit,
        baseline_version="unregistered",
        target_version=str(entry.version),
        baseline_payload={"tag_scores": {}},
        target_payload={"tag_scores": {"in_persona": 1}},
        target_kind=target_kind,
        owner_id=f"{operator_id}:{run_id}:knowledge-apply",
        executor=executor,
        spec_registry=spec_registry,
        trusted_ledger=trusted_ledger,
    )
    deprecate_authority = _run_lab_authority(
        workspace_id=workspace_id,
        commit=commit,
        baseline_version=str(entry.version),
        target_version=f"deprecate-{entry.version}",
        baseline_payload={"tag_scores": {"in_persona": 1}},
        target_payload={"tag_scores": {}},
        target_kind=target_kind,
        owner_id=f"{operator_id}:{run_id}:knowledge-deprecate",
        executor=executor,
        spec_registry=spec_registry,
        trusted_ledger=trusted_ledger,
    )
    subject_ref = knowledge_subject_ref(
        entry_id=entry.entry_id,
        entry_digest=entry.entry_digest,
    )
    evidence_refs = (
        f"evolution-knowledge-entry://sha256/{entry.entry_digest}",
        entry.provenance_ref,
    )
    apply_review = _create_review(
        reviews=reviews,
        run_id=run_id,
        action="knowledge-apply",
        operator_id=operator_id,
        subject_ref=subject_ref,
        subject_version=entry.version,
        lab=apply_authority,
        evidence_refs=evidence_refs,
    )
    deprecate_review = _create_review(
        reviews=reviews,
        run_id=run_id,
        action="knowledge-deprecate",
        operator_id=operator_id,
        subject_ref=subject_ref,
        subject_version=_transition_subject_version(1),
        lab=deprecate_authority,
        evidence_refs=evidence_refs,
    )
    question = _knowledge_question(marker)
    baseline = _run_host_turn(
        host_base_url=host_base_url,
        db_path=db_path,
        message=question,
        person_id=f"{run_id}-knowledge-baseline",
        session_id=f"{run_id}-knowledge-baseline",
        timeout_s=timeout_s,
    )
    activated = service.activate_knowledge(
        workspace_id=workspace_id,
        entry_id=entry.entry_id,
        lab_report_digest=apply_authority["report"].report_digest,
        review_id=apply_review["review_id"],
        review_status_version=apply_review["status_version"],
        expected_generation=0,
        idempotency_key=f"{run_id}:knowledge-apply",
    )
    candidate = _run_host_turn(
        host_base_url=host_base_url,
        db_path=db_path,
        message=question,
        person_id=f"{run_id}-knowledge-candidate",
        session_id=f"{run_id}-knowledge-candidate",
        timeout_s=timeout_s,
    )
    deprecated = service.deprecate_knowledge(
        workspace_id=workspace_id,
        entry_id=entry.entry_id,
        lab_report_digest=deprecate_authority["report"].report_digest,
        review_id=deprecate_review["review_id"],
        review_status_version=deprecate_review["status_version"],
        expected_generation=1,
        idempotency_key=f"{run_id}:knowledge-deprecate",
    )
    rollback = _run_host_turn(
        host_base_url=host_base_url,
        db_path=db_path,
        message=question,
        person_id=f"{run_id}-knowledge-rollback",
        session_id=f"{run_id}-knowledge-rollback",
        timeout_s=timeout_s,
    )
    remaining = repository.search_active_knowledge(
        workspace_id=workspace_id,
        namespace=entry.namespace,
        query=marker,
        top_k=5,
        threshold=0.05,
    )
    expected_knowledge_set_digest = _surface_active_knowledge_set_digest(
        (entry.entry_digest,)
    )
    baseline_model = dict(baseline.get("model") or {})
    candidate_model = dict(candidate.get("model") or {})
    rollback_model = dict(rollback.get("model") or {})
    checks = {
        "baseline_host_turn": _turn_passed(baseline),
        "candidate_host_turn": _turn_passed(candidate),
        "rollback_host_turn": _turn_passed(rollback),
        "baseline_knowledge_prompt_evidence_absent": not bool(
            baseline_model.get("surface_active_knowledge_set_digest")
        ),
        "candidate_knowledge_prompt_evidence_exact": (
            str(
                candidate_model.get("surface_active_knowledge_set_digest")
                or ""
            )
            == expected_knowledge_set_digest
            and int(candidate_model.get("surface_active_knowledge_count") or 0)
            == 1
        ),
        "rollback_knowledge_prompt_evidence_absent": not bool(
            rollback_model.get("surface_active_knowledge_set_digest")
        ),
        "baseline_verification_token_absent": (
            verification_token not in str(baseline.get("render_text") or "")
        ),
        "candidate_verification_token_present": (
            verification_token in str(candidate.get("render_text") or "")
        ),
        "candidate_prompt_changed": (
            _prompt_digest(candidate) != _prompt_digest(baseline)
        ),
        "active_projection_empty_after_deprecate": remaining == [],
        "activation_receipt": activated.event == "ACTIVATED",
        "deprecation_receipt": deprecated.event == "DEPRECATED",
        "reviews_approved": (
            apply_review["status"] == "APPROVED"
            and deprecate_review["status"] == "APPROVED"
        ),
        "docker_apply_two_arms": len(apply_authority["report"].runs) == 2,
        "docker_deprecate_two_arms": (
            len(deprecate_authority["report"].runs) == 2
        ),
        "docker_side_effects_zero": _lab_side_effects_zero(
            apply_authority["report"], deprecate_authority["report"]
        ),
    }
    return {
        "checks": checks,
        "evidence": {
            "entry_id": entry.entry_id,
            "entry_digest": entry.entry_digest,
            "marker_digest": hashlib.sha256(marker.encode()).hexdigest(),
            "verification_token_digest": hashlib.sha256(
                verification_token.encode()
            ).hexdigest(),
            "expected_knowledge_set_digest": expected_knowledge_set_digest,
            "rollback_token_retained_by_conversation_memory": (
                verification_token in str(rollback.get("render_text") or "")
            ),
            "apply_report_digest": apply_authority["report"].report_digest,
            "deprecate_report_digest": (
                deprecate_authority["report"].report_digest
            ),
            "apply_review": apply_review,
            "deprecate_review": deprecate_review,
            "activation_receipt_digest": activated.receipt_digest,
            "deprecation_receipt_digest": deprecated.receipt_digest,
            "baseline": _turn_evidence(baseline),
            "candidate": _turn_evidence(candidate),
            "rollback": _turn_evidence(rollback),
        },
    }


def _run_identity_flow(
    *,
    run_id: str,
    commit: str,
    workspace_id: str,
    operator_id: str,
    host_base_url: str,
    timeout_s: float,
    db_path: Path,
    service: EvolutionVersionActivationService,
    release_service: IdentityReleaseActivationService,
    reviews: ReviewStore,
    executor: TrustedDockerSandboxExecutor,
    spec_registry: SQLiteEvolutionLabV2Ledger,
    trusted_ledger: SQLiteTrustedEvolutionLabLedger,
) -> dict[str, Any]:
    active_before = service.active_identity(workspace_id=workspace_id)
    if active_before is None:
        previous, proposed = _build_identity_pair(run_id=run_id)
        release_service.backfill_previous_release(
            workspace_id=workspace_id,
            snapshot=previous,
            activation_version=1,
            idempotency_key=f"{run_id}:identity-baseline",
        )
        initial_activation_version = 1
        initial_generation = 1
    else:
        previous = IdentitySnapshot.from_mapping(active_before["snapshot"])
        if not previous.release_ref.startswith(
            "release://natsume/ne06-ki-"
        ):
            raise RuntimeError(
                "NE06_IDENTITY_ACTIVE_BASELINE_NOT_ACCEPTANCE_OWNED"
            )
        initial_activation_version = int(
            active_before["activation_version"]
        )
        initial_generation = int(active_before["activation_generation"])
        proposed = _build_identity_candidate(
            run_id=run_id,
            previous=previous,
            release_suffix=f"v{initial_activation_version + 1}",
        )
    activation_version = initial_activation_version + 1
    migration_id = f"identity-migration-{run_id}"
    preparation = release_service.prepare_release(
        migration_id=migration_id,
        workspace_id=workspace_id,
        previous=previous,
        proposed=proposed,
        activation_version=activation_version,
        regression_checks={
            "persona": True,
            "relationship": True,
            "privacy": True,
        },
        allow_major_difference=True,
    )
    if preparation.status != "AWAITING_LAB_REVIEW":
        raise RuntimeError(
            f"NE06_IDENTITY_PREPARATION_BLOCKED:{preparation.status}"
        )
    target_kind = identity_target_kind(migration_id=migration_id)
    apply_authority = _run_lab_authority(
        workspace_id=workspace_id,
        commit=commit,
        baseline_version=str(initial_activation_version),
        target_version=str(activation_version),
        baseline_payload={"tag_scores": {}},
        target_payload={"tag_scores": {"in_persona": 1}},
        target_kind=target_kind,
        owner_id=f"{operator_id}:{run_id}:identity-apply",
        executor=executor,
        spec_registry=spec_registry,
        trusted_ledger=trusted_ledger,
    )
    rollback_authority = _run_lab_authority(
        workspace_id=workspace_id,
        commit=commit,
        baseline_version=str(activation_version),
        target_version=f"rollback-{activation_version}",
        baseline_payload={"tag_scores": {"in_persona": 1}},
        target_payload={"tag_scores": {}},
        target_kind=target_kind,
        owner_id=f"{operator_id}:{run_id}:identity-rollback",
        executor=executor,
        spec_registry=spec_registry,
        trusted_ledger=trusted_ledger,
    )
    evidence_refs = (
        previous.snapshot_ref,
        proposed.snapshot_ref,
        (
            "identity-regression://sha256/"
            f"{preparation.regression_report_digest}"
        ),
        preparation.diff_ref,
    )
    apply_review = _create_review(
        reviews=reviews,
        run_id=run_id,
        action="identity-apply",
        operator_id=operator_id,
        subject_ref=proposed.snapshot_ref,
        subject_version=activation_version,
        lab=apply_authority,
        evidence_refs=evidence_refs,
    )
    rollback_review = _create_review(
        reviews=reviews,
        run_id=run_id,
        action="identity-rollback",
        operator_id=operator_id,
        subject_ref=previous.snapshot_ref,
        subject_version=_transition_subject_version(initial_generation + 1),
        lab=rollback_authority,
        evidence_refs=evidence_refs,
    )
    ready = release_service.bind_release_authorities(
        preparation,
        previous=previous,
        proposed=proposed,
        lab_report_digest=apply_authority["report"].report_digest,
        review_id=apply_review["review_id"],
        review_status_version=apply_review["status_version"],
    )
    message = "今晚想随便聊两句，你就自然接住我吧。"
    baseline = _run_host_turn(
        host_base_url=host_base_url,
        db_path=db_path,
        message=message,
        person_id=f"{run_id}-identity-baseline",
        session_id=f"{run_id}-identity-baseline",
        timeout_s=timeout_s,
    )
    activated = release_service.activate_prepared_release(
        ready,
        expected_generation=initial_generation,
        idempotency_key=f"{run_id}:identity-apply",
    )
    candidate = _run_host_turn(
        host_base_url=host_base_url,
        db_path=db_path,
        message=message,
        person_id=f"{run_id}-identity-candidate",
        session_id=f"{run_id}-identity-candidate",
        timeout_s=timeout_s,
    )
    rolled_back = release_service.rollback_release(
        workspace_id=workspace_id,
        migration_id=migration_id,
        lab_report_digest=rollback_authority["report"].report_digest,
        review_id=rollback_review["review_id"],
        review_status_version=rollback_review["status_version"],
        expected_generation=initial_generation + 1,
        idempotency_key=f"{run_id}:identity-rollback",
    )
    rollback = _run_host_turn(
        host_base_url=host_base_url,
        db_path=db_path,
        message=message,
        person_id=f"{run_id}-identity-rollback",
        session_id=f"{run_id}-identity-rollback",
        timeout_s=timeout_s,
    )
    active = service.active_identity(workspace_id=workspace_id)
    baseline_directive = _turn_identity_directive(baseline)
    candidate_directive = _turn_identity_directive(candidate)
    rollback_directive = _turn_identity_directive(rollback)
    checks = {
        "baseline_host_turn": _turn_passed(baseline),
        "candidate_host_turn": _turn_passed(candidate),
        "rollback_host_turn": _turn_passed(rollback),
        "baseline_directive_present": bool(baseline_directive),
        "candidate_directive_present": bool(candidate_directive),
        "candidate_directive_changed": (
            candidate_directive != baseline_directive
        ),
        "rollback_directive_exact": (
            rollback_directive == baseline_directive
        ),
        "candidate_prompt_changed": (
            _prompt_digest(candidate) != _prompt_digest(baseline)
        ),
        "active_snapshot_exact_previous": bool(
            active
            and str(active["snapshot"]["snapshot_ref"])
            == previous.snapshot_ref
        ),
        "activation_receipt": activated.event == "ACTIVATED",
        "rollback_receipt": rolled_back.event == "ROLLED_BACK",
        "reviews_approved": (
            apply_review["status"] == "APPROVED"
            and rollback_review["status"] == "APPROVED"
        ),
        "docker_apply_two_arms": len(apply_authority["report"].runs) == 2,
        "docker_rollback_two_arms": (
            len(rollback_authority["report"].runs) == 2
        ),
        "docker_side_effects_zero": _lab_side_effects_zero(
            apply_authority["report"], rollback_authority["report"]
        ),
    }
    return {
        "checks": checks,
        "evidence": {
            "migration_id": migration_id,
            "previous_snapshot_ref": previous.snapshot_ref,
            "proposed_snapshot_ref": proposed.snapshot_ref,
            "final_snapshot_ref": (
                str(active["snapshot"]["snapshot_ref"]) if active else ""
            ),
            "initial_activation_version": initial_activation_version,
            "activation_version": activation_version,
            "initial_generation": initial_generation,
            "final_generation": (
                int(active["activation_generation"]) if active else 0
            ),
            "rollback_prompt_matches_baseline_observation": (
                _prompt_digest(rollback) == _prompt_digest(baseline)
            ),
            "apply_report_digest": apply_authority["report"].report_digest,
            "rollback_report_digest": rollback_authority["report"].report_digest,
            "apply_review": apply_review,
            "rollback_review": rollback_review,
            "activation_receipt_digest": activated.receipt_digest,
            "rollback_receipt_digest": rolled_back.receipt_digest,
            "baseline": _turn_evidence(baseline),
            "candidate": _turn_evidence(candidate),
            "rollback": _turn_evidence(rollback),
        },
    }


def _create_review(
    *,
    reviews: ReviewStore,
    run_id: str,
    action: str,
    operator_id: str,
    subject_ref: str,
    subject_version: int,
    lab: Mapping[str, Any],
    evidence_refs: tuple[str, ...],
) -> dict[str, Any]:
    report = lab["report"]
    proposal = lab["proposal"]
    review_id = f"review-{run_id}-{action}"
    checkpoint_id = f"checkpoint://{run_id}/{action}"
    item = ReviewItem(
        review_id=review_id,
        review_type=ReviewType.PRE_EXECUTION_APPROVAL,
        subject_ref=subject_ref,
        subject_version=int(subject_version),
        requester=operator_id,
        reason=f"本机真实验收批准 {action}",
        risk_level=2,
        side_effects=(action,),
        evidence_refs=(
            f"evolution-lab-report://sha256/{report.report_digest}",
            f"evolution-proposal://sha256/{proposal.proposal_digest}",
            *evidence_refs,
        ),
        status=ReviewStatus.PENDING,
        expires_at=int(time.time() * 1000) + 1_800_000,
        resume_phase=ReviewResumePhase.PRE_EXECUTION,
        checkpoint_id=checkpoint_id,
    )
    reviews.create_review(item)
    approved = reviews.approve_review(
        review_id,
        actor_id=operator_id,
        expected_subject_ref=subject_ref,
        expected_subject_version=int(subject_version),
        expected_resume_phase=ReviewResumePhase.PRE_EXECUTION,
        expected_checkpoint_id=checkpoint_id,
    )
    record = dict(approved["review"])
    review = dict(record.get("review") or {})
    return {
        "review_id": review_id,
        "status": str(review.get("status") or ""),
        "status_version": int(record["status_version"]),
        "decision_actor": str(review.get("decision_actor") or ""),
    }


def _transition_subject_version(expected_generation: int) -> int:
    """Bind terminal Review to the generation produced by the transition."""

    return max(0, int(expected_generation)) + 1


def _run_host_turn(
    *,
    host_base_url: str,
    db_path: Path,
    message: str,
    person_id: str,
    session_id: str,
    timeout_s: float,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    started = time.perf_counter()
    with httpx.Client(timeout=timeout_s, trust_env=False) as client:
        with client.stream(
            "POST",
            host_base_url.rstrip("/") + "/v1/chat",
            json={
                "message": message,
                "workspace_id": "host-default",
                "person_id": person_id,
                "session_id": session_id,
                "suppress_edge_tts": True,
                "suppress_edge_vts": True,
            },
        ) as response:
            status_code = int(response.status_code)
            response.raise_for_status()
            for line in response.iter_lines():
                raw = str(line or "").strip()
                if not raw.startswith("data:"):
                    continue
                try:
                    event = json.loads(raw.removeprefix("data:").strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(event, Mapping):
                    events.append(dict(event))
    request_id = next(
        (
            str(event.get("request_id") or "")
            for event in reversed(events)
            if event.get("request_id")
        ),
        "",
    )
    render_text = next(
        (
            _event_text(event)
            for event in reversed(events)
            if str(event.get("phase") or "").upper() == "RENDER"
            and _event_text(event)
        ),
        "",
    )
    commit = next(
        (
            event
            for event in reversed(events)
            if str(event.get("phase") or "").upper() == "COMMIT"
        ),
        {},
    )
    return {
        "status_code": status_code,
        "request_id": request_id,
        "session_id": session_id,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "render_text": render_text,
        "commit_status": str(commit.get("status") or ""),
        "commit_reason_code": str(commit.get("reason_code") or ""),
        "model": _model_attempt(db_path, request_id),
        "reply_plan": _surface_reply_plan(db_path, request_id, timeout_s=10.0),
    }


def _surface_reply_plan(
    db_path: Path,
    request_id: str,
    *,
    timeout_s: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM life_command
                WHERE correlation_id = ?
                  AND command_type = 'foreground_completed'
                ORDER BY created_at_ms DESC
                LIMIT 1
                """,
                (request_id,),
            ).fetchone()
        if row is not None:
            try:
                payload = json.loads(str(row[0] or "{}"))
            except json.JSONDecodeError:
                return {}
            return dict(payload.get("reply_plan") or {})
        if time.monotonic() >= deadline:
            return {}
        time.sleep(0.05)


def _turn_identity_directive(turn: Mapping[str, Any]) -> str:
    prefix = "已激活身份版本的表达底色（不覆盖 Soul）："
    for item in list(
        dict(turn.get("reply_plan") or {}).get("style_anchor") or []
    ):
        text = str(item or "").strip()
        if text.startswith(prefix):
            return text
    return ""


def _prompt_digest(turn: Mapping[str, Any]) -> str:
    return str(dict(turn.get("model") or {}).get("prompt_digest") or "")


def _turn_evidence(turn: Mapping[str, Any]) -> dict[str, Any]:
    model = dict(turn.get("model") or {})
    plan = dict(turn.get("reply_plan") or {})
    render = str(turn.get("render_text") or "")
    return {
        "status_code": int(turn.get("status_code") or 0),
        "request_id": str(turn.get("request_id") or ""),
        "elapsed_ms": int(turn.get("elapsed_ms") or 0),
        "commit_status": str(turn.get("commit_status") or ""),
        "commit_reason_code": str(turn.get("commit_reason_code") or ""),
        "render_digest": hashlib.sha256(render.encode("utf-8")).hexdigest(),
        "provider": str(model.get("provider") or ""),
        "model_id": str(model.get("model_id") or ""),
        "model_status": str(model.get("status") or ""),
        "prompt_digest": str(model.get("prompt_digest") or ""),
        "surface_active_knowledge_set_digest": str(
            model.get("surface_active_knowledge_set_digest") or ""
        ),
        "surface_active_knowledge_count": int(
            model.get("surface_active_knowledge_count") or 0
        ),
        "prompt_tokens": int(model.get("prompt_tokens") or 0),
        "completion_tokens": int(model.get("completion_tokens") or 0),
        "transport_external": bool(model.get("transport_external")),
        "transport_identity_digest": str(
            model.get("transport_identity_digest") or ""
        ),
        "facts_digest": payload_digest(list(plan.get("facts") or [])),
        "style_anchor_digest": payload_digest(
            list(plan.get("style_anchor") or [])
        ),
        "identity_directive": _turn_identity_directive(turn),
    }


def _lab_side_effects_zero(*reports: Any) -> bool:
    return all(
        not bool(getattr(run, "production_side_effects", True))
        for report in reports
        for run in list(getattr(report, "runs", ()) or ())
    )


def _build_identity_pair(*, run_id: str):
    now = int(time.time() * 1000)
    previous = _build_identity_snapshot(
        run_id=run_id,
        release_suffix="v1",
        previous_release_ref=None,
        created_at_ms=now,
        style=StyleFingerprint(
            warmth=0.7,
            curiosity=0.8,
            humor=0.4,
            sensitivity=0.65,
            assertiveness=0.55,
            playfulness=0.45,
            resilience=0.75,
            ts_ms=now,
        ),
    )
    proposed = _build_identity_candidate(
        run_id=run_id,
        previous=previous,
        release_suffix="v2",
        created_at_ms=now + 1,
    )

    return previous, proposed


def _build_identity_candidate(
    *,
    run_id: str,
    previous: IdentitySnapshot,
    release_suffix: str,
    created_at_ms: int | None = None,
) -> IdentitySnapshot:
    now = max(
        int(created_at_ms or time.time() * 1000),
        int(previous.created_at_ms) + 1,
    )
    proposed = _build_identity_snapshot(
        run_id=run_id,
        release_suffix=release_suffix,
        previous_release_ref=previous.release_ref,
        created_at_ms=now,
        style=StyleFingerprint(
            warmth=0.88,
            curiosity=0.45,
            humor=0.82,
            sensitivity=0.82,
            assertiveness=0.55,
            playfulness=0.82,
            resilience=0.65,
            ts_ms=now,
        ),
    )
    if (
        _active_identity_release_directive({"snapshot": previous.to_dict()})
        == _active_identity_release_directive({"snapshot": proposed.to_dict()})
    ):
        raise RuntimeError("NE06_IDENTITY_DIRECTIVE_NOT_DISTINGUISHABLE")
    return proposed


def _build_identity_snapshot(
    *,
    run_id: str,
    release_suffix: str,
    previous_release_ref: str | None,
    created_at_ms: int,
    style: StyleFingerprint,
):
    release_ref = f"release://natsume/{run_id}/{release_suffix}"
    self_model = {
        "recent_mood_trend": "平稳",
        "emotional_stability": 0.78,
        "dominant_recent_emotion": "CURIOUS",
        "bond_growth_perception": "稳定",
        "shared_memory_count": 0,
        "last_deep_talk_days_ago": 0,
        "personality_changes": [],
        "growth_milestones": [],
        "self_identity": "七海",
        "self_values": ["真诚"],
        "self_boundaries": ["不操控"],
        "active_intentions": [],
        "unresolved_tensions": [],
        "agency_score": 0.72,
        "self_continuity_score": 0.86,
        "self_update_count": 0,
        "event_count": 0,
        "self_directive": {},
    }
    bond = {
        "schema_version": "Natsume.bond.projection.v1",
        "projection_id": f"acceptance-{run_id}",
        "person_id": f"synthetic-{run_id}",
        "status": "AVAILABLE",
        "state_available": True,
        "compatibility": {
            "current_stage": "FRIEND",
            "total_score": 0,
            "event_count": 0,
            "trend": "STABLE",
            "capability_hint": "",
            "summary": {},
        },
        "relationship": {
            "vector": {
                "trust": 0.5,
                "affinity": 0.5,
                "safety": 0.7,
                "novelty": 0.3,
                "dependency": 0.0,
                "repair_confidence": 0.5,
                "rupture_load": 0.0,
                "repair_progress": 0.0,
                "bond_stage": "friend",
            },
            "rift": {
                "active": False,
                "rift_level": 0.0,
                "repair_progress": 0.0,
                "rift_cause": "",
                "repair_hint": "",
            },
            "repair": {
                "social_repair_progress": 0.0,
                "rupture_load": 0.0,
                "repair_hint": "",
            },
            "texture": {"style_hint": ""},
            "boundary": {
                "status": "NONE",
                "explicit_user_boundary": False,
                "strength": 0.0,
                "expression_constraint": "NONE",
                "reason": "",
            },
        },
        "evidence_refs": [],
    }
    boundaries = {
        "proactive_enabled": True,
        "requires_explicit_consent": True,
        "quiet_hours_enforced": True,
        "max_daily_initiations": 3,
        "minimum_interval_minutes": 120,
        "allowed_channels": ["LOCAL_UI"],
    }
    startup = build_startup_snapshot(
        {
            "request_id": f"boot-{run_id}-{release_suffix}",
            "summary": {"boot_status": "READY"},
        },
        migration_guard_report={},
        ts_ms=created_at_ms,
    )

    def source(name: str, payload: Any) -> IdentitySourceRef:
        return IdentitySourceRef.from_payload(
            ref=f"state://ne06-live/{run_id}/{release_suffix}/{name}",
            payload=payload,
        )

    return build_identity_snapshot(
        repo_root=ROOT,
        release_ref=release_ref,
        previous_release_ref=previous_release_ref,
        created_at_ms=created_at_ms,
        self_model_snapshot=self_model,
        self_model_source=source("self-model", self_model),
        bond_projection=bond,
        bond_source=source("bond", bond),
        memory_refs=(),
        proactive_boundaries=boundaries,
        proactive_boundary_source=source("proactive-boundary", boundaries),
        style_fingerprint=style,
        style_source=source("style", style.as_dict()),
        startup_snapshot=startup,
        startup_ref=f"startup://{startup.request_id}/{startup.ts_ms}",
        release_manifest={"release": release_ref},
        release_manifest_ref=(
            f"release-manifest://{run_id}/{release_suffix}"
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
