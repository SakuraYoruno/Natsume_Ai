from __future__ import annotations

import json
from pathlib import Path

import pytest

from host_brain.agent_runtime.budget_policy import (
    BUDGET_POLICY_SCHEMA_VERSION,
    BUDGET_RESERVATION_SCHEMA_VERSION,
    BudgetAmount,
    BudgetExceededAction,
    BudgetPolicy,
    BudgetReservation,
    BudgetReservationStatus,
    validate_budget_transition,
)
from host_brain.agent_runtime.contract_errors import (
    BUDGET_RESERVATION_INVALID,
    CONTRACT_VERSION_UNSUPPORTED,
    PLAN_CYCLE,
    REVIEW_TRANSITION_INVALID,
    SUB_AGENT_LINEAGE_INVALID,
    TASK_TRANSITION_INVALID,
    ContractViolation,
)
from host_brain.agent_runtime.contract_schema import build_contract_schema_snapshot
from host_brain.agent_runtime.review_policy import (
    REVIEW_ITEM_SCHEMA_VERSION,
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
    validate_review_transition,
)
from host_brain.agent_runtime.sub_agent_contract import (
    SUB_AGENT_SPEC_SCHEMA_VERSION,
    SubAgentRequestAdapter,
    SubAgentRequestContract,
    SubAgentSpec,
)
from host_brain.agent_runtime.task_graph import (
    TASK_EVENT_SCHEMA_VERSION,
    TASK_GRAPH_SCHEMA_VERSION,
    TASK_NODE_SCHEMA_VERSION,
    EventActorType,
    EventRedactionLevel,
    TaskEvent,
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
    validate_task_graph_transition,
    validate_task_node_transition,
)
from host_brain.agent_runtime.verification_policy import (
    VERIFICATION_RESULT_SCHEMA_VERSION,
    VerificationResult,
    VerificationStatus,
)
from host_brain.agent_runtime.work_order import (
    WORK_ORDER_V2_SCHEMA_VERSION,
    WORK_ORDER_V3_SCHEMA_VERSION,
    AgentWorkOrder,
    WorkOrderV3,
)


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = ROOT / "tests" / "fixtures" / "nle_contracts" / "canonical_contracts.v1.json"


def _v2_payload() -> dict[str, object]:
    return {
        "work_order_id": "wo-v2",
        "domain": "code",
        "run_mode": "inspect",
        "objective": "inspect the runtime",
        "workspace": {"strategy": "temp_workspace"},
        "input_pack": {"user_request": "inspect"},
        "allowed_actions": ["read_files"],
        "forbidden_actions": ["write_files"],
        "acceptance_criteria": ["report exists"],
        "verification_plan": {"required": False},
        "approval_policy": {},
        "review_policy": {"review_required": False},
        "metadata": {"request_id": "req-v2"},
        "legacy_extension": {"preserve": True},
    }


def _budget_policy() -> BudgetPolicy:
    return BudgetPolicy(
        scope="work_order:wo-child",
        limits=BudgetAmount(
            token_count=20_000,
            tool_call_count=20,
            time_ms=120_000,
            concurrency=2,
            retry_count=1,
            model_cost_micros=500_000,
        ),
        on_exceeded=BudgetExceededAction.REVIEW,
    )


def _work_order_v3(*, child: bool = False) -> WorkOrderV3:
    return WorkOrderV3(
        work_order_id="wo-child" if child else "wo-root",
        domain="code",
        run_mode="worker",
        objective="implement a bounded change",
        workspace={"strategy": "temp_workspace"},
        input_pack={"user_request": "implement"},
        allowed_actions=("read_files", "write_files"),
        forbidden_actions=("push",),
        acceptance_criteria=("tests pass",),
        verification_plan={"required": True},
        approval_policy={"required": True},
        review_policy={"review_required": True},
        metadata={"request_id": "req-v3"},
        root_work_order_id="wo-root",
        parent_work_order_id="wo-parent" if child else None,
        task_graph_id="graph-1" if child else None,
        task_node_id="node-2" if child else None,
        graph_revision=3,
        attempt=2,
        capability_requirements=("python", "pytest"),
        risk_level=3,
        input_refs=("artifact://input/1",),
        output_contract={"type": "patch"},
        budget_policy=_budget_policy(),
        business_idempotency_scope="repo:task-0466",
        operation_fingerprint="sha256:operation",
        status_version=7,
        approval_ref="approval://1",
        review_ref="review://1",
    )


