from __future__ import annotations

from copy import deepcopy

import pytest

from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.orchestration.capability_plan_search import (
    CAPABILITY_PLAN_SEARCH_AUTHORITY_INVALID,
    CAPABILITY_PLAN_SEARCH_INVALID,
    CAPABILITY_PLAN_SEARCH_NO_COMPETITION,
    CapabilityPlanSearchError,
    build_capability_plan_search,
    is_capability_plan_intent,
)
from host_brain.orchestration.planning_graph_compiler import PlanningGraphCompiler
from host_brain.planning_search.capability_domain import CapabilitySuccessorCatalog
from host_brain.planning_search.capability_domain_models import (
    CAPABILITY_PLANNING_MODE,
    CapabilityPlanningError,
    CapabilityPlanningSnapshot,
    PlanningCapability,
)
from host_brain.planning_search.contracts import SearchCandidate
from host_brain.skills.registry import SkillMeta, SkillRegistry


def _handler(_payload, _context):  # noqa: ANN001, ANN201
    return {"status": "ok"}


def _registry(
    *,
    include_second: bool = True,
    include_low_risk_alternative: bool = False,
) -> SkillRegistry:
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
        )
    )
    if include_second:
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
            )
        )
    if include_low_risk_alternative:
        registry.register(
            SkillMeta(
                skill_name="research_safe_crosscheck",
                clearance="LOW",
                risk_level=1,
                scope="host",
                reversible=True,
                handler=_handler,
                family="research",
                capability_tags=("research", "evidence", "crosscheck"),
                parallelizable=True,
                fallback_policy="strict_fail",
            )
        )
    registry.register(
        SkillMeta(
            skill_name="research_confirm_required",
            clearance="HIGH",
            risk_level=3,
            scope="host",
            reversible=True,
            handler=_handler,
            require_confirm=True,
            family="research",
            capability_tags=("research",),
            fallback_policy="strict_fail",
        )
    )
    return registry


def _intent() -> tuple[dict, dict]:
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent-ne02-capability-search",
        "request_id": "request-ne02-capability-search",
        "session_id": "session-ne02-capability-search",
        "seq": 12,
        "person_id": "person-ne02-capability-search",
        "principal_id": "person-ne02-capability-search",
        "type": "query",
        "action": "exec_intent",
        "route": "plan",
        "target": "host",
        "target_id": "planning_search",
        "task": "collect and synthesize bounded research evidence",
        "args": {
            "task": "collect and synthesize bounded research evidence",
            "planning_mode": CAPABILITY_PLANNING_MODE,
            "planning_capability_tags": ["research"],
            "planning_max_risk_level": 2,
        },
        "nonce": "nonce-ne02-capability-search",
    }
    governor = {
        "request_id": intent["request_id"],
        "intent_id": intent["id"],
        "decision": "APPROVE",
        "reason_code": "APPROVED",
        "approved_action_binding": build_governed_action_binding(
            intent_envelope=intent,
            approval_token_ref="approval-not-in-search-candidates",
            risk_level=2,
            issued_at_ms=1_000,
            expires_at_ms=61_000,
        ),
        "ts_ms": 1_000,
    }
    return intent, governor


def test_registry_capability_search_generates_competing_paths_without_planned_steps() -> None:
    intent, governor = _intent()
    before = deepcopy(intent)
    adapter = build_capability_plan_search(
        intent,
        governor_result=governor,
        skill_registry=_registry(),
        input_refs=("input://sha256/" + "a" * 64,),
        frozen_context={"user_message": intent["task"]},
    )

    result = adapter.search(adapter.request)
    context = adapter.compilation_context_for(result)
    compiled = PlanningGraphCompiler().compile(
        result,
        created_at=1_234,
        compilation_context=context,
    )

    assert intent == before
    assert result.mcts_used is True
    assert result.side_effects_executed is False
    assert len(result.selected_path) == 2
    selected_capability_id = result.selected_path[0].payload["capability_id"]
    assert result.selected_path[0].candidate_id == (
        f"capability.execute.{selected_capability_id}"
    )
    assert result.selected_path[1].candidate_id == (
        f"capability.synthesize.{selected_capability_id}"
    )
    assert [node.route.split(":", 1)[0] for node in compiled.task_graph.nodes] == [
        "skill",
        "chat",
    ]
    assert compiled.task_graph.nodes[1].dependencies == (
        compiled.task_graph.nodes[0].node_id,
    )
    assert compiled.work_order.input_pack["capability_planning"]["catalog_ref"] == (
        adapter.snapshot.catalog_ref
    )
    assert "intent_draft" not in compiled.work_order.input_pack
    assert set(compiled.work_order.input_pack["approved_candidate_inputs"]) == {
        item.candidate_id for item in result.selected_path
    }
    assert compiled.work_order.allowed_actions == (
        f"skill:{selected_capability_id}",
        "render_reply",
    )


def test_capability_plan_rejects_a_noncanonical_target_before_search() -> None:
    intent, governor = _intent()
    intent["target_id"] = "arbitrary_target"

    assert is_capability_plan_intent(intent) is False
    with pytest.raises(CapabilityPlanSearchError) as caught:
        build_capability_plan_search(
            intent,
            governor_result=governor,
            skill_registry=_registry(),
            input_refs=("input://sha256/" + "a" * 64,),
        )

    assert caught.value.reason_code == CAPABILITY_PLAN_SEARCH_INVALID


