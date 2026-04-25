from __future__ import annotations

import json

from host_brain.bio_ops.workflow import BIO_OPS_WORKFLOW_WRITE_POLICY, run_bio_ops_automation_workflow
from scripts.runtime.run_bio_ops_workflow import main as run_bio_ops_workflow_main


def test_bio_ops_workflow_daily_only_keeps_owner_candidates_pending(tmp_path) -> None:
    manifest = run_bio_ops_automation_workflow(
        output_root=tmp_path,
        run_id="workflow-daily",
        inputs={"evidence_refs": ["pytest:workflow"], "message": "继续"},
        seed_demo_owner_decision=False,
    )

    run_dir = tmp_path / "workflow-daily"
    assert manifest["ok"] is True
    assert manifest["status"] == "AUDITED"
    assert manifest["write_policy"] == BIO_OPS_WORKFLOW_WRITE_POLICY
    assert manifest["direct_long_term_write"] is False
    assert manifest["durable_write_performed"] is False
    assert manifest["owner_candidate_state_write_performed"] is False
    assert manifest["counts"]["pending_owner_adoptions"] >= 1
    assert manifest["counts"]["queued_owner_items"] == 0
    assert manifest["counts"]["imported_owner_candidates"] == 0
    assert manifest["counts"]["owner_candidate_state_items"] == 0
    assert manifest["demo_owner_decision_seed"]["enabled"] is False
    assert (run_dir / "bio_ops_workflow_manifest.json").exists()
    assert (run_dir / "bio_ops_daily_full.json").exists()


def test_bio_ops_workflow_smoke_seed_queues_owner_item_without_durable_write(tmp_path) -> None:
    manifest = run_bio_ops_automation_workflow(
        output_root=tmp_path,
        run_id="workflow-smoke",
        inputs={"evidence_refs": ["pytest:workflow-smoke"], "message": "好的"},
        seed_demo_owner_decision=True,
    )

    run_dir = tmp_path / "workflow-smoke"
    queue_item = (
        run_dir
        / "owner_queues"
        / "social_brain_agent"
        / "social_brain_candidate_queue"
        / "workflow_demo_habit_candidate"
        / "queue_item.json"
    )
    owner_state = (
        run_dir
        / "owner_state"
        / "social_brain_agent"
        / "social_brain_candidate_queue"
        / "workflow_demo_habit_candidate"
        / "owner_candidate_state.json"
    )
    assert manifest["ok"] is True
    assert manifest["status"] == "QUEUED"
    assert manifest["demo_owner_decision_seed"]["enabled"] is True
    assert manifest["counts"]["queued_owner_items"] == 1
    assert manifest["counts"]["imported_owner_candidates"] == 1
    assert manifest["counts"]["owner_candidate_state_items"] == 1
    assert manifest["owner_handoff"]["consumer"]["queued_count"] == 1
    assert manifest["owner_handoff"]["durable_import"]["imported_count"] == 1
    assert manifest["owner_candidate_state_write_performed"] is True
    assert manifest["durable_write_performed"] is False
    assert queue_item.exists()
    assert owner_state.exists()

    payload = json.loads(queue_item.read_text(encoding="utf-8"))
    assert payload["direct_long_term_write"] is False
    assert payload["durable_write_performed"] is False
    assert payload["write_policy"] == "owner_queue_candidate_only"

    state_payload = json.loads(owner_state.read_text(encoding="utf-8"))
    assert state_payload["direct_long_term_write"] is False
    assert state_payload["durable_memory_write_performed"] is False
    assert state_payload["write_policy"] == "owner_durable_candidate_only"


def test_run_bio_ops_workflow_cli_writes_manifest(tmp_path, capsys) -> None:
    exit_code = run_bio_ops_workflow_main(
        [
            "--output-root",
            str(tmp_path),
            "--run-id",
            "workflow-cli",
            "--evidence",
            "pytest:workflow-cli",
            "--seed-demo-owner-decision",
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["run_id"] == "workflow-cli"
    assert payload["counts"]["queued_owner_items"] == 1
    assert payload["counts"]["imported_owner_candidates"] == 1
    assert (tmp_path / "workflow-cli" / "bio_ops_workflow_manifest.json").exists()
