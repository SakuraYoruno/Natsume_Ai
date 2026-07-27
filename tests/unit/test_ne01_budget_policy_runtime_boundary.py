from __future__ import annotations

from typing import Any

import pytest

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetExceededAction,
    BudgetPolicy,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.task_runtime.admission import (
    AdmittedExecution,
    AdmittedTaskNode,
)
from host_brain.task_runtime.budget_runtime import (
    BudgetRuntimeContext,
    DurableBudgetRuntime,
    SUPPORTED_RUNTIME_BUDGET_ACTIONS,
)
from host_brain.task_runtime.errors import (
    GOVERNED_BUDGET_ACTION_UNSUPPORTED,
    GOVERNED_BUDGET_POLICY_REQUIRED,
    GovernedRuntimeError,
)


class _NoMutationStore:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        def reject(*_args: Any, **_kwargs: Any) -> None:
            self.calls.append(name)
            raise AssertionError(f"unsupported Budget action reached Store.{name}")

        return reject


class _NoMutationOutboxFactory:
    @staticmethod
    def create(**_kwargs: Any) -> None:
        raise AssertionError("unsupported Budget action created an Outbox")


def _admitted() -> AdmittedExecution:
    return AdmittedExecution(
        command_id="command-budget-policy",
        work_order_id="work-order-budget-policy",
        assignment_id="assignment-budget-policy",
        executor="CANONICAL",
        graph_id="graph-budget-policy",
        graph_revision=1,
        nodes=(
            AdmittedTaskNode(
                node_id="node-budget-policy",
                worker_route="chat",
                dependencies=(),
                acceptance_criteria=(),
                operation_fingerprint="a" * 64,
                reservation_id="reservation-budget-policy",
            ),
        ),
        business_scope="budget-policy-test",
    )


def _work_order(action: BudgetExceededAction) -> WorkOrderV3:
    return WorkOrderV3(
        work_order_id="work-order-budget-policy",
        domain="chat",
        run_mode="inspect",
        objective="prove unsupported Budget actions fail before persistence",
        root_work_order_id="work-order-budget-policy",
        task_graph_id="graph-budget-policy",
        task_node_id="node-budget-policy",
        graph_revision=1,
        budget_policy=BudgetPolicy(
            scope="work-order:work-order-budget-policy",
            limits=BudgetAmount(
                token_count=100,
                tool_call_count=1,
                time_ms=1_000,
                concurrency=1,
                retry_count=1,
                model_cost_micros=10_000,
            ),
            on_exceeded=action,
        ),
    )


def _context(action: BudgetExceededAction) -> BudgetRuntimeContext:
    policy = _work_order(action).budget_policy
    assert policy is not None
    amount = policy.limits.to_dict()
    return BudgetRuntimeContext(
        budget_id="budget-policy",
        command_id="command-budget-policy",
        graph_id="graph-budget-policy",
        graph_revision=1,
        policy=policy,
        record={
            "status": "ACTIVE",
            "status_version": 0,
            "limits": amount,
            "reserved": amount,
            "consumed": BudgetAmount().to_dict(),
            "remaining": amount,
        },
        node_record={
            "node_budget_id": "node-budget-policy",
            "parent_budget_id": "budget-policy",
            "node_id": "node-budget-policy",
            "attempt_id": "attempt-budget-policy",
            "status": "ACTIVE",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    (BudgetExceededAction.DEGRADE, BudgetExceededAction.REVIEW),
)
async def test_unsupported_action_fails_before_store_or_outbox(
    action: BudgetExceededAction,
) -> None:
    store = _NoMutationStore()
    runtime = DurableBudgetRuntime(
        task_store=store,
        outbox_factory=_NoMutationOutboxFactory(),
    )

    with pytest.raises(GovernedRuntimeError) as caught:
        await runtime.ensure_active(
            admitted=_admitted(),
            work_order=_work_order(action),
        )

    assert caught.value.reason_code == GOVERNED_BUDGET_ACTION_UNSUPPORTED
    assert store.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    (BudgetExceededAction.DEGRADE, BudgetExceededAction.REVIEW),
)
async def test_direct_charge_cannot_bypass_runtime_action_gate(
    action: BudgetExceededAction,
) -> None:
    store = _NoMutationStore()
    runtime = DurableBudgetRuntime(
        task_store=store,
        outbox_factory=_NoMutationOutboxFactory(),
    )

    with pytest.raises(GovernedRuntimeError) as caught:
        await runtime.precharge(
            _context(action),
            node_id="node-budget-policy",
            attempt_id="attempt-budget-policy",
        )

    assert caught.value.reason_code == GOVERNED_BUDGET_ACTION_UNSUPPORTED
    assert store.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    (BudgetExceededAction.DEGRADE, BudgetExceededAction.REVIEW),
)
async def test_direct_finalization_cannot_bypass_runtime_action_gate(
    action: BudgetExceededAction,
) -> None:
    store = _NoMutationStore()
    runtime = DurableBudgetRuntime(
        task_store=store,
        outbox_factory=_NoMutationOutboxFactory(),
    )

    with pytest.raises(GovernedRuntimeError) as caught:
        await runtime.finalize(
            _context(action),
            target_status=BudgetReservationStatus.RELEASED,
        )

    assert caught.value.reason_code == GOVERNED_BUDGET_ACTION_UNSUPPORTED
    assert store.calls == []


@pytest.mark.asyncio
async def test_direct_context_rejects_untyped_action_with_stable_runtime_reason() -> None:
    store = _NoMutationStore()
    runtime = DurableBudgetRuntime(
        task_store=store,
        outbox_factory=_NoMutationOutboxFactory(),
    )
    context = _context(BudgetExceededAction.BLOCK)
    object.__setattr__(context.policy, "on_exceeded", "REVIEW")

    with pytest.raises(GovernedRuntimeError) as caught:
        await runtime.precharge(
            context,
            node_id="node-budget-policy",
            attempt_id="attempt-budget-policy",
        )

    assert caught.value.reason_code == GOVERNED_BUDGET_POLICY_REQUIRED
    assert store.calls == []


def test_runtime_support_matrix_is_explicit_and_block_only() -> None:
    assert SUPPORTED_RUNTIME_BUDGET_ACTIONS == frozenset(
        {BudgetExceededAction.BLOCK}
    )
    assert BudgetPolicy(
        scope="proposal",
        limits=BudgetAmount(),
        on_exceeded=BudgetExceededAction.REVIEW,
    ).to_dict()["on_exceeded"] == "REVIEW"