def _task_node(node_id: str, *, dependencies: tuple[str, ...] = ()) -> TaskNode:
    return TaskNode(
        node_id=node_id,
        dependencies=dependencies,
        route="skill:test",
        input_refs=("artifact://input/1",),
        output_contract={"type": "test_result"},
        retry_policy={"max_attempts": 1},
        risk_level=2,
        acceptance_criteria=("exit code is zero",),
        status=TaskNodeStatus.CREATED,
        status_version=0,
    )


def test_t_nle_04_legacy_agent_work_order_v2_public_shape_is_unchanged() -> None:
    payload = _v2_payload()
    payload.pop("legacy_extension")
    order = AgentWorkOrder.from_mapping(payload)

    assert order.to_dict() == payload
    assert WORK_ORDER_V2_SCHEMA_VERSION == "Natsume.agent.work_order.v2"


def test_t_nle_04_unversioned_v2_payload_round_trips_exactly_through_v3_adapter() -> None:
    payload = _v2_payload()

    canonical = WorkOrderV3.from_mapping(payload)

    assert canonical.schema_version == WORK_ORDER_V3_SCHEMA_VERSION
    assert canonical.root_work_order_id == "wo-v2"
    assert canonical.to_v2_mapping() == payload


def test_t_nle_04_explicit_v2_schema_alias_round_trips_exactly() -> None:
    payload = {"schema_version": WORK_ORDER_V2_SCHEMA_VERSION, **_v2_payload()}

    assert WorkOrderV3.from_mapping(payload).to_v2_mapping() == payload


def test_t_nle_04_v3_serialization_round_trip_is_stable() -> None:
    original = _work_order_v3(child=True)
    original.validate()

    restored = WorkOrderV3.from_mapping(original.to_dict())

    assert restored.to_dict() == original.to_dict()


def test_t_nle_04_v3_to_v2_to_v3_preserves_lineage_and_policy_metadata() -> None:
    original = _work_order_v3(child=True)

    v2_projection = original.to_v2_mapping()
    metadata = dict(v2_projection["metadata"])
    restored = WorkOrderV3.from_mapping(v2_projection)

    assert metadata["root_work_order_id"] == "wo-root"
    assert metadata["parent_work_order_id"] == "wo-parent"
    assert metadata["task_graph_id"] == "graph-1"
    assert metadata["task_node_id"] == "node-2"
    assert metadata["graph_revision"] == 3
    assert metadata["attempt"] == 2
    assert metadata["business_idempotency_scope"] == "repo:task-0466"
    assert restored.to_dict() == original.to_dict()


@pytest.mark.parametrize(
    ("parser", "payload"),
    [
        (WorkOrderV3.from_mapping, {"schema_version": "Natsume.agent.work_order.v99"}),
        (TaskGraph.from_mapping, {"schema_version": "Natsume.agent.task_graph.v99"}),
        (VerificationResult.from_mapping, {"schema_version": "Natsume.agent.verification_result.v99"}),
        (ReviewItem.from_mapping, {"schema_version": "Natsume.agent.review_item.v99"}),
        (SubAgentSpec.from_mapping, {"schema_version": "Natsume.agent.sub_agent_spec.v99"}),
    ],
)
def test_t_nle_04_unknown_contract_versions_fail_closed(parser, payload) -> None:
    with pytest.raises(ContractViolation) as error:
        parser(payload)

    assert error.value.reason_code == CONTRACT_VERSION_UNSUPPORTED


