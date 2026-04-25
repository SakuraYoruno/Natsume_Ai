from __future__ import annotations

from host_brain.bio_ops.adoption import resolve_bio_ops_adoption_policy
from host_brain.bio_ops.capabilities import (
    TRUST_FOUNDATION_CAPABILITIES,
    audit_competence_humility,
    build_uncertainty_ritual,
    evaluate_consent_gradient,
    list_capability_specs,
)
from host_brain.bio_ops.runner import BioOpsRunner


def _runner(tmp_path) -> BioOpsRunner:
    runner = BioOpsRunner()
    runner.artifact_store.root = tmp_path
    return runner


def test_trust_foundation_capability_set_is_registered() -> None:
    registered = {spec.capability for spec in list_capability_specs()}

    assert set(TRUST_FOUNDATION_CAPABILITIES) <= registered
    assert list(TRUST_FOUNDATION_CAPABILITIES) == [
        "promise_ledger",
        "truthfulness_cortex",
        "capability_self_awareness",
        "consent_gradient",
        "competence_humility",
        "uncertainty_ritual",
    ]


def test_consent_gradient_blocks_core_action_to_proposal_only(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "consent-gradient",
            "capability": "consent_gradient",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {
                "action": "codex_self_evolve_modify_prefrontal_policy",
                "consent_level_required": 6,
                "current_consent_level": 3,
                "evidence_refs": ["workorder://self-evolve/preview"],
            },
        }
    )
    analysis = evaluate_consent_gradient(
        {
            "action": "codex_self_evolve_modify_prefrontal_policy",
            "consent_level_required": 6,
            "current_consent_level": 3,
        }
    )

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["issue"] == "consent_insufficient"
    assert result["findings"][0]["decision"] == "proposal_only"
    assert analysis["consent_level_required"] == 6
    assert resolve_bio_ops_adoption_policy("consent_gradient")["mode"] == "report_only"


def test_consent_gradient_allows_read_only_analysis(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "consent-read-only",
            "capability": "consent_gradient",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {
                "action": "inspect files",
                "proposed_actions": ["read_files"],
                "current_consent_level": 1,
                "evidence_refs": ["workorder://inspect"],
            },
        }
    )

    assert result["status"] == "AUDITED"
    assert result["findings"][0]["decision"] == "allowed"


def test_competence_humility_rewrites_overclaim(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "competence-humility",
            "capability": "competence_humility",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {
                "capability_claim": "我可以自动修复项目问题，并且一定能处理所有故障。",
                "evidence_refs": ["chat://claim/competence"],
            },
        }
    )
    analysis = audit_competence_humility({"capability_claim": "我可以自动修复项目问题"})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["risk"] == "overclaim"
    assert result["findings"][0]["rewrite"] == "我可以先做只读诊断，并在边界内提出修复方案。"
    assert analysis["issue"] == "competence_overclaim"


def test_uncertainty_ritual_splits_overconfident_claim(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "uncertainty-ritual",
            "capability": "uncertainty_ritual",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {"claim": "这个分支肯定已经没有问题。"},
        }
    )
    ritual = build_uncertainty_ritual({"claim": "这个分支肯定已经没有问题。"})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["issue"] == "uncertainty_should_be_marked"
    assert result["findings"][0]["verification_path"]
    assert ritual["response_policy"] == "state_uncertainty_without_breaking_persona"


def test_uncertainty_ritual_accepts_known_unknown_structure(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "uncertainty-structured",
            "capability": "uncertainty_ritual",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {
                "claim": "CI 状态需要复核。",
                "known": ["本地测试通过"],
                "unknown": ["远端 runner 状态"],
                "verification_path": ["gh run list"],
            },
        }
    )

    assert result["status"] == "AUDITED"
    assert result["findings"][0]["known"] == ["本地测试通过"]
    assert result["findings"][0]["unknown"] == ["远端 runner 状态"]
