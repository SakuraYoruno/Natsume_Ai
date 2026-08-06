from __future__ import annotations

from pathlib import Path

from scripts.runtime.run_digital_life_scope_privacy_acceptance import (
    run_acceptance,
)


ROOT = Path(__file__).resolve().parents[2]


def test_digital_life_real_sqlite_scope_privacy_and_bounded_replay() -> None:
    receipt = run_acceptance(
        repository_root=ROOT,
        run_id="integration-digital-life-scope-privacy",
        event_count=33,
    )

    expected_head = max(
        (ROOT / "host_brain" / "audit" / "migrations").glob("*.sql"),
        key=lambda path: (int(path.name[:4]), path.name),
    )
    assert receipt["status"] == "PASS_REAL_SQLITE_SCOPE_PRIVACY_BOUNDED_REPLAY"
    assert receipt["database_kind"] == "ISOLATED_REAL_SQLITE"
    assert receipt["migration_head"] == expected_head.name
    assert receipt["migration_head_version"] == int(expected_head.name[:4])

    isolation = receipt["scope_isolation"]
    assert isolation["status"] == "PASS"
    assert isolation["scope_count"] == 4
    assert isolation["distinct_aggregate_count"] == 4
    assert isolation["workspace_count"] == 2
    assert isolation["relationship_subject_count"] == 2
    assert isolation["session_count"] == 2
    assert isolation["scope_violation_count"] == 0
    assert isolation["other_scope_digest_changes_after_primary_sequence"] == 0

    privacy = receipt["privacy"]
    assert privacy == {
        "status": "PASS",
        "raw_modality_omitted": True,
        "direct_raw_command_rejected": True,
        "visual_ttl_ms": 3_000,
        "forbidden_key_occurrences": 0,
        "private_sentinel_occurrences": 0,
        "raw_audio_video_image_persisted": False,
        "user_text_persisted_in_affect_journal": False,
    }

    replay = receipt["bounded_replay"]
    assert replay["status"] == "PASS"
    assert replay["event_sequence_count"] == 33
    assert replay["active_episode_count_before_sequence"] == 4
    assert replay["active_episode_count_after_sequence"] <= 4
    assert replay["component_count_after_sequence"] <= 8
    assert replay["max_episode_evidence_refs"] <= 16
    assert replay["snapshot_bytes_after_sequence"] <= 65_536
    assert replay["restart_digest_equal"] is True
    assert replay["restart_snapshot_equal"] is True

    durable = receipt["durable_state"]
    assert durable["integrity_check"] == "ok"
    assert durable["state_count"] == 4
    assert durable["max_state_bytes"] <= 65_536
    assert durable["max_event_payload_bytes"] <= 262_144
    assert receipt["external_side_effects"] == 0
    assert receipt["private_or_user_data_used"] is False
    assert receipt["temporary_database_removed"] is True
