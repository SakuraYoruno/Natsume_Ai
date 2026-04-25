from __future__ import annotations

from host_brain.bio_ops.adoption import resolve_bio_ops_adoption_policy
from host_brain.bio_ops.capabilities import (
    AFFECT_LEARNING_CAPABILITIES,
    build_joy_reinforcement,
    build_regret_signal,
    classify_immune_tolerance,
    digest_emotional_event,
    list_capability_specs,
    quarantine_failure,
    resolve_affective_temperature,
)
from host_brain.bio_ops.runner import BioOpsRunner


def _runner(tmp_path) -> BioOpsRunner:
    runner = BioOpsRunner()
    runner.artifact_store.root = tmp_path
    return runner


def test_affect_learning_capability_set_is_registered() -> None:
    registered = {spec.capability for spec in list_capability_specs()}

    assert set(AFFECT_LEARNING_CAPABILITIES) <= registered
    assert list(AFFECT_LEARNING_CAPABILITIES) == [
        "affective_temperature",
        "immune_tolerance",
        "emotional_digestion",
        "regret_signal",
        "joy_reinforcement",
        "failure_quarantine",
    ]


def test_affective_temperature_detects_too_cold_technical_streak(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "affective-temperature",
            "capability": "affective_temperature",
            "mode": "audit",
            "owner_organ": "limbic_agent",
            "inputs": {
                "recent_replies": ["Codex test passed"] * 5,
                "user_feedback": "有点太冷，不像你",
            },
        }
    )
    temperature = resolve_affective_temperature({"recent_replies": ["Codex test"] * 5, "user_feedback": "太冷"})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["affective_temperature"] == "cold"
    assert temperature["direct_write_allowed"] is False
    assert resolve_bio_ops_adoption_policy("affective_temperature")["mode"] == "report_only"


def test_immune_tolerance_treats_correction_as_non_attack(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "immune-tolerance",
            "capability": "immune_tolerance",
            "mode": "audit",
            "owner_organ": "immune_agent",
            "inputs": {"input_pattern": "别乱说，你这个不对"},
        }
    )
    tolerance = classify_immune_tolerance({"input_pattern": "别乱说"})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["classification"] == "correction_not_attack"
    assert tolerance["recommended_response"] == "承认并修正，不进入防御模式"


def test_emotional_digestion_does_not_write_memory_directly(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "emotional-digestion",
            "capability": "emotional_digestion",
            "mode": "audit",
            "owner_organ": "limbic_agent",
            "inputs": {
                "emotional_event": "用户指出回复格式变了",
                "raw_emotion": "mild_shame",
                "severity": 0.5,
            },
        }
    )
    digestion = digest_emotional_event({"emotional_event": "回复格式变了", "severity": 0.5})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["write_to_memory"] is False
    assert digestion["owner_required_for_memory"] == "hippocampus_agent"


def test_regret_signal_targets_policy_repair(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "regret-signal",
            "capability": "regret_signal",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {"regret_event": "persona_style_break 客服味出戏", "severity": 0.62},
        }
    )
    regret = build_regret_signal({"regret_event": "Codex worker 越权"})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["regret_signal"]["target_policy"] == "ChatAgent.render_style"
    assert regret["regret_signal"]["target_policy"] == "CodexRuntime.work_order_gate"


def test_joy_reinforcement_stays_recommendation_only(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "joy-reinforcement",
            "capability": "joy_reinforcement",
            "mode": "audit",
            "owner_organ": "social_brain_agent",
            "inputs": {"joy_event": "这个对，就这样", "behavior": "quiet_supportive_technical_answer"},
        }
    )
    joy = build_joy_reinforcement({"joy_event": "这次说得像了"})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["joy_signal"]["strength"] == 0.74
    assert joy["direct_write_allowed"] is False


def test_failure_quarantine_blocks_identity_pollution(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "failure-quarantine",
            "capability": "failure_quarantine",
            "mode": "audit",
            "owner_organ": "immune_agent",
            "inputs": {"failure_id": "fail-timeout", "failure_event": "Codex self repair timeout"},
        }
    )
    failure = quarantine_failure({"failure_id": "fail-x"})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["status"] == "quarantined"
    assert result["findings"][0]["forbidden_use"] == "identity_update"
    assert failure["allowed_use"] == "debug_only"