def test_t_nle_04_task_graph_round_trip_preserves_revision_and_dependencies() -> None:
    graph = TaskGraph(
        graph_id="graph-1",
        root_work_order_id="wo-root",
        revision=3,
        planner_ref="planner://planning-agent/1",
        status=TaskGraphStatus.CREATED,
        created_at=1_700_000_000_000,
        nodes=(_task_node("node-1"), _task_node("node-2", dependencies=("node-1",))),
    )
    graph.validate()

    restored = TaskGraph.from_mapping(graph.to_dict())

    assert restored.to_dict() == graph.to_dict()


def test_t_nle_04_task_graph_cycle_fails_closed_with_stable_reason() -> None:
    graph = TaskGraph(
        graph_id="graph-cycle",
        root_work_order_id="wo-root",
        revision=1,
        planner_ref="planner://test",
        status=TaskGraphStatus.CREATED,
        created_at=1,
        nodes=(
            _task_node("node-a", dependencies=("node-b",)),
            _task_node("node-b", dependencies=("node-a",)),
        ),
    )

    with pytest.raises(ContractViolation) as error:
        graph.validate()

    assert error.value.reason_code == PLAN_CYCLE


def test_t_nle_04_illegal_task_state_transitions_are_rejected() -> None:
    assert validate_task_node_transition("READY", "LEASED") is TaskNodeStatus.LEASED
    assert validate_task_graph_transition("CREATED", "ACTIVE") is TaskGraphStatus.ACTIVE

    with pytest.raises(ContractViolation) as node_error:
        validate_task_node_transition("VERIFIED", "READY")
    with pytest.raises(ContractViolation) as graph_error:
        validate_task_graph_transition("COMPLETED", "ACTIVE")

    assert node_error.value.reason_code == TASK_TRANSITION_INVALID
    assert graph_error.value.reason_code == TASK_TRANSITION_INVALID


@pytest.mark.parametrize(
    ("status", "allows_verified"),
    [
        (VerificationStatus.PASS, True),
        (VerificationStatus.FAIL, False),
        (VerificationStatus.ERROR, False),
        (VerificationStatus.NOT_RUN, False),
    ],
)
def test_t_nle_04_verification_result_status_contract(status, allows_verified) -> None:
    result = VerificationResult(
        verification_id=f"verify-{status.value.lower()}",
        work_order_id="wo-root",
        required=True,
        status=status,
        reason_code=None if status is VerificationStatus.PASS else f"VERIFICATION_{status.value}",
        evidence_refs=("evidence://1",),
    )
    result.validate()

    restored = VerificationResult.from_mapping(result.to_dict())

    assert restored.allows_verified_transition is allows_verified
    assert restored.status is status


def test_t_nle_04_non_pass_verification_requires_stable_reason_code() -> None:
    result = VerificationResult(
        verification_id="verify-fail",
        work_order_id="wo-root",
        required=True,
        status=VerificationStatus.FAIL,
    )

    with pytest.raises(ContractViolation) as error:
        result.validate()

    assert error.value.reason_code == "VERIFICATION_RESULT_INVALID"


def test_t_nle_04_review_types_keep_pre_and_post_execution_resume_separate() -> None:
    review = ReviewItem(
        review_id="review-1",
        review_type=ReviewType.PRE_EXECUTION_APPROVAL,
        subject_ref="task://graph-1/node-1",
        subject_version=4,
        requester="scheduler",
        reason="write operation requires approval",
        risk_level=3,
        side_effects=("write_files",),
        evidence_refs=("evidence://diff/1",),
        status=ReviewStatus.PENDING,
        expires_at=1_800_000_000_000,
        resume_phase=ReviewResumePhase.PRE_EXECUTION,
    )
    review.validate()

    assert ReviewItem.from_mapping(review.to_dict()).to_dict() == review.to_dict()
    assert validate_review_transition("PENDING", "APPROVED") is ReviewStatus.APPROVED
    with pytest.raises(ContractViolation) as error:
        validate_review_transition("CONSUMED", "APPROVED")
    assert error.value.reason_code == REVIEW_TRANSITION_INVALID


