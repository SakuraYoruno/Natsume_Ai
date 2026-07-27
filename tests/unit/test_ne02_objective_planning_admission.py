from __future__ import annotations

from copy import deepcopy

from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.governor.schemas import strict_validate_intent
from host_brain.contracts.skill_execution import SkillExecutionPolicy
from host_brain.orchestration.capability_plan_search import (
    build_capability_plan_search,
    is_capability_plan_intent,
)
from host_brain.skills.registry import SkillMeta, SkillRegistry
from host_brain.tribe.thalamus_agent import (
    ThalamusAgent,
    classify_task_intent_by_rules,
)
from host_brain.tribe.tribe_state import TribeStateRegistry


def _handler(_payload, _context):  # noqa: ANN001, ANN201
    return {"status": "ok"}


def _research_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(
        SkillMeta(
            skill_name="research_fast",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=True,
            handler=_handler,
            family="research",
            capability_tags=("research", "evidence"),
            parallelizable=True,
            fallback_policy="strict_fail",
            execution_policy=SkillExecutionPolicy.INLINE_READ_ONLY,
        )
    )
    registry.register(
        SkillMeta(
            skill_name="research_crosscheck",
            clearance="MEDIUM",
            risk_level=2,
            scope="host",
            reversible=True,
            handler=_handler,
            family="research",
            capability_tags=("research", "evidence", "crosscheck"),
            fallback_policy="strict_fail",
            execution_policy=SkillExecutionPolicy.INLINE_READ_ONLY,
        )
    )
    return registry


def _compile_rule_seed(text: str) -> dict:
    seed = classify_task_intent_by_rules(text)
    assert seed is not None
    return ThalamusAgent(
        state_registry=TribeStateRegistry(),
    ).compile_intent_draft(
        request_id="request-ne02-objective",
        session_id="session-ne02-objective",
        person_id="person-ne02-objective",
        user_message=text,
        raw_route_decision=seed,
        ts_ms=1_800_000_000_000,
    )


def test_research_objective_plan_survives_thalamus_governor_and_dynamic_search() -> None:
    intent = _compile_rule_seed("帮我制定这批资料的调研计划")

    assert intent["route"] == "plan"
    assert intent["target_id"] == "planning_search"
    assert intent["reason_code"] == "THALAMUS_RULE_CAPABILITY_PLAN_RESEARCH"
    assert intent["args"]["planning_mode"] == "CAPABILITY_SEARCH_V1"
    assert intent["args"]["planning_capability_tags"] == ["research"]
    assert intent["args"]["planning_max_risk_level"] == 2
    assert is_capability_plan_intent(intent) is True

    envelope, reason_code, lint = strict_validate_intent(intent)
    assert envelope is not None
    assert reason_code is None
    assert lint["args"] == []

    governor = {
        "request_id": "request-ne02-objective",
        "intent_id": intent["id"],
        "decision": "APPROVE",
        "ts_ms": 1_800_000_000_000,
        "approved_action_binding": build_governed_action_binding(
            intent_envelope=intent,
            approval_token_ref="approval://ne02-objective",
            risk_level=2,
            issued_at_ms=1_799_999_990_000,
            expires_at_ms=1_800_000_100_000,
        ),
    }
    before = deepcopy(intent)
    adapter = build_capability_plan_search(
        intent,
        governor_result=governor,
        skill_registry=_research_registry(),
        request_id="request-ne02-objective",
        input_refs=("input://sha256/" + "a" * 64,),
    )

    result = adapter.search(adapter.request)

    assert intent == before
    assert result.mcts_used is True
    assert result.side_effects_executed is False
    assert len(result.selected_path) == 3
    assert result.selected_path[0].candidate_id in {
        "capability.execute.research_fast",
        "capability.execute.research_crosscheck",
    }
    assert result.selected_path[1].candidate_id in {
        "capability.execute.research_fast",
        "capability.execute.research_crosscheck",
    }
    assert result.selected_path[0].candidate_id != result.selected_path[1].candidate_id
    assert result.selected_path[2].candidate_id.startswith("capability.synthesize.")


def test_frontend_objective_plan_uses_the_same_governed_contract() -> None:
    intent = _compile_rule_seed("请给我一个前端可用性审查方案")

    assert intent["route"] == "plan"
    assert intent["target_id"] == "planning_search"
    assert intent["reason_code"] == "THALAMUS_RULE_CAPABILITY_PLAN_FRONTEND"
    assert intent["args"]["planning_capability_tags"] == ["frontend"]
    assert strict_validate_intent(intent)[1] is None


def test_advice_only_plan_does_not_gain_capability_execution_authority() -> None:
    decision = classify_task_intent_by_rules("请给我前端可用性调研方案，不要调用工具")

    assert decision is not None
    assert decision["route"] == "delegate"
    assert decision["target_id"] == "intent_agent"
