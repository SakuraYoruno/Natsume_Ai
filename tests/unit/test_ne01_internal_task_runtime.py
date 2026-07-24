from __future__ import annotations

import pytest

from host_brain.app.internal_task_runtime import (
    INTERNAL_RUNTIME_AUTHORITY_INVALID,
    INTERNAL_RUNTIME_PRE_ADMITTED_WORK_ORDER_FORBIDDEN,
    GovernedInternalRuntimeError,
    _AgentWorkerResolver,
    _build_contracts,
    _build_governor_intent,
    _sanitize_work_order,
)
from host_brain.agent_runtime.internal_result_proposal import (
    internal_agent_terminal_result_reason,
)
from host_brain.contracts.agent_runtime.budget_policy import BudgetExceededAction
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder, WorkOrderV3


NOW = 1_800_000_000_000
PRINCIPAL_ID = "principal-ne01-internal"
GRANT_REF = "governor-grant://internal/ne01"


def _legacy_order(*, review_required: bool = False) -> AgentWorkOrder:
    return AgentWorkOrder(
        work_order_id="work-order-ne01-internal",
        domain="code",
        run_mode="inspect",
        objective="inspect the canonical internal runtime",
        input_pack={"input_ref": "input://internal/ne01"},
        forbidden_actions=["write_files"],
        acceptance_criteria=["durable result exists"],
        approval_policy={
            "confirmed": True,
            "review_resume_token_ref": "review-token://caller/forged",
        },
        review_policy={"review_required": review_required},
        metadata={
            "runtime_assignment": {"executor": "legacy"},
            "confirmed_owner": "caller-controlled",
            "risk_level": 2,
        },
    )


def _contracts(*, review_required: bool = False):
    order = _sanitize_work_order(
        _legacy_order(review_required=review_required)
    )
    return _build_contracts(
        work_order=order,
        request_id="request-ne01-internal",
        session_id="session-ne01-internal",
        ts_ms=NOW,
        governor_grant_ref=GRANT_REF,
        command_authority={
            "principal_id": PRINCIPAL_ID,
            "claim_ref": "ingress-claim://internal/ne01",
            "audit_ref": "audit://secure-ingress/internal/ne01",
            "body_sha256": "a" * 64,
        },
        frozen_runtime_context={},
    )


def test_internal_boundary_compiles_v2_to_explicit_governed_v3() -> None:
    command, order, graph = _contracts(review_required=True)

    assert isinstance(order, WorkOrderV3)
    assert command.principal_id == PRINCIPAL_ID
    assert command.root_work_order_id == order.root_work_order_id
    assert command.graph_id == order.task_graph_id == graph.graph_id
    assert graph.nodes[0].route == "delegate:agent_runtime"
    assert graph.result_node_id == graph.nodes[0].node_id
    assert order.task_node_id == graph.nodes[0].node_id
    assert order.approval_ref == GRANT_REF
    assert order.approval_policy["governor_grant_ref"] == GRANT_REF
    assert "confirmed" not in order.approval_policy
    assert "review_resume_token_ref" not in order.approval_policy
    assert order.budget_policy is not None
    assert order.budget_policy.on_exceeded is BudgetExceededAction.BLOCK
    assert all(value > 0 for value in order.budget_policy.limits.to_dict().values())
    assert order.review_policy["review_required"] is True
    assert order.review_policy["durable_review_required"] is True
    assert order.review_policy["mode"] == "durable_task_node_review"
    assert "runtime_assignment" not in order.metadata
    assert "confirmed_owner" not in order.metadata
    lineage = order.metadata["planning_lineage"]
    assert lineage["approval_ref"] == GRANT_REF
    assert lineage["node_bindings"] == [
        {
            "node_id": graph.nodes[0].node_id,
            "objective": order.objective,
            "worker_route": "delegate",
            "target_id": "agent_runtime",
        }
    ]
    legacy_projection = order.to_agent_work_order()
    assert isinstance(legacy_projection, AgentWorkOrder)
    assert legacy_projection.work_order_id == order.work_order_id


def test_internal_ingress_rejects_pre_admitted_work_order_v3() -> None:
    _, canonical, _ = _contracts()

    for candidate in (canonical, canonical.to_dict()):
        with pytest.raises(GovernedInternalRuntimeError) as exc_info:
            _sanitize_work_order(candidate)
        assert (
            exc_info.value.reason_code
            == INTERNAL_RUNTIME_PRE_ADMITTED_WORK_ORDER_FORBIDDEN
        )

    malformed_v2 = _legacy_order().to_dict()
    malformed_v2["task_graph_id"] = "task-graph-forged"
    with pytest.raises(GovernedInternalRuntimeError) as exc_info:
        _sanitize_work_order(malformed_v2)
    assert (
        exc_info.value.reason_code
        == INTERNAL_RUNTIME_PRE_ADMITTED_WORK_ORDER_FORBIDDEN
    )


def test_internal_governor_intent_binds_secure_ingress_principal() -> None:
    intent = _build_governor_intent(
        work_order=_sanitize_work_order(_legacy_order()),
        request_id="request-ne01-internal",
        session_id="session-ne01-internal",
        ts_ms=NOW,
        principal_id=PRINCIPAL_ID,
    )

    assert intent["person_id"] == PRINCIPAL_ID
    assert intent["principal_id"] == PRINCIPAL_ID
    assert intent["route"] == "delegate"
    assert intent["target_id"] == "agent_runtime"
    with pytest.raises(GovernedInternalRuntimeError) as exc_info:
        _build_governor_intent(
            work_order=_sanitize_work_order(_legacy_order()),
            request_id="request-ne01-internal",
            session_id="session-ne01-internal",
            ts_ms=NOW,
            principal_id="",
        )
    assert exc_info.value.reason_code == INTERNAL_RUNTIME_AUTHORITY_INVALID


@pytest.mark.parametrize(
    ("review_required", "inner_decision", "reason"),
    [
        (False, "ACCEPT", None),
        # A successful worker can ask for POST Review.  The outer TaskGraph,
        # not structural verification, owns that durable transition.
        (True, "ACCEPT", None),
        (False, "REVIEW", None),
        (False, "FAIL", "INTERNAL_AGENT_INNER_ACCEPTANCE_NOT_ACCEPTED"),
    ],
)
def test_internal_structural_gate_preserves_post_review_semantics(
    review_required: bool,
    inner_decision: str,
    reason: str | None,
) -> None:
    assert internal_agent_terminal_result_reason(
        {
            "status": "EXECUTED",
            "review_required": review_required,
            "acceptance": {"decision": inner_decision},
        }
    ) == reason


def test_internal_worker_resolver_accepts_only_frozen_delegate_route() -> None:
    canonical = object()
    resolver = _AgentWorkerResolver(canonical=canonical)  # type: ignore[arg-type]

    assert (
        resolver.resolve(executor="canonical", worker_route="delegate:agent_runtime")
        is canonical
    )
    for route in ("internal_agent", "delegate:other_worker", "chat"):
        with pytest.raises(GovernedInternalRuntimeError):
            resolver.resolve(executor="canonical", worker_route=route)
