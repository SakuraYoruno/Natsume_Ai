from __future__ import annotations

import json

from host_brain.bio_ops.capabilities import list_capability_specs
from host_brain.bio_ops.daily_cycle import (
    BIO_OPS_DAILY_SCHEDULE_ID,
    BIO_OPS_DAILY_WRITE_POLICY,
    build_bio_ops_daily_schedule,
    run_bio_ops_daily_cycle_artifact_run,
)


def test_bio_ops_daily_cycle_runner_writes_observable_artifacts(tmp_path) -> None:
    manifest = run_bio_ops_daily_cycle_artifact_run(
        output_root=tmp_path,
        run_id="daily-test",
        inputs={
            "evidence_refs": ["pytest:daily"],
            "message": "继续",
            "planned_path": ["retrieval", "chat_llm"],
            "current_hour": 1,
            "user_stress": "high",
        },
    )

    run_dir = tmp_path / "daily-test"
    expected_count = len(list_capability_specs())

    assert manifest["ok"] is True
    assert manifest["run_id"] == "daily-test"
    assert manifest["bio_ops_capability_count"] == expected_count
    assert manifest["direct_long_term_write"] is False
    assert manifest["durable_write_performed"] is False
    assert manifest["write_policy"] == BIO_OPS_DAILY_WRITE_POLICY
    assert manifest["pending_owner_adoptions"] >= 1

    daily_full = json.loads((run_dir / "bio_ops_daily_full.json").read_text(encoding="utf-8"))
    daily_summary = json.loads((run_dir / "bio_ops_daily_summary.json").read_text(encoding="utf-8"))
    schedule_receipt = json.loads((run_dir / "bio_ops_daily_schedule_receipt.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))

    assert daily_full["status"] == "AUDITED"
    assert len(daily_full["results"]) == expected_count
    assert daily_summary["direct_long_term_write"] is False
    assert daily_summary["owner_gated"] is True
    assert schedule_receipt["direct_long_term_write"] is False
    assert schedule_receipt["schedule"]["schedule_id"] == BIO_OPS_DAILY_SCHEDULE_ID
    assert summary["paths"]["daily_full"] == str(run_dir / "bio_ops_daily_full.json")


def test_build_bio_ops_daily_schedule_clamps_time_and_sets_next_run() -> None:
    schedule = build_bio_ops_daily_schedule(hour=99, minute=-5, now_ts=0)

    assert schedule.schedule_id == BIO_OPS_DAILY_SCHEDULE_ID
    assert schedule.capability == "all"
    assert schedule.cadence == "daily@23:00"
    assert schedule.enabled is True
    assert schedule.next_run_ms is not None