def test_t_nle_04_budget_contract_rejects_over_reservation_and_illegal_transition() -> None:
    payload = {
        "schema_version": BUDGET_RESERVATION_SCHEMA_VERSION,
        "budget_id": "budget-1",
        "scope": "work_order:wo-root",
        "limits": {"token_count": 10},
        "reserved": {"token_count": 11},
        "consumed": {"token_count": 0},
        "status": "PENDING",
        "status_version": 0,
    }

    with pytest.raises(ContractViolation) as reservation_error:
        BudgetReservation.from_mapping(payload)
    with pytest.raises(ContractViolation):
        validate_budget_transition(BudgetReservationStatus.CONSUMED, BudgetReservationStatus.ACTIVE)

    assert reservation_error.value.reason_code == BUDGET_RESERVATION_INVALID


def test_t_nle_04_sub_agent_adapter_requires_child_lineage_and_projects_legacy_shape() -> None:
    spec = SubAgentSpec(
        agent_id="test_agent",
        capability_requirements=("python",),
        allowed_actions=("read_files", "write_files"),
        timeout_ms=45_000,
        max_attempts=1,
    )
    request = SubAgentRequestAdapter.adapt(
        child_work_order=_work_order_v3(child=True),
        spec=spec,
        request_id="request-1",
        session_id="session-1",
        context_refs=("context://1",),
    )

    restored = SubAgentRequestContract.from_mapping(request.to_dict())
    legacy = restored.to_legacy_mapping()

    assert legacy["task"] == "implement a bounded change"
    assert legacy["context"]["child_work_order"]["parent_work_order_id"] == "wo-parent"
    with pytest.raises(ContractViolation) as error:
        SubAgentRequestAdapter.adapt(
            child_work_order=_work_order_v3(child=False),
            spec=spec,
            request_id="request-2",
        )
    assert error.value.reason_code == SUB_AGENT_LINEAGE_INVALID


def test_t_nle_04_task_event_round_trip_uses_refs_not_inline_payload() -> None:
    event = TaskEvent(
        event_id="event-1",
        event_type="TASK_NODE_READY",
        ts_ms=1_700_000_000_000,
        trace_id="trace-1",
        actor_type=EventActorType.SYSTEM,
        actor_id="scheduler",
        sequence_no=1,
        cursor="cursor-1",
        work_order_id="wo-root",
        task_graph_id="graph-1",
        task_node_id="node-1",
        payload_ref="artifact://event/1",
        evidence_refs=("evidence://1",),
        redaction_level=EventRedactionLevel.INTERNAL,
    )
    event.validate()

    restored = TaskEvent.from_mapping(event.to_dict())

    assert restored.to_dict() == event.to_dict()
    assert "payload" not in restored.to_dict()


def test_t_nle_04_schema_snapshot_matches_frozen_fixture() -> None:
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    assert build_contract_schema_snapshot() == expected
    assert expected["contracts"]["WorkOrderV3"]["schema_version"] == WORK_ORDER_V3_SCHEMA_VERSION
    assert expected["contracts"]["TaskGraph"]["schema_version"] == TASK_GRAPH_SCHEMA_VERSION
    assert expected["contracts"]["TaskNode"]["schema_version"] == TASK_NODE_SCHEMA_VERSION
    assert expected["contracts"]["TaskEvent"]["schema_version"] == TASK_EVENT_SCHEMA_VERSION
    assert expected["contracts"]["VerificationResult"]["schema_version"] == VERIFICATION_RESULT_SCHEMA_VERSION
    assert expected["contracts"]["ReviewItem"]["schema_version"] == REVIEW_ITEM_SCHEMA_VERSION
    assert expected["contracts"]["BudgetPolicy"]["schema_version"] == BUDGET_POLICY_SCHEMA_VERSION
    assert expected["contracts"]["SubAgentSpec"]["schema_version"] == SUB_AGENT_SPEC_SCHEMA_VERSION


def test_t_nle_04_skill_dag_planner_compatibility_reexports_canonical_types() -> None:
    from host_brain.app.skill_dag_planner import (
        CanonicalTaskGraph,
        CanonicalTaskNode,
        SkillDagPlan,
    )

    assert CanonicalTaskGraph is TaskGraph
    assert CanonicalTaskNode is TaskNode
    assert SkillDagPlan(plan_id="legacy").plan_id == "legacy"
