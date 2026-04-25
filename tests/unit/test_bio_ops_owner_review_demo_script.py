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
    assert owner_import.exists()
    assert owner_packet.exists()

    packet = json.loads(owner_packet.read_text(encoding="utf-8"))
    assert packet["write_policy"] == "owner_intake_packet_only"
    assert packet["direct_long_term_write"] is False
    assert packet["import_candidates"][0]["owner_channel"]["channel_id"] == "social_brain_candidate_queue"
    assert manifest["owner_inbox"]["materialized_count"] == 1
    assert manifest["intake_packets"]["packet_count"] == 1
