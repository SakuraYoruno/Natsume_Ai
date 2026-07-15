from __future__ import annotations

from copy import deepcopy

import pytest

from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.orchestration.plan_intent_search import (
    PLAN_INTENT_GRAPH_INVALID,
    PLAN_INTENT_SEARCH_INVALID,
    PLAN_INTENT_SENSITIVE_FIELD,
    PlanIntentSearchError,
    build_plan_intent_search,
)
from host_brain.orchestration.planning_graph_compiler import PlanningGraphCompiler


def _approved_plan(*steps: dict) -> tuple[dict, dict]:
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent-gr04-plan-search",
        "request_id": "request-gr04-plan-search",
        "ts_ms": 1_000,
        "session_id": "session-gr04-plan-search",
        "seq": 7,
        "person_id": "person-gr04-plan-search",
        "principal_id": "person-gr04-plan-search",
        "type": "query",
        "action": "exec_intent",
        "route": "plan",
        "target": "host",
        "target_id": "planning_search",
        "task": "execute the approved multi-node plan",
        "args": {"task": "execute the approved multi-node plan"},
        "planned_steps": [dict(step) for step in steps],
        "nonce": "nonce-gr04-plan-search",
    }
    binding = build_governed_action_binding(
        intent_envelope=intent,
        approval_token_ref="approval-secret-not-copied-to-candidates",
        risk_level=3,
        issued_at_ms=1_000,
        expires_at_ms=61_000,
    )
    governor = {
        "request_id": intent["request_id"],
        "intent_id": intent["id"],
        "decision": "APPROVE",
        "reason_code": "APPROVED",
        "approved_action_binding": binding,
        "ts_ms": 1_000,
    }
    return intent, governor


def _step(
    candidate_id: str,
    *,
    route: str,
    target_id: str,
    step_index: int,
    dependencies: list[str] | None = None,
    allow_parallel: bool = False,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "step_index": step_index,
        "route": route,
        "target_id": target_id,
        "task_description": f"execute {candidate_id}",
        "dependencies": list(dependencies or []),
        "allow_parallel": allow_parallel,
        "args": {"candidate": candidate_id},
        "risk_level": 2,
    }


def test_approved_plan_search_selects_complete_compiler_ready_mixed_route_path() -> None:
    intent, governor = _approved_plan(
        _step("collect", route="skill", target_id="browser_research", step_index=1),
        _step(
            "analyze",
            route="delegate",
            target_id="intent_agent",
            step_index=2,
            dependencies=["collect"],
        ),
        _step(
            "publish",
            route="chat",
            target_id="surface_reply",
            step_index=3,
            dependencies=["analyze"],
        ),
    )
    before = deepcopy(intent)

    adapter = build_plan_intent_search(
        intent,
        governor_result=governor,
        review_ref="review://gr04/approved-plan",
        business_idempotency_scope="plan-search-gr04",
    )
    result = adapter.run()
    compiled = PlanningGraphCompiler().compile(result, created_at=1_234)

    assert intent == before
    assert [candidate.candidate_id for candidate in result.selected_path] == [
        "collect",
        "analyze",
        "publish",
    ]
    assert result.mcts_used is True
    assert result.side_effects_executed is False
    assert [node.route.split(":", 1)[0] for node in compiled.task_graph.nodes] == [
        "skill",
        "delegate",
        "chat",
    ]
    assert compiled.work_order.business_idempotency_scope == "plan-search-gr04"
    assert compiled.work_order.approval_ref.startswith("governor-decision://sha256/")
    assert compiled.work_order.review_ref == "review://gr04/approved-plan"
    assert compiled.work_order.budget_policy is not None
    for candidate in adapter.candidates:
        payload = candidate.payload
        assert payload["worker_route"] in {"skill", "delegate", "chat"}
        assert payload["target_id"]
        assert payload["approval_ref"] == compiled.work_order.approval_ref
        assert payload["review_ref"] == "review://gr04/approved-plan"
        assert payload["business_idempotency_scope"] == "plan-search-gr04"
        assert payload["candidate_idempotency_key"].startswith(
            "plan-candidate://sha256/"
        )
        assert payload["retry_policy"] == {
            "max_attempts": 1,
            "retry_backoff_ms": 0,
        }


def test_plan_intent_retry_policy_rejects_legacy_backoff_alias() -> None:
    step = _step(
        "legacy-retry",
        route="skill",
        target_id="browser_research",
        step_index=1,
    )
    step["retry_policy"] = {"max_attempts": 2, "backoff_ms": 10}
    intent, governor = _approved_plan(step)

    with pytest.raises(PlanIntentSearchError) as caught:
        build_plan_intent_search(intent, governor_result=governor)

    assert caught.value.reason_code == PLAN_INTENT_GRAPH_INVALID
    assert "unsupported fields: backoff_ms" in caught.value.detail


