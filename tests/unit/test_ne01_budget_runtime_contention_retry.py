from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetPolicy,
    BudgetReservationStatus,
)
from host_brain.persistence.task_repository import (
    TASK_STORE_CAS_CONFLICT,
    TaskStoreError,
)
from host_brain.task_runtime.budget_runtime import (
    BudgetRuntimeContext,
    DurableBudgetRuntime,
)


class _ContendedStore:
    def __init__(self, *, conflicts: int) -> None:
        self.conflicts = int(conflicts)
        self.calls = 0
        self.record = {
            "budget_id": "budget-1",
            "scope": "test",
            "graph_id": "graph-1",
            "graph_revision": 1,
            "status": "ACTIVE",
            "status_version": 7,
            "limits": BudgetAmount(token_count=100, concurrency=8).to_dict(),
            "reserved": BudgetAmount(token_count=100, concurrency=8).to_dict(),
            "consumed": BudgetAmount().to_dict(),
            "remaining": BudgetAmount(token_count=100, concurrency=8).to_dict(),
        }

    def reconcile_budget(self, budget_id: str) -> dict[str, Any]:
        assert budget_id == "budget-1"
        return dict(self.record)

    def get_budget_usage(self, budget_id: str, usage_id: str) -> None:
        assert (budget_id, usage_id) == ("budget-1", "budget-usage://accepted/1")
        return None

    def get_node_budget_reservation(
        self,
        *,
        parent_budget_id: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        assert (parent_budget_id, attempt_id) == ("budget-1", "attempt-1")
        return _node_budget_record()

    def consume_budget_once(self, budget_id: str, **_: Any) -> Any:
        assert budget_id == "budget-1"
        self.calls += 1
        if self.calls <= self.conflicts:
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "simulated admitted sibling CAS wave",
            )
        self.record = {**self.record, "status_version": 8}
        return SimpleNamespace(
            accepted=True,
            replayed=False,
            exceeded_dimensions=(),
        )


class _OutboxFactory:
    @staticmethod
    def create(**kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)


def _node_budget_record() -> dict[str, Any]:
    amount = BudgetAmount(token_count=100, concurrency=1).to_dict()
    return {
        "schema_version": "Natsume.task_runtime.node_budget_reservation.v1",
        "node_budget_id": "node-budget::attempt-1",
        "parent_budget_id": "budget-1",
        "graph_id": "graph-1",
        "graph_revision": 1,
        "node_id": "node-1",
        "attempt_id": "attempt-1",
        "scope": "test",
        "limits": BudgetAmount(token_count=100, concurrency=8).to_dict(),
        "reserved": amount,
        "consumed": BudgetAmount().to_dict(),
        "attempted_consumed": BudgetAmount().to_dict(),
        "remaining": amount,
        "usage_version": 1,
        "parent_version_at_claim": 7,
        "claim_origin": "ATOMIC_READY_CLAIM",
        "attempt_status": "RUNNING",
        "status": "ACTIVE",
    }


@pytest.mark.asyncio
async def test_budget_charge_cas_attempts_scale_with_admitted_concurrency() -> None:
    store = _ContendedStore(conflicts=8)
    policy = BudgetPolicy(
        scope="test",
        limits=BudgetAmount(token_count=100, concurrency=8),
    )
    context = BudgetRuntimeContext(
        budget_id="budget-1",
        command_id="command-1",
        graph_id="graph-1",
        graph_revision=1,
        policy=policy,
        record=dict(store.record),
        node_record=_node_budget_record(),
    )
    runtime = DurableBudgetRuntime(
        task_store=store,
        outbox_factory=_OutboxFactory(),
        cas_retries=1,
    )

    charge = await runtime._charge(  # noqa: SLF001
        context,
        usage_id="budget-usage://accepted/1",
        charge_kind="WORKER_USAGE",
        node_id="node-1",
        attempt_id="attempt-1",
        delta=BudgetAmount(token_count=1),
    )

    assert charge.accepted is True
    assert charge.context.version == 8
    assert store.calls == 9


@pytest.mark.asyncio
async def test_finalization_outbox_uses_the_refreshed_budget_version() -> None:
    class _FinalizeStore:
        def __init__(self) -> None:
            self.record = {
                "budget_id": "budget-1",
                "scope": "test",
                "graph_id": "graph-1",
                "graph_revision": 1,
                "status": "ACTIVE",
                "status_version": 8,
                "limits": BudgetAmount(token_count=100).to_dict(),
                "reserved": BudgetAmount(token_count=100).to_dict(),
                "consumed": BudgetAmount().to_dict(),
                "remaining": BudgetAmount(token_count=100).to_dict(),
            }

        def reconcile_budget(self, budget_id: str) -> dict[str, Any]:
            assert budget_id == "budget-1"
            return dict(self.record)

        def transition_budget(self, budget_id: str, **kwargs: Any) -> dict[str, Any]:
            assert budget_id == "budget-1"
            assert kwargs["expected_version"] == 8
            assert kwargs["outbox"]["aggregate_version"] == 9
            self.record = {
                **self.record,
                "status": kwargs["target_status"],
                "status_version": 9,
            }
            return dict(self.record)

    policy = BudgetPolicy(scope="test", limits=BudgetAmount(token_count=100))
    context = BudgetRuntimeContext(
        budget_id="budget-1",
        command_id="command-1",
        graph_id="graph-1",
        graph_revision=1,
        policy=policy,
        record={
            "budget_id": "budget-1",
            "status": "ACTIVE",
            "status_version": 7,
        },
    )
    runtime = DurableBudgetRuntime(
        task_store=_FinalizeStore(),
        outbox_factory=_OutboxFactory(),
    )

    finalized = await runtime.finalize(
        context,
        target_status=BudgetReservationStatus.RELEASED,
    )

    assert finalized.status == BudgetReservationStatus.RELEASED.value
    assert finalized.version == 9
