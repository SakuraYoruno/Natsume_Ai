from __future__ import annotations

import pytest

from host_brain.bio_ops.adoption import ADOPTION_STATUS_PENDING_REVIEW, resolve_bio_ops_adoption_policy
from host_brain.bio_ops.capabilities import (
    ADAPTIVE_REPAIR_CAPABILITIES,
    build_user_mirror_model,
    debug_narrative,
    list_capability_specs,
    mine_latent_preference,
    narrate_self_repair_story,
    plan_body_plasticity,
    plan_social_recovery,
)
from host_brain.bio_ops.runner import BioOpsRunner


def _runner(tmp_path) -> BioOpsRunner:
    runner = BioOpsRunner()
    runner.artifact_store.root = tmp_path
    return runner


def test_adaptive_repair_capability_set_is_registered() -> None:
    registered = {spec.capability for spec in list_capability_specs()}

    assert set(ADAPTIVE_REPAIR_CAPABILITIES) <= registered
    assert list(ADAPTIVE_REPAIR_CAPABILITIES) == [
        "user_mirror_model",
        "body_plasticity",
        "social_recovery",
        "narrative_debugger",
        "latent_preference_miner",
        "self_repair_story",
    ]


def test_user_mirror_model_is_memory_ethics_review_only(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "user-mirror",
            "capability": "user_mirror_model",
            "mode": "audit",
            "owner_organ": "social_brain_agent",
            "inputs": {"feedback": "喜欢 markdown artifact，别客服，直接实施"},
        }
    )
    mirror = build_user_mirror_model({"feedback": "要 markdown artifact，别客服"})

    assert result["status"] == "PROPOSED"
    assert "structured markdown artifacts" in result["findings"][0]["user_mirror"]["prefers"]
    assert result["findings"][0]["memory_ethics_review_required"] is True
    assert mirror["direct_write_allowed"] is False
    assert resolve_bio_ops_adoption_policy("user_mirror_model")["mode"] == "report_only"


def test_body_plasticity_generates_calibration_plan(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "body-plasticity",
            "capability": "body_plasticity",
            "mode": "audit",
            "owner_organ": "somatic_agent",
            "inputs": {"new_component": "Live2D model v2"},
        }
    )
    adaptation = plan_body_plasticity({"new_component": "TTS backend v2"})

    assert result["status"] == "PROPOSED"
    assert "mouth_open_range" in result["findings"][0]["body_adaptation"]["needs_calibration"]
    assert adaptation["body_adaptation"]["owner_required"] == "somatic_agent"


def test_social_recovery_and_narrative_debugger_find_experience_bugs(tmp_path) -> None:
    runner = _runner(tmp_path)

    recovery = runner.run(
        {
            "task_id": "social-recovery",
            "capability": "social_recovery",
            "mode": "audit",
            "owner_organ": "social_brain_agent",
            "inputs": {"feedback": "刚才太客服，出戏了"},
        }
    )
    narrative = runner.run(
        {
            "task_id": "narrative-debugger",
            "capability": "narrative_debugger",
            "mode": "audit",
            "owner_organ": "chat_agent",
            "inputs": {"reply": "尊敬的用户，很抱歉给您带来不便。", "feedback": "太正式"},
        }
    )
    recovery_plan = plan_social_recovery({"feedback": "不像你"})
    narrative_bug = debug_narrative({"reply": "尊敬的用户"})

    assert recovery["findings"][0]["recovery_event"]["mistake_type"] == "persona_break"
    assert recovery["findings"][0]["recovery_event"]["repair_strategy"] == "acknowledge_and_adjust_style"
    assert narrative["status"] == "PROPOSED"
    assert narrative["findings"][0]["narrative_bug"]["type"] == "correct_action_wrong_expression"
    assert recovery_plan["direct_write_allowed"] is False
    assert narrative_bug["direct_write_allowed"] is False


def test_latent_preference_and_self_repair_story_are_owner_candidates(tmp_path) -> None:
    runner = _runner(tmp_path)

    preference = runner.run(
        {
            "task_id": "latent-preference",
            "capability": "latent_preference_miner",
            "mode": "candidate",
            "owner_organ": "social_brain_agent",
            "inputs": {
                "preference_events": ["asked for md artifact", "liked markdown plan"],
                "evidence_refs": ["evd://preference/1"],
            },
        }
    )
    repair_story = runner.run(
        {
            "task_id": "repair-story",
            "capability": "self_repair_story",
            "mode": "candidate",
            "owner_organ": "immune_agent",
            "inputs": {
                "problem": "voice backend invalid argument",
                "diagnosis": "in-process backend failed",
                "repair": "switch to persistent subprocess backend",
                "evidence_refs": ["evd://repair/1"],
            },
        }
    )
    mined = mine_latent_preference({"preference_events": ["asked for md artifact", "liked markdown plan"]})
    story = narrate_self_repair_story({"problem": "tts timeout", "repair": "fallback text"})

    assert preference["status"] == "CANDIDATE"
    assert preference["metadata"]["bio_ops_adoption"]["status"] == ADOPTION_STATUS_PENDING_REVIEW
    assert preference["metadata"]["bio_ops_adoption"]["candidate"]["owner_organ"] == "social_brain_agent"
    assert repair_story["status"] == "CANDIDATE"
    assert repair_story["metadata"]["bio_ops_adoption"]["candidate"]["owner_organ"] == "immune_agent"
    assert mined["preference_candidate"]["write_policy"] == "soft_memory_candidate"
    assert story["repair_story"]["uses"] == ["ImmuneMemory", "Autobiography", "DreamCompiler", "SelfModel"]


@pytest.mark.parametrize("capability", ADAPTIVE_REPAIR_CAPABILITIES)
def test_adaptive_repair_degrades_without_evidence(tmp_path, capability: str) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": f"missing-{capability}",
            "capability": capability,
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {},
        }
    )

    assert result["status"] == "DEGRADED"
    assert result["reason_code"] == "BIO_OPS_MISSING_EVIDENCE"


@pytest.mark.parametrize("capability", ADAPTIVE_REPAIR_CAPABILITIES)
def test_adaptive_repair_blocks_sensitive_targets_and_ownerless_write_candidates(tmp_path, capability: str) -> None:
    runner = _runner(tmp_path)

    sensitive = runner.run(
        {
            "task_id": f"sensitive-{capability}",
            "capability": capability,
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {"target_paths": ["host_brain/canon/core.yaml"], "evidence_refs": ["evt-1"]},
        }
    )
    ownerless = runner.run(
        {
            "task_id": f"ownerless-{capability}",
            "capability": capability,
            "mode": "candidate",
            "inputs": {"evidence_refs": ["evt-1"], "write_candidate": True},
        }
    )

    assert sensitive["status"] == "BLOCKED"
    assert sensitive["reason_code"] == "BIO_OPS_SENSITIVE_TARGET_BLOCKED"
    assert ownerless["status"] == "BLOCKED"
    assert ownerless["reason_code"] == "BIO_OPS_OWNER_REQUIRED"
