from __future__ import annotations

import pytest

from host_brain.bio_ops.adoption import resolve_bio_ops_adoption_policy
from host_brain.bio_ops.capabilities import (
    PRESENCE_CHANNEL_CAPABILITIES,
    compile_ritual_system,
    design_gentle_degradation,
    list_capability_specs,
    map_attention_topography,
    plan_long_task_companion,
    plan_quiet_presence,
    resolve_channel_soul,
    select_social_mask,
)
from host_brain.bio_ops.runner import BioOpsRunner


def _runner(tmp_path) -> BioOpsRunner:
    runner = BioOpsRunner()
    runner.artifact_store.root = tmp_path
    return runner


def test_presence_channel_capability_set_is_registered() -> None:
    registered = {spec.capability for spec in list_capability_specs()}

    assert set(PRESENCE_CHANNEL_CAPABILITIES) <= registered
    assert list(PRESENCE_CHANNEL_CAPABILITIES) == [
        "channel_soul",
        "long_task_companion_mode",
        "attention_topography",
        "quiet_presence",
        "social_mask_system",
        "ritual_system",
        "gentle_degradation",
    ]


def test_channel_soul_keeps_channel_style_without_persona_write(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "channel-soul",
            "capability": "channel_soul",
            "mode": "audit",
            "owner_organ": "surface_reply",
            "inputs": {"channel": "wechat", "reply": "WorkOrder ResultContract BioOps " * 20},
        }
    )
    channel = resolve_channel_soul({"channel": "wechat", "reply": "WorkOrder " * 80})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["mask"] == "soft_brief"
    assert "too_long_for_channel" in result["findings"][0]["violations"]
    assert channel["direct_write_allowed"] is False
    assert resolve_bio_ops_adoption_policy("channel_soul")["mode"] == "report_only"


def test_long_task_companion_uses_milestone_only_updates(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "long-task-companion",
            "capability": "long_task_companion_mode",
            "mode": "audit",
            "owner_organ": "default_mode_agent",
            "inputs": {
                "long_task": {
                    "task_id": "codex-batch",
                    "status": "running",
                    "elapsed_ms": 20 * 60 * 1000,
                    "updates_sent": 1,
                    "milestones": ["tests passed"],
                }
            },
        }
    )
    companion = plan_long_task_companion({"long_task_status": "failed", "artifact_ref": "artifacts/failure.json"})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["long_task"]["next_update"] == "milestone_update"
    assert "agent_worker" in result["findings"][0]["forbidden_actions"]
    assert companion["long_task"]["next_update"] == "failure_summary_with_artifact"


def test_attention_topography_maps_peaks_and_valleys(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "attention-topography",
            "capability": "attention_topography",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {
                "attention_topics": [
                    {"topic": "Codex BioOps", "height": 0.91},
                    {"topic": "old WSL issue", "height": 0.1},
                ]
            },
        }
    )
    topography = map_attention_topography({"attention_topics": [{"topic": "Live2D", "height": 0.63}]})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["attention_topography"]["peaks"][0]["topic"] == "Codex BioOps"
    assert result["findings"][0]["attention_topography"]["valleys"][0]["topic"] == "old WSL issue"
    assert topography["direct_write_allowed"] is False


def test_quiet_presence_traces_without_interrupting(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "quiet-presence",
            "capability": "quiet_presence",
            "mode": "audit",
            "owner_organ": "default_mode_agent",
            "inputs": {
                "current_hour": 1,
                "user_stress": "high",
                "explicit_help_request": False,
                "since_last_user_turn_ms": 60 * 60 * 1000,
            },
        }
    )
    quiet = plan_quiet_presence({"current_hour": 1, "user_stress": "high"})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["quiet_presence"]["active"] is True
    assert result["findings"][0]["quiet_presence"]["user_visible"] is False
    assert quiet["trace_required"] is True


def test_social_mask_selects_platform_mask(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "social-mask",
            "capability": "social_mask_system",
            "mode": "audit",
            "owner_organ": "surface_reply",
            "inputs": {"channel": "wechat", "reply": "尊敬的用户，我已经为您完成处理。"},
        }
    )
    mask = select_social_mask({"channel": "codex_report", "social_context": "codex"})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["social_mask"] == "wechat_friend_mode"
    assert "persona_mask_regression" in result["findings"][0]["violations"]
    assert mask["social_mask"] == "codex_engineering_mode"


def test_ritual_system_and_gentle_degradation_are_report_only(tmp_path) -> None:
    runner = _runner(tmp_path)

    ritual_result = runner.run(
        {
            "task_id": "nightly-ritual",
            "capability": "ritual_system",
            "mode": "audit",
            "owner_organ": "default_mode_agent",
            "inputs": {"ritual_trigger": "sleep_mode", "ritual_events": ["nightly dream整理"]},
        }
    )
    degrade_result = runner.run(
        {
            "task_id": "gentle-degradation",
            "capability": "gentle_degradation",
            "mode": "audit",
            "owner_organ": "surface_reply",
            "inputs": {"degrade_case": "tts_unavailable", "technical_reason": "Edge TTS timeout"},
        }
    )
    ritual = compile_ritual_system({"ritual_trigger": "major_task_complete"})
    degradation = design_gentle_degradation({"degrade_case": "tts_unavailable"})

    assert ritual_result["status"] == "PROPOSED"
    assert ritual_result["findings"][0]["ritual"]["ritual_id"] == "nightly_glymphatic_ritual"
    assert degrade_result["status"] == "PROPOSED"
    assert degrade_result["findings"][0]["allowed_channels"] == ["text"]
    assert ritual["direct_write_allowed"] is False
    assert degradation["direct_write_allowed"] is False


@pytest.mark.parametrize("capability", PRESENCE_CHANNEL_CAPABILITIES)
def test_presence_channel_degrades_without_evidence(tmp_path, capability: str) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": f"missing-{capability}",
            "capability": capability,
            "mode": "audit",
            "owner_organ": "surface_reply",
            "inputs": {},
        }
    )

    assert result["status"] == "DEGRADED"
    assert result["reason_code"] == "BIO_OPS_MISSING_EVIDENCE"


@pytest.mark.parametrize("capability", PRESENCE_CHANNEL_CAPABILITIES)
def test_presence_channel_blocks_sensitive_targets_and_ownerless_write_candidates(tmp_path, capability: str) -> None:
    runner = _runner(tmp_path)

    sensitive = runner.run(
        {
            "task_id": f"sensitive-{capability}",
            "capability": capability,
            "mode": "audit",
            "owner_organ": "surface_reply",
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