def test_dependency_expansion_preserves_parallel_roots_and_join_topology() -> None:
    intent, governor = _approved_plan(
        _step(
            "source-a",
            route="skill",
            target_id="browser_research",
            step_index=1,
            allow_parallel=True,
        ),
        _step(
            "source-b",
            route="delegate",
            target_id="intent_agent",
            step_index=2,
            allow_parallel=True,
        ),
        _step(
            "join",
            route="chat",
            target_id="surface_reply",
            step_index=3,
            dependencies=["source-a", "source-b"],
        ),
    )

    adapter = build_plan_intent_search(intent, governor_result=governor)
    initial = adapter._expander.expand(  # noqa: SLF001
        state=adapter.request.initial_state,
        path=(),
        depth=0,
        limit=10,
        context=adapter.request.context,
    )
    result = adapter.run()
    path = [candidate.candidate_id for candidate in result.selected_path]

    assert {candidate.candidate_id for candidate in initial} == {"source-a", "source-b"}
    assert path[-1] == "join"
    assert set(path[:2]) == {"source-a", "source-b"}
    join = next(candidate for candidate in result.selected_path if candidate.candidate_id == "join")
    assert join.payload["depends_on_candidate_ids"] == ["source-a", "source-b"]
    compiled = PlanningGraphCompiler().compile(result)
    assert len(compiled.task_graph.nodes[-1].dependencies) == 2


def test_plan_compiler_freezes_context_in_root_work_order_without_mutating_intent() -> None:
    intent, governor = _approved_plan(
        _step("publish", route="chat", target_id="surface_reply", step_index=1),
    )
    before = deepcopy(intent)
    frozen_context = {
        "retrieval_summary": {"status": "OK"},
        "retrieval_evidence_items": [{"evidence_ref": "memory://plan"}],
        "emotion_snapshot": {"mood_state": "focused"},
    }

    compiled = PlanningGraphCompiler().compile(
        build_plan_intent_search(
            intent,
            governor_result=governor,
            frozen_context=frozen_context,
        ).run()
    )

    assert intent == before
    assert compiled.work_order.input_pack["retrieval_summary"] == {"status": "OK"}
    assert compiled.work_order.input_pack["emotion_snapshot"] == {"mood_state": "focused"}


@pytest.mark.parametrize(
    "steps",
    [
        (
            _step(
                "a",
                route="skill",
                target_id="browser_research",
                step_index=1,
                dependencies=["b"],
            ),
            _step(
                "b",
                route="delegate",
                target_id="intent_agent",
                step_index=2,
                dependencies=["a"],
            ),
        ),
        (
            _step(
                "a",
                route="skill",
                target_id="browser_research",
                step_index=1,
                dependencies=["missing"],
            ),
        ),
    ],
)
def test_cycle_and_unknown_dependencies_fail_closed(steps: tuple[dict, ...]) -> None:
    intent, governor = _approved_plan(*steps)

    with pytest.raises(PlanIntentSearchError) as caught:
        build_plan_intent_search(intent, governor_result=governor)

    assert caught.value.reason_code == PLAN_INTENT_GRAPH_INVALID


def test_adapter_has_zero_execution_side_effects_and_rejects_non_json_actions() -> None:
    effects: list[str] = []

    def execute() -> None:
        effects.append("executed")

    step = _step(
        "unsafe",
        route="skill",
        target_id="browser_research",
        step_index=1,
    )
    step["args"] = {"execute": execute}
    intent, governor = _approved_plan(step)

    with pytest.raises(PlanIntentSearchError) as caught:
        build_plan_intent_search(intent, governor_result=governor)

    assert caught.value.reason_code == PLAN_INTENT_SEARCH_INVALID
    assert effects == []


def test_sensitive_step_fields_are_rejected_before_candidate_creation() -> None:
    step = _step(
        "unsafe",
        route="skill",
        target_id="browser_research",
        step_index=1,
    )
    step["args"] = {"api_key": "must-never-enter-search"}
    intent, governor = _approved_plan(step)

    with pytest.raises(PlanIntentSearchError) as caught:
        build_plan_intent_search(intent, governor_result=governor)

    assert caught.value.reason_code == PLAN_INTENT_SENSITIVE_FIELD


def test_plan_search_is_deterministic_across_repeated_runs() -> None:
    intent, governor = _approved_plan(
        _step(
            "left",
            route="skill",
            target_id="browser_research",
            step_index=1,
            allow_parallel=True,
        ),
        _step(
            "right",
            route="delegate",
            target_id="intent_agent",
            step_index=2,
            allow_parallel=True,
        ),
        _step(
            "finish",
            route="chat",
            target_id="surface_reply",
            step_index=3,
            dependencies=["left", "right"],
        ),
    )
    adapter = build_plan_intent_search(intent, governor_result=governor)
    candidate_snapshot = [candidate.to_dict() for candidate in adapter.candidates]

    first = adapter.run()
    second = adapter.run()

    assert first.trace_digest == second.trace_digest
    assert first.to_dict() == second.to_dict()
    assert [candidate.to_dict() for candidate in adapter.candidates] == candidate_snapshot
