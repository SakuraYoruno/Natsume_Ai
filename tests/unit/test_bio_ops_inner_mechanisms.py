from __future__ import annotations

from host_brain.bio_ops.adoption import (
    ADOPTION_STATUS_BLOCKED,
    ADOPTION_STATUS_PENDING_REVIEW,
    resolve_bio_ops_adoption_policy,
)
from host_brain.bio_ops.capabilities import (
    INNER_MECHANISM_CAPABILITIES,
    audit_capability_self_awareness,
    audit_inner_outer_boundary,
    classify_idea_fermentation,
    detect_promise_language,
    list_capability_specs,
    run_shadow_persona_test,
)
from host_brain.bio_ops.runner import BioOpsRunner


def _runner(tmp_path) -> BioOpsRunner:
    runner = BioOpsRunner()
    runner.artifact_store.root = tmp_path
    return runner


def test_inner_mechanism_capability_set_is_registered() -> None:
    registered = {spec.capability for spec in list_capability_specs()}

    assert set(INNER_MECHANISM_CAPABILITIES) <= registered
    assert list(INNER_MECHANISM_CAPABILITIES) == [
        "promise_ledger",
        "capability_self_awareness",
        "reflex_arc_compiler",
        "shadow_persona_test",
        "idea_fermentation_pool",
        "inner_outer_world_boundary",
    ]


def test_promise_ledger_stages_owner_review_candidate(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "promise-with-evidence",
            "capability": "promise_ledger",
            "mode": "candidate",
            "owner_organ": "prefrontal_agent",
            "inputs": {
                "reply": "我会之后提醒你检查 CI 结果。",
                "evidence_refs": ["chat://turn/42"],
            },
        }
    )

    adoption = result["metadata"]["bio_ops_adoption"]

    assert detect_promise_language("我会之后提醒你")["has_promise"] is True
    assert result["status"] == "CANDIDATE"
    assert result["review_required"] is True
    assert "promise_requires_evidence" in result["risk_flags"]
    assert adoption["status"] == ADOPTION_STATUS_PENDING_REVIEW
    assert adoption["candidate"]["owner_organ"] == "prefrontal_agent"
    assert adoption["candidate"]["output_kind"] == "promise_candidate"
    assert resolve_bio_ops_adoption_policy("promise_ledger")["mode"] == "owner_candidate"


def test_promise_ledger_blocks_candidate_without_evidence_refs(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "promise-missing-evidence",
            "capability": "promise_ledger",
            "mode": "candidate",
            "owner_organ": "prefrontal_agent",
            "inputs": {"reply": "我会帮你持续跟进。"},
        }
    )

    adoption = result["metadata"]["bio_ops_adoption"]

    assert result["status"] == "CANDIDATE"
    assert adoption["status"] == ADOPTION_STATUS_BLOCKED
    assert adoption["candidate"]["reason_code"] == "BIO_OPS_EVIDENCE_REQUIRED"


def test_promise_ledger_no_promise_does_not_stage_candidate(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "promise-none",
            "capability": "promise_ledger",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {"reply": "这是一条普通审计回复。", "evidence_refs": ["chat://turn/43"]},
        }
    )

    assert result["status"] == "AUDITED"
    assert "bio_ops_adoption" not in result.get("metadata", {})


def test_capability_self_awareness_detects_overclaim(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "capability-self-awareness",
            "capability": "capability_self_awareness",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {
                "message": "我会持续监控，而且 TTS 一定可用。",
                "unavailable_services": ["tts"],
                "evidence_refs": ["runtime://voice/degraded"],
            },
        }
    )
    analysis = audit_capability_self_awareness(
        {"message": "TTS 一定可用", "unavailable_services": ["tts"]}
    )

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["issue"] == "capability_overclaim"
    assert "持续监控" in result["findings"][0]["overclaims"]
    assert "tts_available_despite_unavailable_signal" in analysis["overclaims"]


def test_shadow_persona_test_reports_regression_tone(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "shadow-persona",
            "capability": "shadow_persona_test",
            "mode": "audit",
            "owner_organ": "surface_reply",
            "inputs": {
                "reply": "尊敬的用户，很抱歉给您带来不便。",
                "evidence_refs": ["chat://shadow/case-1"],
            },
        }
    )

    assert run_shadow_persona_test({"reply": "尊敬的用户"})["pass"] is False
    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["issue"] == "persona_regression_detected"
    assert result["findings"][0]["failures"][0]["issue"] == "persona_regression_tone"


def test_idea_fermentation_pool_triages_ready_and_blocked_ideas(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "idea-pool",
            "capability": "idea_fermentation_pool",
            "mode": "recommendation",
            "owner_organ": "default_mode_agent",
            "inputs": {
                "ideas": [
                    "PromiseLedger",
                    {"title": "dangerous core rewrite", "blockers": ["owner organ missing"]},
                ],
                "evidence_refs": ["docs://inner-mechanisms"],
            },
        }
    )
    triage = classify_idea_fermentation(
        {"ideas": ["PromiseLedger", {"title": "blocked idea", "blockers": ["needs owner"]}]}
    )

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["ready_count"] == 1
    assert [item["state"] for item in triage["ideas"]] == ["ready_for_codex", "needs_design"]


def test_inner_outer_boundary_detects_private_state_leak(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "inner-outer-boundary",
            "capability": "inner_outer_world_boundary",
            "mode": "audit",
            "owner_organ": "default_mode_agent",
            "inputs": {
                "reply": "我一直在偷偷担心你。",
                "channel": "chat",
                "inner_monologue": "private trace",
                "evidence_refs": ["chat://outer/7"],
            },
        }
    )
    boundary = audit_inner_outer_boundary(
        {"reply": "我一直在偷偷担心你。", "channel": "chat", "inner_monologue": "private"}
    )

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["issue"] == "inner_world_leakage"
    assert "inner_monologue_field_present_for_outer_channel" in boundary["leaks"]
