from __future__ import annotations

from dataclasses import replace

import pytest

from host_brain.contracts.agent_runtime.budget_policy import BUDGET_POLICY_SCHEMA_VERSION
from host_brain.orchestration.goal_planning import (
    GoalPlanningDecision,
    GoalPlanningRequest,
    GoalPlanningService,
)
from host_brain.orchestration.planning_graph_compiler import (
    PlanningCompilationContext,
    PlanningGraphCompiler,
)
from host_brain.contracts.planning_search.contracts import MCTSMode, PlanningGoal, SearchBudget
from host_brain.planning_search.code_domain import (
    CODE_DOMAIN,
    CODE_SHADOW_MODE,
    CodePatchSpec,
    CodePlanningDomain,
    CodePlanningError,
    CodeSuccessorCatalog,
    CodeTaskSnapshot,
    bind_selected_code_inputs,
)


def _snapshot(*, virtual_model_ready: bool = True) -> CodeTaskSnapshot:
    return CodeTaskSnapshot(
        task_ref="task://code/d16-shadow-repair",
        workspace_snapshot_ref="snapshot://code/d16-baseline",
        workspace_digest="a" * 64,
        objective_ref="objective://code/d16-shadow-repair",
        failing_test_ids=("test://code/d16/repair",),
        target_artifact_digests={"artifact://code/target-a": "b" * 64},
        allowed_path_refs=("pathref://code/allowed-a",),
        forbidden_path_refs=("pathref://code/forbidden-a",),
        max_changed_files=2,
        verification_profile_ref="verification://code/d16-unit",
        patch_catalog=(
            CodePatchSpec(
                patch_id="safe",
                patch_ref="patch://code/safe",
                affected_artifact_refs=("artifact://code/target-a",),
                test_ids=("test://code/d16/repair",),
                estimated_pass_probability=0.92,
                estimated_risk=0.10,
                estimated_cost=0.10,
            ),
            CodePatchSpec(
                patch_id="risky",
                patch_ref="patch://code/risky",
                affected_artifact_refs=("artifact://code/target-a",),
                test_ids=("test://code/d16/repair",),
                estimated_pass_probability=0.54,
                estimated_risk=0.75,
                estimated_cost=0.70,
            ),
        ),
        source_refs=("evidence://code/d16-failure",),
        virtual_model_ready=virtual_model_ready,
    )


def _goal() -> PlanningGoal:
    return PlanningGoal(
        goal_id="goal-code-d16-shadow",
        domain=CODE_DOMAIN,
        objective="Select a shadow-only Code repair path from frozen evidence.",
        success_criteria=("a bounded unit-verification path is selected",),
        frozen_context_refs=(
            "task://code/d16-shadow-repair",
            "snapshot://code/d16-baseline",
            "verification://code/d16-unit",
        ),
    )


def _request(
    snapshot: CodeTaskSnapshot,
    *,
    mcts_mode: MCTSMode = MCTSMode.AUTO,
) -> GoalPlanningRequest:
    return GoalPlanningRequest(
        request_id="request-code-d16-shadow",
        goal=_goal(),
        state=snapshot.to_virtual_state(),
        budget=SearchBudget(
            max_candidates=32,
            max_expansions=16,
            max_transitions=16,
            max_rollouts=8,
            max_depth=3,
            branching_factor=3,
        ),
        context={"planned_steps": ["legacy input must not control successors"]},
        mcts_mode=mcts_mode,
    )


def _context(
    request: GoalPlanningRequest,
    *,
    input_pack: dict[str, object] | None = None,
    run_mode: str = "inspect",
    allowed_actions: tuple[str, ...] = ("generate_report",),
    forbidden_actions: tuple[str, ...] = ("write_files", "network"),
    workspace: dict[str, object] | None = None,
) -> PlanningCompilationContext:
    return PlanningCompilationContext(
        domain=CODE_DOMAIN,
        run_mode=run_mode,
        plan_objective=request.goal.objective,
        allowed_actions=allowed_actions,
        forbidden_actions=forbidden_actions,
        verification_plan={"required": True, "profile": "unit"},
        approval_policy={"required": True, "mode": "governed"},
        review_policy={"review_required": False, "mode": "shadow"},
        budget_policy={
            "schema_version": BUDGET_POLICY_SCHEMA_VERSION,
            "scope": "d16-code-shadow",
            "limits": {
                "token_count": 1_000,
                "tool_call_count": 4,
                "time_ms": 1_000,
                "concurrency": 1,
                "retry_count": 1,
                "model_cost_micros": 1_000,
            },
            "on_exceeded": "BLOCK",
        },
        business_idempotency_scope="d16-code-shadow",
        approval_ref="approval://d16/code",
        review_ref="review://d16/code",
        workspace=workspace or {"strategy": "temp_workspace"},
        input_pack=input_pack or {},
        input_refs=("input://code/d16-shadow",),
        goal=request.goal,
        virtual_state=request.state,
    )


def _decision(snapshot: CodeTaskSnapshot) -> GoalPlanningDecision:
    request = _request(snapshot)
    return GoalPlanningService(
        ports=CodePlanningDomain(snapshot).planning_ports()
    ).search(request)


