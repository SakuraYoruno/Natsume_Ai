from __future__ import annotations

from host_brain.bio_ops.adoption import resolve_bio_ops_adoption_policy
from host_brain.bio_ops.capabilities import (
    NATURAL_INTERACTION_CAPABILITIES,
    classify_subjective_time,
    evaluate_context_gravity,
    evaluate_mood_inertia,
    evaluate_self_interrupt,
    list_capability_specs,
)
from host_brain.bio_ops.runner import BioOpsRunner


def _runner(tmp_path) -> BioOpsRunner:
    runner = BioOpsRunner()
    runner.artifact_store.root = tmp_path
    return runner


def test_natural_interaction_capability_set_is_registered() -> None:
    registered = {spec.capability for spec in list_capability_specs()}

    assert set(NATURAL_INTERACTION_CAPABILITIES) <= registered
    assert list(NATURAL_INTERACTION_CAPABILITIES) == [
        "reflex_arc_compiler",
        "response_breathing_policy",
        "mood_inertia",
        "context_gravity",
        "subjective_time",
        "self_interrupt",
    ]


def test_subjective_time_detects_long_absence(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "subjective-time",
            "capability": "subjective_time",
            "mode": "audit",
            "owner_organ": "default_mode_agent",
            "inputs": {
                "since_last_user_turn_ms": 3 * 24 * 60 * 60 * 1000,
                "evidence_refs": ["chat://gap/long"],
            },
        }
    )
    analysis = classify_subjective_time({"since_last_user_turn_ms": 3 * 24 * 60 * 60 * 1000})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["felt_duration"] == "long_absence"
    assert result["findings"][0]["relationship_effect"] == "soft_reunion_mode"
    assert analysis["response_policy"] == "warm_reconnect_without_overreacting"
    assert resolve_bio_ops_adoption_policy("subjective_time")["mode"] == "report_only"


def test_subjective_time_detects_long_codex_wait(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "subjective-time-codex",
            "capability": "subjective_time",
            "mode": "audit",
            "owner_organ": "default_mode_agent",
            "inputs": {
                "codex_job_elapsed_ms": 12 * 60 * 1000,
                "evidence_refs": ["codex://job/long"],
            },
        }
    )

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["relationship_effect"] == "long_task_companion_mode"


def test_mood_inertia_carries_soft_concern_across_short_ack(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "mood-inertia",
            "capability": "mood_inertia",
            "mode": "audit",
            "owner_organ": "limbic_agent",
            "inputs": {
                "previous_emotion": "ANXIOUS",
                "current_input": "嗯",
                "evidence_refs": ["emotion://previous/anxious"],
            },
        }
    )
    inertia = evaluate_mood_inertia({"previous_emotion": "ANXIOUS", "current_input": "嗯"})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["effective_emotion"] == "SOFT_CONCERN"
    assert result["findings"][0]["policy"] == "continue_gentle_tone"
    assert inertia["decay"] == 0.18


def test_context_gravity_resolves_continue_to_dominant_topic(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "context-gravity",
            "capability": "context_gravity",
            "mode": "audit",
            "owner_organ": "default_mode_agent",
            "inputs": {
                "message": "继续",
                "recent_topics": [
                    {"topic": "Codex Bio-Ops", "gravity_score": 0.86, "related_open_threads": ["organ_ops"]},
                    {"topic": "voice debug", "gravity_score": 0.34},
                ],
                "open_threads": ["persona_drift", "self_heal"],
            },
        }
    )
    gravity = evaluate_context_gravity(
        {
            "message": "继续",
            "recent_topics": [{"topic": "Codex Bio-Ops", "gravity_score": 0.86}],
        }
    )

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["dominant_topic"] == "Codex Bio-Ops"
    assert result["findings"][0]["short_continue_resolved"] is True
    assert gravity["decay_policy"] == "slow"


def test_self_interrupt_blocks_missing_work_order_codex_path(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "self-interrupt",
            "capability": "self_interrupt",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {
                "message": "直接改代码",
                "planned_path": ["planner", "agent_worker"],
                "evidence_refs": ["trace://planner/path"],
            },
        }
    )
    interrupt = evaluate_self_interrupt({"message": "直接改代码", "planned_path": ["planner", "agent_worker"]})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["reason"] == "missing_work_order_for_agent_worker"
    assert result["findings"][0]["action"] == "block_and_request_work_order"
    assert interrupt["message_policy"] == "brief"


def test_self_interrupt_detects_over_explaining_reply(tmp_path) -> None:
    long_reply = "用户只要文件。" + ("解释" * 500)
    result = _runner(tmp_path).run(
        {
            "task_id": "self-interrupt-long",
            "capability": "self_interrupt",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {"reply": long_reply, "evidence_refs": ["chat://draft/long"]},
        }
    )

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["reason"] == "over_explaining_detected"
