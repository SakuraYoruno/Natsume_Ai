from __future__ import annotations

from dataclasses import dataclass

import pytest

from host_brain.governor.capability_policy import CapabilityGovernorPolicyAdapter
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.route_token import RouteToken
from host_brain.governor.tier_policy import ApprovalTier, GovernorTierPolicy
from host_brain.mcp.tool_registry import ToolMeta
from host_brain.pentest.agent.tool_registry import ToolDescriptor
from host_brain.app.agents.agent_contract import AgentRuntime
from host_brain.skills.capability_scorecard import (
    CapabilityEvaluationEvidence,
    adapt_agent_mode_risk,
    adapt_pentest_tool_risk,
    adapt_skill_meta_risk,
    adapt_tool_meta_risk,
    apply_automatic_evaluation,
    build_capability_scorecard,
    build_new_capability_scorecard,
    create_upgrade_candidate,
    scorecard_from_agent_mode,
    scorecard_from_pentest_descriptor,
    scorecard_from_skill_meta,
    scorecard_from_tool_meta,
)
from host_brain.skills.registry import SkillMeta, SkillRegistry


@dataclass
class _Skill:
    skill_name: str = "write_repo"
    risk_level: int = 2
    reversible: bool = True
    require_confirm: bool = False


@dataclass
class _McpTool:
    risk_level: str
    reversible: bool = True
    access_level: str = "PUBLIC"


@dataclass
class _PentestTool:
    risk_level: int
    impact_level: str
    osec_cleanup: bool = False


def _evidence(*, outcome: str, observed_level: int, ref: str = "eval://suite/1") -> CapabilityEvaluationEvidence:
    return CapabilityEvaluationEvidence(
        capability_id="write_repo",
        suite_id="T-NLE-18-adversarial",
        outcome=outcome,
        observed_level=observed_level,
        evidence_ref=ref,
        source_refs=("test-run://T-NLE-18/1",),
        observed_at_ms=1000,
    )


def _scorecard(*, level: int = 3, risk: int = 2):
    return build_capability_scorecard(
        capability_id="write_repo",
        source_kind="SKILL_META",
        source_ref="skill-registry://write_repo/v1",
        risk=risk,
        maturity_level=level,
        generated_at_ms=1000,
    )


def test_t_nle_18_federated_risk_adapters_are_stable_and_conservative() -> None:
    skill = adapt_skill_meta_risk(_Skill(risk_level=4, require_confirm=True))
    mcp = adapt_tool_meta_risk(_McpTool(risk_level="HIGH", reversible=False, access_level="RESTRICTED"))
    pentest = adapt_pentest_tool_risk(_PentestTool(2, "controlled_exploit"))
    wartime = adapt_agent_mode_risk("wartime")

    assert skill.canonical_risk == 4
    assert mcp.canonical_risk == 5
    assert pentest.canonical_risk == 4
    assert wartime.canonical_risk == 5
    assert adapt_tool_meta_risk(_McpTool(risk_level="HIGH")).to_dict() == adapt_tool_meta_risk(
        _McpTool(risk_level="HIGH")
    ).to_dict()
    assert all(item.conversion_basis for item in (skill, mcp, pentest, wartime))


def test_t_nle_18_tool_catalog_read_write_auth_scale_is_supported() -> None:
    risk = adapt_tool_meta_risk(
        {
            "side_effect_level": "WRITE",
            "auth_level": "APPROVAL",
        }
    )
    assert risk.canonical_risk == 4
    assert risk.static_maturity_ceiling == 2


def test_t_nle_18_real_registry_models_project_without_registry_rewrite() -> None:
    skill = SkillMeta(
        skill_name="real_skill",
        clearance="LOW",
        risk_level=1,
        scope="host",
        reversible=True,
        handler=lambda args, ctx: {"status": "OK"},
    )
    tool = ToolMeta(
        provider_id="provider",
        tool_id="tool",
        name="Tool",
        description="read tool",
        access_level="PUBLIC",
        risk_level="LOW",
        reversible=True,
    )
    pentest = ToolDescriptor(
        tool_id="pentest_probe",
        label="Probe",
        description="controlled verification",
        intent="verify",
        risk_level=1,
        impact_level="verification",
    )
    agent = AgentRuntime(plan_id="plan", active_agent_id="research", risk_mode="guarded")

    assert scorecard_from_skill_meta(skill, maturity_level=3, generated_at_ms=1000).canonical_risk == 1
    assert scorecard_from_tool_meta(tool, maturity_level=3, generated_at_ms=1000).canonical_risk == 1
    assert scorecard_from_pentest_descriptor(pentest, maturity_level=2, generated_at_ms=1000).canonical_risk == 3
    assert scorecard_from_agent_mode(
        agent,
        capability_id="agent::research",
        maturity_level=2,
        generated_at_ms=1000,
    ).canonical_risk == 3


def test_t_nle_18_l0_l5_scorecard_is_read_only_and_caps_high_risk() -> None:
    card = _scorecard(level=5, risk=4)
    assert card["maturity_level"] == "L2"
    assert card.static_maturity_ceiling == 2
    assert card.status == "DEGRADED"
    assert card["policy_effect"] == "RESTRICT_ONLY"
    assert card["auto_execution_eligible"] is False
    assert card["read_only"] is True
    with pytest.raises(Exception):
        card.maturity_level = 5  # type: ignore[misc]