def test_code_successors_are_dynamic_and_ignore_legacy_planned_steps() -> None:
    snapshot = _snapshot()
    catalog = CodeSuccessorCatalog(snapshot=snapshot)
    root = snapshot.to_virtual_state()

    initial = catalog.expand(
        state=root,
        path=(),
        depth=0,
        limit=8,
        context={"planned_steps": ["pretend-patch"]},
    )
    assert [item.candidate_id for item in initial] == [
        "code.inspect.targeted",
        "code.inspect.broad",
    ]
    assert initial == catalog.expand(
        state=root,
        path=(),
        depth=0,
        limit=8,
        context={"planned_steps": ["a different fixed plan"]},
    )

    inspected = catalog.transition(
        state=root,
        candidate=initial[0],
        path=(initial[0],),
        context={},
    )
    patches = catalog.expand(
        state=inspected,
        path=(initial[0],),
        depth=1,
        limit=8,
        context={},
    )
    assert [item.candidate_id for item in patches] == [
        "code.patch.risky",
        "code.patch.safe",
    ]
    safe_patch = next(item for item in patches if item.candidate_id == "code.patch.safe")
    assert safe_patch.payload["depends_on_candidate_ids"] == [
        "code.inspect.targeted"
    ]

    patched = catalog.transition(
        state=inspected,
        candidate=safe_patch,
        path=(initial[0], safe_patch),
        context={},
    )
    verification = catalog.expand(
        state=patched,
        path=(initial[0], safe_patch),
        depth=2,
        limit=8,
        context={},
    )
    assert [item.candidate_id for item in verification] == [
        "code.verify.unit.safe",
        "code.rollback.safe",
    ]


def test_code_mcts_is_physically_unavailable_without_frozen_model_readiness() -> None:
    snapshot = _snapshot(virtual_model_ready=False)
    decision = GoalPlanningService(
        ports=CodePlanningDomain(snapshot).planning_ports()
    ).search(_request(snapshot, mcts_mode=MCTSMode.ALWAYS))

    assert decision.result.mcts_used is False
    assert [item.candidate_id for item in decision.result.selected_path] == [
        "code.inspect.targeted"
    ]
    assert decision.result.side_effects_executed is False
    assert any(
        event.event == "mcts_skipped"
        and event.details["reason"] == "simulation_ports_not_configured"
        for event in decision.result.trace
    )


def test_code_goal_search_compiles_only_catalog_bound_shadow_inputs() -> None:
    snapshot = _snapshot()
    decision = _decision(snapshot)
    context = bind_selected_code_inputs(
        _context(decision.request),
        snapshot=snapshot,
        decision=decision,
    )
    compiled = PlanningGraphCompiler().compile(
        decision.result,
        created_at=16,
        compilation_context=context,
    )

    assert decision.result.mcts_used is True
    assert [item.candidate_id for item in decision.result.selected_path] == [
        "code.inspect.targeted",
        "code.patch.safe",
        "code.verify.unit.safe",
    ]
    assert compiled.virtual_state_digest == decision.request.state.state_digest
    assert compiled.work_order.run_mode == "inspect"
    assert compiled.work_order.allowed_actions == ("generate_report",)
    assert {"write_files", "network"}.issubset(
        compiled.work_order.forbidden_actions
    )
    assert dict(compiled.work_order.workspace) == {"strategy": "temp_workspace"}
    assert all(node.route == "skill:agent_worker" for node in compiled.task_graph.nodes)
    input_pack = compiled.work_order.input_pack
    assert input_pack["code_planning"]["mode"] == CODE_SHADOW_MODE
    assert input_pack["code_planning"]["selected_candidate_ids"] == [
        item.candidate_id for item in decision.result.selected_path
    ]
    assert set(input_pack["approved_candidate_inputs"]) == {
        item.candidate_id for item in decision.result.selected_path
    }
    for approved in input_pack["approved_candidate_inputs"].values():
        assert approved["args"]["run_mode"] == "inspect"
        assert approved["args"]["workspace_strategy"] == "temp_workspace"
        assert approved["args"]["code_shadow_mode"] is True


def test_code_binding_rejects_root_policy_that_can_escape_shadow_mode() -> None:
    snapshot = _snapshot()
    decision = _decision(snapshot)
    unsafe_contexts = (
        (_context(decision.request, run_mode="execute"), "run_mode=inspect"),
        (
            _context(decision.request, allowed_actions=("read_files",)),
            "allows only generate_report",
        ),
        (
            _context(decision.request, forbidden_actions=("write_files",)),
            "forbid write_files and network",
        ),
        (
            _context(
                decision.request,
                workspace={
                    "strategy": "temp_workspace",
                    "workspace_ref": "workspace://production/main",
                },
            ),
            "empty temp_workspace",
        ),
    )

    for unsafe_context, message in unsafe_contexts:
        with pytest.raises(CodePlanningError, match=message):
            bind_selected_code_inputs(
                unsafe_context,
                snapshot=snapshot,
                decision=decision,
            )


