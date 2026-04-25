from __future__ import annotations

import json

from scripts.runtime.generate_bio_ops_owner_review_demo import generate_demo_run


def test_generate_bio_ops_owner_review_demo_run(tmp_path) -> None:
    manifest = generate_demo_run(output_root=tmp_path, run_id="demo-run")

    run_dir = tmp_path / "demo-run"
    assert manifest["ok"] is True
    assert manifest["run_id"] == "demo-run"
    assert (run_dir / "bio_ops_daily_full.json").exists()
    assert (run_dir / "bio_ops_task_artifacts" / "habit_compiler" / "demo_habit_compiler" / "adoption_candidate.json").exists()
    assert (run_dir / "bio_ops_task_artifacts" / "habit_compiler" / "demo_habit_compiler" / "adoption_decision.json").exists()

    owner_import = run_dir / "owner_inbox" / "social_brain_agent" / "demo_habit_candidate" / "owner_import_candidate.json"
    owner_packet = run_dir / "owner_inbox" / "social_brain_agent" / "owner_intake_packet.json"
    owner_queue_item = (
        run_dir
        / "owner_queues"
        / "social_brain_agent"
        / "social_brain_candidate_queue"
        / "demo_habit_candidate"
        / "queue_item.json"
    )
    owner_state = (
        run_dir
        / "owner_state"
        / "social_brain_agent"
        / "social_brain_candidate_queue"
        / "demo_habit_candidate"
        / "owner_candidate_state.json"
    )
    assert owner_import.exists()
    assert owner_packet.exists()
    assert owner_queue_item.exists()
    assert owner_state.exists()

    packet = json.loads(owner_packet.read_text(encoding="utf-8"))
    assert packet["write_policy"] == "owner_intake_packet_only"
    assert packet["direct_long_term_write"] is False
    assert packet["import_candidates"][0]["owner_channel"]["channel_id"] == "social_brain_candidate_queue"
    queue_item = json.loads(owner_queue_item.read_text(encoding="utf-8"))
    assert queue_item["write_policy"] == "owner_queue_candidate_only"
    assert queue_item["durable_write_performed"] is False
    state = json.loads(owner_state.read_text(encoding="utf-8"))
    assert state["write_policy"] == "owner_durable_candidate_only"
    assert state["direct_long_term_write"] is False
    assert state["durable_memory_write_performed"] is False
    assert manifest["owner_inbox"]["materialized_count"] == 1
    assert manifest["intake_packets"]["packet_count"] == 1
    assert manifest["owner_consumer"]["queued_count"] == 1
    assert manifest["owner_queues"]["status_counts"]["QUEUED_FOR_OWNER"] == 1
    assert manifest["owner_state_import"]["imported_count"] == 1
    assert manifest["owner_candidate_state"]["status_counts"]["IMPORTED_FOR_OWNER_REVIEW"] == 1
    assert manifest["owner_state_review"]["status"] == "OWNER_STATE_ACCEPTED"
    assert manifest["owner_runtime_hints"]["count"] == 1
    assert manifest["owner_hint_runtime_trace"]["owner_behavior_overlay"]["applied"] is True