def test_capability_mode_is_explicit_and_refuses_legacy_preplanned_graphs() -> None:
    intent, governor = _intent()
    intent["planned_steps"] = [{"candidate_id": "smuggled"}]

    assert is_capability_plan_intent(intent) is True
    with pytest.raises(CapabilityPlanSearchError) as caught:
        build_capability_plan_search(
            intent,
            governor_result=governor,
            skill_registry=_registry(),
            input_refs=("input://sha256/" + "b" * 64,),
        )

    assert caught.value.reason_code == CAPABILITY_PLAN_SEARCH_INVALID
    assert "planned_steps" in caught.value.detail


def test_registry_requires_two_non_confirming_eligible_capabilities() -> None:
    intent, governor = _intent()

    with pytest.raises(CapabilityPlanSearchError) as caught:
        build_capability_plan_search(
            intent,
            governor_result=governor,
            skill_registry=_registry(include_second=False),
            input_refs=("input://sha256/" + "c" * 64,),
        )

    assert caught.value.reason_code == CAPABILITY_PLAN_SEARCH_NO_COMPETITION


def test_governor_risk_ceiling_tightens_the_frozen_candidate_catalog() -> None:
    intent, governor = _intent()
    intent["args"]["planning_max_risk_level"] = 3
    governor["approved_action_binding"] = build_governed_action_binding(
        intent_envelope=intent,
        approval_token_ref="approval-risk-ceiling",
        risk_level=1,
        issued_at_ms=1_000,
        expires_at_ms=61_000,
    )

    adapter = build_capability_plan_search(
        intent,
        governor_result=governor,
        skill_registry=_registry(include_low_risk_alternative=True),
        input_refs=("input://sha256/" + "f" * 64,),
    )

    assert adapter.snapshot.max_risk_level == 1
    assert len(adapter.snapshot.source_refs) == 1
    assert adapter.snapshot.source_refs[0].startswith("governor-decision://sha256/")
    assert adapter.snapshot.source_refs[0] in adapter.snapshot.to_virtual_state().source_refs
    assert {item.capability_id for item in adapter.snapshot.capabilities} == {
        "research_fast",
        "research_safe_crosscheck",
    }
    assert all(item.risk_level <= 1 for item in adapter.snapshot.capabilities)
    assert dict(adapter.base_context.input_pack["capability_plan_request"]) == {
        "route": "plan",
        "planning_mode": CAPABILITY_PLANNING_MODE,
        "task": intent["task"],
        "requested_tags": ("research",),
        "requested_max_risk_level": 3,
        "governor_max_risk_level": 1,
        "effective_max_risk_level": 1,
    }


def test_capability_plan_rejects_a_missing_or_invalid_governor_risk_ceiling() -> None:
    intent, governor = _intent()
    governor["approved_action_binding"]["risk_level"] = 0

    with pytest.raises(CapabilityPlanSearchError) as caught:
        build_capability_plan_search(
            intent,
            governor_result=governor,
            skill_registry=_registry(),
            input_refs=("input://sha256/" + "1" * 64,),
        )

    assert caught.value.reason_code == CAPABILITY_PLAN_SEARCH_AUTHORITY_INVALID


def test_capability_mode_rejects_non_ascii_requested_tags() -> None:
    intent, governor = _intent()
    intent["args"]["planning_capability_tags"] = ["研究"]
    governor["approved_action_binding"] = build_governed_action_binding(
        intent_envelope=intent,
        approval_token_ref="approval-not-in-search-candidates",
        risk_level=2,
        issued_at_ms=1_000,
        expires_at_ms=61_000,
    )

    with pytest.raises(CapabilityPlanSearchError) as caught:
        build_capability_plan_search(
            intent,
            governor_result=governor,
            skill_registry=_registry(),
            input_refs=("input://sha256/" + "e" * 64,),
        )

    assert caught.value.reason_code == CAPABILITY_PLAN_SEARCH_INVALID


def test_catalog_rejects_forged_capability_candidate_before_compilation() -> None:
    snapshot = CapabilityPlanningSnapshot(
        task_ref="input://sha256/" + "d" * 64,
        objective="collect bounded research evidence",
        requested_tags=("research",),
        max_risk_level=2,
        capabilities=(
            PlanningCapability(
                capability_id="research_fast",
                family="research",
                capability_tags=("research",),
                risk_level=1,
                reversible=True,
                parallelizable=True,
                estimated_cost=0.1,
            ),
            PlanningCapability(
                capability_id="research_crosscheck",
                family="research",
                capability_tags=("research",),
                risk_level=2,
                reversible=True,
                parallelizable=False,
                estimated_cost=0.2,
            ),
        ),
    )
    catalog = CapabilitySuccessorCatalog(snapshot=snapshot)
    state = snapshot.to_virtual_state()
    forged = SearchCandidate(
        candidate_id="capability.execute.research_fast",
        prior_score=99.0,
        payload={"capability_action": "execute", "capability_id": "research_fast"},
    )

    with pytest.raises(CapabilityPlanningError):
        catalog.transition(
            state=state,
            candidate=forged,
            path=(forged,),
            context={},
        )
