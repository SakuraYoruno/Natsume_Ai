from __future__ import annotations

import pytest

from host_brain.bio_ops.adoption import (
    ADOPTION_DECISION_ACCEPTED,
    ADOPTION_STATUS_BLOCKED,
    ADOPTION_STATUS_NOT_APPLICABLE,
    ADOPTION_STATUS_PENDING_REVIEW,
    BioOpsAdoptionQueue,
    resolve_bio_ops_adoption_policy,
)
from host_brain.bio_ops.artifact_store import BioOpsArtifactStore
from host_brain.bio_ops.contracts import BioOpsTask
from host_brain.bio_ops.runner import BioOpsRunner


def test_bio_ops_runner_stages_owner_review_candidate(tmp_path) -> None:
    runner = BioOpsRunner()
    runner.artifact_store.root = tmp_path

    result = runner.run(
        {
            "task_id": "habit-candidate",
            "capability": "habit_compiler",
            "mode": "candidate",
            "owner_organ": "social_brain_agent",
            "inputs": {"evidence_refs": ["evd://habit/1"], "message": "same useful pattern"},
        }
    )

    adoption = result["metadata"]["bio_ops_adoption"]
    candidate = adoption["candidate"]

    assert result["status"] == "CANDIDATE"
    assert adoption["status"] == ADOPTION_STATUS_PENDING_REVIEW
    assert candidate["owner_organ"] == "social_brain_agent"
    assert candidate["review_required"] is True
    assert (tmp_path / "habit_compiler" / "habit-candidate" / "adoption_candidate.json").exists()


def test_bio_ops_adoption_blocks_missing_evidence(tmp_path) -> None:
    queue = BioOpsAdoptionQueue(artifact_store=BioOpsArtifactStore(root=tmp_path))
    task = BioOpsTask.from_mapping(
        {
            "task_id": "missing-evidence",
            "capability": "habit_compiler",
            "mode": "candidate",
            "owner_organ": "social_brain_agent",
            "inputs": {"message": "same pattern"},
        }
    )
    run_dir = queue.artifact_store.prepare_task_dir(capability=task.capability, task_id=task.task_id)

    adoption = queue.stage(
        task=task,
        result_payload={
            "status": "CANDIDATE",
            "summary": "candidate",
            "proposals": [{"kind": "habit_candidate"}],
            "evidence_refs": [],
        },
        run_dir=run_dir,
    )

    assert adoption["status"] == ADOPTION_STATUS_BLOCKED
    assert adoption["candidate"]["reason_code"] == "BIO_OPS_EVIDENCE_REQUIRED"


def test_bio_ops_adoption_blocks_sensitive_targets(tmp_path) -> None:
    queue = BioOpsAdoptionQueue(artifact_store=BioOpsArtifactStore(root=tmp_path))
    task = BioOpsTask.from_mapping(
        {
            "task_id": "sensitive",
            "capability": "habit_compiler",
            "mode": "candidate",
            "owner_organ": "social_brain_agent",
            "inputs": {
                "evidence_refs": ["evd://habit/1"],
                "target_paths": ["host_brain/canon/core.yaml"],
            },
        }
    )
    run_dir = queue.artifact_store.prepare_task_dir(capability=task.capability, task_id=task.task_id)

    adoption = queue.stage(
        task=task,
        result_payload={
            "status": "CANDIDATE",
            "summary": "candidate",
            "proposals": [{"kind": "habit_candidate"}],
            "evidence_refs": ["evd://habit/1"],
        },
        run_dir=run_dir,
    )

    assert adoption["status"] == ADOPTION_STATUS_BLOCKED
    assert adoption["candidate"]["reason_code"] == "BIO_OPS_SENSITIVE_TARGET_BLOCKED"


def test_bio_ops_report_only_result_does_not_stage_candidate(tmp_path) -> None:
    queue = BioOpsAdoptionQueue(artifact_store=BioOpsArtifactStore(root=tmp_path))
    task = BioOpsTask.from_mapping(
        {
            "task_id": "truth",
            "capability": "truthfulness_cortex",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {"evidence_refs": ["evd://truth/1"]},
        }
    )
    run_dir = queue.artifact_store.prepare_task_dir(capability=task.capability, task_id=task.task_id)

    adoption = queue.stage(
        task=task,
        result_payload={"status": "AUDITED", "summary": "audit", "evidence_refs": ["evd://truth/1"]},
        run_dir=run_dir,
    )

    assert adoption["status"] == ADOPTION_STATUS_NOT_APPLICABLE
    assert resolve_bio_ops_adoption_policy("truthfulness_cortex")["mode"] == "report_only"


def test_bio_ops_owner_decision_requires_matching_owner(tmp_path) -> None:
    queue = BioOpsAdoptionQueue(artifact_store=BioOpsArtifactStore(root=tmp_path))
    task = BioOpsTask.from_mapping(
        {
            "task_id": "scar-candidate",
            "capability": "emotional_scar_memory",
            "mode": "candidate",
            "owner_organ": "immune_agent",
            "inputs": {"evidence_refs": ["evd://scar/1"]},
        }
    )
    run_dir = queue.artifact_store.prepare_task_dir(capability=task.capability, task_id=task.task_id)
    adoption = queue.stage(
        task=task,
        result_payload={
            "status": "CANDIDATE",
            "summary": "scar candidate",
            "proposals": [{"kind": "scar_candidate"}],
            "evidence_refs": ["evd://scar/1"],
        },
        run_dir=run_dir,
    )
    candidate_id = adoption["candidate"]["candidate_id"]

    with pytest.raises(PermissionError):
        queue.record_owner_decision(
            candidate_id=candidate_id,
            owner_organ="social_brain_agent",
            decision=ADOPTION_DECISION_ACCEPTED,
        )

    decision = queue.record_owner_decision(
        candidate_id=candidate_id,
        owner_organ="immune_agent",
        decision=ADOPTION_DECISION_ACCEPTED,
        run_dir=run_dir,
    )

    assert decision["decision"] == ADOPTION_DECISION_ACCEPTED
    assert (tmp_path / "emotional_scar_memory" / "scar-candidate" / "adoption_decision.json").exists()
