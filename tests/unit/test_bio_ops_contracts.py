from __future__ import annotations

from host_brain.bio_ops import BioOpsTask, validate_bio_ops_task_safety
from host_brain.bio_ops.artifact_store import BioOpsArtifactStore
from host_brain.bio_ops.contracts import BioOpsResult, BioOpsSchedule


def test_bio_ops_task_contract_roundtrip() -> None:
    task = BioOpsTask.from_mapping(
        {
            "task_id": "bio-1",
            "capability": "truthfulness_cortex",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {"text": "我已经保存了", "evidence_refs": ["evt-1"]},
            "scope": {"target_paths": ["host_brain/app/surface_reply_runtime.py"]},
            "work_order_ref": "wo-1",
        }
    )

    task.validate()

    assert task.to_dict()["capability"] == "truthfulness_cortex"
    assert task.target_paths == ["host_brain/app/surface_reply_runtime.py"]
    assert task.is_write_candidate is False


def test_bio_ops_result_and_schedule_contracts() -> None:
    result = BioOpsResult(
        status="AUDITED",
        task_id="bio-1",
        capability="capability_self_awareness",
        summary="ok",
    )
    schedule = BioOpsSchedule.from_mapping(
        {
            "schedule_id": "sched-1",
            "capability": "capability_self_awareness",
            "cadence": "daily",
        }
    )

    assert result.to_dict()["status"] == "AUDITED"
    assert schedule.to_dict()["enabled"] is True


def test_bio_ops_safety_blocks_sensitive_targets_and_ownerless_candidates() -> None:
    sensitive = BioOpsTask.from_mapping(
        {
            "capability": "habit_compiler",
            "mode": "audit",
            "inputs": {"target_paths": ["host_brain/persona/personality_core.yaml"]},
        }
    )
    ownerless = BioOpsTask.from_mapping(
        {
            "capability": "habit_compiler",
            "mode": "candidate",
            "inputs": {"evidence_refs": ["evt-1"], "write_candidate": True},
        }
    )

    assert validate_bio_ops_task_safety(sensitive)[1] == "BIO_OPS_SENSITIVE_TARGET_BLOCKED"
    assert validate_bio_ops_task_safety(ownerless)[1] == "BIO_OPS_OWNER_REQUIRED"


def test_bio_ops_artifact_store_writes_digest(tmp_path) -> None:
    store = BioOpsArtifactStore(root=tmp_path)
    run_dir = store.prepare_task_dir(capability="truthfulness_cortex", task_id="bio-1")
    artifact = store.write_json(run_dir / "result.json", {"status": "AUDITED"})

    assert artifact.path.endswith("result.json")
    assert len(artifact.digest) == 64
