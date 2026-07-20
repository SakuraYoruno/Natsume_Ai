from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount, BudgetPolicy
from host_brain.task_runtime.admission import AdmittedExecution, AdmittedTaskNode
from host_brain.task_runtime.budget_runtime import BudgetRuntimeContext
from host_brain.task_runtime.errors import (
    GOVERNED_OPERATION_RECOVERY_REQUIRED,
    GovernedRuntimeError,
)
from host_brain.task_runtime.operation_checkpoint_continuation import (
    CommittedCheckpointContinuation,
)
from host_brain.task_runtime.operation_checkpoint_validation import (
    WorkerBudgetUsageCheckpointState,
)
from host_brain.task_runtime.operation_recovery_runtime import (
    CommittedOperationCheckpoint,
)


class _FailingBudgetRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def reconcile_worker_checkpoint(self, *_: Any, **__: Any) -> Any:
        self.calls.append("reconcile")
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            "durable usage identity drift",
        )

    async def settle_worker_checkpoint(self, *_: Any, **__: Any) -> Any:
        self.calls.append("settle")
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            "durable usage write is ambiguous",
        )


class _ForbiddenLifecycle:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        async def forbidden(*_: Any, **__: Any) -> Any:
            self.calls.append(name)
            raise AssertionError(f"lifecycle mutation is forbidden on recovery hold: {name}")

        return forbidden


class _Outbox:
    def __init__(self) -> None:
        self.pending_calls = 0
        self.build_calls: list[dict[str, Any]] = []

    async def publish_pending(self, *_: Any, **__: Any) -> None:
        self.pending_calls += 1

    def build(self, **payload: Any) -> dict[str, Any]:
        self.build_calls.append(dict(payload))
        return dict(payload)


def _admitted() -> AdmittedExecution:
    return AdmittedExecution(
        command_id="command-1",
        work_order_id="work-order-1",
        assignment_id="assignment-1",
        executor="canonical",
        graph_id="graph-1",
        graph_revision=1,
        nodes=(
            AdmittedTaskNode(
                node_id="node-1",
                worker_route="skill:demo",
                dependencies=(),
                acceptance_criteria=("verified",),
                operation_fingerprint="fingerprint-1",
                reservation_id="reservation-1",
            ),
        ),
        business_scope="scope-1",
    )


def _budget() -> BudgetRuntimeContext:
    limits = BudgetAmount(token_count=10, time_ms=1_000)
    return BudgetRuntimeContext(
        budget_id="budget-1",
        command_id="command-1",
        graph_id="graph-1",
        graph_revision=1,
        policy=BudgetPolicy(scope="graph", limits=limits),
        record={
            "budget_id": "budget-1",
            "status": "ACTIVE",
            "status_version": 1,
            "limits": limits.to_dict(),
            "consumed": BudgetAmount().to_dict(),
            "remaining": limits.to_dict(),
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("usage_state", "expected_budget_call"),
    (
        (WorkerBudgetUsageCheckpointState.EXACT_ACCEPTED, "reconcile"),
        (WorkerBudgetUsageCheckpointState.MISSING, "settle"),
    ),
)
async def test_budget_recovery_error_holds_without_terminal_state_writes(
    usage_state: WorkerBudgetUsageCheckpointState,
    expected_budget_call: str,
) -> None:
    admitted = _admitted()
    graph = {"graph_id": "graph-1", "status": "ACTIVE", "status_version": 2}
    node = {"node_id": "node-1", "status": "LEASED", "status_version": 3}
    attempt = {
        "attempt_id": "attempt-1",
        "status": "LEASED",
        "status_version": 2,
    }
    reservation = {
        "reservation_id": "reservation-1",
        "status": "COMMITTED",
        "status_version": 4,
        "fencing_token": 7,
    }
    lease = {
        "lease_id": "lease-1",
        "status": "ACTIVE",
        "status_version": 2,
        "fencing_token": 7,
    }
    budget = _budget()
    before = deepcopy(
        {
            "graph": graph,
            "node": node,
            "attempt": attempt,
            "reservation": reservation,
            "lease": lease,
            "budget": budget.record,
        }
    )
    checkpoint = CommittedOperationCheckpoint(
        reservation=reservation,
        attempt=attempt,
        worker_commit={"phase": "COMMIT", "done": True},
        receipt_ref="worker-projection://test/" + "a" * 64,
        payload_digest="sha256:" + "a" * 64,
        payload_digest_source="receipt_identity_digest",
        budget_usage_state=usage_state,
        budget_usage=(
            {
                "usage_id": "budget-usage://worker/attempt-1",
                "accepted": True,
            }
            if usage_state is WorkerBudgetUsageCheckpointState.EXACT_ACCEPTED
            else None
        ),
        durable_elapsed_ms=(
            25 if usage_state is WorkerBudgetUsageCheckpointState.MISSING else None
        ),
        lease=lease,
    )
    budgets = _FailingBudgetRuntime()
    lifecycle = _ForbiddenLifecycle()
    outbox = _Outbox()
    continuation = CommittedCheckpointContinuation(
        task_store=object(),
        checkpoint_uow=object(),
        runtime_outbox=outbox,
        budget_runtime=budgets,
        node_lifecycle=lifecycle,
        verifier=object(),
        acceptance=object(),
        review_runtime=None,
        owner_id="owner-1",
        graph_finalizer=object(),
    )

    events = [
        event
        async for event in continuation.resume(
            admitted=admitted,
            command=SimpleNamespace(
                surface="internal",
                route_type="skill",
                target_id="demo",
            ),
            work_order=object(),
            budget=budget,
            graph=graph,
            node=node,
            checkpoint=checkpoint,
            node_grant_ref=None,
            defer_shared_terminal=False,
        )
    ]

    assert budgets.calls == [expected_budget_call]
    assert lifecycle.calls == []
    assert outbox.build_calls == []
    assert outbox.pending_calls == 1
    assert {
        "graph": graph,
        "node": node,
        "attempt": attempt,
        "reservation": reservation,
        "lease": lease,
        "budget": budget.record,
    } == before
    assert len(events) == 1
    receipt = events[0]["execution_receipt"]
    assert receipt["status"] == "RECOVERY_REQUIRED"
    assert receipt["verification_status"] == "NOT_RUN"
    assert receipt["accepted"] is False
    assert receipt["projection"]["recovery_state"] == "HOLD"
