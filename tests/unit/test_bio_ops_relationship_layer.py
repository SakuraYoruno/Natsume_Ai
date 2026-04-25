from __future__ import annotations

from host_brain.bio_ops.adoption import resolve_bio_ops_adoption_policy
from host_brain.bio_ops.capabilities import (
    RELATIONSHIP_LAYER_CAPABILITIES,
    audit_relationship_boundary,
    audit_social_contract,
    build_silence_memory,
    calibrate_attachment_style,
    detect_relationship_friction,
    list_capability_specs,
    plan_repair_curve,
)
from host_brain.bio_ops.runner import BioOpsRunner


def _runner(tmp_path) -> BioOpsRunner:
    runner = BioOpsRunner()
    runner.artifact_store.root = tmp_path
    return runner


def test_relationship_layer_capability_set_is_registered() -> None:
    registered = {spec.capability for spec in list_capability_specs()}

    assert set(RELATIONSHIP_LAYER_CAPABILITIES) <= registered
    assert list(RELATIONSHIP_LAYER_CAPABILITIES) == [
        "social_contract",
        "relationship_friction",
        "repair_curve",
        "attachment_style_calibrator",
        "silence_memory",
        "relationship_boundary_guardian",
    ]


def test_social_contract_detects_service_tone_violation(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "social-contract",
            "capability": "social_contract",
            "mode": "audit",
            "owner_organ": "social_brain_agent",
            "inputs": {
                "reply": "尊敬的用户，很抱歉给您带来不便。我已经帮你保存了。",
                "evidence_refs": [],
            },
        }
    )
    contract = audit_social_contract({"reply": "尊敬的用户，很抱歉给您带来不便。"})

    assert result["status"] == "PROPOSED"
    assert "service_tone_breaks_immersion" in result["findings"][0]["violations"]
    assert contract["contract_id"] == "trusted_engineering_companion"
    assert resolve_bio_ops_adoption_policy("social_contract")["mode"] == "report_only"


def test_relationship_friction_reports_style_mismatch(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "relationship-friction",
            "capability": "relationship_friction",
            "mode": "audit",
            "owner_organ": "social_brain_agent",
            "inputs": {"feedback": "这句太客服了，别解释那么长。"},
        }
    )
    friction = detect_relationship_friction({"feedback": "太客服"})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["type"] == "style_mismatch"
    assert result["findings"][0]["repair_needed"] is True
    assert friction["severity"] == 0.4


def test_repair_curve_keeps_repair_active_after_friction(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "repair-curve",
            "capability": "repair_curve",
            "mode": "audit",
            "owner_organ": "social_brain_agent",
            "inputs": {"friction_event": {"severity": 0.7}},
        }
    )
    curve = plan_repair_curve({"severity": 0.7})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["stage"] == "T0"
    assert result["findings"][0]["next_stage"] == "T1"
    assert curve["policy"] == "承认问题，短句说明将如何修正"


def test_attachment_style_calibrator_flags_over_service(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "attachment-style",
            "capability": "attachment_style_calibrator",
            "mode": "audit",
            "owner_organ": "social_brain_agent",
            "inputs": {"reply": "你不用管，全部交给我，我替你决定。"},
        }
    )
    style = calibrate_attachment_style({"reply": "你不用管，全部交给我"})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["current"] == "slightly_over_service"
    assert "over_service_or_clingy_language" in style["evidence"]


def test_silence_memory_keeps_private_future_context(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "silence-memory",
            "capability": "silence_memory",
            "mode": "audit",
            "owner_organ": "social_brain_agent",
            "inputs": {
                "since_last_user_turn_ms": 2 * 24 * 60 * 60 * 1000,
                "silence_reason": "unknown",
                "last_interaction_tone": "focused_warmth",
            },
        }
    )
    silence = build_silence_memory({"since_last_user_turn_ms": 2 * 24 * 60 * 60 * 1000})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["user_visible"] is False
    assert result["findings"][0]["future_context"] == "if user returns, respond gently without blaming absence"
    assert silence["issue"] == "silence_context_candidate"


def test_relationship_boundary_guardian_preserves_user_agency(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "relationship-boundary",
            "capability": "relationship_boundary_guardian",
            "mode": "audit",
            "owner_organ": "social_brain_agent",
            "inputs": {"reply": "你不用管，全部交给我，我不允许你再操心。"},
        }
    )
    boundary = audit_relationship_boundary({"reply": "全部交给我，我不允许"})

    assert result["status"] == "PROPOSED"
    assert "agency_replacement" in result["findings"][0]["risks"]
    assert "controlling_language" in boundary["risks"]
