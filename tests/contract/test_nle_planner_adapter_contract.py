"""T-NLE-05: one graph revision across planner compatibility adapters."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from host_brain.contracts.agent_runtime.contract_errors import (
    PLAN_CYCLE,
    SUB_AGENT_ADAPTER_INVALID,
    SUB_AGENT_LINEAGE_INVALID,
    TASK_GRAPH_INVALID,
    ContractViolation,
)
from host_brain.contracts.agent_runtime.task_graph import TaskGraphCompletionPolicy
from host_brain.agent_runtime.planner_adapter import (
    CanonicalPlannerOutput,
    GraphRevisionIdentity,
    PLANNER_NEEDS_INPUT,
    adapt_planning_agent_plan,
    adapt_skill_dag_plan,
    build_child_work_order_for_plan_node,
    build_planner_shadow_comparison,
    validate_same_graph_revision,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.agents.planning_agent import PlanStep, StepStatus, TaskPlan
from host_brain.executive_runtime.skill_dag import (
    SkillDagNode,
    SkillDagPlan,
    build_skill_dag_plan,
)
from host_brain.app.sub_agent_protocol import (
    ALL_AGENT_IDS,
    SUB_AGENT_REGISTRY_VIEW_SCHEMA_VERSION,
    build_sub_agent_request_from_child_work_order,
    get_sub_agent_registry_view,
)


def _node(
    node_id: str,
    *,
    dependencies: tuple[str, ...] | set[str] = (),
    allow_parallel: bool = True,
    route: str = "skill",
    target_id: str = "read_only_status",
) -> SkillDagNode:
    return SkillDagNode(
        node_id=node_id,
        step_index=1,
        route=route,
        target_id=target_id,
        description=f"execute {node_id}",
        task_description=f"execute {node_id}",
        route_reason="test:t-nle-05",
        allow_parallel=allow_parallel,
        skill_family="test",
        risk_level=1,
        dependencies=dependencies,  # type: ignore[arg-type]
        capability_tags=(target_id,),
    )


def _dag(
    *nodes: SkillDagNode,
    plan_id: str = "graph-t-nle-05",
    parallel_window: int = 16,
    execution_batches: list[list[str]] | None = None,
) -> SkillDagPlan:
    return SkillDagPlan(
        plan_id=plan_id,
        nodes=list(nodes),
        execution_batches=list(execution_batches or []),
        parallel_window=parallel_window,
    )


def _identity(*, graph_id: str = "graph-t-nle-05", revision: int = 4) -> GraphRevisionIdentity:
    return GraphRevisionIdentity(
        graph_id=graph_id,
        revision=revision,
        root_work_order_id="wo-root",
    )


def _parent_work_order() -> WorkOrderV3:
    return WorkOrderV3(
        work_order_id="wo-root",
        domain="code",
        run_mode="worker",
        objective="execute the canonical plan",
        workspace={"strategy": "temp_workspace"},
        input_pack={"user_request": "execute the canonical plan"},
        allowed_actions=("read_files",),
        forbidden_actions=("push",),
        acceptance_criteria=("all nodes have evidence",),
        verification_plan={"required": True},
        approval_policy={"required": True},
        review_policy={"review_required": True},
        metadata={"request_id": "req-t-nle-05"},
        root_work_order_id="wo-root",
        graph_revision=4,
        capability_requirements=("python",),
        risk_level=2,
        input_refs=("artifact://root/input",),
        output_contract={"type": "plan_result"},
        business_idempotency_scope="t-nle-05",
        operation_fingerprint="sha256:root-operation",
        approval_ref="approval://root",
        review_ref="review://root",
    )


@pytest.mark.parametrize(
    ("plan", "expected_batches"),
    [
        (
            _dag(
                _node("a"),
                _node("b", dependencies=("a",)),
                _node("c", dependencies=("b",)),
            ),
            (("a",), ("b",), ("c",)),
        ),
        (
            _dag(_node("a"), _node("b"), _node("c")),
            (("a", "b", "c"),),
        ),
        (
            _dag(
                _node("a"),
                _node("b", dependencies=("a",)),
                _node("c", dependencies=("a",)),
                _node("d", dependencies=("b", "c")),
            ),
            (("a",), ("b", "c"), ("d",)),
        ),
    ],
)
def test_t_nle_05_preserves_sequential_parallel_and_mixed_dependencies(
    plan: SkillDagPlan,
    expected_batches: tuple[tuple[str, ...], ...],
) -> None:
    adapted = adapt_skill_dag_plan(plan, identity=_identity())

    assert adapted.execution_batches == expected_batches
    for node in adapted.nodes:
        assert len(node.dependency_refs) == len(node.dependencies)
        assert all(
            f"/revisions/{adapted.identity.revision}/nodes/" in ref
            for ref in node.dependency_refs
        )
        graph_node = next(item for item in adapted.task_graph.nodes if item.node_id == node.node_id)
        assert graph_node.dependencies == node.dependencies
        assert graph_node.route == f"{node.route_type}:{node.target_id}"
        assert graph_node.retry_policy == {
            "max_attempts": 1,
            "retry_backoff_ms": 0,
        }
        assert set(node.dependency_refs).issubset(set(graph_node.input_refs))


def test_t_nle_05_ready_siblings_parallel_unless_source_explicitly_disables_it() -> None:
    parallel = adapt_skill_dag_plan(
        _dag(
            _node("a"),
            _node("b", dependencies=("a",)),
            _node("c", dependencies=("a",)),
        ),
        identity=_identity(),
    )
    serial = adapt_skill_dag_plan(
        _dag(
            _node("a"),
            _node("b", dependencies=("a",), allow_parallel=False),
            _node("c", dependencies=("a",), allow_parallel=False),
        ),
        identity=_identity(),
    )

    assert parallel.execution_batches == (("a",), ("b", "c"))
    assert parallel.task_graph.result_node_id == "c"
    assert (
        parallel.task_graph.completion_policy
        is TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1
    )
    assert serial.execution_batches == (("a",), ("b",), ("c",))
    assert serial.task_graph.result_node_id == "c"
    assert (
        serial.task_graph.completion_policy
        is TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1
    )

    atomic_plan = _dag(
        _node("a"),
        _node("b", dependencies=("a",)),
        _node("c", dependencies=("a",)),
    )
    atomic_plan.completion_policy = "ATOMIC_GRAPH_SUCCESS_V1"  # type: ignore[attr-defined]
    atomic = adapt_skill_dag_plan(atomic_plan, identity=_identity())
    assert atomic.task_graph.result_node_id == "c"
    assert (
        atomic.task_graph.completion_policy
        is TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1
    )


def test_t_nle_05_skill_dag_retry_limit_maps_to_canonical_attempt_policy() -> None:
    source = replace(
        _node("retrying"),
        retry_limit=2,
        retry_backoff_ms=250,
    )

    adapted = adapt_skill_dag_plan(_dag(source), identity=_identity())

    assert adapted.node("retrying").retry_policy == {
        "max_attempts": 3,
        "retry_backoff_ms": 250,
    }
    assert adapted.task_graph.nodes[0].retry_policy == {
        "max_attempts": 3,
        "retry_backoff_ms": 250,
    }


def test_t_nle_05_set_dependencies_are_deterministic() -> None:
    adapted = adapt_skill_dag_plan(
        _dag(_node("a"), _node("b"), _node("c", dependencies={"b", "a"})),
        identity=_identity(),
    )

    assert adapted.node("c").dependencies == ("a", "b")


def test_t_nle_05_inline_execution_context_requires_governed_refs() -> None:
    unsafe = _node("unsafe")
    unsafe.args_payload = {"workspace_secret": "inline-value"}

    with pytest.raises(ContractViolation) as error:
        adapt_skill_dag_plan(_dag(unsafe), identity=_identity())

    assert error.value.reason_code == TASK_GRAPH_INVALID
    assert "without governed refs" in error.value.detail


def test_t_nle_05_cycle_fails_closed_with_plan_cycle() -> None:
    cyclic = _dag(
        _node("a", dependencies=("b",)),
        _node("b", dependencies=("a",)),
    )

    with pytest.raises(ContractViolation) as error:
        adapt_skill_dag_plan(cyclic, identity=_identity())

    assert error.value.reason_code == PLAN_CYCLE


def test_t_nle_05_planning_agent_scaffold_ref_and_ready_siblings_are_preserved() -> None:
    plan = TaskPlan(
        task_id="planning-source",
        original_request="analyze then compare",
        created_ts_ms=10,
        steps=[
            PlanStep(1, "reason", "reason", status=StepStatus.PENDING),
            PlanStep(2, "collect", "act", skill_name="read_only_status", depends_on=[1]),
            PlanStep(3, "compare b", "act", skill_name="read_only_status", depends_on=[2]),
            PlanStep(4, "compare c", "act", skill_name="read_only_status", depends_on=[2]),
            PlanStep(5, "synthesize", "synthesize", depends_on=[3, 4]),
        ],
    )

    adapted = adapt_planning_agent_plan(plan, identity=_identity())

    assert adapted.execution_batches == (("step-2",), ("step-3", "step-4"))
    assert adapted.node("step-2").input_refs == (
        "artifact://planner-context/planning-source/steps/1",
    )
    assert adapted.node("step-2").retry_policy == {
        "max_attempts": 1,
        "retry_backoff_ms": 0,
    }


def test_t_nle_05_planning_agent_non_executable_dependency_fails_closed() -> None:
    plan = TaskPlan(
        task_id="planning-invalid",
        original_request="ask then execute",
        steps=[
            PlanStep(1, "ask user", "ask"),
            PlanStep(2, "execute", "act", skill_name="read_only_status", depends_on=[1]),
        ],
    )

    with pytest.raises(ContractViolation) as error:
        adapt_planning_agent_plan(plan, identity=_identity())

    assert error.value.reason_code == PLANNER_NEEDS_INPUT
    assert "requires user input" in error.value.detail


def test_t_nle_05_all_adapters_reuse_one_graph_revision_and_emit_shadow_diff() -> None:
    identity = _identity()
    planning = TaskPlan(
        task_id="planning-source",
        original_request="collect then summarize",
        steps=[
            PlanStep(1, "reason", "reason"),
            PlanStep(2, "collect", "act", skill_name="read_only_status", depends_on=[1]),
            PlanStep(3, "summarize", "act", skill_name="read_only_status", depends_on=[2]),
        ],
    )
    skill_dag = _dag(
        _node("step-2"),
        _node("step-3", dependencies=("step-2",)),
        plan_id="skill-dag-source",
    )

    planning_output = adapt_planning_agent_plan(planning, identity=identity)
    dag_output = adapt_skill_dag_plan(skill_dag, identity=identity)
    validate_same_graph_revision(planning_output, dag_output)
    comparison = build_planner_shadow_comparison(planning_output, dag_output)

    assert planning_output.task_graph.graph_id == dag_output.task_graph.graph_id == identity.graph_id
    assert planning_output.task_graph.revision == dag_output.task_graph.revision == identity.revision
    assert comparison["same_graph_id"] is True
    assert comparison["same_revision"] is True
    assert comparison["same_dependencies"] is True
    assert comparison["same_semantics"] is False
    assert comparison["compatible"] is False
    assert "SEMANTIC_MISMATCH" in comparison["mismatch_reasons"]


def test_t_nle_05_shadow_comparison_checks_complete_execution_semantics() -> None:
    identity = _identity()
    source = adapt_skill_dag_plan(_dag(_node("a")), identity=identity)
    same_semantics = CanonicalPlannerOutput.build(
        identity=identity,
        source_kind="different_adapter",
        source_plan_id="different-source-id",
        planner_ref="planner://different-provenance",
        created_at=999,
        nodes=source.nodes,
        result_node_id=source.task_graph.result_node_id or "a",
        parallel_window=source.parallel_window,
    )
    equivalent = build_planner_shadow_comparison(source, same_semantics)

    assert equivalent["same_semantics"] is True
    assert equivalent["compatible"] is True

    risk_drift = CanonicalPlannerOutput.build(
        identity=identity,
        source_kind="different_adapter",
        source_plan_id="different-source-id",
        planner_ref="planner://different-provenance",
        created_at=999,
        nodes=(replace(source.nodes[0], risk_level=5),),
        result_node_id=source.task_graph.result_node_id or "a",
        parallel_window=source.parallel_window,
    )
    mismatch = build_planner_shadow_comparison(source, risk_drift)

    assert mismatch["same_dependencies"] is True
    assert mismatch["same_routes"] is True
    assert mismatch["same_semantics"] is False
    assert mismatch["mismatch_reasons"] == ["SEMANTIC_MISMATCH"]


def test_t_nle_05_revision_mismatch_is_visible_and_fails_validation() -> None:
    source = adapt_skill_dag_plan(_dag(_node("a")), identity=_identity(revision=4))
    consumer = adapt_skill_dag_plan(_dag(_node("a")), identity=_identity(revision=5))
    comparison = build_planner_shadow_comparison(source, consumer)

    assert comparison["same_graph_id"] is True
    assert comparison["same_revision"] is False
    assert comparison["mismatch_reasons"] == ["REVISION_MISMATCH", "SEMANTIC_MISMATCH"]
    with pytest.raises(ContractViolation) as error:
        validate_same_graph_revision(source, consumer)
    assert error.value.reason_code == TASK_GRAPH_INVALID


def test_t_nle_05_child_work_order_is_the_only_canonical_sub_agent_request_source() -> None:
    plan = adapt_skill_dag_plan(
        _dag(
            _node(
                "delegate",
                dependencies=("collect",),
                route="delegate",
                target_id="intent_agent",
            ),
            _node("collect"),
        ),
        identity=_identity(),
    )
    parent = _parent_work_order()
    child = build_child_work_order_for_plan_node(
        parent_work_order=parent,
        plan=plan,
        node_id="delegate",
    )
    request = build_sub_agent_request_from_child_work_order(
        child_work_order=child,
        agent_id="intent_agent",
        request_id="sub-request",
        session_id="session-1",
    )
    canonical_request = request.context["canonical_sub_agent_request"]

    assert child.root_work_order_id == parent.root_work_order_id
    assert child.parent_work_order_id == parent.work_order_id
    assert child.task_graph_id == plan.identity.graph_id
    assert child.graph_revision == plan.identity.revision
    assert child.task_node_id == "delegate"
    assert canonical_request["child_work_order"]["task_node_id"] == "delegate"
    assert canonical_request["context_refs"] == list(child.input_refs)
    assert request.context["child_work_order"]["parent_work_order_id"] == "wo-root"


def test_t_nle_05_root_work_order_cannot_be_projected_as_sub_agent_request() -> None:
    with pytest.raises(ContractViolation) as error:
        build_sub_agent_request_from_child_work_order(
            child_work_order=_parent_work_order(),
            agent_id="intent_agent",
            request_id="invalid-root-request",
        )

    assert error.value.reason_code == SUB_AGENT_LINEAGE_INVALID


def test_t_nle_05_sub_agent_registry_view_is_deterministic_and_read_only() -> None:
    view = get_sub_agent_registry_view()
    agent_ids = [entry["agent_id"] for entry in view["agents"]]

    assert view["schema_version"] == SUB_AGENT_REGISTRY_VIEW_SCHEMA_VERSION
    assert agent_ids == sorted(ALL_AGENT_IDS)
    assert len(agent_ids) == len(set(agent_ids))
    assert all(entry["capability"] for entry in view["agents"])


def test_t_nle_05_canonical_payload_round_trip_preserves_revision_and_refs() -> None:
    source = adapt_skill_dag_plan(
        _dag(_node("a"), _node("b", dependencies=("a",))),
        identity=_identity(),
    )

    restored = CanonicalPlannerOutput.from_mapping(source.to_persistence_input())

    assert restored.to_dict() == source.to_dict()
    validate_same_graph_revision(source, restored)


def test_t_nle_05_canonical_payload_rejects_semantic_and_batch_drift() -> None:
    source = adapt_skill_dag_plan(
        _dag(
            _node("a", allow_parallel=False),
            _node("b"),
        ),
        identity=_identity(),
    )

    semantic_drift = deepcopy(source.to_dict())
    semantic_drift["task_graph"]["nodes"][0]["risk_level"] = 5
    semantic_drift["task_graph"]["nodes"][0]["input_refs"] = []
    with pytest.raises(ContractViolation) as semantic_error:
        CanonicalPlannerOutput.from_mapping(semantic_drift)
    assert semantic_error.value.reason_code == TASK_GRAPH_INVALID
    assert "exact semantic projection" in semantic_error.value.detail

    batch_drift = deepcopy(source.to_dict())
    batch_drift["execution_batches"] = [["a", "b"]]
    with pytest.raises(ContractViolation) as batch_error:
        CanonicalPlannerOutput.from_mapping(batch_drift)
    assert batch_error.value.reason_code == TASK_GRAPH_INVALID
    assert "deterministic planner projection" in batch_error.value.detail


def test_t_nle_05_parent_graph_revision_cannot_authorize_another_plan() -> None:
    plan = adapt_skill_dag_plan(
        _dag(_node("delegate", route="delegate", target_id="intent_agent")),
        identity=_identity(),
    )
    parent = _parent_work_order()
    parent.task_graph_id = "other-graph"
    parent.task_node_id = "other-node"
    parent.graph_revision = 99
    parent.validate()

    with pytest.raises(ContractViolation) as error:
        build_child_work_order_for_plan_node(
            parent_work_order=parent,
            plan=plan,
            node_id="delegate",
        )

    assert error.value.reason_code == SUB_AGENT_LINEAGE_INVALID
    assert "graph identity" in error.value.detail


def test_t_nle_05_child_target_binding_rejects_sub_agent_redirect() -> None:
    plan = adapt_skill_dag_plan(
        _dag(_node("delegate", route="delegate", target_id="intent_agent")),
        identity=_identity(),
    )
    child = build_child_work_order_for_plan_node(
        parent_work_order=_parent_work_order(),
        plan=plan,
        node_id="delegate",
    )

    with pytest.raises(ContractViolation) as error:
        build_sub_agent_request_from_child_work_order(
            child_work_order=child,
            agent_id="pentest_agent",
            request_id="redirect-attempt",
        )

    assert error.value.reason_code == SUB_AGENT_ADAPTER_INVALID
    assert "bound to intent_agent" in error.value.detail


def test_t_nle_05_child_target_binding_requires_target_capability() -> None:
    plan = adapt_skill_dag_plan(
        _dag(_node("delegate", route="delegate", target_id="intent_agent")),
        identity=_identity(),
    )
    child = build_child_work_order_for_plan_node(
        parent_work_order=_parent_work_order(),
        plan=plan,
        node_id="delegate",
    )
    child.capability_requirements = tuple(
        item for item in child.capability_requirements if item != "intent_agent"
    )
    child.validate()

    with pytest.raises(ContractViolation) as error:
        build_sub_agent_request_from_child_work_order(
            child_work_order=child,
            agent_id="intent_agent",
            request_id="missing-target-capability",
        )

    assert error.value.reason_code == SUB_AGENT_ADAPTER_INVALID
    assert "capability requirements" in error.value.detail


def test_t_nle_05_child_target_binding_rejects_lineage_replay() -> None:
    plan = adapt_skill_dag_plan(
        _dag(_node("delegate", route="delegate", target_id="intent_agent")),
        identity=_identity(),
    )
    child = build_child_work_order_for_plan_node(
        parent_work_order=_parent_work_order(),
        plan=plan,
        node_id="delegate",
    )
    child.parent_work_order_id = "wo-other-parent"
    child.validate()

    with pytest.raises(ContractViolation) as error:
        build_sub_agent_request_from_child_work_order(
            child_work_order=child,
            agent_id="intent_agent",
            request_id="lineage-replay",
        )

    assert error.value.reason_code == SUB_AGENT_LINEAGE_INVALID
    assert "lineage" in error.value.detail


def test_t_nle_05_skill_dag_parallel_window_and_batches_are_preserved() -> None:
    nodes = (_node("a"), _node("b"), _node("c"))
    source = _dag(
        *nodes,
        parallel_window=1,
        execution_batches=[["a"], ["b"], ["c"]],
    )

    adapted = adapt_skill_dag_plan(source, identity=_identity())

    assert adapted.parallel_window == 1
    assert adapted.execution_batches == (("a",), ("b",), ("c",))

    source.execution_batches = [["a", "b", "c"]]
    with pytest.raises(ContractViolation) as error:
        adapt_skill_dag_plan(source, identity=_identity())
    assert error.value.reason_code == TASK_GRAPH_INVALID
    assert "parallel_window" in error.value.detail


def test_t_nle_05_skill_dag_preserves_builder_batch_order() -> None:
    source = build_skill_dag_plan(
        plan_id="builder-order",
        parallel_window=1,
        steps=[
            {
                "step_index": 2,
                "node_id": "b",
                "route": "skill",
                "target_id": "read_only_status",
                "description": "b",
                "task_description": "b",
                "allow_parallel": True,
            },
            {
                "step_index": 1,
                "node_id": "a",
                "route": "skill",
                "target_id": "read_only_status",
                "description": "a",
                "task_description": "a",
                "allow_parallel": True,
            },
        ],
    )

    adapted = adapt_skill_dag_plan(source, identity=_identity())

    assert adapted.parallel_window == source.parallel_window == 1
    assert [node.node_id for node in adapted.nodes] == ["a", "b"]
    assert [list(batch) for batch in adapted.execution_batches] == source.execution_batches


def test_t_nle_05_ask_only_plan_fails_closed_with_stable_reason() -> None:
    plan = TaskPlan(
        task_id="needs-input",
        original_request="ambiguous request",
        steps=[PlanStep(1, "Which repository?", "ask")],
    )

    with pytest.raises(ContractViolation) as error:
        adapt_planning_agent_plan(plan, identity=_identity())

    assert error.value.reason_code == PLANNER_NEEDS_INPUT
    assert "requires user input" in error.value.detail


@pytest.mark.parametrize(
    "needs_input_marker",
    ("plan", "step"),
)
def test_t_nle_05_needs_input_status_fails_closed_with_stable_reason(
    needs_input_marker: str,
) -> None:
    step = PlanStep(1, "collect", "act", skill_name="read_only_status")
    plan = TaskPlan(
        task_id="needs-input-status",
        original_request="ambiguous request",
        steps=[step],
    )
    if needs_input_marker == "plan":
        plan.status = "needs_input"
    else:
        step.status = StepStatus.NEEDS_INPUT

    with pytest.raises(ContractViolation) as error:
        adapt_planning_agent_plan(plan, identity=_identity())

    assert error.value.reason_code == PLANNER_NEEDS_INPUT
    assert "requires user input" in error.value.detail
