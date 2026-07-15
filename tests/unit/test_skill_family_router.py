from __future__ import annotations

from types import SimpleNamespace

from host_brain.executive_runtime.skill_routing import build_skill_route_decision


class _FakeSkillRegistry:
    def __init__(self, *, family: str = "memory", risk_level: int = 2) -> None:
        self._meta = SimpleNamespace(
            family=family,
            risk_level=risk_level,
            capability_tags=("episodic", "recall"),
        )

    def get(self, _skill_id: str):  # noqa: ANN001
        return self._meta


def test_build_skill_route_decision_for_skill_uses_registry_meta() -> None:
    decision = build_skill_route_decision(
        route_type="skill",
        target_id="memory_store",
        reason_code="keyword:remember",
        skill_registry=_FakeSkillRegistry(family="memory", risk_level=1),
    )

    assert decision.route_type == "skill"
    assert decision.skill_family == "memory"
    assert decision.skill_id == "memory_store"
    assert decision.risk_level == 1
    assert "episodic" in decision.capability_tags


def test_build_skill_route_decision_for_codex_skill_maps_code_family() -> None:
    decision = build_skill_route_decision(
        route_type="skill",
        target_id="agent_worker",
        reason_code="keyword:debug",
        skill_registry=None,
    )

    assert decision.route_type == "skill"
    assert decision.skill_family == "code"
    assert decision.executor_target == "agent_worker"
    assert decision.skill_id == "agent_worker"
    assert decision.requires_model_profile is True


def test_build_skill_route_decision_for_delegate_maps_intent_family() -> None:
    decision = build_skill_route_decision(
        route_type="delegate",
        target_id="intent_agent",
        reason_code="keyword:plan",
        skill_registry=None,
    )

    assert decision.route_type == "delegate"
    assert decision.skill_family == "reasoning"
    assert decision.executor_target == "intent_agent"
    assert decision.requires_model_profile is True


def test_build_skill_route_decision_for_chat_defaults_social() -> None:
    decision = build_skill_route_decision(
        route_type="chat",
        target_id="",
        reason_code="default_chat",
    )

    assert decision.route_type == "chat"
    assert decision.skill_family == "social"
    assert decision.requires_model_profile is False


def test_build_skill_route_decision_for_gap_defaults_executive() -> None:
    decision = build_skill_route_decision(
        route_type="gap",
        target_id="runtime_cap_001",
        reason_code="capability_gap:ACTION_INTENT_FALLBACK_CHAT",
    )

    assert decision.route_type == "gap"
    assert decision.skill_family == "executive"
    assert decision.requires_model_profile is True