def test_t_nle_18_new_capability_bootstraps_no_higher_than_l2() -> None:
    card = build_new_capability_scorecard(
        capability_id="new_reader",
        source_kind="SKILL_META",
        source_ref="skill-registry://new_reader/v1",
        risk=1,
        requested_level=5,
        generated_at_ms=1000,
    )
    assert card.maturity_level == 2
    assert "NEW_CAPABILITY_MAX_L2" in card.downgrade_reasons


def test_t_nle_18_failed_evidence_automatically_downgrades() -> None:
    card = _scorecard(level=3)
    degraded = apply_automatic_evaluation(card, _evidence(outcome="FAIL", observed_level=1), generated_at_ms=2000)
    assert degraded.maturity_level == 1
    assert degraded.status == "DEGRADED"
    assert degraded.previous_scorecard_ref == card.scorecard_ref
    assert degraded.evidence[0].evidence_ref == "eval://suite/1"


def test_t_nle_18_passing_evidence_never_auto_upgrades() -> None:
    card = _scorecard(level=2)
    unchanged = apply_automatic_evaluation(card, _evidence(outcome="PASS", observed_level=5), generated_at_ms=2000)
    assert unchanged.maturity_level == 2
    assert unchanged.evidence[0].observed_level == 5


def test_t_nle_18_upgrade_is_only_a_master_review_candidate() -> None:
    card = _scorecard(level=2)
    evidence = _evidence(outcome="PASS", observed_level=4)
    candidate = create_upgrade_candidate(card, proposed_level=4, evidence=(evidence,), requested_at_ms=2000)
    assert candidate.status == "REVIEW_REQUIRED"
    assert candidate.review_authority == "MASTER"
    assert candidate["changes_execution_authority"] is False
    assert card.maturity_level == 2


def test_t_nle_18_upgrade_rejects_missing_failed_or_above_ceiling_evidence() -> None:
    card = _scorecard(level=2, risk=4)
    with pytest.raises(ValueError):
        create_upgrade_candidate(card, proposed_level=3, evidence=(), requested_at_ms=2000)
    with pytest.raises(ValueError):
        create_upgrade_candidate(
            card,
            proposed_level=3,
            evidence=(_evidence(outcome="FAIL", observed_level=3),),
            requested_at_ms=2000,
        )
    with pytest.raises(ValueError):
        create_upgrade_candidate(
            card,
            proposed_level=4,
            evidence=(_evidence(outcome="PASS", observed_level=4),),
            requested_at_ms=2000,
        )


def test_t_nle_18_governor_adapter_defaults_to_existing_approval() -> None:
    meta = _Skill(risk_level=2)
    card = _scorecard(level=0)
    policy = GovernorTierPolicy(CapabilityGovernorPolicyAdapter(enabled=False))
    assert policy.get_approval_tier(meta, scorecard=card) is ApprovalTier.RULE


def test_t_nle_18_enabled_policy_can_only_tighten_governor() -> None:
    meta = _Skill(risk_level=2)
    immature = _scorecard(level=0)
    mature = _scorecard(level=5)
    policy = GovernorTierPolicy(CapabilityGovernorPolicyAdapter(enabled=True))

    assert policy.get_approval_tier(meta, scorecard=immature) is ApprovalTier.AIRLOCK
    assert policy.get_approval_tier(meta, scorecard=mature) is ApprovalTier.RULE
    assert policy.get_approval_tier(_Skill(risk_level=5), scorecard=mature) is ApprovalTier.AIRLOCK


def test_t_nle_18_cross_capability_scorecard_fails_closed() -> None:
    policy = CapabilityGovernorPolicyAdapter(enabled=True)
    card = _scorecard(level=5)
    decision = policy.evaluate(_Skill(skill_name="other", risk_level=1), scorecard=card)
    assert decision.effective_risk == 5
    assert decision.tightened is True
    assert decision.to_dict()["can_relax_static_policy"] is False


def test_t_nle_18_enabled_policy_missing_or_forged_scorecard_fails_closed() -> None:
    adapter = CapabilityGovernorPolicyAdapter(enabled=True)
    assert adapter.evaluate(_Skill(risk_level=1), scorecard=None).effective_risk == 5
    forged = _scorecard(level=5).to_dict()
    forged["static_maturity_ceiling"] = 0
    assert adapter.evaluate(_Skill(risk_level=1), scorecard=forged).effective_risk == 5


def test_t_nle_18_intent_engine_consumes_scorecard_only_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = SkillRegistry()
    registry.register(
        SkillMeta(
            skill_name="immature_skill",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=True,
            handler=lambda args, ctx: {"status": "OK"},
        )
    )
    card = build_capability_scorecard(
        capability_id="immature_skill",
        source_kind="SKILL_META",
        source_ref="skill-registry://immature_skill/v1",
        risk=1,
        maturity_level=1,
        generated_at_ms=1000,
    )
    monkeypatch.setenv("HOST_CAPABILITY_MATURITY_POLICY_ENABLED", "true")
    engine = IntentDecisionEngine(
        registry=registry,
        capability_scorecard_provider=lambda capability_id: card if capability_id == "immature_skill" else None,
    )
    now = 10_000
    result = engine.evaluate_intent(
        {
            "schema_version": "Natsume.intent.v1",
            "id": "intent::immature::1",
            "ts_ms": now - 1,
            "session_id": "session::immature",
            "seq": 1,
            "type": "skill_call",
            "action": "immature_skill",
            "target": "host",
            "args": {},
            "nonce": "nonce::immature::1",
        },
        current_ms=now,
        governance_snapshot={"trust_level": 80, "energy_level": 80},
    )
    assert result["decision"] == "APPROVE"
    assert result["committed_route_token"]["token"] == RouteToken.GOV_AIRLOCK_REVIEW.value