def test_code_input_binding_rejects_forged_candidate_and_static_state_drift() -> None:
    snapshot = _snapshot()
    decision = _decision(snapshot)
    original = decision.result.selected_path[0]
    forged = replace(
        original,
        payload={**original.payload, "objective": "smuggled arbitrary agent prompt"},
    )
    forged_result = replace(
        decision.result,
        selected_candidate=forged,
        selected_path=(forged, *decision.result.selected_path[1:]),
    )
    forged_decision = replace(decision, result=forged_result)

    with pytest.raises(CodePlanningError, match="non-catalog"):
        bind_selected_code_inputs(
            _context(decision.request),
            snapshot=snapshot,
            decision=forged_decision,
        )

    drifted_constraints = decision.request.state.identity_dict()["constraints"]
    drifted_constraints["max_changed_files"] = 9
    drifted = decision.request.state.replace(constraints=drifted_constraints)
    with pytest.raises(CodePlanningError, match="isolated shadow-only"):
        CodeSuccessorCatalog(snapshot=snapshot).expand(
            state=drifted,
            path=(),
            depth=0,
            limit=8,
            context={},
        )


def test_code_snapshot_rejects_file_uri_as_a_disguised_workspace_path() -> None:
    with pytest.raises(CodePlanningError, match="not a path"):
        CodePatchSpec(
            patch_id="unsafe",
            patch_ref="file://C:/Natsume_Ai/secret.patch",
            affected_artifact_refs=("artifact://code/target-a",),
            test_ids=("test://code/d16/repair",),
            estimated_pass_probability=0.5,
            estimated_risk=0.5,
            estimated_cost=0.5,
        )
    with pytest.raises(CodePlanningError, match="objective_ref.*not a path"):
        replace(_snapshot(), objective_ref="C:/Natsume_Ai/secret-objective.txt")
    with pytest.raises(CodePlanningError, match="failing_test_ids.*not a path"):
        replace(
            _snapshot(),
            failing_test_ids=("tests/unit/test_code_boundary.py::test_repair",),
        )


def test_code_snapshot_rejects_non_boolean_and_unbounded_patch_inputs() -> None:
    with pytest.raises(CodePlanningError, match="virtual_model_ready must be boolean"):
        _snapshot(virtual_model_ready="false")  # type: ignore[arg-type]
    with pytest.raises(CodePlanningError, match="requires_lookahead must be boolean"):
        CodePatchSpec(
            patch_id="strict-bool",
            patch_ref="patch://code/strict-bool",
            affected_artifact_refs=("artifact://code/target-a",),
            test_ids=("test://code/d16/repair",),
            estimated_pass_probability=0.5,
            estimated_risk=0.5,
            estimated_cost=0.5,
            requires_lookahead="false",  # type: ignore[arg-type]
        )
    outside_target = CodePatchSpec(
        patch_id="outside-target",
        patch_ref="patch://code/outside-target",
        affected_artifact_refs=("artifact://code/not-frozen",),
        test_ids=("test://code/d16/repair",),
        estimated_pass_probability=0.5,
        estimated_risk=0.5,
        estimated_cost=0.5,
    )
    with pytest.raises(CodePlanningError, match="within frozen targets"):
        replace(_snapshot(), patch_catalog=(outside_target,))


def test_code_binding_requires_goal_refs_root_state_and_no_legacy_intent() -> None:
    snapshot = _snapshot()
    decision = _decision(snapshot)
    with pytest.raises(CodePlanningError, match="legacy intent_draft"):
        bind_selected_code_inputs(
            _context(
                decision.request,
                input_pack={
                    "intent_draft": {
                        "route": "skill",
                        "target_id": "agent_worker",
                        "args": {"smuggled": True},
                    }
                },
            ),
            snapshot=snapshot,
            decision=decision,
        )

    incomplete_goal = replace(
        decision.request.goal,
        frozen_context_refs=(snapshot.task_ref,),
    )
    incomplete_request = replace(decision.request, goal=incomplete_goal)
    incomplete_decision = replace(decision, request=incomplete_request)
    with pytest.raises(CodePlanningError, match="goal/state"):
        bind_selected_code_inputs(
            _context(decision.request),
            snapshot=snapshot,
            decision=incomplete_decision,
        )

    root = snapshot.to_virtual_state()
    catalog = CodeSuccessorCatalog(snapshot=snapshot)
    inspect = catalog.expand(state=root, path=(), depth=0, limit=8, context={})[0]
    inspected = catalog.transition(
        state=root,
        candidate=inspect,
        path=(inspect,),
        context={},
    )
    non_root_request = replace(decision.request, state=inspected)
    non_root_result = replace(
        decision.result,
        state_snapshot=inspected,
        virtual_state_schema=inspected.schema_version,
        virtual_state_digest=inspected.state_digest,
    )
    non_root_decision = GoalPlanningDecision(
        request=non_root_request,
        result=non_root_result,
    )
    with pytest.raises(CodePlanningError, match="fresh root snapshot"):
        bind_selected_code_inputs(
            _context(non_root_request),
            snapshot=snapshot,
            decision=non_root_decision,
            state=inspected,
        )
