from __future__ import annotations

from scripts.runtime.test_digital_life_restart_driver import (
    _host_is_ready,
    restart_continuity_failures,
)


def _snapshot(*, revision: int, episode: str = "episode-1") -> dict[str, object]:
    return {
        "aggregate_person_id": "relationship::one",
        "state_revision": revision,
        "state_version": revision,
        "state_digest": f"digest-{revision}",
        "event_digest": f"event-{revision}",
        "active_episodes": [
            {
                "episode_id": episode,
                "response_receipt_ref": "life-event://response-1",
                "session_id": "session-1",
                "source_kind": "CHAT_RESPONSE",
            }
        ],
        "last_aftereffect_episode_id": "",
        "last_aftereffect_source_receipt_ref": "",
        "repair_required": False,
        "proactive_permission": "UNKNOWN",
    }


def test_restart_continuity_accepts_exact_reopen_and_linked_aftereffect() -> None:
    before = _snapshot(revision=2)
    recovered = dict(before)
    after = {
        **_snapshot(revision=4, episode="episode-2"),
        "last_aftereffect_episode_id": "episode-1",
        "last_aftereffect_source_receipt_ref": "life-event://response-1",
    }

    assert restart_continuity_failures(
        host_pid_before=101,
        host_pid_after=202,
        recovered=recovered,
        before=before,
        after=after,
        target_episode_id="episode-1",
        target_response_receipt_ref="life-event://response-1",
    ) == []


def test_restart_continuity_rejects_pid_state_and_aftereffect_drift() -> None:
    before = _snapshot(revision=2)
    recovered = {**before, "state_digest": "changed"}
    after = {
        **_snapshot(revision=2, episode="episode-2"),
        "last_aftereffect_episode_id": "wrong",
        "last_aftereffect_source_receipt_ref": "life-event://wrong",
    }

    failures = restart_continuity_failures(
        host_pid_before=101,
        host_pid_after=101,
        recovered=recovered,
        before=before,
        after=after,
        target_episode_id="episode-1",
        target_response_receipt_ref="life-event://response-1",
    )

    assert "HOST_PROCESS_NOT_REPLACED" in failures
    assert "RECOVERED_STATE_DIGEST_DRIFT" in failures
    assert "RELATIONSHIP_REVISION_NOT_ADVANCED" in failures
    assert "POST_RESTART_AFTEREFFECT_EPISODE_DRIFT" in failures
    assert "POST_RESTART_AFTEREFFECT_RECEIPT_DRIFT" in failures


def test_host_ready_accepts_only_expected_remote_edge_gap() -> None:
    snapshot = {
        "health": {
            "http_status": 200,
            "payload": {
                "status": "ACCEPTANCE_PENDING_EXTERNAL",
                "details": {
                    "summary": {
                        "required_unavailable": 0,
                        "runtime_component_unavailable": 0,
                    }
                },
            },
        },
        "ready": {
            "http_status": 200,
            "payload": {
                "ready": False,
                "status": "ACCEPTANCE_PENDING_EXTERNAL",
                "summary": {
                    "runtime_component_unavailable": 0,
                    "dependency_summary": {
                        "unavailable": 0,
                        "not_tested_ids": ["edge.rpc.asr", "edge.rpc.tts"],
                    },
                },
            },
        },
    }

    assert _host_is_ready(snapshot) is True


def test_host_ready_rejects_non_edge_or_unavailable_dependency() -> None:
    snapshot = {
        "health": {
            "http_status": 200,
            "payload": {
                "status": "ACCEPTANCE_PENDING_EXTERNAL",
                "details": {
                    "summary": {
                        "required_unavailable": 0,
                        "runtime_component_unavailable": 0,
                    }
                },
            },
        },
        "ready": {
            "http_status": 200,
            "payload": {
                "ready": False,
                "status": "ACCEPTANCE_PENDING_EXTERNAL",
                "summary": {
                    "runtime_component_unavailable": 0,
                    "dependency_summary": {
                        "unavailable": 1,
                        "not_tested_ids": "host.database",
                    },
                },
            },
        },
    }

    assert _host_is_ready(snapshot) is False
