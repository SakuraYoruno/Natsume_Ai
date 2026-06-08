from __future__ import annotations

from scripts.runtime.run_life_memory_lifecycle_scenario_batch import run_lifecycle_scenario_batch


def test_life_memory_lifecycle_scenario_batch_passes() -> None:
    report = run_lifecycle_scenario_batch()

    assert report["success"] is True
    assert report["failed_count"] == 0
    assert report["passed_count"] == report["case_count"] == 8
    case_ids = {item["case_id"] for item in report["cases"]}
    assert {
        "forgetting_demotes_low_value_without_erasing_preference",
        "semantic_anchor_survives_forgetting",
        "sleep_replay_compacts_scene_without_swallowing_preference",
        "retention_prunes_expired_gone_and_refreshes_indexes",
        "gone_episode_hidden_from_default_recall",
        "sleep_replay_distills_failure_not_delivery_noise",
        "sleep_replay_distills_relationship_feedback",
        "policy_backfill_classifies_memory_management_lanes",
    }.issubset(case_ids)
