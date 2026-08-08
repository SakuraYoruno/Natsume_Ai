from __future__ import annotations

from pathlib import Path

from scripts.runtime.run_companion_profile_behavior_acceptance import (
    run_acceptance,
)


ROOT = Path(__file__).resolve().parents[2]


def test_companion_profile_changes_host_behavior_and_rolls_back_exactly() -> None:
    receipt = run_acceptance(
        repository_root=ROOT,
        run_id="integration-companion-profile-behavior",
    )

    expected_head = max(
        (ROOT / "host_brain" / "audit" / "migrations").glob("*.sql"),
        key=lambda path: (int(path.name[:4]), path.name),
    )
    assert receipt["status"] == (
        "PASS_REAL_SQLITE_PROFILE_HOST_BEHAVIOR_EXACT_ROLLBACK"
    )
    assert receipt["database_kind"] == "ISOLATED_REAL_SQLITE"
    assert receipt["migration_head"] == expected_head.name
    assert receipt["migration_head_version"] == int(expected_head.name[:4])

    assert receipt["evidence"] == {
        "pre_canary_aftereffect_count": 5,
        "pre_canary_evidence_day_count": 5,
        "canary_interaction_count": 5,
        "confidence_lower_bound": 0.95,
        "boundary_violation_count": 0,
    }
    lifecycle = receipt["lifecycle"]
    assert lifecycle["status"] == "PASS"
    assert lifecycle["candidate_profile_id"] == lifecycle["active_profile_id"]
    assert lifecycle["rollback_profile_id"] == lifecycle["baseline_profile_id"]
    assert lifecycle["rollback_restored_exact_predecessor"] is True

    behavior = receipt["behavior"]
    assert behavior["status"] == "PASS_HOST_PROTOCOL_ONLY"
    assert all(behavior["changed_during_canary"].values())
    assert all(behavior["preserved_after_activation"].values())
    assert all(behavior["restored_after_rollback"].values())
    assert behavior["model_generation_executed"] is False
    assert behavior["physical_edge_effect_executed"] is False

    durable = receipt["durable_state"]
    assert durable["integrity_check"] == "ok"
    assert durable["event_count"] == 8
    assert durable["event_kinds"] == [
        "CANARY_STARTED",
        "CANARY_INTERACTION",
        "CANARY_INTERACTION",
        "CANARY_INTERACTION",
        "CANARY_INTERACTION",
        "CANARY_INTERACTION",
        "ACTIVATED",
        "ROLLED_BACK",
    ]
    assert durable["immutable_guard_rejected_update"] is True
    assert durable["restart_snapshot_equal"] is True
    assert receipt["external_side_effects"] == 0
    assert receipt["private_or_user_data_used"] is False
    assert receipt["temporary_database_removed"] is True
